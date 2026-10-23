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
REPORT_SIGNALS = ("relatorio", "relatorios", "indicador", "indicadores", "markup", "cmv", "rentabilidade", "curva abc", "estatistica de vendas", "vendas por produto", "posicao de estoque", "projecao de compras")


def _has_signal(text: str, term: str) -> bool:
    if len(term) <= 3:
        return bool(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text))
    return term in text


def _canonical_module(value: str) -> str:
    """Normalize stored Portuguese labels and internal module keys."""
    text = curation.normalize(str(value or '')).strip()
    aliases = {
        'produto': 'products', 'produtos': 'products', 'produtos e estoque': 'products', 'estoque': 'products',
        'fiscal': 'fiscal', 'fiscal e notas': 'fiscal', 'integracao': 'integrations', 'integracoes': 'integrations',
        'venda': 'sales', 'vendas': 'sales', 'vendas e pedidos': 'sales', 'pedido': 'sales', 'pedidos': 'sales',
        'financeiro': 'finance', 'logistica': 'logistics', 'compra': 'purchases', 'compras': 'purchases',
        'cadastro': 'registration', 'cadastros': 'registration', 'producao': 'production',
        'infraestrutura': 'infrastructure', 'relatorio': 'reports', 'relatorios': 'reports',
    }
    return aliases.get(text, text)


def route(subject: str, problem: str) -> dict:
    text = curation.normalize(f"{subject} {problem}")
    matches = {key: [term for term in terms if _has_signal(text, term)] for key, terms in SIGNALS.items()}
    matches = {key: terms for key, terms in matches.items() if terms}
    print_hits = [term for term in PRINT_SIGNALS if _has_signal(text, term)]
    shipping_hits = [term for term in SHIP_SIGNALS if _has_signal(text, term)]
    report_hits = [term for term in REPORT_SIGNALS if _has_signal(text, term)]
    business_margin = bool(_has_signal(text, "margem") and re.search(r"\b(?:venda|vendas|lucro|custo|produto|produtos)\b", text))
    technical_print = bool(any(_has_signal(text, term) for term in ("fr3", "fastreport", "etiqueta", "bobina", "impressora", "zebra", "argox")))
    if business_margin:
        report_hits.append("margem de venda")
    if report_hits and (business_margin or not technical_print):
        matches["reports"] = sorted(set(report_hits))
    label_context = bool(re.search(r"\betiquet", text))
    if label_context and print_hits:
        matches.setdefault("products", []).extend(print_hits)
    if label_context and shipping_hits:
        matches.setdefault("logistics", []).extend(shipping_hits)
    if label_context and print_hits and shipping_hits:
        matches.setdefault("integrations", []).append("etiqueta com contexto de integracao e impressao")
    ranked = sorted(matches, key=lambda key: (
        -len(set(matches[key])),
        0 if key == 'reports' and report_hits and not technical_print else 1,
        key))[:3]
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


def _load_configured_specialists(specialists: list[dict] | None = None) -> list[dict]:
    if isinstance(specialists, list) and specialists:
        return [s for s in specialists if isinstance(s, dict) and s.get("enabled", True)]
    try:
        import admin_console
        state = admin_console.get_state()
        configured = state.get("specialists") or admin_console.DEFAULT_SPECIALISTS
        return [s for s in configured if isinstance(s, dict) and s.get("enabled", True)]
    except Exception:
        return []


def resolve_specialist(subject: str, problem: str, specialists: list[dict] | None = None) -> tuple[dict | None, dict]:
    """Route the active ticket problem to the best enabled ERP domain specialist."""
    route_info = route(subject, problem)
    specs = _load_configured_specialists(specialists)
    if not specs:
        return None, route_info
    text = curation.normalize(f"{subject} {problem}")
    best_spec = None
    best_score = (0, 0)
    ranked_modules = route_info.get("modules", [])
    for spec in specs:
        spec_modules = [str(m) for m in (spec.get("modules") or [])]
        if spec.get("id") and spec["id"] not in spec_modules:
            spec_modules.append(str(spec["id"]))
        kw_hits = sum(
            1 for kw in (spec.get("keywords") or [])
            if kw and _has_signal(text, curation.normalize(str(kw)))
        )
        mod_rank = 0
        for idx, mod in enumerate(ranked_modules):
            if mod in spec_modules:
                mod_rank = max(mod_rank, len(ranked_modules) - idx)
        score = (mod_rank, kw_hits)
        if score > best_score:
            best_score = score
            best_spec = spec
    if best_spec:
        return best_spec, route_info
    for mod in ranked_modules:
        for spec in specs:
            if spec.get("id") == mod or mod in (spec.get("modules") or []):
                return spec, route_info
    return None, route_info


def filter_by_specialist(items: list[dict], specialist: dict | None, route_info: dict | None = None) -> list[dict]:
    """Keep sources and resolved tickets scoped to the active specialist's ERP domain."""
    if not items or (not specialist and not route_info):
        return list(items or [])
    allowed_modules = set(specialist.get("modules") or []) if specialist else set()
    if specialist and specialist.get("id"):
        allowed_modules.add(str(specialist["id"]))
    if route_info:
        allowed_modules.update(route_info.get("modules") or [])
    # Integrations frequently touch carriers (logistics), orders (sales), payments (finance), and products.
    if "integrations" in allowed_modules:
        allowed_modules.update({"logistics", "sales", "products", "finance"})
    elif "reports" in allowed_modules:
        # Business reports are cross-domain by nature. The specialist still
        # relies on retrieval/applicability gates before using any source.
        allowed_modules.update({"sales", "finance", "products", "purchases", "fiscal", "logistics", "production", "registration"})
    elif "products" in allowed_modules:
        allowed_modules.update({"registration", "logistics"})
    elif "sales" in allowed_modules:
        allowed_modules.update({"finance", "fiscal"})
    allowed_modules.discard("unclassified")
    if not allowed_modules:
        return list(items)

    in_domain = []
    neutral = []
    for item in items:
        if (item.get("source_type") == "REVIEWED_PROCEDURE" or
                item.get("evidence_status") == "human_approved_procedure"):
            in_domain.append(item)
            continue
        title = str(item.get("title") or item.get("subject") or "")
        body = " ".join(
            str(item.get(k) or "")
            for k in ("problem", "problem_statement", "diagnosis", "technical_narrative", "solution", "solution_statement")
        )
        raw_modules = item.get("modules") or route(title, body).get("modules") or []
        item_modules = {_canonical_module(module) for module in raw_modules}
        item_modules.discard("unclassified")
        if not item_modules:
            neutral.append(item)
        elif item_modules & allowed_modules:
            in_domain.append(item)
    return in_domain + neutral if in_domain else list(items)
