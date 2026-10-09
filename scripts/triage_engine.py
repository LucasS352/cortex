"""
DeskCortex -- TriageEngine: Motor de Triagem Adaptativa (Fase 2)
================================================================
Responsavel por:
  1. Detectar se a mensagem e vaga (precisa de investigacao) ou direta
  2. Atualizar o InvestigationState com informacoes extraidas da resposta
  3. Gerar a proxima pergunta de triagem (menor numero possivel)
  4. Decidir quando parar de perguntar e prescrever solucao

Principio (D1 deliberado):
  "Faca o menor numero possivel de perguntas para reduzir
   significativamente a incerteza. Cada pergunta deve eliminar
   uma classe inteira de causas possiveis."

Trava T1: nunca mover para "prescribing" apenas por confidence alta.
Precisa de evidencia compativel + ausencia de conflito.
"""

from __future__ import annotations
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

from chat_session import (
    ChatSession, InvestigationState, Evidence, EvidenceType,
    ResolutionStatus, RiskLevel, Hypothesis
)


# -------------------------------------------------------------------------
# Mapeamento de intencoes conhecidas
# -------------------------------------------------------------------------

# Padrao de regex -> (intent, module, perguntas de triagem ordenadas)
INTENT_MAP = [
    (
        r"pedido.*(nao|n[ao]{2}).*(ca[iu]+u|aparec|integr|master|sistem)",
        "pedido_nao_integrado",
        "Ecommerce",
        [
            "O pedido veio de qual integracao? (AnyMarket, B2B, VTEX, SKYHUB, Olist, outro marketplace?)",
            "O pedido aparece no painel da {integration}? Me passa o numero dele.",
            "Na tela de Ocorrencias do pedido no Master, o que aparece na coluna Erro? (Acesse: Gerenciamento > Pedidos > [numero] > aba Ocorrencias)",
        ],
    ),
    (
        r"(erro|rejeicao|rejei[cç][aã]o|nao.*emitir|nao.*transmit).*(nota|nf|nfe|fiscal)",
        "erro_nota_fiscal",
        "Fiscal",
        [
            "Qual e a mensagem exata do erro ou codigo de rejeicao?",
            "E uma NF-e de venda, entrada/compra ou devolucao?",
            "O certificado digital esta valido? (Acesse: Configuracoes > Certificado)",
        ],
    ),
    (
        r"(boleto|segunda via|refaturam).*(nao|n[ao]{2})?.*(gerar|emitir|abrir|imprimir)",
        "problema_boleto",
        "Financeiro",
        [
            "O erro aparece ao gerar ou ao tentar imprimir o boleto?",
            "Qual banco esta configurado para o boleto? (Bradesco, Caixa, Itau...)",
        ],
    ),
    (
        r"(estoque|saldo|quantidade).*(nao|errad|negativ|diverg|diferen|zer|inconsist)",
        "estoque_divergente",
        "Estoque",
        [
            "O estoque aparece errado em qual produto especificamente? (codigo ou nome)",
            "A divergencia aconteceu apos uma entrada de compra, venda ou inventario?",
        ],
    ),
    (
        r"(nao.*logar|nao.*acess|senha.*errad|usuario.*bloqueado|login)",
        "problema_acesso",
        "Configuracoes",
        [
            "O erro aparece ao tentar logar no Master ou em algum modulo especifico?",
            "E um usuario especifico ou todos os usuarios com esse problema?",
        ],
    ),
    (
        r"(marketplace|anymarket|vtex|skyhub|b2w|magalu|olist|tray).*(nao|erro|falha|parou)",
        "integracao_marketplace",
        "Ecommerce",
        [
            "Qual marketplace esta com problema? (AnyMarket, VTEX, B2W/Shoptime, Magalu, Olist...)",
            "O problema e com pedidos, produtos/estoque, ou preco?",
            "Ha quanto tempo esta acontecendo? Houve alguma alteracao recente no sistema?",
        ],
    ),
]

# Integracoes conhecidas para extracao de entidade
KNOWN_INTEGRATIONS = {
    "anymarket": "AnyMarket",
    "vtex": "VTEX",
    "b2w": "B2W",
    "shoptime": "B2W",
    "americanas": "B2W",
    "magalu": "Magalu",
    "magazine": "Magalu",
    "olist": "Olist",
    "skyhub": "Skyhub",
    "tray": "Tray",
    "loja integrada": "Loja_Integrada",
    "moovin": "Moovin",
    "hub2b": "Hub2b",
    "b2b": "B2B",
    "mercos": "Mercos",
    "bling": "Bling",
    "mercado livre": "Mercado_Livre",
    "meli": "Mercado_Livre",
    "shopee": "Shopee",
    "amazon": "Amazon",
    "shein": "Shein",
    "nuvemshop": "Nuvemshop",
}

ORDER_RE = re.compile(
    r"\b(?:pedido|order|n[uú]mero|num|n[ro]|#)\s*(?:[eé]|eh|foi|ser|de\s+n[uú]mero)?\s*[:#\-]?\s*(?:o\s+)?(\d{4,12})\b",
    re.I
)
CFOP_RE = re.compile(r"\b([123567]\d{3})\b")
NFE_KEY_RE = re.compile(r"\b(\d{44})\b")
SEFAZ_REJ_RE = re.compile(r"(?:(?:rejei[cç][aã]o|cstat)\s*[:\-]?\s*(\d{3})|(?:->|\b)(\d{3})\s*[-:]\s*rejei[cç][aã]o)", re.I)
BANCOS_MAP = {
    "bradesco": "Bradesco",
    "itau": "Itaú",
    "itaú": "Itaú",
    "caixa": "Caixa Econômica",
    "banco do brasil": "Banco do Brasil",
    "bb": "Banco do Brasil",
    "santander": "Santander",
    "sicoob": "Sicoob",
    "sicredi": "Sicredi",
    "inter": "Banco Inter",
    "safra": "Safra",
}

# Hipoteses base por intent (confidence inicial sem evidencia)
BASE_HYPOTHESES = {
    "pedido_nao_integrado": [
        ("sku_nao_cadastrado", 0.30),
        ("forma_pagamento_nao_vinculada", 0.25),
        ("transportadora_nao_vinculada", 0.20),
        ("sincronizador_offline", 0.15),
        ("nao_integrou_anymarket", 0.10),
    ],
    "erro_nota_fiscal": [
        ("instabilidade_sefaz_timeout", 0.40),
        ("regime_tributario_divergente", 0.30),
        ("cfop_incorreto", 0.20),
        ("duplicidade_nfe", 0.15),
        ("dados_destinatario", 0.10),
        ("certificado_expirado", 0.10),
        ("tributacao_incorreta", 0.05),
    ],
    "problema_boleto": [
        ("convenio_incorreto", 0.40),
        ("certificado_banco", 0.35),
        ("configuracao_carteira", 0.25),
    ],
    "estoque_divergente": [
        ("lancamento_incorreto", 0.40),
        ("nota_sem_baixa", 0.35),
        ("inventario_nao_finalizado", 0.25),
    ],
    "problema_acesso": [
        ("senha_expirada", 0.35),
        ("permissao_negada", 0.30),
        ("usuario_inativo", 0.20),
        ("firebird_offline", 0.15),
    ],
    "integracao_marketplace": [
        ("sincronizador_offline", 0.35),
        ("token_expirado", 0.30),
        ("sku_mapeamento", 0.20),
        ("regra_preco", 0.15),
    ],
}

# Erros especificos que confirmam hipoteses (Evidence -> Hypothesis mapping)
ERROR_CONFIRMATION_MAP = {
    r"SKU_NOT_FOUND":             ("sku_nao_cadastrado",            1.00),
    r"PRODUTO NAO LOCALIZADO":    ("sku_nao_cadastrado",            1.00),
    r"FORMA DE PAGAMENTO":        ("forma_pagamento_nao_vinculada", 0.95),
    r"CONDICAO DE PAGAMENTO":     ("forma_pagamento_nao_vinculada", 0.95),
    r"PAGAMENTO NAO VINCULADO":   ("forma_pagamento_nao_vinculada", 1.00),
    r"TRANSPORTADORA":            ("transportadora_nao_vinculada",  0.95),
    r"FRETE NAO VINCULADO":       ("transportadora_nao_vinculada",  1.00),
    r"METODO DE ENVIO":           ("transportadora_nao_vinculada",  0.90),
    r"10060":                     ("instabilidade_sefaz_timeout",   1.00),
    r"CONNECTION TIMED OUT":      ("instabilidade_sefaz_timeout",   1.00),
    r"TIMED OUT":                 ("instabilidade_sefaz_timeout",   1.00),
    r"TIMEOUT":                   ("sincronizador_offline",         0.85),
    r"CONNECTION REFUSED":        ("sincronizador_offline",         0.90),
    r"OFFLINE":                   ("sincronizador_offline",         0.90),
    r"CERTIFICADO":               ("certificado_expirado",          0.90),
    r"CFOP":                      ("cfop_incorreto",                0.85),
    r"REJEICAO\s*[:\-]?\s*783":   ("cfop_incorreto",                1.00),
    r"\b783\b":                   ("cfop_incorreto",                1.00),
    r"REJEICAO\s*[:\-]?\s*481":   ("regime_tributario_divergente",  1.00),
    r"\b481\b":                   ("regime_tributario_divergente",  1.00),
    r"REGIME.*TRIBUTARIO":        ("regime_tributario_divergente",  1.00),
    r"CRT.*DIVERG":               ("regime_tributario_divergente",  1.00),
    r"DIVERGE.*SEFAZ":            ("regime_tributario_divergente",  1.00),
    r"CODIGO REGIME TRIBUTARIO":  ("regime_tributario_divergente",  1.00),
    r"REJEICAO\s*[:\-]?\s*539":   ("duplicidade_nfe",               1.00),
    r"\b539\b":                   ("duplicidade_nfe",               1.00),
    r"REJEICAO\s*[:\-]?\s*204":   ("duplicidade_nfe",               1.00),
    r"\b204\b":                   ("duplicidade_nfe",               1.00),
    r"DUPLICIDADE":               ("duplicidade_nfe",               0.95),
    r"USUARIO INATIVO":           ("usuario_inativo",               1.00),
    r"SENHA EXPIRADA":            ("senha_expirada",                1.00),
    r"CONVENIO":                  ("convenio_incorreto",            0.90),
}


@dataclass
class TriageResult:
    needs_more_info: bool
    next_question: Optional[str]
    state_updated: bool
    ready_to_prescribe: bool
    confirmed_cause: Optional[str]


class TriageEngine:
    """
    Motor de triagem adaptativa.
    Analisa cada mensagem do usuario, atualiza o investigation_state
    e decide a proxima acao (perguntar mais ou prescrever solucao).
    """

    def _detect_intent(self, message: str) -> tuple[Optional[str], Optional[str], Optional[list[str]]]:
        """
        Detecta a intencao usando combinacao de Regex e Bag-of-Words sobre texto normalizado.
        Retorna (intent, module, questions).
        """
        msg_norm = self._normalize(message).lower()
        tokens = set(re.findall(r"\w+", msg_norm))

        # 1. Tentativa via Regex estrito em INTENT_MAP
        for pattern, intent, module, questions in INTENT_MAP:
            if re.search(pattern, message.lower(), re.I) or re.search(pattern, msg_norm, re.I):
                return intent, module, questions

        # 2. Tentativa via Bag-of-Words / co-ocorrencia de conceitos-chave
        # Pedido nao integrado
        if ({"pedido", "pedidos", "compra", "compras"} & tokens and
            {"nao", "erro", "falha", "sumiu", "sumiram", "onde"} & tokens and
            {"caiu", "cairam", "integrou", "integraram", "apareceu", "apareceram", "subiu", "subiram", "importou", "importaram", "master", "sistema", "anymarket", "vtex"} & tokens):
            return "pedido_nao_integrado", "Ecommerce", INTENT_MAP[0][3]

        # Erro nota fiscal
        if ({"nota", "notas", "nf", "nfe", "danfe", "fiscal"} & tokens and
            {"erro", "rejeicao", "rejeitada", "rejeitado", "trava", "travou", "falha", "transmitir", "emissao", "emitir", "autorizar", "783", "539"} & tokens):
            return "erro_nota_fiscal", "Fiscal", INTENT_MAP[1][3]

        # Boleto
        if ({"boleto", "boletos", "bancario"} & tokens and
            {"gerar", "emitir", "imprimir", "abrir", "erro", "falha", "nao", "problema"} & tokens):
            return "problema_boleto", "Financeiro", INTENT_MAP[2][3]

        # Estoque
        if ({"estoque", "saldo", "quantidade", "inventario"} & tokens and
            {"errado", "errada", "negativo", "diverge", "divergente", "divergencia", "divergindo", "diferenca", "diferente", "zerado", "zerou", "nao", "inconsistente"} & tokens):
            return "estoque_divergente", "Estoque", INTENT_MAP[3][3]

        # Acesso
        if ({"logar", "login", "acessar", "acesso", "senha", "usuario"} & tokens and
            {"nao", "bloqueado", "bloqueada", "invalido", "invalida", "expirada", "esqueci", "erro", "recuperar"} & tokens):
            return "problema_acesso", "Configuracoes", INTENT_MAP[4][3]

        # Integracao Marketplace
        if ({"marketplace", "anymarket", "vtex", "skyhub", "b2w", "magalu", "olist", "tray", "shopee", "amazon", "shein"} & tokens and
            {"parou", "travou", "erro", "falha", "nao", "sincronizar", "integrar", "comunicar"} & tokens):
            return "integracao_marketplace", "Ecommerce", INTENT_MAP[5][3]

        return None, None, None

    def analyze_first_message(self, session: ChatSession, message: str) -> TriageResult:
        """
        Analisa a primeira mensagem e inicializa o investigation_state.
        """
        state = session.state

        # 1. Identificar intent e modulo
        intent, module, _ = self._detect_intent(message)
        if intent:
            state.intent = intent
            state.module = module
            state.symptoms.append(message[:200])
            state.resolution_status = ResolutionStatus.INVESTIGATING
            state.investigation_step = 1

            # Inicializar hipoteses base
            for cause, conf in BASE_HYPOTHESES.get(intent, []):
                state.add_hypothesis(cause, conf)

            # Preencher missing_information inicial
            state.missing_information = self._build_missing_info(intent, state)

            # Tentar extrair entidade ja na primeira mensagem
            self._extract_entities(message, state)

            # Gerar primeira pergunta de triagem baseada nas informacoes ainda pendentes
            next_q = self._build_next_question_from_state(state)
            return TriageResult(
                needs_more_info=True,
                next_question=next_q,
                state_updated=True,
                ready_to_prescribe=False,
                confirmed_cause=None,
            )

        # Intent desconhecido -- mensagem direta, sem investigacao forcada
        state.intent = "consulta_geral"
        state.resolution_status = ResolutionStatus.INVESTIGATING
        return TriageResult(
            needs_more_info=False,
            next_question=None,
            state_updated=True,
            ready_to_prescribe=False,
            confirmed_cause=None,
        )

    def process_user_response(self, session: ChatSession, message: str) -> TriageResult:
        """
        Processa resposta do usuario, atualiza estado e decide proximo passo.
        """
        state = session.state
        state.investigation_step += 1
        msg_lower = message.lower()

        # 0. Se a intenção ainda não foi definida ou era apenas saudação/consulta geral,
        # ou se o cliente agora declarou um erro específico de outro módulo, atualiza o intent
        if state.intent in (None, "consulta_geral") or any(k in msg_lower for k in ["erro", "rejei", "falha", "nota fiscal", "nf", "nfe", "anymarket", "pedido", "boleto"]):
            new_intent, new_mod, _ = self._detect_intent(message)
            if new_intent and (state.intent in (None, "consulta_geral") or new_intent != state.intent):
                state.intent = new_intent
                state.module = new_mod
                state.symptoms.append(message[:200])
                state.resolution_status = ResolutionStatus.INVESTIGATING
                for cause, conf in BASE_HYPOTHESES.get(new_intent, []):
                    state.add_hypothesis(cause, conf)
                state.missing_information = self._build_missing_info(new_intent, state)

        # 1. Extrair entidades da resposta
        self._extract_entities(message, state)

        # 2. Ajuste Bayesiano / Eliminacao de hipoteses
        self._update_hypotheses_and_evidence(message, state)

        # 3. Verificar se resposta contem erro especifico que confirma hipotese
        self._check_error_confirmation(message, state)

        # 4. Trava T1 estrita: so confirma se houver evidencia fatual compativel e sem conflito
        auto_confirmed = state.check_confirmation()
        if auto_confirmed:
            state.confirmed_cause = auto_confirmed
            state.resolution_status = ResolutionStatus.PRESCRIBING
            return TriageResult(
                needs_more_info=False,
                next_question=None,
                state_updated=True,
                ready_to_prescribe=True,
                confirmed_cause=auto_confirmed,
            )

        # 5. Atualizar missing_information com o que foi respondido
        self._update_missing_info(message, state)

        # 6. Se ainda ha informacoes criticas faltando, gerar proxima pergunta
        if state.missing_information:
            next_q = self._build_next_question_from_state(state)
            if next_q:
                return TriageResult(
                    needs_more_info=True,
                    next_question=next_q,
                    state_updated=True,
                    ready_to_prescribe=False,
                    confirmed_cause=None,
                )

        # 7. Sem informacoes criticas faltando: deixar o LLM decidir com base nas hipoteses
        return TriageResult(
            needs_more_info=False,
            next_question=None,
            state_updated=True,
            ready_to_prescribe=False,
            confirmed_cause=None,
        )

    # -------------------------------------------------------------------------
    # Helpers privados
    # -------------------------------------------------------------------------

    def _normalize(self, text: str) -> str:
        """Remove acentos e caracteres especiais, mantendo texto ASCII limpo."""
        return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('utf-8')

    def _update_hypotheses_and_evidence(self, message: str, state: InvestigationState):
        """
        Raciocinio diagnostico / Bayesiano:
        Ajusta confiancas de hipoteses e elimina causas refutadas pelo relato.
        """
        msg_lower = message.lower()

        # Pedido nao integrado: AnyMarket / marketplace
        if state.intent == "pedido_nao_integrado":
            not_in_hub = (
                any(neg in msg_lower for neg in ["nao", "nem", "sem"])
                and any(hub in msg_lower for hub in ["anymarket", "painel", "integrador", "hub", "integracao"])
                and any(v in msg_lower for v in ["aparece", "esta", "ta", "te", "tem", "consta", "chegou", "subiu", "foi", "vejo", "achei", "encontro"])
            ) or any(term in msg_lower for term in ["nao aparece no painel", "nao esta no painel", "nem na anymarket", "nao ta na anymarket", "nao tem no painel", "nao te na anymarket"])

            if not_in_hub:
                state.add_evidence(
                    source="usuario",
                    type=EvidenceType.FATO,
                    content="Pedido nao consta no painel intermediario da integracao/marketplace (AnyMarket)",
                )
                state.eliminate_hypothesis("sku_nao_cadastrado", "Pedido sequer chegou ao intermediador")
                state.eliminate_hypothesis("forma_pagamento_nao_vinculada", "Pedido nao chegou ao intermediador")
                state.eliminate_hypothesis("transportadora_nao_vinculada", "Pedido nao chegou ao intermediador")
                for h in state.hypotheses:
                    if h.cause == "nao_integrou_anymarket":
                        h.confidence = 0.95
                        if "nao consta no painel" not in h.supporting_evidence:
                            h.supporting_evidence.append("nao consta no painel")
                # Se nao esta na AnyMarket, a causa raiz e externa (marketplace de origem nao enviou)
                state.missing_information = []
                state.confirmed_cause = "nao_integrou_anymarket"
                state.resolution_status = ResolutionStatus.PRESCRIBING

            if any(term in msg_lower for term in ["forma de pagamento", "pagamento", "condicao de pagamento"]):
                for h in state.hypotheses:
                    if h.cause == "forma_pagamento_nao_vinculada":
                        h.confidence = max(h.confidence, 0.75)

            if any(term in msg_lower for term in ["transportadora", "frete", "metodo de envio", "envio"]):
                for h in state.hypotheses:
                    if h.cause == "transportadora_nao_vinculada":
                        h.confidence = max(h.confidence, 0.75)

        # Problema de acesso: discriminacao individual vs geral
        elif state.intent == "problema_acesso":
            if any(term in msg_lower for term in ["so no meu", "so eu", "apenas meu", "outros conseguem", "outros entram"]):
                state.eliminate_hypothesis("firebird_offline", "Outros colaboradores conseguem acessar normalmente")
                for h in state.hypotheses:
                    if h.cause in ("senha_expirada", "permissao_negada", "usuario_inativo"):
                        h.confidence = min(0.80, h.confidence + 0.35)

            elif any(term in msg_lower for term in ["todos", "ninguem", "na empresa toda", "todos os usuarios"]):
                state.eliminate_hypothesis("senha_expirada", "Problema simultaneo em todos os usuarios")
                state.eliminate_hypothesis("usuario_inativo", "Problema simultaneo em todos os usuarios")
                for h in state.hypotheses:
                    if h.cause == "firebird_offline":
                        h.confidence = 0.85
                        if "afeta todos os usuarios" not in h.supporting_evidence:
                            h.supporting_evidence.append("afeta todos os usuarios")

        # Nota fiscal: validade de certificado e rejeições SEFAZ
        elif state.intent == "erro_nota_fiscal":
            if any(term in msg_lower for term in ["certificado valido", "certificado ok", "esta valido", "nao esta vencido", "ja verifiquei o certificado"]):
                state.eliminate_hypothesis("certificado_expirado", "Cliente confirmou certificado digital valido")

            if any(term in msg_lower for term in ["10060", "timed out", "timeout", "tempo esgotado", "http: 500", "http 500", "500", "nfeautorizacao"]):
                for h in state.hypotheses:
                    if h.cause == "instabilidade_sefaz_timeout":
                        h.confidence = 1.00
                        if "erro 10060 timeout sefaz" not in h.supporting_evidence:
                            h.supporting_evidence.append("erro 10060 timeout sefaz")
                state.confirmed_cause = "instabilidade_sefaz_timeout"
                state.resolution_status = ResolutionStatus.PRESCRIBING
                state.missing_information = []
                state.add_evidence(
                    source="sistema",
                    type=EvidenceType.FATO,
                    content="Erro Interno 10060: Connection timed out ao contatar webservice da SEFAZ"
                )

            elif any(term in msg_lower for term in ["481", "regime tributario", "regime", "crt", "diverge do cadastro", "divergencia"]):
                for h in state.hypotheses:
                    if h.cause == "regime_tributario_divergente":
                        h.confidence = 1.00
                        if "rejeicao 481 sefaz" not in h.supporting_evidence:
                            h.supporting_evidence.append("rejeicao 481 sefaz")
                state.confirmed_cause = "regime_tributario_divergente"
                state.resolution_status = ResolutionStatus.PRESCRIBING
                state.missing_information = []

            elif any(term in msg_lower for term in ["783", "cfop", "interestadual"]):
                for h in state.hypotheses:
                    if h.cause == "cfop_incorreto":
                        h.confidence = 1.00
                        if "rejeicao 783 sefaz" not in h.supporting_evidence:
                            h.supporting_evidence.append("rejeicao 783 sefaz")
                state.confirmed_cause = "cfop_incorreto"
                state.resolution_status = ResolutionStatus.PRESCRIBING
                state.missing_information = []

            elif any(term in msg_lower for term in ["539", "204", "duplicidade"]):
                for h in state.hypotheses:
                    if h.cause == "duplicidade_nfe":
                        h.confidence = 1.00
                        if "duplicidade nfe sefaz" not in h.supporting_evidence:
                            h.supporting_evidence.append("duplicidade nfe sefaz")
                state.confirmed_cause = "duplicidade_nfe"
                state.resolution_status = ResolutionStatus.PRESCRIBING
                state.missing_information = []

            elif any(term in msg_lower for term in ["629", "valor do produto difere", "quantidade comercial", "unitario de comercializacao"]):
                state.add_hypothesis("rejeicao_629_valor_produto", 1.00)
                state.confirmed_cause = "rejeicao_629_valor_produto"
                state.resolution_status = ResolutionStatus.PRESCRIBING
                state.missing_information = []
                state.add_evidence(
                    source="sistema",
                    type=EvidenceType.FATO,
                    content="Rejeição 629 SEFAZ: Valor do Produto difere do produto Valor Unitário de Comercialização e Quantidade Comercial (desconto no item superior a 100%)"
                )

            else:
                m_rej = SEFAZ_REJ_RE.search(message)
                if m_rej:
                    code = m_rej.group(1) or m_rej.group(2)
                    cause_name = f"rejeicao_{code}_sefaz"
                    state.add_hypothesis(cause_name, 1.00)
                    state.confirmed_cause = cause_name
                    state.resolution_status = ResolutionStatus.PRESCRIBING
                    state.missing_information = []
                    state.add_evidence(
                        source="sistema",
                        type=EvidenceType.FATO,
                        content=f"Rejeição SEFAZ identificada: {code}"
                    )

    def _extract_entities(self, message: str, state: InvestigationState):
        """Extrai integracao, numero de pedido, CFOP, chave NF-e, rejeicao SEFAZ e bancos da mensagem."""
        msg_lower = message.lower()

        # 1. Integracao
        if not state.integration:
            for keyword, canonical in KNOWN_INTEGRATIONS.items():
                if keyword in msg_lower:
                    state.integration = canonical
                    state.add_evidence(
                        source="usuario",
                        type=EvidenceType.FATO,
                        content=f"Integracao identificada: {canonical}",
                    )
                    state.missing_information = [
                        m for m in state.missing_information
                        if m not in ("integracao_origem", "marketplace_especifico")
                    ]
                    break

        # 2. Numero do pedido
        if not state.entity:
            m = ORDER_RE.search(message)
            if not m and "numero_pedido" in state.missing_information:
                m_digits = re.search(r"\b(\d{4,12})\b", message)
                if m_digits:
                    m = m_digits

            if m:
                order_id = m.group(1)
                state.entity = {"type": "order", "id": order_id}
                state.add_evidence(
                    source="usuario",
                    type=EvidenceType.FATO,
                    content=f"Pedido #{order_id} confirmado pelo usuario",
                )
                state.missing_information = [
                    mi for mi in state.missing_information
                    if mi != "numero_pedido"
                ]

        # 3. CFOP (ex: 5102, 6108)
        m_cfop = CFOP_RE.search(message)
        if m_cfop:
            cfop_val = m_cfop.group(1)
            state.add_evidence(
                source="usuario",
                type=EvidenceType.FATO,
                content=f"CFOP informado: {cfop_val}",
            )
            if not state.entity:
                state.entity = {"type": "nfe", "cfop": cfop_val}
            else:
                state.entity["cfop"] = cfop_val
            state.missing_information = [
                m for m in state.missing_information
                if "cfop" not in m.lower()
            ]

        # 4. Chave NF-e (44 digitos)
        m_nfe = NFE_KEY_RE.search(message)
        if m_nfe:
            nfe_key = m_nfe.group(1)
            state.add_evidence(
                source="usuario",
                type=EvidenceType.FATO,
                content=f"Chave NF-e identificada: {nfe_key}",
            )
            if not state.entity:
                state.entity = {"type": "nfe", "chave": nfe_key}
            else:
                state.entity["chave"] = nfe_key
            state.missing_information = [
                m for m in state.missing_information
                if "chave" not in m.lower()
            ]

        # 5. Codigo de Rejeicao SEFAZ (ex: Rejeicao 783, 481, 539, etc.)
        m_rej = SEFAZ_REJ_RE.search(message)
        rej_code = None
        if m_rej:
            rej_code = m_rej.group(1) or (m_rej.group(2) if len(m_rej.groups()) > 1 else None)
        elif re.search(r"\b(481|783|539|204|694|10060)\b", message):
            m_code = re.search(r"\b(481|783|539|204|694|10060)\b", message)
            rej_code = m_code.group(1)
        elif re.search(r"regime\s*tribut.*diverg", self._normalize(message), re.I):
            rej_code = "481"
        elif any(w in msg_lower for w in ["10060", "timed out", "connection timed out"]):
            rej_code = "10060"

        if rej_code:
            state.add_evidence(
                source="usuario",
                type=EvidenceType.FATO,
                content=f"Rejeicao SEFAZ identificada: {rej_code}",
            )
            if not state.entity:
                state.entity = {"type": "nfe", "rejection_code": rej_code}
            else:
                state.entity["rejection_code"] = rej_code
            state.missing_information = [
                m for m in state.missing_information
                if "mensagem_erro" not in m and "rejeicao" not in m
            ]

        # 6. Banco emissor de boleto
        for b_key, b_name in BANCOS_MAP.items():
            if b_key in msg_lower:
                state.add_evidence(
                    source="usuario",
                    type=EvidenceType.FATO,
                    content=f"Banco emissor identificado: {b_name}",
                )
                if not state.entity:
                    state.entity = {"type": "boleto", "bank": b_name}
                else:
                    state.entity["bank"] = b_name
                state.missing_information = [
                    m for m in state.missing_information
                    if "banco" not in m.lower()
                ]
                break

    def _check_error_confirmation(self, message: str, state: InvestigationState) -> Optional[str]:
        """
        Verifica se a mensagem contem um erro que confirma diretamente uma hipotese.
        Trava T1: confirma causa via evidencia, nao apenas confidence.
        """
        msg_norm = self._normalize(message)
        for error_pattern, (cause, conf) in ERROR_CONFIRMATION_MAP.items():
            if re.search(error_pattern, msg_norm, re.I):
                state.add_evidence(
                    source="usuario",
                    type=EvidenceType.CONFIRMACAO,
                    content=f"Erro confirmado pelo usuario: {error_pattern}",
                    confidence=conf,
                )
                # Atualizar confidence da hipotese correspondente
                for h in state.hypotheses:
                    if h.cause == cause:
                        h.confidence = conf
                        if error_pattern not in h.supporting_evidence:
                            h.supporting_evidence.append(error_pattern)
                return cause
        return None

    def _build_missing_info(self, intent: str, state: InvestigationState) -> list[str]:
        """Retorna lista de informacoes criticas ainda desconhecidas."""
        missing = []
        if intent == "pedido_nao_integrado":
            if not state.integration:
                missing.append("integracao_origem")
            if not state.entity:
                missing.append("numero_pedido")
            missing.append("ocorrencias_do_pedido")
        elif intent == "erro_nota_fiscal":
            missing.append("mensagem_erro_exata")
            missing.append("tipo_nf")
        elif intent == "integracao_marketplace":
            if not state.integration:
                missing.append("marketplace_especifico")
            missing.append("tipo_problema_marketplace")
        elif intent == "problema_boleto":
            missing.extend(["etapa_falha", "banco_emissor"])
        elif intent == "estoque_divergente":
            missing.extend(["codigo_produto", "origem_movimento"])
        elif intent == "problema_acesso":
            missing.extend(["abrangencia_usuarios", "modulo_afetado"])
        return missing

    def _update_missing_info(self, message: str, state: InvestigationState):
        """Remove itens de missing_information que foram respondidos."""
        msg_lower = message.lower()
        if "ocorrencia" in msg_lower or "erro" in msg_lower or "falha" in msg_lower:
            state.missing_information = [m for m in state.missing_information if "ocorrencia" not in m.lower()]
        if any(word in msg_lower for word in ["venda", "entrada", "devolucao", "compra"]):
            state.missing_information = [m for m in state.missing_information if "tipo_nf" not in m]
        if any(word in msg_lower for word in ["gerar", "imprimir", "emitir"]):
            state.missing_information = [m for m in state.missing_information if "etapa_falha" not in m]
        if any(word in msg_lower for word in ["bradesco", "itau", "caixa", "bb", "santander", "sicoob", "sicredi", "inter"]):
            state.missing_information = [m for m in state.missing_information if "banco_emissor" not in m]
        if re.search(r"(sku|\b\d+\b|produto)", msg_lower):
            state.missing_information = [m for m in state.missing_information if "codigo_produto" not in m]
        if any(word in msg_lower for word in ["compra", "venda", "inventario"]):
            state.missing_information = [m for m in state.missing_information if "origem_movimento" not in m]
        if any(word in msg_lower for word in ["meu", "todos", "usuario", "so eu"]):
            state.missing_information = [m for m in state.missing_information if "abrangencia_usuarios" not in m]
        if any(word in msg_lower for word in ["master", "pdv", "estoque", "financeiro", "vendas", "modulo", "tela", "sistema"]):
            state.missing_information = [m for m in state.missing_information if "modulo_afetado" not in m]
        if any(word in msg_lower for word in ["erro", "rejeicao", "codigo", "mensagem"]) or re.search(r"\d{2,}", msg_lower):
            state.missing_information = [m for m in state.missing_information if "mensagem_erro_exata" not in m]
        if any(word in msg_lower for word in ["pedido", "estoque", "preco"]):
            state.missing_information = [m for m in state.missing_information if "tipo_problema_marketplace" not in m]

    def _build_next_question_from_state(self, state: InvestigationState) -> Optional[str]:
        """Gera pergunta baseada no que ainda esta faltando no estado."""
        priority_questions = {
            "integracao_origem": "O pedido veio de qual integracao? (AnyMarket, VTEX, B2W, Olist...)",
            "numero_pedido": "Qual e o numero exato do pedido? (para eu conseguir rastrear)",
            "ocorrencias_do_pedido": (
                "Acesse no Master: Gerenciamento > Pedidos > [numero do pedido] > aba Ocorrencias. "
                "O que aparece na coluna Erro?"
            ),
            "mensagem_erro_exata": "Qual e a mensagem de erro exata que aparece na tela?",
            "tipo_nf": "E uma NF-e de venda, de entrada (compra) ou devolucao?",
            "marketplace_especifico": "Qual marketplace especifico? (AnyMarket, VTEX, B2W, Magalu, Olist...)",
            "tipo_problema_marketplace": "O problema e com pedidos, com produtos/estoque ou com preco?",
            "etapa_falha": "O erro acontece ao gerar o boleto ou ao tentar imprimir?",
            "banco_emissor": "Qual banco esta configurado para o boleto? (Bradesco, Caixa, Itau, Santander...)",
            "codigo_produto": "Qual o codigo ou nome do produto com estoque errado?",
            "origem_movimento": "A divergencia aconteceu apos uma entrada de compra, venda ou inventario?",
            "abrangencia_usuarios": "O problema e apenas no seu usuario ou todos os usuarios estao com o mesmo problema?",
            "modulo_afetado": "O erro aparece ao tentar logar no Master ou em algum modulo especifico?",
        }
        for missing in state.missing_information:
            if missing in priority_questions:
                q = priority_questions[missing]
                return q.replace("{integration}", state.integration or "integracao")
        return None
