"""
DeskCortex — FiscalSkill (Camada de Domínio)
============================================
Especialista em validações de regras e estruturas fiscais da SEFAZ e ERP Master.
Responsável por:
  - Validação estrita de Chave de Acesso (44 dígitos, Módulo 11, decodificação de UF/Modelo)
  - Diagnóstico estrutural de Códigos de Rejeição SEFAZ (481, 783, 539, etc.)
  - Não armazena manuais hardcoded (delega a busca de soluções ricas ao GroundedRAG)
"""

from __future__ import annotations
import logging
import re
from typing import Any, Dict, List, Optional

from chat_session import Evidence, EvidenceType
from ..base_skill import BaseSkill, Capability, RiskLevel, SkillResult

logger = logging.getLogger("DeskCortex.FiscalSkill")

UF_IBGE_MAP = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO",
    "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL", "28": "SE", "29": "BA",
    "31": "MG", "32": "ES", "33": "RJ", "35": "SP",
    "41": "PR", "42": "SC", "43": "RS",
    "50": "MS", "51": "MT", "52": "GO", "53": "DF"
}

# Categorização estrutural de rejeições da SEFAZ
SEFAZ_REJECTIONS_META = {
    "481": {
        "title": "Código Regime Tributário do emitente diverge do cadastro na SEFAZ",
        "category": "cadastro_tributario",
        "field_divergence": "CRT (Código de Regime Tributário: 1-Simples Nacional vs 3-Regime Normal)",
        "menu_path": "Configurações > Empresa / Filiais > aba Parâmetros Fiscais",
        "official_source": "SEFAZ / CCC (Cadastro Centralizado de Contribuintes)",
    },
    "783": {
        "title": "CFOP de operação interestadual para consumidor final não contribuinte",
        "category": "regra_fiscal_cfop",
        "field_divergence": "CFOP incompatível com indicador de IE ou destinatário interestadual",
        "menu_path": "Vendas > Faturamento > aba Itens da NF-e",
        "official_source": "Nota Técnica SEFAZ 2015.003",
    },
    "539": {
        "title": "Duplicidade de NF-e com diferença na Chave de Acesso",
        "category": "autorizacao_sefaz",
        "field_divergence": "Nota com mesmo número/série já autorizada na SEFAZ",
        "menu_path": "Vendas > Faturamento > NF-e > Consultar na SEFAZ",
        "official_source": "Portal Nacional da NF-e",
    },
    "204": {
        "title": "Duplicidade de NF-e",
        "category": "autorizacao_sefaz",
        "field_divergence": "Mesmo número e série já transmitidos anteriormente",
        "menu_path": "Vendas > Faturamento > NF-e",
        "official_source": "Portal Nacional da NF-e",
    },
    "694": {
        "title": "Não informado o grupo de ICMS para a UF de destino (DIFAL)",
        "category": "partilha_icms",
        "field_divergence": "Venda interestadual a não contribuinte sem tags de partilha de ICMS",
        "menu_path": "Configurações > Parâmetros de Tributação > ICMS Interestadual",
        "official_source": "Emenda Constitucional 87/2015",
    },
    "629": {
        "title": "Valor do Produto difere do produto Valor Unitário de Comercialização e Quantidade Comercial",
        "category": "calculo_item_nfe",
        "field_divergence": "Desconto no item superior a 100% ou divergência entre unitário x quantidade e total",
        "menu_path": "Vendas > Faturamento > NF-e > aba Itens da Nota",
        "official_source": "Manual de Orientação do Contribuinte (MOC) / SEFAZ",
    },
    "10060": {
        "title": "Erro Interno 10060: Connection timed out (Instabilidade SEFAZ / HTTP 500)",
        "category": "comunicacao_sefaz",
        "field_divergence": "Tempo limite esgotado na comunicação com os webservices da SEFAZ",
        "menu_path": "Vendas > Faturamento > NF-e (ou Contingência Offline NFC-e)",
        "official_source": "Webservices SEFAZ / Manual de Integração",
    }
}


class FiscalSkill(BaseSkill):
    """
    Skill especialista no Domínio Fiscal e Tributário do ERP Master.
    """

    name = "fiscal_skill"
    description = "Valida chaves de acesso NF-e de 44 dígitos e diagnostica rejeições da SEFAZ"
    version = "2.0.0"
    capabilities = [
        Capability.VALIDATE_FISCAL_KEY,
        Capability.DIAGNOSE_FISCAL_REJECTION,
    ]
    risk_level = RiskLevel.ANALYSIS

    def can_handle(self, state: Any, context: Optional[Dict[str, Any]] = None) -> bool:
        context = context or {}
        # Pode tratar se houver chave 44d, rejeição sefaz explícita ou módulo Fiscal ativo
        if context.get("nfe_key") or context.get("rejection_code"):
            return True
        if getattr(state, "module", None) == "Fiscal" or getattr(state, "intent", None) == "erro_nota_fiscal":
            return True
        return False

    def execute(self, state: Any, context: Optional[Dict[str, Any]] = None) -> SkillResult:
        context = context or {}
        data: Dict[str, Any] = {}
        errors: List[str] = []

        # 1. Validação de Chave de Acesso se presente
        nfe_key = context.get("nfe_key")
        if not nfe_key and hasattr(state, "entity") and state.entity and state.entity.get("type") == "nfe_key":
            nfe_key = state.entity.get("id")

        if nfe_key:
            clean_key = re.sub(r"\D", "", str(nfe_key))
            key_validation = self._validate_nfe_key(clean_key)
            data["key_validation"] = key_validation
            if not key_validation["is_valid"]:
                errors.append(key_validation["error"])

        # 2. Diagnóstico de Rejeição SEFAZ
        rejection_code = context.get("rejection_code")
        if not rejection_code and hasattr(state, "evidence"):
            for ev in state.evidence:
                m = re.search(r"Rejei[cç][aã]o\s+(?:SEFAZ\s+)?(\d{3,5})", ev.content, re.I) or re.search(r"(\d{3,5})\s*[-:]\s*Rejei[cç][aã]o", ev.content, re.I)
                if m:
                    rejection_code = m.group(1)
                    break

        if rejection_code:
            diag = self._diagnose_rejection(rejection_code, state)
            data["rejection_diagnosis"] = diag

        return SkillResult(
            success=len(errors) == 0,
            data=data,
            errors=errors,
            confidence=0.98 if data else 0.50,
            metadata={"skill": self.name}
        )

    def _validate_nfe_key(self, key: str) -> Dict[str, Any]:
        """Validação algorítmica de chave NF-e (44 dígitos com Módulo 11)."""
        if len(key) != 44:
            return {
                "is_valid": False,
                "error": f"Chave de acesso deve conter exatamente 44 dígitos numéricos (recebido: {len(key)}).",
                "key": key
            }

        # Cálculo do Dígito Verificador (Módulo 11)
        weights = [4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        total = sum(int(digit) * weight for digit, weight in zip(key[:43], weights))
        remainder = total % 11
        expected_dv = 0 if remainder in (0, 1) else 11 - remainder
        actual_dv = int(key[43])

        is_dv_valid = (expected_dv == actual_dv)
        uf_code = key[:2]
        uf_name = UF_IBGE_MAP.get(uf_code, f"Desconhecida ({uf_code})")
        year = f"20{key[2:4]}"
        month = key[4:6]
        cnpj = key[6:20]
        model = "55 (NF-e)" if key[20:22] == "55" else "65 (NFC-e)" if key[20:22] == "65" else key[20:22]
        series = key[22:25]
        number = key[25:34].lstrip("0")

        return {
            "is_valid": is_dv_valid,
            "error": None if is_dv_valid else f"Dígito verificador inválido: calculado {expected_dv}, encontrado {actual_dv}",
            "key": key,
            "uf": uf_name,
            "competence": f"{month}/{year}",
            "cnpj": cnpj,
            "model": model,
            "series": series,
            "number": number,
            "expected_dv": expected_dv,
            "actual_dv": actual_dv
        }

    def _diagnose_rejection(self, code: str, state: Any) -> Dict[str, Any]:
        """Gera metadados determinísticos para o código de rejeição sem engessar a prescrição."""
        meta = SEFAZ_REJECTIONS_META.get(str(code), {
            "title": f"Rejeição SEFAZ {code}",
            "category": "generica",
            "field_divergence": "Parâmetro tributário divergente na SEFAZ",
            "menu_path": "Vendas > Faturamento > NF-e",
            "official_source": "Portal Nacional da SEFAZ"
        })

        # Adicionar hipótese inicial ou evidência contextual no InvestigationState
        if hasattr(state, "add_hypothesis"):
            cause_name = f"Rejeição {code}: {meta['title']}"
            state.add_hypothesis(
                cause=cause_name,
                confidence=0.95,
                supporting=[f"Rejeição SEFAZ {code}"]
            )

        return {
            "rejection_code": code,
            "meta": meta,
            "search_query_recommended": f"Rejeição {code} {meta['title']} ERP Master"
        }
