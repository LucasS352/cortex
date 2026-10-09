"""
DeskCortex -- Reranker: Ordenacao Deterministica de Candidatos (Fase 2)
========================================================================
Trava T3 (deliberada): o reranker e DETERMINÍSTICO.
Formula de score ponderado com base em sinais objetivos.
O Gemini so entra DEPOIS, raciocínando sobre os top-5 ja ordenados.

Formula:
  final_score = (
      semantic_score * 0.55       +   # similaridade coseno da busca hibrida
      authority_weight * 0.25     +   # MANUAL=1.0, KB=0.9, GOLD=0.85, SILVER=0.70
      module_bonus * 0.08         +   # +1.0 se modulo bate com investigation_state
      integration_bonus * 0.06   +   # +1.0 se integracao bate
      media_bonus * 0.04         +   # +1.0 se tem GIF/PNG relevante
      shortcut_bonus * 0.02          # +1.0 se tem atalho relevante
  )

Apos ordenacao, o Evidence Engine classifica cada chunk:
  - Fonte "manual" + info de como-funciona -> EvidenceType.EVIDENCIA (interpretacao)
  - Nao e FATO (Trava T2: manual nao e evidencia do caso atual)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from chat_session import InvestigationState, EvidenceType


# -------------------------------------------------------------------------
# Pesos de autoridade por source_type
# -------------------------------------------------------------------------

AUTHORITY_WEIGHTS = {
    "MANUAL": 1.00,
    "KB":     0.90,
    "GOLD":   0.85,
    "SILVER": 0.70,
}

# -------------------------------------------------------------------------
# Pesos da formula final
# -------------------------------------------------------------------------

W_SEMANTIC    = 0.55
W_AUTHORITY   = 0.25
W_MODULE      = 0.08
W_INTEGRATION = 0.06
W_MEDIA       = 0.04
W_SHORTCUT    = 0.02


@dataclass
class RankedChunk:
    """Candidato apos reranking com score final e classificacao Evidence."""
    # Dados originais do banco
    node_id: int
    source_type: str
    authority_score: int
    content_text: str
    title: str
    manual_category: Optional[str]
    media_path: Optional[str]
    media_type: Optional[str]
    media_metadata: Optional[dict]
    shortcut_keys: Optional[list[str]]
    screen_path: Optional[str]

    # Scores parciais (para auditoria/debug)
    semantic_score: float = 0.0
    authority_weight: float = 0.0
    module_bonus: float = 0.0
    integration_bonus: float = 0.0
    media_bonus: float = 0.0
    shortcut_bonus: float = 0.0

    # Score final
    final_score: float = 0.0

    # Classificacao Evidence Engine (Trava T2)
    evidence_type: EvidenceType = EvidenceType.EVIDENCIA
    evidence_source: str = "manual"
    evidence_note: str = ""
    answer_use: str = "supporting_reference"
    quality_flags: list[str] = field(default_factory=list)
    retrieval_score: float = 0.0

    def has_media(self) -> bool:
        return bool(self.media_path)

    def has_shortcuts(self) -> bool:
        return bool(self.shortcut_keys)

    def to_context_block(self) -> str:
        """Serializa para inclusao no prompt do LLM."""
        lines = [
            f"[{self.source_type} | score={self.final_score:.3f} | {self.evidence_type.value.upper()}]",
            f"Titulo: {self.title or '(sem titulo)'}",
        ]
        lines.append(f"Uso permitido: {self.answer_use}. Lacunas: {', '.join(self.quality_flags) or 'não avaliadas'}.")
        if self.answer_use == "investigation_only":
            lines.append("Registro insuficiente para prescrever correção. Solicite evidência adicional; não reconstrua passos ausentes.")
        if self.manual_category:
            lines.append(f"Categoria: {self.manual_category}")
        if self.screen_path:
            lines.append(f"Caminho no sistema: {self.screen_path}")
        if self.shortcut_keys:
            lines.append(f"Atalhos: {', '.join(self.shortcut_keys)}")
        if self.media_path:
            lines.append(f"[[MEDIA:{self.media_path}]] (tipo: {self.media_type})")
        lines.append(f"Conteudo:\n{self.content_text[:1200]}")
        return "\n".join(lines)

    def to_response_dict(self) -> dict:
        """Para inclusao no response JSON do /api/chat."""
        return {
            "node_id": self.node_id,
            "source_type": self.source_type,
            "title": self.title,
            "evidence_type": self.evidence_type.value,
            "evidence_note": self.evidence_note,
            "final_score": round(self.final_score, 4),
            "answer_use": self.answer_use,
            "quality_flags": self.quality_flags,
            "retrieval_score": self.retrieval_score,
            "has_media": self.has_media(),
            "media_path": self.media_path,
            "media_type": self.media_type,
            "screen_path": self.screen_path,
            "shortcut_keys": self.shortcut_keys or [],
        }

    def to_generation_dict(self) -> dict:
        """Internal prompt data; the public evidence DTO intentionally omits text.

        Sending to_response_dict() to the dialogue engine dropped the actual
        procedure and produced a reference heading with no solution underneath.
        """
        return {**self.to_response_dict(), "content_text": self.content_text}


class Reranker:
    """
    Reranker determinístico para candidatos da busca hibrida.

    Trava T3: sem LLM aqui.
    O score e calculado por formula ponderada com sinais objetivos.
    O Gemini recebe apenas os top-K ja ordenados.
    """

    def __init__(self, top_k: int = 5):
        self.top_k = top_k

    def rerank(
        self,
        candidates: list[dict],
        state: InvestigationState,
        top_k: Optional[int] = None,
    ) -> list[RankedChunk]:
        """
        Recebe lista de dicts do banco (top-20 da busca hibrida)
        e retorna lista ordenada de RankedChunk (top-K).

        Parametros de cada candidate dict (vem do hybrid_search):
          - node_id, source_type, authority_score, content_text, title
          - manual_category, media_path, media_type, media_metadata
          - shortcut_keys, screen_path
          - semantic_score (float 0-1, score hibrido bruto)
        """
        k = top_k or self.top_k
        ranked = []

        for c in candidates:
            chunk = self._score_candidate(c, state)
            ranked.append(chunk)

        # Ordenar por final_score descendente
        ranked.sort(key=lambda x: (x.retrieval_score, x.final_score), reverse=True)

        # Aplicar Evidence Engine nos top-K (Trava T2)
        top = ranked[:k]
        self._classify_evidence(top, state)

        return top

    def _score_candidate(self, c: dict, state: InvestigationState) -> RankedChunk:
        """Calcula o score final de um candidato."""

        # ------------------------------------------------------------------
        # FIX BUG 1: hybrid_search retorna 'semantic_sim', nao 'semantic_score'
        # Tambem aceita 'combined_score' como fallback
        # ------------------------------------------------------------------
        sem_raw = c.get("semantic_sim") or c.get("semantic_score") or c.get("combined_score") or c.get("similarity") or 0.0
        sem = float(sem_raw) if sem_raw is not None else 0.0
        sem = max(0.0, min(1.0, sem))

        # ------------------------------------------------------------------
        # FIX BUG 2: Normalizar source_type usando 'tier' como fonte primaria
        # O banco grava: OFFICIAL_KB, VALIDATED_TICKET, PROBABLE_TICKET
        # O reranker precisa: KB, GOLD, SILVER, MANUAL
        # ------------------------------------------------------------------
        raw_src = (c.get("tier") or c.get("source_type") or "SILVER").upper()
        if "MANUAL" in raw_src:
            src = "MANUAL"
        elif raw_src in ("KB", "OFFICIAL_KB"):
            src = "KB"
        elif raw_src in ("GOLD", "VALIDATED_TICKET", "VALIDATED"):
            src = "GOLD"
        elif raw_src in ("SILVER", "PROBABLE_TICKET", "PROBABLE"):
            src = "SILVER"
        else:
            src = "SILVER"

        # Authority weight
        auth = AUTHORITY_WEIGHTS.get(src, 0.70)

        # Module bonus
        mod_bonus = 0.0
        cat = (c.get("manual_category") or "").lower()
        # Tambem verificar campo 'modules' (array) do banco para tickets
        modules_arr = c.get("modules") or []
        modules_text = " ".join(m.lower() for m in modules_arr) if isinstance(modules_arr, list) else ""
        state_mod = (state.module or "").lower()
        state_int = (state.integration or "").lower()
        if state_mod and (state_mod in cat or state_mod in modules_text):
            mod_bonus = 1.0
        elif state_mod and len(state_mod) > 3 and (state_mod[:6] in cat or state_mod[:6] in modules_text):
            mod_bonus = 0.5

        # Integration bonus
        # FIX: usar fallback chain para content_text (campo nao existe no SQL)
        content_raw = (c.get("content_for_embedding") or c.get("solution")
                       or c.get("problem") or c.get("content_text") or "")
        content_low = content_raw.lower()
        title_low = (c.get("title") or "").lower()
        int_bonus = 0.0
        if state_int and (state_int in content_low or state_int in title_low):
            int_bonus = 1.0
        elif state_int and len(state_int) > 3 and state_int[:5] in content_low:
            int_bonus = 0.5

        # Media bonus -- ter GIF relevante e valioso
        media_bonus = 0.0
        if c.get("media_path"):
            media_bonus = 1.0
            # Bonus extra se o GIF tem metadata estruturada com o modulo certo
            meta = c.get("media_metadata") or {}
            meta_module = (meta.get("module") or "").lower()
            if state_mod and state_mod in meta_module:
                media_bonus = 1.2  # FIX: bonus extra com efeito real (antes era 1.5 clampado a 1.0)

        # Shortcut bonus
        shortcut_bonus = 0.0
        if c.get("shortcut_keys"):
            shortcut_bonus = 1.0

        # Calcular final_score
        final = (
            sem                       * W_SEMANTIC    +
            auth                      * W_AUTHORITY   +
            mod_bonus                 * W_MODULE      +
            int_bonus                 * W_INTEGRATION +
            min(media_bonus, 1.2)     * W_MEDIA       +
            shortcut_bonus            * W_SHORTCUT
        )

        return RankedChunk(
            node_id=c.get("id", 0),
            source_type=src,
            authority_score=c.get("source_authority") or c.get("authority_score", 70),
            content_text=content_raw,
            title=c.get("title") or c.get("subject") or "",
            manual_category=c.get("manual_category"),
            media_path=c.get("media_path"),
            media_type=c.get("media_type"),
            media_metadata=c.get("media_metadata"),
            shortcut_keys=c.get("shortcut_keys"),
            screen_path=c.get("screen_path"),
            semantic_score=sem,
            authority_weight=auth,
            module_bonus=mod_bonus,
            integration_bonus=int_bonus,
            media_bonus=media_bonus,
            shortcut_bonus=shortcut_bonus,
            final_score=final,
            answer_use=c.get("answer_use", "supporting_reference"),
            quality_flags=c.get("quality_flags") or [],
            retrieval_score=float(c.get("retrieval_score") or 0),
        )

    def _classify_evidence(self, chunks: list[RankedChunk], state: InvestigationState):
        """
        Evidence Engine -- Trava T2:
        Classifica cada chunk segundo seu papel epistemico.

        O manual NUNCA e EvidenceType.FATO.
        Ele e fonte de interpretacao (EVIDENCIA) do caso atual.

        "O manual explica o que SKU_NOT_FOUND significa.
         O cliente confirmando que viu SKU_NOT_FOUND e o FATO."
        """
        for chunk in chunks:
            if chunk.source_type == "MANUAL":
                chunk.evidence_type = EvidenceType.EVIDENCIA
                chunk.evidence_source = "manual"
                chunk.evidence_note = (
                    "Fonte de interpretacao: explica como o Master deveria funcionar. "
                    "Nao e evidencia de que esse problema especifico ocorreu."
                )
            elif chunk.source_type == "KB":
                chunk.evidence_type = EvidenceType.EVIDENCIA
                chunk.evidence_source = "manual"
                chunk.evidence_note = "Artigo oficial de suporte -- contexto de interpretacao."
            elif chunk.source_type in ("GOLD", "SILVER"):
                # Chamados historicos sao hipoteses, nao fatos do caso atual
                chunk.evidence_type = EvidenceType.HIPOTESE
                chunk.evidence_source = "historico"
                chunk.evidence_note = (
                    "Chamado historico com solucao similar -- hipotese, nao confirmacao. "
                    "O caso atual pode ter causa diferente."
                )

    def format_for_llm(self, ranked: list[RankedChunk]) -> str:
        """Formata os top-K chunks para inclusao no prompt do LLM."""
        blocks = []
        for i, chunk in enumerate(ranked, 1):
            blocks.append(f"--- CONTEXTO {i} ---\n{chunk.to_context_block()}")
        return "\n\n".join(blocks)

    def extract_media_refs(self, ranked: list[RankedChunk]) -> list[dict]:
        """Extrai referencias de midia dos chunks para envio na resposta."""
        refs = []
        seen = set()
        for chunk in ranked:
            if chunk.media_path and chunk.media_path not in seen:
                seen.add(chunk.media_path)
                meta = chunk.media_metadata or {}
                refs.append({
                    "path": chunk.media_path,
                    "type": chunk.media_type or "unknown",
                    "title": meta.get("title") or chunk.title,
                    "module": meta.get("module") or chunk.manual_category or "",
                    "screen": meta.get("screen") or chunk.screen_path or "",
                    "flow": meta.get("flow") or [],
                })
        return refs

    def extract_shortcuts(self, ranked: list[RankedChunk]) -> list[str]:
        """Extrai atalhos unicos dos chunks."""
        shortcuts = []
        seen = set()
        for chunk in ranked:
            for sk in (chunk.shortcut_keys or []):
                if sk not in seen:
                    seen.add(sk)
                    shortcuts.append(sk)
        return shortcuts
