"""
DeskCortex — InvestigationSkill (Camada de Raciocínio)
======================================================
Responsável pela avaliação de hipóteses com base em evidências fáticas observadas,
eliminação rastreável de causas refutadas e aplicação estrita da Trava T1.

Diretriz Arquitetural:
  - Scoring de hipóteses fundamentado em evidências (sem jargão 'Bayesiano' prematuro).
  - Trava T1: Confiança alta (> 0.80) sozinha NUNCA confirma causa raiz sem
    evidência fática compatível (source='sistema' ou type=FATO) e ausência de contradição.
  - Trava T2: O manual/conhecimento é interpretação, nunca evidência primária do caso.
"""

from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

from chat_session import ResolutionStatus, EvidenceType, RiskLevel
from triage_engine import TriageEngine
from ..base_skill import BaseSkill, Capability, RiskLevel as SkillRisk, SkillResult

logger = logging.getLogger("DeskCortex.InvestigationSkill")


class InvestigationSkill(BaseSkill):
    """
    Skill de Raciocínio Investigativo e Evidence Engine.
    """

    name = "investigation_skill"
    description = "Avalia hipóteses baseadas em evidências fáticas, aplica Trava T1 e direciona triagem"
    version = "2.0.0"
    capabilities = [
        Capability.EVALUATE_HYPOTHESES,
    ]
    risk_level = SkillRisk.ANALYSIS

    def __init__(self):
        self._triage_engine = TriageEngine()

    def can_handle(self, state: Any, context: Optional[Dict[str, Any]] = None) -> bool:
        return hasattr(state, "hypotheses") and hasattr(state, "evidence")

    def execute(self, state: Any, context: Optional[Dict[str, Any]] = None) -> SkillResult:
        context = context or {}
        user_message = context.get("user_message", "")

        # 1. Atualizar evidências e scoring de hipóteses com base na mensagem do usuário
        if user_message:
            self._triage_engine._update_hypotheses_and_evidence(user_message, state)
            self._triage_engine._extract_entities(user_message, state)

        # 2. Se houver código de erro ou rejeição explicita no contexto, checar confirmação
        rejection_code = context.get("rejection_code")
        if rejection_code:
            self._triage_engine._check_error_confirmation(f"Rejeição {rejection_code}", state)

        for err in context.get("error_codes", []):
            self._triage_engine._check_error_confirmation(err, state)

        # 3. Trava T1: Verificação estrita de confirmação
        auto_confirmed = state.check_confirmation()
        ready_to_prescribe = False

        if auto_confirmed:
            state.confirmed_cause = auto_confirmed
            state.resolution_status = ResolutionStatus.PRESCRIBING
            state.missing_information = []
            ready_to_prescribe = True
            logger.info(f"[InvestigationSkill] Trava T1 satisfeita: Causa confirmada '{auto_confirmed}'")
        else:
            state.resolution_status = ResolutionStatus.INVESTIGATING

        # 4. Determinar próxima pergunta se ainda faltar informação
        next_question = None
        if not ready_to_prescribe:
            next_question = self._triage_engine._build_next_question_from_state(state)

        top_hyp = state.top_hypothesis()

        return SkillResult(
            success=True,
            data={
                "confirmed_cause": state.confirmed_cause,
                "ready_to_prescribe": ready_to_prescribe,
                "status": state.resolution_status.value,
                "next_question": next_question,
                "missing_information": list(state.missing_information),
                "top_hypothesis": {
                    "cause": top_hyp.cause if top_hyp else None,
                    "confidence": top_hyp.confidence if top_hyp else 0.0,
                } if top_hyp else None,
                "eliminated_hypotheses": list(state.eliminated_hypotheses),
                "evidence_count": len(state.evidence),
            },
            confidence=top_hyp.confidence if top_hyp else 0.50,
            metadata={"skill": self.name, "trava_t1_applied": True}
        )
