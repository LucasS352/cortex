"""
DeskCortex — Ingestão de Knowledge Nodes v2 (Paralela + Batch)
================================================================
Versão otimizada com:
  - ThreadPoolExecutor para chamadas paralelas à API Gemini
  - Batch commit de 50 nodes por vez (reduz overhead de transação)
  - Reprocessamento de nodes sem embedding (--fix-missing)
  - Relatório de velocidade em tempo real

Melhoria esperada: de ~2 docs/min para ~20-40 docs/min.

Uso:
    python ingest_knowledge_nodes_v2.py --poc          # 20 KB + 20 GOLD + 20 SILVER
    python ingest_knowledge_nodes_v2.py --full         # pipeline completo
    python ingest_knowledge_nodes_v2.py --only kb      # apenas KB
    python ingest_knowledge_nodes_v2.py --only gold    # apenas GOLD
    python ingest_knowledge_nodes_v2.py --only silver  # apenas SILVER
    python ingest_knowledge_nodes_v2.py --fix-missing  # reprocessa sem embedding
    python ingest_knowledge_nodes_v2.py --workers 5   # ajustar paralelismo
"""

import argparse
import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import urllib.request
import urllib.error
import psycopg
from psycopg.rows import dict_row
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.live import Live
from rich.progress import (
    Progress, SpinnerColumn, BarColumn, TextColumn,
    TimeElapsedColumn, TimeRemainingColumn, MofNCompleteColumn
)

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    POSTGRES_DSN, GEMINI_API_KEY,
    GEMINI_EMBEDDING_MODEL, GEMINI_EMBEDDING_DIMENSIONS
)

console = Console(force_terminal=True)

TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"[ \t]+")

# Controle de rate limiting thread-safe
_rate_lock = Lock()
_last_call_time = 0.0
_call_count = 0
_error_count = 0


# ─────────────────────────────────────────────────────────────────────────────
# Limpeza de texto
# ─────────────────────────────────────────────────────────────────────────────

def clean_text(text: str, max_chars: int = 3000) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = TAG_RE.sub(" ", text)
    text = WHITESPACE_RE.sub(" ", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)[:max_chars]


# ─────────────────────────────────────────────────────────────────────────────
# Composição de texto de embedding
# ─────────────────────────────────────────────────────────────────────────────

def compose_ticket_text(row: dict) -> str:
    parts = []
    if row.get("subject"):
        parts.append(f"ASSUNTO: {row['subject']}")
    modules = row.get("detected_modules")
    if isinstance(modules, str):
        try: modules = json.loads(modules)
        except: modules = []
    if modules:
        parts.append(f"MÓDULOS: {', '.join(modules)}")
    errors = row.get("detected_error_codes")
    if isinstance(errors, str):
        try: errors = json.loads(errors)
        except: errors = []
    if errors:
        parts.append(f"ERROS/CÓDIGOS: {', '.join(errors)}")
    if row.get("service_full"):
        parts.append(f"SERVIÇO: {row['service_full']}")
    if row.get("problem_statement"):
        parts.append(f"PROBLEMA: {clean_text(row['problem_statement'], 800)}")
    if row.get("technical_narrative"):
        n = clean_text(row["technical_narrative"], 600)
        if n: parts.append(f"DIAGNÓSTICO: {n}")
    if row.get("solution_statement"):
        parts.append(f"SOLUÇÃO: {clean_text(row['solution_statement'], 800)}")
    evidence_data = row.get("evidence_data")
    if isinstance(evidence_data, str):
        try: evidence_data = json.loads(evidence_data)
        except: evidence_data = {}
    if isinstance(evidence_data, dict):
        tags = evidence_data.get("evidence_tags", [])
        if tags: parts.append(f"EVIDÊNCIA: {', '.join(tags)}")
    return "\n".join(parts)


def compose_kb_text(row: dict) -> str:
    parts = []
    if row.get("title"):
        parts.append(f"ARTIGO OFICIAL: {row['title']}")
    cats = row.get("categories")
    if isinstance(cats, list) and cats:
        names = []
        for c in cats:
            if isinstance(c, dict): names.append(c.get("name") or str(c))
            else: names.append(str(c))
        if names: parts.append(f"CATEGORIA: {', '.join(names)}")
    if row.get("summary"):
        parts.append(f"RESUMO: {clean_text(row['summary'], 400)}")
    if row.get("content_text"):
        parts.append(f"CONTEÚDO: {clean_text(row['content_text'], 2500)}")
    elif row.get("content_html"):
        parts.append(f"CONTEÚDO: {clean_text(row['content_html'], 2500)}")
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Gemini Embedding — chamada individual (thread-safe)
# ─────────────────────────────────────────────────────────────────────────────

def call_gemini_embed(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float] | None:
    global _last_call_time, _call_count, _error_count

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"{GEMINI_EMBEDDING_MODEL}:embedContent?key={GEMINI_API_KEY}"
    )
    payload = {
        "model": GEMINI_EMBEDDING_MODEL,
        "content": {"parts": [{"text": text[:8000]}]},
        "taskType": task_type
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})

    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read())
                with _rate_lock:
                    _call_count += 1
                    _last_call_time = time.time()
                return data["embedding"]["values"]
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 2 ** (attempt + 2)
                time.sleep(wait)
            elif e.code in (500, 503):
                time.sleep(2 ** attempt)
            else:
                with _rate_lock:
                    _error_count += 1
                return None
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt)
            else:
                with _rate_lock:
                    _error_count += 1
                return None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Preparação de registros (CPU — sem I/O)
# ─────────────────────────────────────────────────────────────────────────────

def prepare_kb_record(row: dict) -> dict:
    content = compose_kb_text(row)
    return {
        "_content": content,
        "source_type": "OFFICIAL_KB",
        "source_id": f"kb-{row['id']}",
        "source_external_id": None,
        "title": clean_text(row.get("title") or "Artigo KB", 200),
        "content_for_embedding": content,
        "problem": None,
        "diagnosis": None,
        "solution": clean_text(row.get("content_text") or row.get("content_html") or "", 2000),
        "result": None,
        "product": "Master ERP",
        "modules": None,
        "detected_error_codes": None,
        "version_reference": None,
        "tier": "KB",
        "confidence": "HIGH",
        "technical_score": 100,
        "solution_score": 100,
        "validation_score": 100,
        "final_score": 100,
        "validation_evidence": ["OFFICIAL_DOCUMENTATION"],
        "hard_block_reason": None,
        "source_authority": 100,
        "metadata": json.dumps({"categories": row.get("categories"), "external_id": row.get("external_id")}),
    }


def prepare_ticket_record(row: dict, tier: str) -> dict:
    content = compose_ticket_text(row)
    source_type = "VALIDATED_TICKET" if tier == "GOLD" else "PROBABLE_TICKET"
    confidence = "HIGH" if tier == "GOLD" else "MEDIUM"
    authority = 85 if tier == "GOLD" else 60

    modules = row.get("detected_modules")
    if isinstance(modules, str):
        try: modules = json.loads(modules)
        except: modules = None

    errors = row.get("detected_error_codes")
    if isinstance(errors, str):
        try: errors = json.loads(errors)
        except: errors = None

    evidence_data = row.get("evidence_data")
    if isinstance(evidence_data, str):
        try: evidence_data = json.loads(evidence_data)
        except: evidence_data = {}
    evidence_tags = evidence_data.get("evidence_tags", []) if isinstance(evidence_data, dict) else []

    return {
        "_content": content,
        "source_type": source_type,
        "source_id": f"ticket-{row['external_id']}",
        "source_external_id": row["external_id"],
        "title": clean_text(row.get("subject") or "Sem título", 200),
        "content_for_embedding": content,
        "problem": clean_text(row.get("problem_statement") or "", 1000),
        "diagnosis": clean_text(row.get("technical_narrative") or "", 800),
        "solution": clean_text(row.get("solution_statement") or "", 1000),
        "result": None,
        "product": "Master ERP",
        "modules": modules if isinstance(modules, list) else None,
        "detected_error_codes": errors if isinstance(errors, list) else None,
        "version_reference": None,
        "tier": tier,
        "confidence": confidence,
        "technical_score": row.get("technical_score", 0),
        "solution_score": row.get("solution_score", 0),
        "validation_score": row.get("validation_score", 0),
        "final_score": row.get("final_score", 0),
        "validation_evidence": evidence_tags if evidence_tags else None,
        "hard_block_reason": row.get("hard_block_reason"),
        "source_authority": authority,
        "metadata": json.dumps({
            "service_full": row.get("service_full"),
            "service_first_level": row.get("service_first_level"),
        }),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline paralelo principal
# ─────────────────────────────────────────────────────────────────────────────

INSERT_SQL = """
    INSERT INTO ticket_knowledge_nodes (
        source_type, source_id, source_external_id,
        title, content_for_embedding,
        problem, diagnosis, solution, result,
        product, modules, detected_error_codes, version_reference,
        tier, confidence, technical_score, solution_score,
        validation_score, final_score,
        validation_evidence, hard_block_reason, source_authority,
        metadata, embedding, embedding_generated, embedding_model
    ) VALUES (
        %(source_type)s, %(source_id)s, %(source_external_id)s,
        %(title)s, %(content_for_embedding)s,
        %(problem)s, %(diagnosis)s, %(solution)s, %(result)s,
        %(product)s, %(modules)s, %(detected_error_codes)s, %(version_reference)s,
        %(tier)s, %(confidence)s, %(technical_score)s, %(solution_score)s,
        %(validation_score)s, %(final_score)s,
        %(validation_evidence)s, %(hard_block_reason)s, %(source_authority)s,
        %(metadata)s, %(embedding)s, %(embedding_generated)s, %(embedding_model)s
    )
    ON CONFLICT DO NOTHING
    RETURNING id;
"""


def embed_record(record: dict) -> dict:
    """Gera o embedding para um record (executa em thread pool)."""
    content = record.get("_content", "")
    if not content.strip():
        record["embedding"] = None
        record["embedding_generated"] = False
        record["embedding_model"] = GEMINI_EMBEDDING_MODEL
        return record

    embedding = call_gemini_embed(content, "RETRIEVAL_DOCUMENT")
    record["embedding"] = embedding
    record["embedding_generated"] = embedding is not None
    record["embedding_model"] = GEMINI_EMBEDDING_MODEL
    return record


def ingest_batch(records: list[dict], workers: int, progress, task_id) -> int:
    """
    Processa um batch de records em paralelo:
    1. ThreadPoolExecutor gera embeddings simultaneamente
    2. Insere no banco em lote (batch commit)
    """
    count = 0

    # Fase 1: Gerar embeddings em paralelo
    embedded = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(embed_record, rec): rec for rec in records}
        for future in as_completed(futures):
            try:
                result = future.result()
                embedded.append(result)
            except Exception:
                pass
            progress.advance(task_id)

    # Fase 2: Inserir em lote (única transação)
    if not embedded:
        return 0

    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as conn:
        for rec in embedded:
            rec.pop("_content", None)
            result = conn.execute(INSERT_SQL, rec).fetchone()
            if result:
                count += 1
        conn.commit()

    return count


def process_source(source_name: str, records: list[dict], workers: int, batch_size: int = 50) -> int:
    """Processa uma fonte completa com barra de progresso."""
    total = len(records)
    if total == 0:
        return 0

    console.rule(f"[bold cyan]{source_name} ({total} documentos | {workers} workers | batch {batch_size})")

    t_start = time.perf_counter()
    inserted = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=35),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
        TextColumn("• [bold green]{task.fields[speed]:.1f} doc/min"),
        console=console,
        refresh_per_second=4,
    ) as progress:
        task = progress.add_task(source_name, total=total, speed=0.0)

        # Processar em batches
        for i in range(0, total, batch_size):
            batch = records[i:i + batch_size]
            n = ingest_batch(batch, workers, progress, task)
            inserted += n

            elapsed = time.perf_counter() - t_start
            speed = (i + len(batch)) / (elapsed / 60) if elapsed > 0 else 0
            progress.update(task, speed=speed)

    elapsed_total = time.perf_counter() - t_start
    final_speed = total / (elapsed_total / 60) if elapsed_total > 0 else 0
    console.print(
        f"  ✅ {inserted}/{total} inseridos | "
        f"{elapsed_total:.0f}s total | "
        f"{final_speed:.1f} docs/min | "
        f"⚡ {_error_count} erros de embedding"
    )
    return inserted


def fix_missing_embeddings(workers: int) -> int:
    """Reprocessa nodes que estão sem embedding."""
    console.rule("[bold yellow]🔧 Reprocessando Nodes Sem Embedding")

    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as conn:
        rows = conn.execute("""
            SELECT id, content_for_embedding, source_type
            FROM ticket_knowledge_nodes
            WHERE embedding_generated = FALSE
              AND content_for_embedding IS NOT NULL
              AND LENGTH(content_for_embedding) > 10
            ORDER BY id
        """).fetchall()

    if not rows:
        console.print("  [green]✅ Nenhum node sem embedding![/]")
        return 0

    console.print(f"  [yellow]⚠️ {len(rows)} nodes sem embedding. Reprocessando...[/]")

    def fix_one(row):
        emb = call_gemini_embed(row["content_for_embedding"], "RETRIEVAL_DOCUMENT")
        return row["id"], emb

    count = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fix_one, row): row for row in rows}
        with psycopg.connect(POSTGRES_DSN) as conn:
            batch_updates = []
            for future in as_completed(futures):
                node_id, embedding = future.result()
                if embedding:
                    batch_updates.append((embedding, node_id))
                    count += 1

                if len(batch_updates) >= 50:
                    for emb, nid in batch_updates:
                        conn.execute(
                            "UPDATE ticket_knowledge_nodes SET embedding = %s, embedding_generated = TRUE, updated_at = NOW() WHERE id = %s",
                            (emb, nid)
                        )
                    conn.commit()
                    batch_updates = []

            if batch_updates:
                for emb, nid in batch_updates:
                    conn.execute(
                        "UPDATE ticket_knowledge_nodes SET embedding = %s, embedding_generated = TRUE, updated_at = NOW() WHERE id = %s",
                        (emb, nid)
                    )
                conn.commit()

    console.print(f"  ✅ {count}/{len(rows)} nodes reprocessados com sucesso!")
    return count


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="DeskCortex — Ingestão v2 (Paralela)")
    parser.add_argument("--poc", action="store_true", help="20 KB + 20 GOLD + 20 SILVER")
    parser.add_argument("--full", action="store_true", help="Pipeline completo")
    parser.add_argument("--only", choices=["kb", "gold", "silver"])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=8, help="Threads paralelas (default: 8)")
    parser.add_argument("--batch", type=int, default=50, help="Tamanho do batch (default: 50)")
    parser.add_argument("--fix-missing", action="store_true", help="Reprocessar nodes sem embedding")
    args = parser.parse_args()

    if not GEMINI_API_KEY:
        console.print("[bold red]❌ GEMINI_API_KEY não configurada![/]")
        sys.exit(1)

    console.print(Panel.fit(
        f"[bold white on blue] DeskCortex — Knowledge Nodes v2 (Paralelo) [/]\n"
        f"[dim]Modelo: {GEMINI_EMBEDDING_MODEL} | Workers: {args.workers} | Batch: {args.batch}[/]",
        border_style="blue"
    ))

    if args.fix_missing:
        fix_missing_embeddings(args.workers)
        return

    # Definir limites
    if args.poc:
        kb_lim, gold_lim, silver_lim = 20, 20, 20
        console.print("[bold yellow]🧪 Modo POC: 20 KB + 20 GOLD + 20 SILVER[/]")
    elif args.full:
        kb_lim = gold_lim = silver_lim = 0
        console.print("[bold green]🚀 Modo COMPLETO[/]")
    else:
        kb_lim = gold_lim = silver_lim = args.limit if args.limit > 0 else 20

    t0 = time.perf_counter()
    total_inserted = 0

    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as conn:
        # ── KB ──────────────────────────────────────────────────────────────
        if not args.only or args.only == "kb":
            q = "SELECT id, external_id, title, summary, categories, content_html, content_text FROM raw_kb_articles ORDER BY id"
            if kb_lim: q += f" LIMIT {kb_lim}"
            kb_rows = conn.execute(q).fetchall()
            records = [prepare_kb_record(r) for r in kb_rows if compose_kb_text(r).strip()]
            total_inserted += process_source("📘 KB Oficial", records, args.workers, args.batch)

        # ── GOLD ─────────────────────────────────────────────────────────────
        if not args.only or args.only == "gold":
            q = """SELECT external_id, subject, service_full, service_first_level,
                          problem_statement, technical_narrative, solution_statement,
                          detected_modules, detected_error_codes, evidence_data,
                          technical_score, solution_score, validation_score, final_score,
                          hard_block_reason
                   FROM refined_tickets WHERE quality_tier = 'GOLD'
                   ORDER BY final_score DESC, validation_score DESC"""
            if gold_lim: q += f" LIMIT {gold_lim}"
            gold_rows = conn.execute(q).fetchall()
            records = [prepare_ticket_record(r, "GOLD") for r in gold_rows if compose_ticket_text(r).strip()]
            total_inserted += process_source("🌟 GOLD", records, args.workers, args.batch)

        # ── SILVER ───────────────────────────────────────────────────────────
        if not args.only or args.only == "silver":
            q = """SELECT external_id, subject, service_full, service_first_level,
                          problem_statement, technical_narrative, solution_statement,
                          detected_modules, detected_error_codes, evidence_data,
                          technical_score, solution_score, validation_score, final_score,
                          hard_block_reason
                   FROM refined_tickets WHERE quality_tier = 'SILVER'
                   ORDER BY final_score DESC, validation_score DESC"""
            if silver_lim: q += f" LIMIT {silver_lim}"
            silver_rows = conn.execute(q).fetchall()
            records = [prepare_ticket_record(r, "SILVER") for r in silver_rows if compose_ticket_text(r).strip()]
            total_inserted += process_source("🥈 SILVER", records, args.workers, args.batch)

    elapsed = time.perf_counter() - t0
    speed = total_inserted / (elapsed / 60) if elapsed > 0 else 0

    # Resumo final
    console.rule("[bold green]✅ Pipeline Concluído")
    tbl = Table(show_lines=True)
    tbl.add_column("Métrica", style="cyan")
    tbl.add_column("Valor", style="bold green")
    tbl.add_row("Nodes inseridos", str(total_inserted))
    tbl.add_row("Chamadas Gemini", str(_call_count))
    tbl.add_row("Erros de embedding", str(_error_count))
    tbl.add_row("Tempo total", f"{elapsed:.0f}s ({elapsed/60:.1f} min)")
    tbl.add_row("Velocidade média", f"{speed:.1f} docs/min")
    console.print(tbl)

    # Estado final do banco
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as conn:
        stats = conn.execute("""
            SELECT source_type, tier, COUNT(*) as total,
                   SUM(CASE WHEN embedding_generated THEN 1 ELSE 0 END) as com_embedding
            FROM ticket_knowledge_nodes
            GROUP BY source_type, tier ORDER BY total DESC
        """).fetchall()
        console.rule("[bold cyan]Estado do ticket_knowledge_nodes")
        tbl2 = Table(show_lines=True)
        tbl2.add_column("Source Type", style="cyan")
        tbl2.add_column("Tier", style="yellow")
        tbl2.add_column("Total", style="bold green", justify="right")
        tbl2.add_column("Com Embedding", style="bold blue", justify="right")
        for r in stats:
            tbl2.add_row(r["source_type"], r["tier"], str(r["total"]), str(r["with_embedding"] if "with_embedding" in r else r["com_embedding"]))
        console.print(tbl2)


if __name__ == "__main__":
    main()
