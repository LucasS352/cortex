"""
DeskCortex — SkillRegistry
==========================
Gerenciador central de capacidades especializadas.
Permite registro determinístico e resolução por CAPABILITIES semânticas.
O Core requisita a Capability desejada e o Registry despacha para a Skill apta.
"""

from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional
from .base_skill import BaseSkill, Capability, SkillResult

logger = logging.getLogger("DeskCortex.SkillRegistry")


class SkillRegistry:
    """
    Registro e orquestrador de Skills especializadas.
    O coração da arquitetura de capacidades da Fase 3.
    """

    def __init__(self):
        self._skills_by_name: Dict[str, BaseSkill] = {}
        self._skills_by_capability: Dict[Capability, List[BaseSkill]] = {
            cap: [] for cap in Capability
        }

    def register(self, skill: BaseSkill) -> BaseSkill:
        """Registra uma instância de skill no catálogo e mapeia suas capacidades."""
        if not isinstance(skill, BaseSkill):
            raise TypeError(f"A skill deve herdar de BaseSkill. Recebido: {type(skill)}")

        self._skills_by_name[skill.name] = skill
        for cap in skill.capabilities:
            if cap in self._skills_by_capability:
                if skill not in self._skills_by_capability[cap]:
                    self._skills_by_capability[cap].append(skill)
            else:
                self._skills_by_capability[cap] = [skill]

        logger.info(f"[SkillRegistry] Skill '{skill.name}' registrada com capacidades: {[c.value for c in skill.capabilities]}")
        return skill

    def get_skill(self, name: str) -> Optional[BaseSkill]:
        """Retorna uma skill pelo seu nome único."""
        return self._skills_by_name.get(name)

    def resolve_capability(self, capability: Capability) -> List[BaseSkill]:
        """
        Resolve quais skills estão registradas e aptas a fornecer a capacidade requisitada.
        """
        return list(self._skills_by_capability.get(capability, []))

    def execute_capability(
        self,
        capability: Capability,
        state: Any,
        context: Optional[Dict[str, Any]] = None
    ) -> List[SkillResult]:
        """
        Executa todas as skills aptas para a capacidade solicitada.
        """
        skills = self.resolve_capability(capability)
        results: List[SkillResult] = []

        for skill in skills:
            if skill.can_handle(state, context):
                try:
                    res = skill.execute(state, context)
                    results.append(res)
                except Exception as e:
                    logger.error(f"[SkillRegistry] Erro na skill '{skill.name}' para capability '{capability.value}': {e}", exc_info=True)
                    results.append(SkillResult(
                        success=False,
                        errors=[f"Exceção em {skill.name}: {str(e)}"],
                        confidence=0.0,
                        metadata={"skill": skill.name, "capability": capability.value}
                    ))

        return results

    def execute_single_capability(
        self,
        capability: Capability,
        state: Any,
        context: Optional[Dict[str, Any]] = None
    ) -> Optional[SkillResult]:
        """
        Executa a primeira skill apta (maior prioridade) para a capacidade informada.
        """
        skills = self.resolve_capability(capability)
        for skill in skills:
            if skill.can_handle(state, context):
                try:
                    return skill.execute(state, context)
                except Exception as e:
                    logger.error(f"[SkillRegistry] Erro ao executar '{skill.name}' para '{capability.value}': {e}", exc_info=True)
                    return SkillResult(
                        success=False,
                        errors=[f"Exceção em {skill.name}: {str(e)}"],
                        confidence=0.0,
                        metadata={"skill": skill.name, "capability": capability.value}
                    )
        return None

    def list_all(self) -> List[Dict[str, Any]]:
        """Retorna metadados de todas as skills registradas."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "version": s.version,
                "capabilities": [c.value for c in s.capabilities],
                "risk_level": s.risk_level.value,
            }
            for s in self._skills_by_name.values()
        ]

    def list_capabilities(self) -> Dict[str, List[str]]:
        """Mapeia todas as capacidades registradas e quais skills as implementam."""
        return {
            cap.value: [s.name for s in skills]
            for cap, skills in self._skills_by_capability.items()
        }


# Instância global (Singleton)
_GLOBAL_REGISTRY: Optional[SkillRegistry] = None


def get_global_registry() -> SkillRegistry:
    """Retorna a instância singleton do SkillRegistry, inicializando as skills padrão."""
    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        _GLOBAL_REGISTRY = SkillRegistry()
        _load_default_skills(_GLOBAL_REGISTRY)
    return _GLOBAL_REGISTRY


def _load_default_skills(registry: SkillRegistry):
    """Carrega dinamicamente as skills padrão do DeskCortex."""
    try:
        from .perception.vision_skill import VisionSkill
        registry.register(VisionSkill())
    except Exception as e:
        logger.warning(f"Não foi possível carregar VisionSkill: {e}")

    try:
        from .domain.fiscal_skill import FiscalSkill
        registry.register(FiscalSkill())
    except Exception as e:
        logger.warning(f"Não foi possível carregar FiscalSkill: {e}")

    try:
        from .domain.ecommerce_skill import EcommerceSkill
        registry.register(EcommerceSkill())
    except Exception as e:
        logger.warning(f"Não foi possível carregar EcommerceSkill: {e}")

    try:
        from .knowledge.rag_skill import GroundedRAGSkill
        registry.register(GroundedRAGSkill())
    except Exception as e:
        logger.warning(f"Não foi possível carregar GroundedRAGSkill: {e}")

    try:
        from .reasoning.investigation_skill import InvestigationSkill
        registry.register(InvestigationSkill())
    except Exception as e:
        logger.warning(f"Não foi possível carregar InvestigationSkill: {e}")

    try:
        from .presentation.dialogue_skill import DialogueSkill
        registry.register(DialogueSkill())
    except Exception as e:
        logger.warning(f"Não foi possível carregar DialogueSkill: {e}")
