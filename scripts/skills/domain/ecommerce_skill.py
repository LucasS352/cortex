"""
DeskCortex — EcommerceSkill (Camada de Domínio)
==============================================
Especialista em Integrações de E-commerce, Hubs (AnyMarket, VTEX, SkyHub, Olist)
e Marketplaces (Mercado Livre, Shopee, Amazon, Magalu, Shein).
"""

from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

from chat_session import Evidence, EvidenceType
from ..base_skill import BaseSkill, Capability, RiskLevel, SkillResult

logger = logging.getLogger("DeskCortex.EcommerceSkill")


class EcommerceSkill(BaseSkill):
    """
    Skill especialista na Causalidade de Integrações de E-commerce do ERP Master.
    """

    name = "ecommerce_skill"
    description = "Diagnostica causalidade de pedidos não integrados e erros de de-para no Master"
    version = "2.0.0"
    capabilities = [
        Capability.DIAGNOSE_ECOMMERCE_ORDER,
    ]
    risk_level = RiskLevel.ANALYSIS

    def can_handle(self, state: Any, context: Optional[Dict[str, Any]] = None) -> bool:
        context = context or {}
        if context.get("ecommerce_error") or context.get("order_number"):
            return True
        if getattr(state, "module", None) == "Ecommerce" or getattr(state, "intent", None) == "pedido_nao_integrado":
            return True
        return False

    def execute(self, state: Any, context: Optional[Dict[str, Any]] = None) -> SkillResult:
        context = context or {}
        ecommerce_error = context.get("ecommerce_error")
        integration = context.get("integration") or getattr(state, "integration", None) or "AnyMarket"
        order_number = context.get("order_number")

        data: Dict[str, Any] = {}
        detected_cause = None

        # 1. Diagnóstico baseado em erros de De-para conhecidos
        if ecommerce_error == "SKU_NOT_FOUND":
            detected_cause = "Produto/SKU não cadastrado ou não vinculado no De-para"
            data["remediation_screen"] = "Configurações > E-commerce > De-para de Produtos"
            data["diagnostic_type"] = "mapping_sku"

        elif ecommerce_error == "FORMA_PAGAMENTO_NAO_VINCULADA":
            detected_cause = "Forma de pagamento não associada no De-para de Pagamentos"
            data["remediation_screen"] = "Configurações > E-commerce > De-para de Formas de Pagamento"
            data["diagnostic_type"] = "mapping_payment"

        elif ecommerce_error == "TRANSPORTADORA_NAO_VINCULADA":
            detected_cause = "Transportadora / Método de Envio não vinculado no De-para"
            data["remediation_screen"] = "Configurações > E-commerce > De-para de Transportadoras"
            data["diagnostic_type"] = "mapping_carrier"

        # 2. Diagnóstico de causalidade por localização do pedido
        in_hub = context.get("order_in_hub")
        if in_hub is False:
            detected_cause = f"Pedido bloqueado no canal de origem ({integration}) antes do hub"
            data["causality"] = "external_marketplace"
            data["recommendation"] = f"Verifique o status do pedido diretamente no painel do canal {integration} (análise de risco ou pagamento pendente)."

        # Adicionar hipótese no InvestigationState
        if detected_cause and hasattr(state, "add_hypothesis"):
            state.add_hypothesis(
                cause=detected_cause,
                confidence=0.95,
                supporting=[f"Erro {ecommerce_error or 'de integração'} confirmado no ERP Master."]
            )

        data["detected_cause"] = detected_cause
        data["integration"] = integration
        data["order_number"] = order_number

        return SkillResult(
            success=True,
            data=data,
            confidence=0.95 if detected_cause else 0.70,
            metadata={"skill": self.name}
        )
