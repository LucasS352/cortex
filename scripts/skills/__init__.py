"""
DeskCortex — Skills Framework
=============================
Arquitetura de Capacidades Especializadas e Registro Dinâmico
"""

from .base_skill import BaseSkill, Capability, SkillResult, RiskLevel
from .registry import SkillRegistry, get_global_registry

__all__ = [
    "BaseSkill",
    "Capability",
    "SkillResult",
    "RiskLevel",
    "SkillRegistry",
    "get_global_registry",
]
