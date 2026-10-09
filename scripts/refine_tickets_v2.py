"""
DeskCortex — Pipeline de Refinaria v2: 4 Dimensões, Evidências e Regras de Bloqueio Rígidas
=============================================================================================
Implementa o motor avançado de inteligência com:
  1. 4 Dimensões: Technical Score, Solution Score, Validation Score, Noise Score
  2. Evidência Forte vs Evidência Fraca (Confirmação do cliente, release, KB)
  3. Regras de Bloqueio Rígidas (Hard Block: PR Reprovado -> NUNCA GOLD; Inatividade -> IGNORE)
  4. 3 Níveis Operacionais para o Copilot:
     - GOLD: Conhecimento Confirmado ("Faça X para resolver Y")
     - SILVER: Conhecimento Provável ("Procedimento padrão, verifique Y")
     - REVIEW: Conhecimento Investigativo ("Diagnóstico útil, consulte N2")
     - IGNORE: Ruído / Descarte
  5. Amostragem Estratificada de Calibração (500 tickets: 100 GOLD, 100 SILVER, 100 REVIEW, 100 IGNORE, 100 RANDOM)

Uso:
    python refine_tickets_v2.py
    python refine_tickets_v2.py --limit 2000
    python refine_tickets_v2.py --export-calibration
"""

import argparse
import html
import json
import os
import re
import sys
import time
from pathlib import Path

# Forçar UTF-8 no Windows
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import psycopg
from psycopg.rows import dict_row
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

sys.path.insert(0, str(Path(__file__).parent))
from config import POSTGRES_DSN, OUTPUT_DIR

console = Console(force_terminal=True)


def get_db_connection() -> psycopg.Connection:
    return psycopg.connect(POSTGRES_DSN, row_factory=dict_row)


# ---------------------------------------------------------------------------
# Dicionários de Limpeza, Sinais e Evidências
# ---------------------------------------------------------------------------
TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"[ \t]+")

SIGNATURE_PATTERNS = [
    re.compile(r"(?i)atenciosamente.*", re.DOTALL),
    re.compile(r"(?i)att[,.]?.*", re.DOTALL),
    re.compile(r"(?i)cordialmente.*", re.DOTALL),
    re.compile(r"(?i)equipe alternativa sistemas.*", re.DOTALL),
    re.compile(r"(?i)alternativa sistemas.*", re.DOTALL),
    re.compile(r"(?i)enviado do meu i?phone.*", re.DOTALL),
    re.compile(r"(?i)enviado do meu android.*", re.DOTALL),
    re.compile(r"(?i)obter o outlook para.*", re.DOTALL),
    re.compile(r"(?i)este e-?mail foi enviado por um sistema.*", re.DOTALL),
]

SYSTEM_NOISE_STARTS = (
    "atribuição de chamado",
    "alteração de status",
    "mudança de responsável",
    "chamado pausado",
    "chamado reaberto",
    "notificação enviada",
    "gatilho executado",
    "atribuição de equipe",
    "atribuição automática",
    "reclassificação",
    "responsável alterado",
    "status alterado",
    "justificativa de pausa",
)

TECHNICAL_KEYWORDS = {
    "SINC": ["sinc", "sincronizador", "sincronizacao", "tdmcoresinc", "coresinc"],
    "Anymarket": ["anymarket", "any market"],
    "Mercado Livre": ["mercado livre", "mercadolivre", "meli"],
    "Shopee": ["shopee"],
    "Nuvemshop": ["nuvemshop", "nuvem shop"],
    "Sigep/Correios": ["sigep", "correios", "rastreio", "etiqueta correios"],
    "Tray": ["tray", "traycommerce"],
    "VTEX": ["vtex"],
    "Bling": ["bling"],
    "Tiny": ["tiny"],
    "NF-e": ["nfe", "nf-e", "nota fiscal", "danfe", "chave de acesso"],
    "NFC-e": ["nfce", "nfc-e", "cupom fiscal", "danfe nfc-e"],
    "Fiscal": ["csosn", "cfop", "icms", "pis", "cofins", "sped", "sintegra", "cst", "aliquota", "substituicao tributaria"],
    "Boleto/CNAB": ["boleto", "cnab", "remessa", "retorno cnab", "banco do brasil", "itau", "bradesco", "santander", "sicoob", "sicredi"],
    "Estoque": ["estoque", "saldo de estoque", "kardex", "reserva", "conferencia", "inventario", "grade"],
    "Vendas": ["venda", "pedido de venda", "orcamento", "faturamento", "cancelamento de venda"],
    "Financeiro": ["contas a receber", "contas a pagar", "fluxo de caixa", "baixa de titulo", "conciliacao"],
    "Firebird/DB": ["firebird", "deadlock", "access violation", "gfix", "tabela", "select", "update", "banco corrompido"],
}

ERROR_PATTERNS = [
    re.compile(r"\b10061\b"),
    re.compile(r"(?i)\baccess\s+violation\b"),
    re.compile(r"(?i)\bdeadlock\b"),
    re.compile(r"(?i)\brejei[cç][aã]o\s*[:\d]*\b"),
    re.compile(r"(?i)\berro\s+\d+\b"),
    re.compile(r"(?i)\bexception\b"),
    re.compile(r"(?i)\btimeout\b"),
    re.compile(r"(?i)\bsql\s+error\b"),
]

# ---------------------------------------------------------------------------
# Sinais de Evidência Forte vs Fraca vs Bloqueios Rígidos
# ---------------------------------------------------------------------------
STRONG_EVIDENCE_CLIENT_CONFIRMATION = [
    "obrigado deu certo", "obrigada deu certo", "funcionou perfeitamente",
    "deu certo obrigado", "deu certo obrigada", "resolvido obrigado",
    "agora funcionou", "agora deu certo", "consegui emitir",
    "sincronizou com sucesso", "consegui faturar", "ficou certo",
    "valeu deu certo", "perfeito obrigado", "perfeito obrigada",
    "emitidas com sucesso", "emitida com sucesso", "impresso com sucesso", "impressas com sucesso"
]

STRONG_EVIDENCE_RELEASE_VERSION = [
    "sistema atualizado", "versao atualizada", "versão atualizada",
    "patch aplicado", "atualizado no executavel", "atualizado no executável",
    "versao de release", "versão de release", "liberado na versao", "liberado na versão",
    "foi adicionado o campo", "foram adicionados os campos", "corrigido no fonte"
]

STRONG_EVIDENCE_SQL_PROCEDURE = [
    "update venda set", "update notafiscal set", "update produto set",
    "left join calcula_estoque", "commit;", "select pro_codigo"
]

WEAK_EVIDENCE_KEYWORDS = [
    "ajustado", "ajustada", "ajustamos",
    "corrigido", "corrigida", "corrigimos",
    "homologado", "homologada",
    "orientado", "orientada", "orientamos",
    "configurado", "configurada", "configuramos",
    "reprocessado", "reprocessamos", "realizamos", "troca da url",
    "com sucesso", "resolvido",
    "acredito que", "tente realizar", "favor verificar"
]


HARD_BLOCK_PR_REJECTED = [
    "pr reprovado", "pr rejeitado", "reprovado em homologacao", "reprovado em homologação",
    "reprovado pelo dev", "não foi aprovado pelo desenvolvimento", "desenvolvimento reprovou"
]

HARD_BLOCK_PERSISTED_ERROR = [
    "ainda nao deu certo", "ainda não deu certo", "o erro continua",
    "persistiu o erro", "persistiu o problema", "nao funcionou", "não funcionou",
    "voltou a dar erro", "reaberto"
]

HARD_BLOCK_INACTIVITY = [
    "inatividade", "falta de retorno", "sem retorno",
    "48 horas", "sem contato", "nao respondeu", "não respondeu",
    "fechado por falta", "fechando por inatividade"
]


def clean_html(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = TAG_RE.sub(" ", text)
    text = WHITESPACE_RE.sub(" ", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def strip_signature(text: str) -> str:
    if not text:
        return ""
    cleaned = text
    for pattern in SIGNATURE_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    return cleaned.strip()


def extract_modules_and_errors(text: str) -> tuple[list[str], list[str]]:
    text_lower = text.lower()
    modules = []
    for mod, kws in TECHNICAL_KEYWORDS.items():
        if any(kw in text_lower for kw in kws):
            modules.append(mod)

    error_codes = []
    for pat in ERROR_PATTERNS:
        matches = pat.findall(text)
        if matches:
            for m in matches:
                m_str = m if isinstance(m, str) else m[0]
                if m_str.strip() and m_str.strip() not in error_codes:
                    error_codes.append(m_str.strip()[:30])

    return modules, error_codes


def evaluate_evidence(full_text: str, solution_lower: str, actions: list[str]) -> tuple[str, int, list[str]]:
    """Avalia o nível de evidência da solução: STRONG, WEAK, NONE."""
    evidence_points = 0
    evidence_tags = []

    # 1. Confirmação do Cliente (Evidência Mais Forte)
    if any(kw in full_text for kw in STRONG_EVIDENCE_CLIENT_CONFIRMATION):
        evidence_points += 40
        evidence_tags.append("CLIENT_CONFIRMED")

    # 2. Release / Versão / Atualização Confirmada
    if any(kw in solution_lower for kw in STRONG_EVIDENCE_RELEASE_VERSION):
        evidence_points += 35
        evidence_tags.append("RELEASE_PATCH_CONFIRMED")

    # 3. Procedimento SQL Executável Validado
    if any(kw in solution_lower for kw in STRONG_EVIDENCE_SQL_PROCEDURE):
        evidence_points += 30
        evidence_tags.append("SQL_SCRIPT_VALIDATED")

    # 4. Evidência Fraca (Afirmação do Atendente sem Confirmação)
    if any(kw in solution_lower for kw in WEAK_EVIDENCE_KEYWORDS):
        evidence_points += 20
        evidence_tags.append("AGENT_STATED_FIX")

    if evidence_points >= 40:
        level = "STRONG"
    elif evidence_points >= 20:
        level = "WEAK"
    else:
        level = "NONE"

    return level, min(evidence_points, 100), evidence_tags


def compute_scores_v2(subject: str, problem: str, technical_narrative: str,
                      solution: str, base_status: str, total_actions: int,
                      substantive_actions: list[str], modules: list[str],
                      error_codes: list[str]) -> tuple[int, int, int, int, int, str, str, str, bool, dict]:
    """Calcula os 4 scores, avalia regras de bloqueio e determina o Tier Operacional."""
    full_text = f"{subject}\n{problem}\n{technical_narrative}\n{solution}".lower()
    solution_lower = solution.lower()
    agent_actions_count = len(substantive_actions) - 1 if len(substantive_actions) > 1 else 0

    # -----------------------------------------------------------------------
    # 1. Avaliação de Evidências & Validation Score
    # -----------------------------------------------------------------------
    evidence_level, validation_score, evidence_tags = evaluate_evidence(full_text, solution_lower, substantive_actions)

    # -----------------------------------------------------------------------
    # 2. Detecção de Regras de Bloqueio Rígidas (Hard Blocks)
    # -----------------------------------------------------------------------
    hard_block_reason = None
    is_blocked_gold = False

    if any(kw in full_text for kw in HARD_BLOCK_PR_REJECTED):
        hard_block_reason = "PR_REJECTED"
        is_blocked_gold = True
        validation_score = max(0, validation_score - 50)

    elif any(kw in full_text for kw in HARD_BLOCK_PERSISTED_ERROR):
        hard_block_reason = "ISSUE_PERSISTED_OR_REOPENED"
        is_blocked_gold = True
        validation_score = max(0, validation_score - 40)

    elif any(kw in full_text for kw in HARD_BLOCK_INACTIVITY):
        hard_block_reason = "INACTIVITY_NO_RETURN"
        is_blocked_gold = True

    elif base_status == "Canceled" or "teste" in subject.lower():
        hard_block_reason = "CANCELED_OR_TEST"
        is_blocked_gold = True

    elif agent_actions_count == 0 and not any(kw in solution_lower for kw in ["ações realizadas", "resolução:", "realizamos", "troca da url", "emitidas com sucesso", "resolvido"]):
        hard_block_reason = "ZERO_AGENT_REPLY"
        is_blocked_gold = True


    # -----------------------------------------------------------------------
    # 3. Technical Score (0 a 100)
    # -----------------------------------------------------------------------
    tech_score = 0
    tech_score += min(len(modules) * 15, 45)  # Módulos citados
    if error_codes:
        tech_score += min(len(error_codes) * 12, 25)  # Códigos de erro
    if len(technical_narrative) > 200 or len(solution) > 200:
        tech_score += 20
    elif len(technical_narrative) > 50 or len(solution) > 50:
        tech_score += 10
    if any(term in full_text for term in ["tabela", "select", "configuracao", "painel", "parametro", "campo", "registro", "versao", "dll"]):
        tech_score += 10
    tech_score = min(tech_score, 100)

    # -----------------------------------------------------------------------
    # 4. Solution Score (0 a 100)
    # -----------------------------------------------------------------------
    sol_score = 0
    if any(kw in solution_lower for kw in WEAK_EVIDENCE_KEYWORDS + STRONG_EVIDENCE_RELEASE_VERSION):
        sol_score += 40
    if agent_actions_count >= 1:
        sol_score += 20
    if agent_actions_count >= 2:
        sol_score += 15
    if len(solution) > 60:
        sol_score += 15
    if base_status in ("Closed", "Resolved"):
        sol_score += 10
    sol_score = min(sol_score, 100)

    # -----------------------------------------------------------------------
    # 5. Noise Score (0 a 100)
    # -----------------------------------------------------------------------
    noise_score = 0
    if hard_block_reason in ("INACTIVITY_NO_RETURN", "CANCELED_OR_TEST"):
        noise_score += 70
    if hard_block_reason == "ZERO_AGENT_REPLY":
        noise_score += 85
    if len(full_text.strip()) < 30:
        noise_score += 60
    noise_score = min(noise_score, 100)

    # -----------------------------------------------------------------------
    # 6. Score Final Ponderado & Tiering com Regras de Bloqueio Rígidas
    # -----------------------------------------------------------------------
    final_score = int(max(0, min(100, (tech_score * 0.30) + (sol_score * 0.35) + (validation_score * 0.35) - (noise_score * 0.60))))

    is_forwarded_to_dev = any(kw in solution_lower for kw in [
        "encaminhado para o setor de desenvolvimento",
        "encaminhado ao desenvolvimento",
        "time de desenvolvimento já está ciente",
        "aguardando retorno do dev",
        "abrir demanda para o dev"
    ])

    # Árvore de Decisão Rigorosa
    if hard_block_reason in ("ZERO_AGENT_REPLY", "INACTIVITY_NO_RETURN", "CANCELED_OR_TEST") or noise_score >= 50:
        tier = "IGNORE"
    elif hard_block_reason in ("PR_REJECTED", "ISSUE_PERSISTED_OR_REOPENED") or is_forwarded_to_dev:
        tier = "REVIEW"  # Preserva o diagnóstico para consulta investigativa, NUNCA recomenda diretamente
    elif final_score >= 55 and validation_score >= 20 and evidence_level in ("STRONG", "WEAK") and sol_score >= 40 and not is_blocked_gold:
        tier = "GOLD"    # 🟢 Conhecimento Confirmado com Evidência
    elif final_score >= 35 and sol_score >= 20 and not is_blocked_gold:
        tier = "SILVER"  # 🟡 Conhecimento Provável / Procedimento Padrão
    elif final_score >= 15:
        tier = "REVIEW"  # 🔴 Conhecimento Investigativo / Diagnóstico
    else:
        tier = "IGNORE"

    evidence_data = {
        "evidence_level": evidence_level,
        "evidence_tags": evidence_tags,
        "hard_block_reason": hard_block_reason,
        "is_blocked_gold": is_blocked_gold,
        "substantive_actions_count": len(substantive_actions),
    }

    return (tech_score, sol_score, validation_score, noise_score, final_score,
            tier, evidence_level, hard_block_reason, is_blocked_gold, evidence_data)


def process_ticket_v2(raw_id: str, tenant_id: str, ext_id: int, payload: dict) -> dict:
    subject = payload.get("subject") or "Sem assunto"
    category = payload.get("category")
    urgency = payload.get("urgency")
    status = payload.get("status")
    base_status = payload.get("baseStatus") or "Unknown"
    created_date = payload.get("createdDate")
    resolved_date = payload.get("resolvedIn")
    closed_date = payload.get("closedIn")
    service_first = payload.get("serviceFirstLevel")
    service_full = " > ".join(payload.get("serviceFull", [])) if payload.get("serviceFull") else service_first

    clients = payload.get("clients", [])
    client = clients[0] if clients else {}
    org = client.get("organization") or {}
    client_name = client.get("businessName")
    org_name = org.get("businessName") if isinstance(org, dict) else None
    client_email = client.get("email")

    # AI drafts are suggestions, never evidence of a procedure executed by support.
    actions = [a for a in payload.get("actions", [])
               if not (str(a.get('type')) == '1' and 'CORTEX-AI-NOTE:' in str(a.get('description') or ''))]
    total_actions = len(actions)

    substantive_actions = []
    for act in actions:
        if act.get("isDeleted"):
            continue
        desc_raw = act.get("description") or ""
        desc_clean = strip_signature(clean_html(desc_raw))
        if not desc_clean or len(desc_clean) < 10:
            continue
        desc_lower = desc_clean.lower()
        if any(desc_lower.startswith(s) for s in SYSTEM_NOISE_STARTS):
            continue
        substantive_actions.append(desc_clean)

    # Contagem precisa de ações de atendente vs cliente baseada no tipo oficial Movidesk (2 = Atendente)
    agent_actions_count = sum(1 for a in actions if a.get("type") == 2 and not a.get("isDeleted"))
    client_actions_count = sum(1 for a in actions if a.get("type") == 1 and not a.get("isDeleted"))

    if len(substantive_actions) == 1:
        text = substantive_actions[0]
        sol_match = re.search(r"(?i)(?:Ações realizadas para Resolução|Resolução|Como foi resolvido|Solução):\s*", text)
        if sol_match:
            problem_statement = text[:sol_match.start()].strip() or subject
            solution_statement = text[sol_match.start():].strip()
            if agent_actions_count == 0:
                agent_actions_count = 1
        else:
            problem_statement = text
            solution_statement = ""
        technical_narrative = ""
    else:
        problem_statement = substantive_actions[0] if substantive_actions else subject
        technical_narrative = "\n---\n".join(substantive_actions[1:-1]) if len(substantive_actions) > 2 else ""
        solution_statement = substantive_actions[-1] if len(substantive_actions) > 1 else ""
        if agent_actions_count == 0:
            agent_actions_count = max(0, len(substantive_actions) - 1)


    full_text = f"{subject}\n{problem_statement}\n{technical_narrative}\n{solution_statement}"
    modules, error_codes = extract_modules_and_errors(full_text)

    (tech_score, sol_score, val_score, noise_score, final_score,
     tier, evidence_level, hard_block_reason, is_blocked_gold, evidence_data) = compute_scores_v2(
        subject, problem_statement, technical_narrative, solution_statement,
        base_status, total_actions, substantive_actions, modules, error_codes
    )

    return {
        "tenant_id": tenant_id,
        "raw_ticket_id": raw_id,
        "external_id": ext_id,
        "subject": subject[:350],
        "service_full": service_full,
        "service_first_level": service_first,
        "category": category,
        "urgency": urgency,
        "status": status,
        "base_status": base_status,
        "client_business_name": client_name,
        "client_organization_name": org_name,
        "client_email": client_email,
        "created_date": created_date,
        "resolved_date": resolved_date,
        "closed_date": closed_date,
        "total_actions": total_actions,
        "agent_actions_count": agent_actions_count,
        "client_actions_count": client_actions_count,
        "problem_statement": problem_statement,
        "technical_narrative": technical_narrative,
        "solution_statement": solution_statement,
        "detected_modules": json.dumps(modules, ensure_ascii=False),
        "detected_error_codes": json.dumps(error_codes, ensure_ascii=False),
        "technical_score": tech_score,
        "solution_score": sol_score,
        "validation_score": val_score,
        "noise_score": noise_score,
        "final_score": final_score,
        "quality_tier": tier,
        "evidence_level": evidence_level,
        "hard_block_reason": hard_block_reason,
        "is_blocked_gold": is_blocked_gold,
        "evidence_data": json.dumps(evidence_data, ensure_ascii=False),
        "discard_reason": hard_block_reason,
    }


def refine_all_tickets_v2(limit: int = 0):
    console.print(Panel.fit(
        "[bold white on blue] DeskCortex — Refinaria v2: 4 Dimensões & Regras de Bloqueio Rígidas [/]",
        border_style="blue",
    ))

    t_start = time.perf_counter()

    with get_db_connection() as conn:
        count_query = "SELECT COUNT(*) as c FROM raw_tickets"
        if limit > 0:
            count_query = f"SELECT {limit} as c"
        total_tickets = conn.execute(count_query).fetchone()["c"]

        console.print(f"  🎯 Processando: [bold cyan]{total_tickets:,} tickets[/]")

        batch_size = 1000
        offset = 0
        processed_count = 0
        tier_counts = {"GOLD": 0, "SILVER": 0, "REVIEW": 0, "IGNORE": 0}
        blocked_count = 0

        upsert_sql = """
            INSERT INTO refined_tickets (
                tenant_id, raw_ticket_id, external_id, subject, service_full,
                service_first_level, category, urgency, status, base_status,
                client_business_name, client_organization_name, client_email,
                created_date, resolved_date, closed_date, total_actions,
                agent_actions_count, client_actions_count, problem_statement,
                technical_narrative, solution_statement, detected_modules,
                detected_error_codes, technical_score, solution_score,
                validation_score, noise_score, final_score, quality_tier,
                evidence_level, hard_block_reason, is_blocked_gold,
                evidence_data, discard_reason
            ) VALUES (
                %(tenant_id)s, %(raw_ticket_id)s, %(external_id)s, %(subject)s, %(service_full)s,
                %(service_first_level)s, %(category)s, %(urgency)s, %(status)s, %(base_status)s,
                %(client_business_name)s, %(client_organization_name)s, %(client_email)s,
                %(created_date)s, %(resolved_date)s, %(closed_date)s, %(total_actions)s,
                %(agent_actions_count)s, %(client_actions_count)s, %(problem_statement)s,
                %(technical_narrative)s, %(solution_statement)s, %(detected_modules)s,
                %(detected_error_codes)s, %(technical_score)s, %(solution_score)s,
                %(validation_score)s, %(noise_score)s, %(final_score)s, %(quality_tier)s,
                %(evidence_level)s, %(hard_block_reason)s, %(is_blocked_gold)s,
                %(evidence_data)s, %(discard_reason)s
            )
            ON CONFLICT (tenant_id, external_id)
            DO UPDATE SET
                subject = EXCLUDED.subject,
                service_full = EXCLUDED.service_full,
                service_first_level = EXCLUDED.service_first_level,
                category = EXCLUDED.category,
                urgency = EXCLUDED.urgency,
                status = EXCLUDED.status,
                base_status = EXCLUDED.base_status,
                client_business_name = EXCLUDED.client_business_name,
                client_organization_name = EXCLUDED.client_organization_name,
                client_email = EXCLUDED.client_email,
                created_date = EXCLUDED.created_date,
                resolved_date = EXCLUDED.resolved_date,
                closed_date = EXCLUDED.closed_date,
                total_actions = EXCLUDED.total_actions,
                agent_actions_count = EXCLUDED.agent_actions_count,
                client_actions_count = EXCLUDED.client_actions_count,
                problem_statement = EXCLUDED.problem_statement,
                technical_narrative = EXCLUDED.technical_narrative,
                solution_statement = EXCLUDED.solution_statement,
                detected_modules = EXCLUDED.detected_modules,
                detected_error_codes = EXCLUDED.detected_error_codes,
                technical_score = EXCLUDED.technical_score,
                solution_score = EXCLUDED.solution_score,
                validation_score = EXCLUDED.validation_score,
                noise_score = EXCLUDED.noise_score,
                final_score = EXCLUDED.final_score,
                quality_tier = EXCLUDED.quality_tier,
                evidence_level = EXCLUDED.evidence_level,
                hard_block_reason = EXCLUDED.hard_block_reason,
                is_blocked_gold = EXCLUDED.is_blocked_gold,
                evidence_data = EXCLUDED.evidence_data,
                discard_reason = EXCLUDED.discard_reason,
                refined_at = NOW();
        """

        while True:
            if limit > 0 and processed_count >= limit:
                break

            query = f"""
                SELECT id, tenant_id, external_id, payload
                FROM raw_tickets
                ORDER BY external_id ASC
                LIMIT {batch_size} OFFSET {offset};
            """
            batch_rows = conn.execute(query).fetchall()
            if not batch_rows:
                break

            records = []
            for row in batch_rows:
                rec = process_ticket_v2(str(row["id"]), str(row["tenant_id"]), row["external_id"], row["payload"])
                records.append(rec)
                tier_counts[rec["quality_tier"]] += 1
                if rec["is_blocked_gold"]:
                    blocked_count += 1

            with conn.cursor() as cur:
                cur.executemany(upsert_sql, records)
            conn.commit()

            processed_count += len(records)
            offset += batch_size

            elapsed = time.perf_counter() - t_start
            speed = processed_count / elapsed if elapsed > 0 else 0
            pct = (processed_count * 100.0) / total_tickets if total_tickets > 0 else 100

            console.print(
                f"  📦 Processados: [bold green]{processed_count:,}/{total_tickets:,}[/] "
                f"({pct:.1f}%) | "
                f"🌟 GOLD: {tier_counts['GOLD']:,} | "
                f"🥈 SILVER: {tier_counts['SILVER']:,} | "
                f"🔍 REVIEW: {tier_counts['REVIEW']:,} | "
                f"🚫 IGNORE: {tier_counts['IGNORE']:,} | "
                f"⚡ {speed:.0f} t/s"
            )

    t_total = time.perf_counter() - t_start
    console.print(f"\n✅ Refinaria v2 concluída em [bold yellow]{t_total:.2f} segundos[/] ({processed_count/t_total:.0f} tickets/s)!\n")

    # Resumo Final em Tabela
    console.rule("[bold green]Resumo dos Níveis de Conhecimento do DeskCortex")
    summary_tbl = Table(show_lines=True)
    summary_tbl.add_column("Tier de Conhecimento", style="cyan", width=25)
    summary_tbl.add_column("Critério de Evidência", style="white", width=35)
    summary_tbl.add_column("Total de Chamados", style="bold green", justify="right", width=20)
    summary_tbl.add_column("% da Base", style="yellow", justify="right", width=15)
    summary_tbl.add_column("Comportamento no Copilot", style="magenta", width=35)

    tier_destinos = {
        "GOLD": ("Evidência Forte (Cliente/Release/SQL)", "🟢 Recomendação Direta com Alta Certeza"),
        "SILVER": ("Evidência Padrão / Procedimento Tela", "🟡 Orientação Operacional com Ressalva"),
        "REVIEW": ("Diagnóstico / PR Pendente / Incompleto", "🔴 Apoio Investigativo (Não prescreve)"),
        "IGNORE": ("Bloqueio: Inatividade / 0 respostas", "🚫 Descarte Total (Não entra no RAG)"),
    }

    for tier, (criterio, dest) in tier_destinos.items():
        cnt = tier_counts[tier]
        pct = round((cnt * 100.0) / processed_count, 2) if processed_count > 0 else 0
        summary_tbl.add_row(f"[{'bold green' if tier=='GOLD' else ('cyan' if tier=='SILVER' else ('yellow' if tier=='REVIEW' else 'red'))}]{tier}[/]",
                            criterio, f"{cnt:,}", f"{pct}%", dest)

    console.print(summary_tbl)


def export_calibration_sample_500():
    """Gera a amostra de calibração estratificada (500 tickets) focando em fronteiras + aleatórios."""
    console.rule("[bold cyan]Exportando Amostra Estratificada de Calibração (500 Tickets)")

    dataset = {
        "GOLD_frontier": [],
        "SILVER_frontier": [],
        "REVIEW_frontier": [],
        "IGNORE_frontier": [],
        "RANDOM_control": []
    }

    with get_db_connection() as conn:
        # 1. GOLD na fronteira inferior (score 60 a 70)
        dataset["GOLD_frontier"] = conn.execute("""
            SELECT external_id, subject, service_full, technical_score, solution_score,
                   validation_score, noise_score, final_score, quality_tier, evidence_level,
                   hard_block_reason, problem_statement, solution_statement
            FROM refined_tickets
            WHERE quality_tier = 'GOLD'
            ORDER BY final_score ASC, validation_score ASC
            LIMIT 100;
        """).fetchall()

        # 2. SILVER na fronteira (score 40 a 50)
        dataset["SILVER_frontier"] = conn.execute("""
            SELECT external_id, subject, service_full, technical_score, solution_score,
                   validation_score, noise_score, final_score, quality_tier, evidence_level,
                   hard_block_reason, problem_statement, solution_statement
            FROM refined_tickets
            WHERE quality_tier = 'SILVER'
            ORDER BY final_score ASC
            LIMIT 100;
        """).fetchall()

        # 3. REVIEW na fronteira (score 20 a 30 e bloqueados)
        dataset["REVIEW_frontier"] = conn.execute("""
            SELECT external_id, subject, service_full, technical_score, solution_score,
                   validation_score, noise_score, final_score, quality_tier, evidence_level,
                   hard_block_reason, problem_statement, solution_statement
            FROM refined_tickets
            WHERE quality_tier = 'REVIEW'
            ORDER BY final_score DESC
            LIMIT 100;
        """).fetchall()

        # 4. IGNORE na fronteira superior (score 10 a 20)
        dataset["IGNORE_frontier"] = conn.execute("""
            SELECT external_id, subject, service_full, technical_score, solution_score,
                   validation_score, noise_score, final_score, quality_tier, evidence_level,
                   hard_block_reason, problem_statement, solution_statement
            FROM refined_tickets
            WHERE quality_tier = 'IGNORE'
            ORDER BY final_score DESC
            LIMIT 100;
        """).fetchall()

        # 5. RANDOM CONTROL (100 aleatórios da base toda)
        dataset["RANDOM_control"] = conn.execute("""
            SELECT external_id, subject, service_full, technical_score, solution_score,
                   validation_score, noise_score, final_score, quality_tier, evidence_level,
                   hard_block_reason, problem_statement, solution_statement
            FROM refined_tickets
            ORDER BY RANDOM()
            LIMIT 100;
        """).fetchall()

    output_path = OUTPUT_DIR / "calibration_sample_500.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2, default=str)

    console.print(f"💾 Amostra de calibração (500 tickets) salva em: [bold green]{output_path}[/]\n")


def main():
    parser = argparse.ArgumentParser(description="DeskCortex — Refinaria v2 com 4 Dimensões e Regras de Bloqueio")
    parser.add_argument("--limit", type=int, default=0, help="Limitar quantidade de tickets")
    parser.add_argument("--export-calibration", action="store_true", help="Exportar amostra estratificada de 500 tickets")
    args = parser.parse_args()

    refine_all_tickets_v2(args.limit)
    export_calibration_sample_500()


if __name__ == "__main__":
    main()
