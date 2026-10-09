"""
DeskCortex — DialogueSkill (Camada de Apresentação)
===================================================
Responsável por orquestrar a fala final com o cliente em tom empático, humano,
acolhedor e de especialista sênior (Romário da Alternativa Sistemas).

Diretrizes:
  - 100% livre de tom mecânico de robô ou URA telefônica.
  - Didática de usabilidade (orienta telas, menus e atalho F3).
  - Guardrail Anti-Loop: Se o cliente já enviou uma imagem/print, NUNCA solicita print novamente.
  - Resolução de dúvidas de causalidade técnica (ex: pedido que não chegou na AnyMarket).
"""

from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

from chat_session import ChatSession, ResolutionStatus
from romario_dialogue import RomarioDialogueEngine
from ..base_skill import BaseSkill, Capability, RiskLevel, SkillResult

logger = logging.getLogger("DeskCortex.DialogueSkill")


class DialogueSkill(BaseSkill):
    """
    Skill especialista na Apresentação e Humanização do Diálogo de Suporte.
    """

    name = "dialogue_skill"
    description = "Gera respostas naturais, acolhedoras e orientadas a usabilidade com a persona Romário"
    version = "2.0.0"
    capabilities = [
        Capability.HUMANIZE_RESPONSE,
    ]
    risk_level = RiskLevel.INTERACTION

    def can_handle(self, state: Any, context: Optional[Dict[str, Any]] = None) -> bool:
        return True

    def execute(self, state: Any, context: Optional[Dict[str, Any]] = None) -> SkillResult:
        context = context or {}
        session = context.get("session")
        triage_next_question = context.get("triage_next_question")
        media_refs = context.get("media_refs", [])
        shortcuts = context.get("shortcuts", [])
        user_message = context.get("user_message", "")

        # 1. Verificar se o cliente já enviou imagem na sessão
        has_sent_image = False
        if session:
            for turn in getattr(session, "turns", []):
                if hasattr(turn, "attachments") and turn.attachments:
                    has_sent_image = True
                    break
                if hasattr(turn, "content") and "[screenshot enviado" in turn.content.lower():
                    has_sent_image = True
                    break

        if hasattr(state, "evidence"):
            for ev in state.evidence:
                if ev.source == "sistema" and ("tela" in ev.content.lower() or "detectad" in ev.content.lower()):
                    has_sent_image = True
                    break

        # 2. Gerar resposta humanizada
        top_candidates = context.get("top_candidates") or []
        reply = RomarioDialogueEngine.generate_response(
            session=session,
            state=state,
            triage_next_question=triage_next_question,
            top_candidates=top_candidates
        )

        # 3. Guardrail Anti-Loop adicional sobre o texto final gerado
        if has_sent_image:
            # Substituir qualquer menção remanescente a pedir print
            for forbidden in [
                "cola um print da tela aqui com ctrl+v",
                "cola um print da tela aqui com ctrl+v pra gente analisar juntos!",
                "cola um print aqui com ctrl+v",
                "manda um print da tela",
                "se puder mandar um print",
                "manda um print",
            ]:
                if forbidden in reply.lower():
                    reply = reply.replace(forbidden, "me confirma se você consegue acessar essa tela no Master")

        return SkillResult(
            success=True,
            data={
                "reply": reply,
                "tone_profile": "senior_support_brazil",
                "has_sent_image": has_sent_image,
                "media_refs": media_refs,
                "shortcuts": shortcuts,
            },
            confidence=0.98,
            metadata={"skill": self.name, "persona": "Romário"}
        )
