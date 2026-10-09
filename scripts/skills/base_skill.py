"""
DeskCortex — BaseSkill & Contratos de Capacidades
=================================================
Contratos fundamentais para o ecossistema de Skills especializadas.
Define as Capabilities semânticas, resultados estruturados e entidades visuais.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Capability(str, Enum):
    """Capacidades semânticas que o Core pode requisitar do SkillRegistry."""
    # Percepção
    EXTRACT_ERROR_IMAGE         = "perception.extract_error_image"
    EXTRACT_ENTITIES_TEXT       = "perception.extract_entities_text"

    # Domínio
    VALIDATE_FISCAL_KEY         = "domain.fiscal.validate_key"
    DIAGNOSE_FISCAL_REJECTION   = "domain.fiscal.diagnose_rejection"
    DIAGNOSE_ECOMMERCE_ORDER    = "domain.ecommerce.diagnose_order"

    # Conhecimento
    RETRIEVE_KNOWLEDGE          = "knowledge.retrieve"

    # Raciocínio
    EVALUATE_HYPOTHESES         = "reasoning.evaluate_hypotheses"

    # Apresentação
    HUMANIZE_RESPONSE           = "presentation.humanize_response"


class RiskLevel(str, Enum):
    """Nível de risco operacional da Skill."""
    READ_ONLY   = "read_only"    # Apenas inspeciona ou lê dados
    ANALYSIS    = "analysis"     # Processa dados e gera hipóteses
    INTERACTION = "interaction"  # Dialoga diretamente com o usuário
    ACTION      = "action"       # Efeito colateral de alteração no sistema


@dataclass
class VisualEntity:
    """Entidade visual estruturada extraída pela camada de Percepção."""
    type: str                  # "rejection_code", "nfe_key", "order_number", "module", "screen"
    value: str                 # "481", "35230912...", "12345", "Fiscal"
    confidence: float = 1.0    # 0.0 a 1.0
    raw_snippet: str = ""      # Trecho original do OCR que originou a entidade

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "value": self.value,
            "confidence": self.confidence,
            "raw_snippet": self.raw_snippet,
        }


@dataclass
class VisionResult:
    """Resultado estruturado e tipado da análise visual."""
    raw_text: str = ""
    normalized_text: str = ""
    entities: List[VisualEntity] = field(default_factory=list)
    regions: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = 1.0
    summary_observation: str = ""

    def get_entities_by_type(self, entity_type: str) -> List[VisualEntity]:
        """Filtra entidades pelo tipo desejado."""
        return [e for e in self.entities if e.type == entity_type]

    def get_first_value(self, entity_type: str) -> Optional[str]:
        """Retorna o valor da primeira entidade do tipo indicado com maior confiança."""
        matching = [e for e in self.entities if e.type == entity_type]
        if not matching:
            return None
        best = max(matching, key=lambda e: e.confidence)
        return best.value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_text": self.raw_text,
            "normalized_text": self.normalized_text,
            "entities": [e.to_dict() for e in self.entities],
            "regions": self.regions,
            "confidence": self.confidence,
            "summary_observation": self.summary_observation,
        }


@dataclass
class SkillResult:
    """Resultado padronizado retornado pela execução de qualquer Skill."""
    success: bool
    data: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "errors": self.errors,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


class BaseSkill(ABC):
    """
    Classe base para todas as Skills do DeskCortex.
    Cada Skill é dona de capacidades semânticas específicas, possui risco definido
    e executa operações puras ou controladas recebendo o InvestigationState e contexto.
    """

    name: str = "base_skill"
    description: str = "Base Skill"
    version: str = "1.0.0"
    capabilities: List[Capability] = []
    risk_level: RiskLevel = RiskLevel.READ_ONLY

    def can_handle(self, state: Any, context: Optional[Dict[str, Any]] = None) -> bool:
        """Verifica se a skill está habilitada para o estado/contexto atual."""
        return True

    @abstractmethod
    def execute(self, state: Any, context: Optional[Dict[str, Any]] = None) -> SkillResult:
        """
        Executa a capacidade da skill.
        :param state: InvestigationState (ou dict compatível)
        :param context: Dict com dados transientes da requisição (ex: image_bytes, user_message)
        :return: SkillResult estruturado
        """
        pass

    def __repr__(self) -> str:
        caps = ", ".join(c.value for c in self.capabilities)
        return f"<{self.__class__.__name__} name='{self.name}' v={self.version} caps=[{caps}]>"
