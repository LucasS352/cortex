"""
DeskCortex — Pipeline de Ingestão em Lote Ultra-Rápido (batchEmbedContents)
===========================================================================
Usa o endpoint oficial `batchEmbedContents` do Gemini para enviar até 25 textos
em uma única requisição HTTP, alcançando velocidades de ~600-900 docs/min.

Operações suportadas:
  --fix-missing      : Repara nodes existentes que estão sem vetor
  --kb               : Ingestão de todos os 1.400 artigos da KB
  --gold             : Ingestão de todos os 2.830 tickets GOLD
  --silver           : Ingestão dos 25.075 tickets SILVER
  --all-core         : Executa fix-missing + KB + GOLD (~4.230 docs em ~5 min)
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import psycopg
from psycopg.rows import dict_row
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn, MofNCompleteColumn

sys.path.insert(0, str(Path(__file__).parent))
from config import POSTGRES_DSN, GEMINI_API_KEY, GEMINI_EMBEDDING_MODEL

console = Console(force_terminal=True)

TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"[ \t]+")
BATCH_SIZE = 25  # Quantidade ideal por requisição batchEmbedContents


def clean_text(text: str, max_chars: int = 3000) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = TAG_RE.sub(" ", text)
    text = WHITESPACE_RE.sub(" ", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)[:max_chars]


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


def call_batch_embed(texts: list[str], max_retries: int = 3) -> list[list[float] | None]:
    """Chama batchEmbedContents enviando uma lista de textos."""
    url = f"https://generativelanguage.googleapis.com/v1beta/{GEMINI_EMBEDDING_MODEL}:batchEmbedContents?key={GEMINI_API_KEY}"
    
    requests = [
        {
            "model": GEMINI_EMBEDDING_MODEL,
            "content": {"parts": [{"text": t[:8000] if t else "vazio"}]},
            "taskType": "RETRIEVAL_DOCUMENT"
        }
        for t in texts
    ]
    
    payload = {"requests": requests}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=35) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                embeddings = data.get("embeddings", [])
                results = []
                for e in embeddings:
                    results.append(e.get("values"))
                # Garantir mesmo tamanho
                while len(results) < len(texts):
                    results.append(None)
                return results
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 2 ** (attempt + 2)
                console.print(f"  [yellow]Rate limit 429, aguardando {wait}s...[/]")
                time.sleep(wait)
            elif e.code in (500, 503):
                time.sleep(2 ** attempt)
            else:
                console.print(f"  [red]HTTP {e.code}: {e.read().decode('utf-8')[:200]}[/]")
                break
        except Exception as ex:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                console.print(f"  [red]Erro batch: {ex}[/]")
                break
    return [None] * len(texts)


def fix_missing_embeddings(conn):
    """Repara nós que já foram inseridos mas ficaram sem vetor."""
    console.rule("[bold yellow]🔧 Reparando Nós Existentes Sem Vetor")
    
    rows = conn.execute("""
        SELECT id, content_for_embedding
        FROM ticket_knowledge_nodes
        WHERE embedding_generated = FALSE 
          AND content_for_embedding IS NOT NULL
        ORDER BY id;
    """).fetchall()
    
    total = len(rows)
    if total == 0:
        console.print("  [green]✅ Nenhum nó com embedding pendente![/]")
        return 0
        
    console.print(f"  Encontrados [yellow]{total}[/] nós para calcular vetor.")
    fixed = 0
    t0 = time.perf_counter()
    
    for i in range(0, total, BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        texts = [r["content_for_embedding"] for r in batch]
        vectors = call_batch_embed(texts)
        
        for r, vec in zip(batch, vectors):
            if vec is not None:
                conn.execute("""
                    UPDATE ticket_knowledge_nodes
                    SET embedding = %s, embedding_generated = TRUE, updated_at = NOW()
                    WHERE id = %s
                """, (vec, r["id"]))
                fixed += 1
        conn.commit()
        time.sleep(0.2)
        
    elapsed = time.perf_counter() - t0
    console.print(f"  [bold green]✅ {fixed}/{total} nós reparados em {elapsed:.1f}s![/]")
    return fixed


def ingest_kb(conn):
    """Ingere os artigos da KB que ainda não estão em ticket_knowledge_nodes."""
    console.rule("[bold cyan]📘 Ingerindo Artigos da Base de Conhecimento (KB)")
    
    # Buscar apenas artigos que ainda NÃO foram inseridos
    rows = conn.execute("""
        SELECT r.id, r.external_id, r.title, r.summary, r.categories, r.content_html, r.content_text
        FROM raw_kb_articles r
        LEFT JOIN ticket_knowledge_nodes k ON k.source_id = 'kb-' || r.id::text
        WHERE k.id IS NULL
        ORDER BY r.id;
    """).fetchall()
    
    total = len(rows)
    if total == 0:
        console.print("  [green]✅ Todos os 1.400 artigos da KB já estão no banco![/]")
        return 0
        
    console.print(f"  Faltam [cyan]{total}[/] artigos para ingerir.")
    t0 = time.perf_counter()
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
        console=console
    ) as progress:
        task = progress.add_task("Artigos KB", total=total, speed=0.0)
        
        for i in range(0, total, BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            texts = [compose_kb_text(r) for r in batch]
            vectors = call_batch_embed(texts)
            
            for r, text, vec in zip(batch, texts, vectors):
                source_id = f"kb-{r['id']}"
                has_vec = vec is not None
                
                conn.execute("""
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
                        'OFFICIAL_KB', %s, NULL,
                        %s, %s,
                        NULL, NULL, %s, NULL,
                        'Master ERP', NULL, NULL, NULL,
                        'KB', 'HIGH', 100, 100, 100, 100,
                        ARRAY['OFFICIAL_DOCUMENTATION'], NULL, 100,
                        %s, %s, %s, %s
                    )
                    ON CONFLICT (source_id) DO UPDATE 
                    SET embedding = EXCLUDED.embedding, embedding_generated = EXCLUDED.embedding_generated, updated_at = NOW()
                """, (
                    source_id,
                    clean_text(r.get("title") or "Artigo KB", 200),
                    text,
                    clean_text(r.get("content_text") or r.get("content_html") or "", 2000),
                    json.dumps({"categories": r.get("categories"), "external_id": r.get("external_id")}),
                    vec,
                    has_vec,
                    GEMINI_EMBEDDING_MODEL
                ))
                inserted += 1
                progress.advance(task)
                
            conn.commit()
            elapsed = time.perf_counter() - t0
            speed = inserted / (elapsed / 60) if elapsed > 0 else 0
            progress.update(task, speed=speed)
            time.sleep(0.2)
            
    elapsed = time.perf_counter() - t0
    console.print(f"  [bold green]✅ {inserted} artigos KB concluídos em {elapsed:.1f}s ({inserted/(elapsed/60):.0f} doc/min)![/]")
    return inserted


def ingest_tickets(conn, tier: str):
    """Ingere tickets GOLD ou SILVER em lotes de 25."""
    tier_name = "GOLD (Soluções Validadas)" if tier == "GOLD" else "SILVER (Procedimentos Prováveis)"
    emoji = "🌟" if tier == "GOLD" else "🥈"
    console.rule(f"[bold cyan]{emoji} Ingerindo Tickets {tier_name}")
    
    source_type = "VALIDATED_TICKET" if tier == "GOLD" else "PROBABLE_TICKET"
    confidence = "HIGH" if tier == "GOLD" else "MEDIUM"
    authority = 85 if tier == "GOLD" else 60
    
    rows = conn.execute(f"""
        SELECT r.external_id, r.subject, r.service_full, r.service_first_level,
               r.problem_statement, r.technical_narrative, r.solution_statement,
               r.detected_modules, r.detected_error_codes, r.evidence_data,
               r.technical_score, r.solution_score, r.validation_score, r.final_score,
               r.hard_block_reason
        FROM refined_tickets r
        LEFT JOIN ticket_knowledge_nodes k ON k.source_id = 'ticket-' || r.external_id::text
        WHERE r.quality_tier = '{tier}'
          AND (k.id IS NULL OR k.embedding_generated = FALSE)
        ORDER BY r.final_score DESC, r.validation_score DESC;
    """).fetchall()
    
    total = len(rows)
    if total == 0:
        console.print(f"  [green]✅ Todos os tickets {tier} já estão indexados com embedding![/]")
        return 0
        
    console.print(f"  Pendentes para ingestão/vetorização: [bold yellow]{total}[/] chamados.")
    t0 = time.perf_counter()
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
        console=console
    ) as progress:
        task = progress.add_task(f"Tickets {tier}", total=total, speed=0.0)
        
        for i in range(0, total, BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            texts = [compose_ticket_text(r) for r in batch]
            vectors = call_batch_embed(texts)
            
            for r, text, vec in zip(batch, texts, vectors):
                source_id = f"ticket-{r['external_id']}"
                has_vec = vec is not None
                
                modules = r.get("detected_modules")
                if isinstance(modules, str):
                    try: modules = json.loads(modules)
                    except: modules = None
                if isinstance(modules, list):
                    modules = [str(m) for m in modules if m is not None]
                    if not modules: modules = None
                else:
                    modules = None

                errors = r.get("detected_error_codes")
                if isinstance(errors, str):
                    try: errors = json.loads(errors)
                    except: errors = None
                if isinstance(errors, list):
                    errors = [str(e) for e in errors if e is not None]
                    if not errors: errors = None
                else:
                    errors = None

                evidence_data = r.get("evidence_data")
                if isinstance(evidence_data, str):
                    try: evidence_data = json.loads(evidence_data)
                    except: evidence_data = {}
                evidence_tags = evidence_data.get("evidence_tags", []) if isinstance(evidence_data, dict) else []
                if isinstance(evidence_tags, list):
                    evidence_tags = [str(t) for t in evidence_tags if t is not None]
                    if not evidence_tags: evidence_tags = None
                else:
                    evidence_tags = None

                clean_vec = [float(v) for v in vec] if vec is not None else None

                conn.execute("""
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
                        %s, %s, %s,
                        %s, %s,
                        %s, %s, %s, NULL,
                        'Master ERP', %s, %s, NULL,
                        %s, %s, %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    ON CONFLICT (source_id) DO UPDATE 
                    SET embedding = EXCLUDED.embedding, embedding_generated = EXCLUDED.embedding_generated, updated_at = NOW()
                """, (
                    source_type,
                    source_id,
                    r["external_id"],
                    clean_text(r.get("subject") or "Sem título", 200),
                    text,
                    clean_text(r.get("problem_statement") or "", 1000),
                    clean_text(r.get("technical_narrative") or "", 800),
                    clean_text(r.get("solution_statement") or "", 1000),
                    modules,
                    errors,
                    tier,
                    confidence,
                    r.get("technical_score", 0),
                    r.get("solution_score", 0),
                    r.get("validation_score", 0),
                    r.get("final_score", 0),
                    evidence_tags,
                    r.get("hard_block_reason"),
                    authority,
                    json.dumps({
                        "service_full": r.get("service_full"),
                        "service_first_level": r.get("service_first_level")
                    }),
                    clean_vec,
                    has_vec,
                    GEMINI_EMBEDDING_MODEL
                ))
                inserted += 1
                progress.advance(task)
                
            conn.commit()
            elapsed = time.perf_counter() - t0
            speed = inserted / (elapsed / 60) if elapsed > 0 else 0
            progress.update(task, speed=speed)
            time.sleep(0.2)
            
    elapsed = time.perf_counter() - t0
    console.print(f"  [bold green]✅ {inserted} tickets {tier} concluídos em {elapsed:.1f}s ({inserted/(elapsed/60):.0f} doc/min)![/]")
    return inserted


def main():
    parser = argparse.ArgumentParser(description="DeskCortex — Ingestão em Lote Ultra-Rápido")
    parser.add_argument("--fix-missing", action="store_true", help="Reparar nós existentes sem vetor")
    parser.add_argument("--kb", action="store_true", help="Ingerir artigos KB")
    parser.add_argument("--gold", action="store_true", help="Ingerir tickets GOLD")
    parser.add_argument("--silver", action="store_true", help="Ingerir tickets SILVER")
    parser.add_argument("--all-core", action="store_true", help="Executar fix-missing + KB + GOLD")
    args = parser.parse_args()

    console.print(Panel.fit(
        f"[bold white on blue] DeskCortex — Ingestão em Lote Ultra-Rápida [/]\n"
        f"[dim]Modelo: {GEMINI_EMBEDDING_MODEL} | Batch: {BATCH_SIZE} docs/req[/]",
        border_style="blue"
    ))

    t_start = time.perf_counter()

    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as conn:
        if args.fix_missing or args.all_core:
            fix_missing_embeddings(conn)

        if args.kb or args.all_core:
            ingest_kb(conn)

        if args.gold or args.all_core:
            ingest_tickets(conn, "GOLD")

        if args.silver:
            ingest_tickets(conn, "SILVER")

        # Exibir estado final
        stats = conn.execute("""
            SELECT source_type, tier, COUNT(*) as total,
                   SUM(CASE WHEN embedding_generated THEN 1 ELSE 0 END) as com_embedding,
                   SUM(CASE WHEN NOT embedding_generated THEN 1 ELSE 0 END) as sem_embedding
            FROM ticket_knowledge_nodes
            GROUP BY source_type, tier ORDER BY total DESC;
        """).fetchall()

        console.rule("[bold cyan]Estado Atual de ticket_knowledge_nodes")
        tbl = Table(show_lines=True)
        tbl.add_column("Source Type", style="cyan")
        tbl.add_column("Tier", style="yellow")
        tbl.add_column("Total Nodes", style="bold green", justify="right")
        tbl.add_column("Com Embedding", style="bold blue", justify="right")
        tbl.add_column("Sem Embedding", style="bold red", justify="right")
        for r in stats:
            tbl.add_row(r["source_type"], r["tier"], str(r["total"]), str(r["com_embedding"]), str(r["sem_embedding"]))
        console.print(tbl)

    elapsed = time.perf_counter() - t_start
    console.print(f"\n[bold green]🏁 Processo finalizado em {elapsed:.1f}s ({elapsed/60:.1f} min)![/]")


if __name__ == "__main__":
    main()
