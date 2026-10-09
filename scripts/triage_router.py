"""Conservative multi-topic routing using the existing curation taxonomy.

Routing is descriptive. It never authorizes an answer or a Movidesk write.
"""
from __future__ import annotations

import re

import knowledge_curation as curation


SIGNALS = {
    "products": ("produto", "grade", "estoque", "inventario", "sku", "tabela de preco"),
    "fiscal": ("nota fiscal", "notas fiscais", "nf-e", "nfe", "nfc-e", "nfce", "sefaz", "cfop", "imposto", "tribut"),
    "integrations": (
        "marketplace", "mercado livre", "mercadolivre", "shopee", "anymarket", "vtex",
        "loja integrada", "lojas integradas", "site", "tray", "magalu", "wehub", "skyhub",
        "api", "sincroniz", "sincronizador", "ocorrencia", "ocorrencias", "integrador", "integrado",
        "subindo", "subiu", "nao sobe", "nao subiu", "desceu", "nao desce", "nao desceu",
        "pedido nao", "produto nao sub", "estoque nao atualiz", "preco nao atualiz", "integracao"
    ),
    "sales": ("pedido", "venda", "orcamento", "faturamento"),
    "finance": ("boleto", "cnab", "contas a receber", "pagamento", "conciliacao"),
    "logistics": ("transportadora", "frete", "correios", "rastreio", "plp", "etiqueta de envio"),
    "purchases": ("compra", "fornecedor", "entrada de mercadoria", "xml de entrada"),
    "registration": ("cadastro", "cadastrar", "cliente", "parametro"),
    "production": ("producao", "materia-prima", "ordem de producao"),
    "infrastructure": ("firebird", "banco de dados", "servidor", "backup", "fdb", "conexao"),
}
PRINT_SIGNALS = ("fr3", "fastreport", "gondola", "impressora", "margem", "bobina", "desalinhad", "cortad", "codigo de barras", "zebra", "argox")
SHIP_SIGNALS = ("envio", "transportadora", "marketplace", "mercado livre", "shopee", "correios", "plp", "baixar etiqueta")


def _has_signal(text: str, term: str) -> bool:
    if len(term) <= 3:
        return bool(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text))
    return term in text


def route(subject: str, problem: str) -> dict:
    text = curation.normalize(f"{subject} {problem}")
    matches = {key: [term for term in terms if _has_signal(text, term)] for key, terms in SIGNALS.items()}
    matches = {key: terms for key, terms in matches.items() if terms}
    print_hits = [term for term in PRINT_SIGNALS if _has_signal(text, term)]
    shipping_hits = [term for term in SHIP_SIGNALS if _has_signal(text, term)]
    label_context = bool(re.search(r"\betiquet", text))
    if label_context and print_hits:
        matches.setdefault("products", []).extend(print_hits)
    if label_context and shipping_hits:
        matches.setdefault("logistics", []).extend(shipping_hits)
    if label_context and print_hits and shipping_hits:
        matches.setdefault("integrations", []).append("etiqueta com contexto de integracao e impressao")
    ranked = sorted(matches, key=lambda key: (-len(set(matches[key])), key))[:3]
    if not ranked:
        ranked = [curation.classify(subject, [], "")]
    ambiguous = len(ranked) > 1 or (label_context and print_hits and shipping_hits)
    labels = {key: curation.MODULE_NAMES.get(key, key) for key in ranked}
    return {
        "modules": ranked,
        "module_labels": [labels[key] for key in ranked],
        "signals": {key: sorted(set(matches.get(key, []))) for key in ranked},
        "needs_review": ambiguous or ranked == ["unclassified"],
        "reason": "Assuntos relacionados; conferir o contexto e as fontes." if ambiguous else
                  "Assunto sugerido pelos termos do chamado; confirmar com as fontes.",
        "confidence_notice": "Classificação temática, não probabilidade de diagnóstico correto.",
    }
