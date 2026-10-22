"""
DeskCortex — GroundedRAGSkill (Camada de Conhecimento)
======================================================
Recuperador determinístico de manuais oficiais, procedimentos com GIFs e
documentação técnica do ERP Master via PostgreSQL Híbrido (70% vetor + 30% FTS).

Respeita estritamente:
  - Trava T2: O manual é fonte de interpretação e diretriz, nunca fato fático do cliente.
  - Trava T3: Reranking matemático determinístico (elimina alucinações de ranking).
"""

from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional
import psycopg
from psycopg.rows import dict_row

from config import POSTGRES_DSN
from chat_session import Evidence, EvidenceType
from reranker import Reranker as DeterministicReranker
from ..base_skill import BaseSkill, Capability, RiskLevel, SkillResult

logger = logging.getLogger("DeskCortex.GroundedRAGSkill")


class GroundedRAGSkill(BaseSkill):
    """
    Skill especialista em resgate de conhecimento validado e aterrado (Grounded RAG).
    """

    name = "rag_skill"
    description = "Busca híbrida 70/30 no banco com reranking determinístico de manuais e mídias"
    version = "2.0.0"
    capabilities = [
        Capability.RETRIEVE_KNOWLEDGE,
    ]
    risk_level = RiskLevel.READ_ONLY

    def __init__(self):
        self.reranker = DeterministicReranker()

    def can_handle(self, state: Any, context: Optional[Dict[str, Any]] = None) -> bool:
        return True

    def execute(self, state: Any, context: Optional[Dict[str, Any]] = None) -> SkillResult:
        context = context or {}
        query_text = context.get("query_text")
        top_k = context.get("top_k", 5)
        module = context.get("module") or getattr(state, "module", None) or "TODOS"

        # 1. Compor query se não fornecida explicitamente
        if not query_text:
            parts = []
            if getattr(state, "intent", None) and state.intent != "consulta_geral":
                parts.append(state.intent.replace("_", " "))
            if getattr(state, "module", None) and state.module not in ("TODOS", "consulta_geral"):
                parts.append(state.module)
            if getattr(state, "confirmed_cause", None):
                parts.append(state.confirmed_cause)
            if hasattr(state, "symptoms") and state.symptoms:
                parts.extend(state.symptoms[:2])
            if context.get("rejection_code"):
                parts.append(f"Rejeição {context['rejection_code']}")
            if context.get("ecommerce_error"):
                parts.append(str(context["ecommerce_error"]))

            query_text = " ".join(parts).strip() or context.get("user_message", "") or "Manual do ERP Master Alternativa Sistemas"

        # Limpar quaisquer tokens internos de sistema que possam quebrar a sintaxe do FTS
        import re
        for internal_token in ["consulta_geral", "consulta geral", "pedido_nao_integrado", "erro_nota_fiscal", "problema_boleto", "estoque_divergente"]:
            query_text = re.sub(r'\b' + re.escape(internal_token) + r'\b', '', query_text, flags=re.I).strip()
        if not query_text:
            query_text = context.get("user_message", "") or "Manual ERP Master"

        # 2. Obter embedding via helper do sistema
        embedding = None
        try:
            from simulator_app import get_query_embedding, hybrid_search
            embedding = get_query_embedding(query_text)
        except Exception as e:
            logger.info(f"[GroundedRAGSkill] Não foi possível obter embedding dinâmico ({e}). Usando FTS.")
            hybrid_search = None

        candidates: List[Dict[str, Any]] = []
        try:
            with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as conn:
                if hybrid_search:
                    raw_candidates = hybrid_search(
                        conn,
                        query_text=query_text,
                        embedding=embedding,
                        module=module,
                        top_k=20
                    )
                    candidates = [dict(r) for r in raw_candidates]
                else:
                    # Fallback SQL direto caso simulator_app não esteja importado
                    from simulator_app import lexical_search
                    candidates = [dict(r) for r in lexical_search(conn, query_text, module=module, top_k=20)]

                # Se a busca inicial retornou vazia e temos user_message diferente de query_text, tenta com user_message
                user_msg_clean = (context.get("user_message") or "").strip()
                if not candidates and user_msg_clean and user_msg_clean != query_text:
                    from simulator_app import lexical_search
                    raw_c = lexical_search(conn, user_msg_clean, module="TODOS", top_k=20)
                    candidates = [dict(r) for r in raw_c]
        except Exception as e:
            logger.error(f"[GroundedRAGSkill] Falha na conexão PostgreSQL: {e}", exc_info=True)
            return SkillResult(
                success=False,
                errors=[f"Erro ao consultar base de conhecimento: {e}"],
                confidence=0.0
            )

        from knowledge_access import assessment
        evidence_assessment = assessment(candidates[:top_k])
        # Inapplicable histories remain in retrieval diagnostics, not in the
        # procedural context sent to the dialogue model.
        usable = candidates[:1] if evidence_assessment['can_support_procedure'] else []
        top_ranked = self.reranker.rerank(usable, state, top_k=top_k)
        context_text = self.reranker.format_for_llm(top_ranked)
        if not evidence_assessment["can_support_procedure"]:
            context_text = ("EVIDÊNCIA INSUFICIENTE PARA UM PROCEDIMENTO COMPLETO. "
                            + evidence_assessment["message"] + "\n" + context_text)
        media_refs = self.reranker.extract_media_refs(top_ranked)
        shortcuts = self.reranker.extract_shortcuts(top_ranked)

        # 4. Trava T2: Adicionar o manual como Evidência de INTERPRETAÇÃO (nunca FATO)
        if top_ranked and hasattr(state, "add_evidence"):
            best = top_ranked[0]
            if best.source_type in ("MANUAL", "KB", "OFFICIAL_KB"):
                state.add_evidence(
                    source="manual",
                    type=EvidenceType.EVIDENCIA,
                    content=f"Manual oficial [{best.manual_category or 'Procedimento'}]: {best.title}. {best.content_text[:200]}...",
                    confidence=0.90
                )

        return SkillResult(
            success=True,
            data={
                "query": query_text,
                "top_ranked": top_ranked,
                "context_text": context_text,
                "media_refs": media_refs,
                "shortcuts": shortcuts,
                "candidate_count": len(top_ranked),
                "evidence_assessment": evidence_assessment,
            },
            confidence=0.50 if evidence_assessment["can_support_procedure"] else 0.0,
            metadata={"skill": self.name, "candidates_searched": len(candidates)}
        )
