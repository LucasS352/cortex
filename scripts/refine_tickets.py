"""
DeskCortex — Pipeline de Refinaria, Limpeza de Ruído e Scoring dos 105k Chamados
==================================================================================
Transforma os 104.962 tickets brutos de `raw_tickets` em conhecimento estruturado:
  1. Limpeza de HTML, assinaturas de e-mail e mensagens sociais vazias
  2. Separação de ações por autor (Agente vs Cliente vs Sistema)
  3. Extração semântica (Problema, Narrativa Técnica, Solução Candidata)
  4. Detecção de Módulos (SINC, NF-e, Anymarket, etc.) e Códigos de Erro
  5. Cálculo de Scoring Multidimensional (Technical, Solution, Noise)
  6. Classificação em Tiers: GOLD (90-100), SILVER (70-89), REVIEW (40-69), IGNORE

Uso:
    python refine_tickets.py
    python refine_tickets.py --limit 1000
    python refine_tickets.py --export-samples
"""

import argparse
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
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
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

sys.path.insert(0, str(Path(__file__).parent))
from config import POSTGRES_DSN, OUTPUT_DIR

console = Console(force_terminal=True)


def get_db_connection() -> psycopg.Connection:
    """Cria conexão com o banco de dados PostgreSQL."""
    return psycopg.connect(POSTGRES_DSN, row_factory=dict_row)


# ---------------------------------------------------------------------------
# Regex & Dicionários de Limpeza de Texto e Ruído
# ---------------------------------------------------------------------------
TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"[ \t]+")
NEWLINE_RE = re.compile(r"\n{3,}")

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

SOLUTION_KEYWORDS = [
    "resolvido", "resolvida", "resolvidos",
    "ajustado", "ajustada", "ajustamos",
    "corrigido", "corrigida", "corrigimos",
    "homologado", "homologada",
    "atualizado", "atualizada", "atualizamos a versao",
    "liberado", "liberada", "liberamos",
    "orientado", "orientada", "orientamos o cliente",
    "configurado", "configurada", "configuramos",
    "reprocessado", "reprocessamos",
    "sincronizado com sucesso", "concluido", "finalizado com sucesso"
]

NOISE_KEYWORDS = [
    "inatividade", "falta de retorno", "sem retorno",
    "48 horas", "sem contato", "nao respondeu", "não respondeu",
    "cancelado pelo cliente", "aberto por engano", "duplicado",
    "chamado de teste", "desconsiderar", "reprovado em homologacao", "reprovado em homologação",
    "pr reprovado", "pr rejeitado"
]


def clean_html(text: str) -> str:
    """Remove HTML, entidades e normaliza espaçamento."""
    if not text:
        return ""
    text = html.unescape(text)
    text = TAG_RE.sub(" ", text)
    text = WHITESPACE_RE.sub(" ", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def strip_signature(text: str) -> str:
    """Remove assinaturas e rodapés de e-mail."""
    if not text:
        return ""
    cleaned = text
    for pattern in SIGNATURE_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    return cleaned.strip()


def extract_modules_and_errors(text: str) -> tuple[list[str], list[str]]:
    """Detecta módulos do ERP Master e códigos de erro presentes no texto."""
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


def compute_scores(subject: str, problem: str, technical_narrative: str,
                   solution: str, base_status: str, total_actions: int,
                   agent_actions_count: int, modules: list[str],
                   error_codes: list[str]) -> tuple[int, int, int, int, str, str]:
    """Calcula technical_score, solution_score, noise_score, final_score e quality_tier."""
    full_text = f"{subject}\n{problem}\n{technical_narrative}\n{solution}".lower()
    solution_lower = solution.lower()

    # 1. Technical Score (0 a 100)
    tech_score = 0
    tech_score += min(len(modules) * 15, 45)  # Até 45 pts por módulos identificados
    if error_codes:
        tech_score += min(len(error_codes) * 10, 25)  # Até 25 pts por códigos de erro
    if len(technical_narrative) > 200:
        tech_score += 20
    elif len(technical_narrative) > 50:
        tech_score += 10
    if any(term in full_text for term in ["tabela", "select", "configuracao", "painel", "parametro", "campo", "registro", "versao"]):
        tech_score += 10
    tech_score = min(tech_score, 100)

    # 2. Solution Score (0 a 100)
    sol_score = 0
    if any(kw in solution_lower for kw in SOLUTION_KEYWORDS):
        sol_score += 40
    if agent_actions_count >= 1:
        sol_score += 20
    if agent_actions_count >= 2:
        sol_score += 15
    if len(solution) > 80:
        sol_score += 15
    if base_status in ("Closed", "Resolved"):
        sol_score += 10
    sol_score = min(sol_score, 100)

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


def compute_scores(subject: str, problem: str, technical_narrative: str,
                   solution: str, base_status: str, total_actions: int,
                   agent_actions_count: int, modules: list[str],
                   error_codes: list[str]) -> tuple[int, int, int, int, str, str]:
    """Calcula technical_score, solution_score, noise_score, final_score e quality_tier."""
    full_text = f"{subject}\n{problem}\n{technical_narrative}\n{solution}".lower()
    solution_lower = solution.lower()

    # 1. Technical Score (0 a 100)
    tech_score = 0
    tech_score += min(len(modules) * 15, 45)  # Até 45 pts por módulos identificados
    if error_codes:
        tech_score += min(len(error_codes) * 10, 25)  # Até 25 pts por códigos de erro
    if len(technical_narrative) > 200 or len(solution) > 200:
        tech_score += 20
    elif len(technical_narrative) > 50 or len(solution) > 50:
        tech_score += 10
    if any(term in full_text for term in ["tabela", "select", "configuracao", "configuração", "painel", "parametro", "parâmetro", "campo", "registro", "versao", "versão", "dll", "sinc"]):
        tech_score += 10
    tech_score = min(tech_score, 100)

    # 2. Solution Score (0 a 100)
    sol_score = 0
    if any(kw in solution_lower for kw in SOLUTION_KEYWORDS):
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

    # 3. Noise Score (0 a 100)
    noise_score = 0
    discard_reason = None

    if any(kw in full_text for kw in NOISE_KEYWORDS):
        noise_score += 65
        discard_reason = "inatividade_ou_cancelado"

    if agent_actions_count == 0:
        noise_score += 80
        discard_reason = "sem_resposta_tecnica"
    elif total_actions <= 1:
        noise_score += 60
        discard_reason = "apenas_abertura"

    if base_status == "Canceled" or "teste" in subject.lower():
        noise_score += 70
        discard_reason = "cancelado_ou_teste"

    if len(full_text.strip()) < 30:
        noise_score += 50
        discard_reason = "conversa_vazia"

    noise_score = min(noise_score, 100)

    # 4. Final Score & Tiering
    final_score = int(max(0, min(100, (tech_score * 0.40) + (sol_score * 0.50) - (noise_score * 0.70))))

    if noise_score >= 50 or agent_actions_count == 0 or total_actions <= 1:
        tier = "IGNORE"
    elif final_score >= 55 and sol_score >= 35:
        tier = "GOLD"
    elif final_score >= 35 and sol_score >= 20:
        tier = "SILVER"
    elif final_score >= 20:
        tier = "REVIEW"
    else:
        tier = "IGNORE"

    return tech_score, sol_score, noise_score, final_score, tier, discard_reason


def process_ticket(raw_id: str, tenant_id: str, ext_id: int, payload: dict) -> dict:
    """Processa e refina um ticket individual."""
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

    # Cliente
    clients = payload.get("clients", [])
    client = clients[0] if clients else {}
    org = client.get("organization") or {}
    client_name = client.get("businessName")
    org_name = org.get("businessName") if isinstance(org, dict) else None
    client_email = client.get("email")

    # Ações
    actions = payload.get("actions", [])
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

    problem_statement = substantive_actions[0] if substantive_actions else subject
    technical_narrative = "\n---\n".join(substantive_actions[1:-1]) if len(substantive_actions) > 2 else ""
    solution_statement = substantive_actions[-1] if len(substantive_actions) > 1 else ""
    agent_actions_count = len(substantive_actions) - 1 if len(substantive_actions) > 1 else 0
    client_actions_count = 1 if substantive_actions else 0

    # Extrair módulos e erros
    full_text = f"{subject}\n{problem_statement}\n{technical_narrative}\n{solution_statement}"
    modules, error_codes = extract_modules_and_errors(full_text)

    # Calcular pontuação
    tech_score, sol_score, noise_score, final_score, tier, discard_reason = compute_scores(
        subject, problem_statement, technical_narrative, solution_statement,
        base_status, total_actions, agent_actions_count, modules, error_codes
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
        "noise_score": noise_score,
        "final_score": final_score,
        "quality_tier": tier,
        "discard_reason": discard_reason,
    }


def refine_all_tickets(limit: int = 0):
    """Executa a refinaria completa em lotes com alta performance."""
    console.print(Panel.fit(
        "[bold white on blue] DeskCortex — Execução do Pipeline de Refinaria & Scoring [/]",
        border_style="blue",
    ))

    t_start = time.perf_counter()

    with get_db_connection() as conn:
        count_query = "SELECT COUNT(*) as c FROM raw_tickets"
        if limit > 0:
            count_query = f"SELECT {limit} as c"
        total_tickets = conn.execute(count_query).fetchone()["c"]

        console.print(f"  🎯 Total de tickets para processar: [bold cyan]{total_tickets:,}[/]")
        console.print("  🚀 Iniciando processamento em lotes de 1.000 tickets...")

        batch_size = 1000
        offset = 0
        processed_count = 0
        tier_counts = {"GOLD": 0, "SILVER": 0, "REVIEW": 0, "IGNORE": 0}

        upsert_sql = """
            INSERT INTO refined_tickets (
                tenant_id, raw_ticket_id, external_id, subject, service_full,
                service_first_level, category, urgency, status, base_status,
                client_business_name, client_organization_name, client_email,
                created_date, resolved_date, closed_date, total_actions,
                agent_actions_count, client_actions_count, problem_statement,
                technical_narrative, solution_statement, detected_modules,
                detected_error_codes, technical_score, solution_score,
                noise_score, final_score, quality_tier, discard_reason
            ) VALUES (
                %(tenant_id)s, %(raw_ticket_id)s, %(external_id)s, %(subject)s, %(service_full)s,
                %(service_first_level)s, %(category)s, %(urgency)s, %(status)s, %(base_status)s,
                %(client_business_name)s, %(client_organization_name)s, %(client_email)s,
                %(created_date)s, %(resolved_date)s, %(closed_date)s, %(total_actions)s,
                %(agent_actions_count)s, %(client_actions_count)s, %(problem_statement)s,
                %(technical_narrative)s, %(solution_statement)s, %(detected_modules)s,
                %(detected_error_codes)s, %(technical_score)s, %(solution_score)s,
                %(noise_score)s, %(final_score)s, %(quality_tier)s, %(discard_reason)s
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
                noise_score = EXCLUDED.noise_score,
                final_score = EXCLUDED.final_score,
                quality_tier = EXCLUDED.quality_tier,
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
                rec = process_ticket(str(row["id"]), str(row["tenant_id"]), row["external_id"], row["payload"])
                records.append(rec)
                tier_counts[rec["quality_tier"]] += 1

            # Inserção em lote no PostgreSQL
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
    console.print(f"\n✅ Refinaria concluída em [bold yellow]{t_total:.2f} segundos[/] ({processed_count/t_total:.0f} tickets/segundo)!\n")

    # Resumo Final em Tabela
    console.rule("[bold green]Resumo Final da Refinaria & Tiers de Qualidade")

    summary_tbl = Table(show_lines=True)
    summary_tbl.add_column("Tier de Qualidade", style="cyan", width=25)
    summary_tbl.add_column("Faixa de Score", style="white", justify="center", width=20)
    summary_tbl.add_column("Total de Chamados", style="bold green", justify="right", width=20)
    summary_tbl.add_column("% da Base", style="yellow", justify="right", width=15)
    summary_tbl.add_column("Destino no DeskCortex", style="magenta", width=35)

    tier_destinos = {
        "GOLD": ("70 - 100", "🌟 Golden Dataset (RAG / Embeddings Direto)"),
        "SILVER": ("45 - 69", "🥈 Conhecimento Secundário (Busca Híbrida)"),
        "REVIEW": ("25 - 44", "🔍 Banco de Diagnósticos para Refinamento"),
        "IGNORE": ("0 - 24", "🚫 Descarte (Inatividade, Cancelados, Ruído)"),
    }

    for tier, (faixa, dest) in tier_destinos.items():
        cnt = tier_counts[tier]
        pct = round((cnt * 100.0) / processed_count, 2) if processed_count > 0 else 0
        summary_tbl.add_row(f"[{'bold green' if tier=='GOLD' else ('cyan' if tier=='SILVER' else 'red')}]{tier}[/]",
                            faixa, f"{cnt:,}", f"{pct}%", dest)

    console.print(summary_tbl)


def export_validation_samples(sample_size: int = 100):
    """Exporta amostras representativas de cada Tier para validação humana."""
    console.rule("[bold cyan]Exportando Amostras para Validação")

    samples = {"GOLD": [], "SILVER": [], "REVIEW": [], "IGNORE": []}

    with get_db_connection() as conn:
        for tier in samples.keys():
            query = f"""
                SELECT external_id, subject, service_full, technical_score,
                       solution_score, noise_score, final_score, quality_tier,
                       discard_reason, detected_modules, problem_statement,
                       solution_statement
                FROM refined_tickets
                WHERE quality_tier = '{tier}'
                ORDER BY final_score {'DESC' if tier in ('GOLD', 'SILVER') else 'ASC'}
                LIMIT {sample_size};
            """
            rows = conn.execute(query).fetchall()
            samples[tier] = rows

    output_path = OUTPUT_DIR / "sample_validation.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(samples, f, ensure_ascii=False, indent=2, default=str)

    console.print(f"💾 Amostras de validação salvas em: [bold green]{output_path}[/]\n")


def main():
    parser = argparse.ArgumentParser(description="DeskCortex — Pipeline de Refinaria & Scoring")
    parser.add_argument("--limit", type=int, default=0, help="Limitar quantidade de tickets para teste")
    parser.add_argument("--export-samples", action="store_true", help="Exportar amostras de validação após refinaria")
    args = parser.parse_args()

    refine_all_tickets(args.limit)
    export_validation_samples(100)


if __name__ == "__main__":
    main()
