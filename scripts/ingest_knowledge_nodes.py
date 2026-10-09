"""
DeskCortex — Ingestão de Knowledge Nodes (POC 60 nodes + Pipeline Completo)
=============================================================================
Transforma tickets refinados e artigos da KB em Knowledge Nodes estruturados
com embeddings Gemini Embedding 2 (3072 dims) e armazena no pgvector.

Fluxo:
  refined_tickets (GOLD/SILVER) + raw_kb_articles
       ↓
  Composição do content_for_embedding (texto rico e estruturado)
       ↓
  Gemini Embedding 2 (models/gemini-embedding-2, 3072 dims)
       ↓
  ticket_knowledge_nodes (pgvector)

Uso:
    python ingest_knowledge_nodes.py --poc        # 20 KB + 20 GOLD + 20 SILVER
    python ingest_knowledge_nodes.py --full       # pipeline completo (~29k)
    python ingest_knowledge_nodes.py --only kb    # apenas artigos KB
    python ingest_knowledge_nodes.py --only gold  # apenas GOLD
    python ingest_knowledge_nodes.py --only silver # apenas SILVER
"""

import argparse
import html
import json
import os
import re
import sys
import time
from pathlib import Path

os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import urllib.request
import psycopg
from psycopg.rows import dict_row
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    POSTGRES_DSN, GEMINI_API_KEY,
    GEMINI_EMBEDDING_MODEL, GEMINI_EMBEDDING_DIMENSIONS, GEMINI_EMBED_RPM_LIMIT
)

console = Console(force_terminal=True)

TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"[ \t]+")


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
# Composição do texto de embedding (o coração do Knowledge Node)
# ─────────────────────────────────────────────────────────────────────────────

def compose_ticket_embedding_text(row: dict) -> str:
    """
    Constrói o texto estruturado que representa o conhecimento do ticket.
    O vetor deve capturar o significado do problema e da solução,
    não a conversa bruta.
    """
    parts = []

    if row.get("subject"):
        parts.append(f"ASSUNTO: {row['subject']}")

    modules = row.get("detected_modules")
    if isinstance(modules, str):
        try:
            modules = json.loads(modules)
        except Exception:
            modules = []
    if modules:
        parts.append(f"MÓDULOS: {', '.join(modules)}")

    errors = row.get("detected_error_codes")
    if isinstance(errors, str):
        try:
            errors = json.loads(errors)
        except Exception:
            errors = []
    if errors:
        parts.append(f"ERROS/CÓDIGOS: {', '.join(errors)}")

    if row.get("service_full"):
        parts.append(f"SERVIÇO: {row['service_full']}")

    if row.get("problem_statement"):
        parts.append(f"PROBLEMA: {clean_text(row['problem_statement'], 800)}")

    if row.get("technical_narrative"):
        narrative = clean_text(row["technical_narrative"], 600)
        if narrative:
            parts.append(f"DIAGNÓSTICO/INVESTIGAÇÃO: {narrative}")

    if row.get("solution_statement"):
        parts.append(f"SOLUÇÃO: {clean_text(row['solution_statement'], 800)}")

    evidence = row.get("evidence_data")
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except Exception:
            evidence = {}
    if isinstance(evidence, dict):
        tags = evidence.get("evidence_tags", [])
        if tags:
            parts.append(f"EVIDÊNCIA DE VALIDAÇÃO: {', '.join(tags)}")

    return "\n".join(parts)


def compose_kb_embedding_text(row: dict) -> str:
    """
    Constrói o texto de embedding para artigos oficiais da Base de Conhecimento.
    """
    parts = []

    if row.get("title"):
        parts.append(f"ARTIGO OFICIAL: {row['title']}")

    if row.get("categories"):
        cats = row["categories"]
        if isinstance(cats, list) and len(cats) > 0:
            # categories pode ser lista de strings ou lista de dicts com 'name'
            cat_names = []
            for c in cats:
                if isinstance(c, dict):
                    cat_names.append(c.get("name") or c.get("title") or str(c))
                else:
                    cat_names.append(str(c))
            if cat_names:
                parts.append(f"CATEGORIA: {', '.join(cat_names)}")

    if row.get("summary"):
        parts.append(f"RESUMO: {clean_text(row['summary'], 400)}")

    if row.get("content_text"):
        parts.append(f"CONTEÚDO: {clean_text(row['content_text'], 2500)}")
    elif row.get("content_html"):
        parts.append(f"CONTEÚDO: {clean_text(row['content_html'], 2500)}")

    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Gemini Embedding API (com retry e rate limiting)
# ─────────────────────────────────────────────────────────────────────────────

class GeminiEmbedder:
    def __init__(self, api_key: str, model: str, rpm_limit: int = 90):
        self.api_key = api_key
        self.model = model
        self.rpm_limit = rpm_limit
        self._min_interval = 60.0 / rpm_limit
        self._last_call = 0.0
        self._total_calls = 0
        self._errors = 0

    def embed(self, text: str, task_type: str = "RETRIEVAL_DOCUMENT", retries: int = 3) -> list[float] | None:
        """Gera embedding para um texto com retry automático."""
        # Rate limiting
        elapsed = time.time() - self._last_call
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

        url = f"https://generativelanguage.googleapis.com/v1beta/{self.model}:embedContent?key={self.api_key}"
        payload = {
            "model": self.model,
            "content": {"parts": [{"text": text[:8000]}]},  # máx 8192 tokens
            "taskType": task_type
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"}
        )

        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read())
                    self._last_call = time.time()
                    self._total_calls += 1
                    return data["embedding"]["values"]
            except urllib.error.HTTPError as e:
                if e.code == 429:  # Rate limit
                    wait = 2 ** (attempt + 2)
                    console.print(f"  [yellow]⚡ Rate limit (429), aguardando {wait}s...[/]")
                    time.sleep(wait)
                elif e.code in (500, 503):
                    wait = 2 ** attempt
                    time.sleep(wait)
                else:
                    self._errors += 1
                    console.print(f"  [red]❌ Erro HTTP {e.code} ao gerar embedding[/]")
                    return None
            except Exception as ex:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    self._errors += 1
                    console.print(f"  [red]❌ Erro ao gerar embedding: {ex}[/]")
                    return None
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Funções de Ingestão por Fonte
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


def ingest_kb_articles(conn, embedder: GeminiEmbedder, limit: int = 0) -> int:
    """Ingere artigos oficiais da KB como OFFICIAL_KB (autoridade 100)."""
    query = "SELECT id, external_id, title, summary, categories, content_html, content_text FROM raw_kb_articles ORDER BY id"
    if limit > 0:
        query += f" LIMIT {limit}"

    rows = conn.execute(query).fetchall()
    count = 0

    console.rule("[bold cyan]📘 Ingerindo Artigos Oficiais da Base de Conhecimento")

    for row in rows:
        content = compose_kb_embedding_text(row)
        if not content.strip():
            continue

        embedding = embedder.embed(content, task_type="RETRIEVAL_DOCUMENT")
        embedding_ok = embedding is not None

        record = {
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
            "embedding": embedding,
            "embedding_generated": embedding_ok,
            "embedding_model": embedder.model,
        }

        result = conn.execute(INSERT_SQL, record).fetchone()
        if result:
            count += 1
        conn.commit()

        status = "✅" if embedding_ok else "⚠️ (sem embedding)"
        console.print(f"  📘 KB {count}/{len(rows)} [{status}] {record['title'][:60]}")

    return count


def ingest_tickets(conn, embedder: GeminiEmbedder, tier: str, limit: int = 0) -> int:
    """Ingere tickets GOLD ou SILVER como Knowledge Nodes."""

    if tier == "GOLD":
        source_type = "VALIDATED_TICKET"
        confidence = "HIGH"
        source_authority = 85
        tier_label = "GOLD"
    else:
        source_type = "PROBABLE_TICKET"
        confidence = "MEDIUM"
        source_authority = 60
        tier_label = "SILVER"

    query = f"""
        SELECT external_id, subject, service_full, service_first_level,
               problem_statement, technical_narrative, solution_statement,
               detected_modules, detected_error_codes, evidence_data,
               technical_score, solution_score, validation_score, final_score,
               hard_block_reason, validation_score
        FROM refined_tickets
        WHERE quality_tier = '{tier}'
        ORDER BY final_score DESC, validation_score DESC
    """
    if limit > 0:
        query += f" LIMIT {limit}"

    rows = conn.execute(query).fetchall()
    count = 0

    tier_emoji = "🌟" if tier == "GOLD" else "🥈"
    console.rule(f"[bold cyan]{tier_emoji} Ingerindo Tickets {tier} ({len(rows)} candidatos)")

    for i, row in enumerate(rows, 1):
        content = compose_ticket_embedding_text(row)
        if not content.strip():
            continue

        modules = row.get("detected_modules")
        if isinstance(modules, str):
            try:
                modules = json.loads(modules)
            except Exception:
                modules = None

        errors = row.get("detected_error_codes")
        if isinstance(errors, str):
            try:
                errors = json.loads(errors)
            except Exception:
                errors = None

        evidence_data = row.get("evidence_data")
        if isinstance(evidence_data, str):
            try:
                evidence_data = json.loads(evidence_data)
            except Exception:
                evidence_data = {}
        evidence_tags = evidence_data.get("evidence_tags", []) if isinstance(evidence_data, dict) else []

        embedding = embedder.embed(content, task_type="RETRIEVAL_DOCUMENT")
        embedding_ok = embedding is not None

        record = {
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
            "tier": tier_label,
            "confidence": confidence,
            "technical_score": row.get("technical_score", 0),
            "solution_score": row.get("solution_score", 0),
            "validation_score": row.get("validation_score", 0),
            "final_score": row.get("final_score", 0),
            "validation_evidence": evidence_tags if evidence_tags else None,
            "hard_block_reason": row.get("hard_block_reason"),
            "source_authority": source_authority,
            "metadata": json.dumps({
                "service_full": row.get("service_full"),
                "service_first_level": row.get("service_first_level"),
            }),
            "embedding": embedding,
            "embedding_generated": embedding_ok,
            "embedding_model": embedder.model,
        }

        result = conn.execute(INSERT_SQL, record).fetchone()
        if result:
            count += 1
        conn.commit()

        status = "✅" if embedding_ok else "⚠️ (sem embedding)"
        console.print(f"  {tier_emoji} {tier} {i}/{len(rows)} [{status}] #{row['external_id']} | Score: {row['final_score']} | {record['title'][:50]}")

    return count


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="DeskCortex — Ingestão de Knowledge Nodes")
    parser.add_argument("--poc", action="store_true", help="POC: 20 KB + 20 GOLD + 20 SILVER")
    parser.add_argument("--full", action="store_true", help="Pipeline completo (~29k nodes)")
    parser.add_argument("--only", type=str, choices=["kb", "gold", "silver"], help="Apenas uma fonte")
    parser.add_argument("--limit", type=int, default=0, help="Limitar quantidade por fonte")
    args = parser.parse_args()

    if not GEMINI_API_KEY:
        console.print("[bold red]❌ GEMINI_API_KEY não configurada no .env![/]")
        sys.exit(1)

    embedder = GeminiEmbedder(GEMINI_API_KEY, GEMINI_EMBEDDING_MODEL, GEMINI_EMBED_RPM_LIMIT)

    console.print(Panel.fit(
        f"[bold white on blue] DeskCortex — Knowledge Nodes Ingestion [/]\n"
        f"[dim]Modelo: {GEMINI_EMBEDDING_MODEL} | Dimensões: {GEMINI_EMBEDDING_DIMENSIONS} | RPM: {GEMINI_EMBED_RPM_LIMIT}[/]",
        border_style="blue"
    ))

    # Definir limites
    if args.poc:
        kb_limit, gold_limit, silver_limit = 20, 20, 20
        console.print("[bold yellow]🧪 Modo POC: 20 KB + 20 GOLD + 20 SILVER[/]")
    elif args.full:
        kb_limit, gold_limit, silver_limit = 0, 0, 0
        console.print("[bold green]🚀 Modo COMPLETO: todos os documentos[/]")
    elif args.limit > 0:
        kb_limit = gold_limit = silver_limit = args.limit
    else:
        kb_limit, gold_limit, silver_limit = 20, 20, 20  # POC padrão

    t_start = time.perf_counter()
    total = 0

    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as conn:
        if not args.only or args.only == "kb":
            total += ingest_kb_articles(conn, embedder, kb_limit)

        if not args.only or args.only == "gold":
            total += ingest_tickets(conn, embedder, "GOLD", gold_limit)

        if not args.only or args.only == "silver":
            total += ingest_tickets(conn, embedder, "SILVER", silver_limit)

    t_total = time.perf_counter() - t_start

    # Resumo final
    console.rule("[bold green]✅ Ingestão Concluída")
    summary = Table(show_lines=True)
    summary.add_column("Métrica", style="cyan")
    summary.add_column("Valor", style="bold green")
    summary.add_row("Total de Knowledge Nodes ingeridos", str(total))
    summary.add_row("Total de chamadas à API Gemini", str(embedder._total_calls))
    summary.add_row("Erros de embedding", str(embedder._errors))
    summary.add_row("Tempo total", f"{t_total:.1f}s")
    console.print(summary)

    # Verificar no banco
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as conn:
        stats = conn.execute("""
            SELECT source_type, tier, COUNT(*) as total,
                   SUM(CASE WHEN embedding_generated THEN 1 ELSE 0 END) as with_embedding
            FROM ticket_knowledge_nodes
            GROUP BY source_type, tier
            ORDER BY total DESC
        """).fetchall()

        console.rule("[bold cyan]Estado Atual do ticket_knowledge_nodes")
        tbl = Table(show_lines=True)
        tbl.add_column("Source Type", style="cyan")
        tbl.add_column("Tier", style="yellow")
        tbl.add_column("Total Nodes", style="bold green", justify="right")
        tbl.add_column("Com Embedding", style="bold blue", justify="right")
        for r in stats:
            tbl.add_row(r["source_type"], r["tier"], str(r["total"]), str(r["with_embedding"]))
        console.print(tbl)


if __name__ == "__main__":
    main()
