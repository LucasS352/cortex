"""
DeskCortex — Teste de Busca Semântica e Híbrida (POC Validation)
=================================================================
Valida a qualidade dos embeddings e da busca híbrida com consultas reais.

Uso:
    python search_test.py
    python search_test.py --query "erro ao emitir nota fiscal"
"""

import argparse
import json
import os
import sys
import time
import urllib.request
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
from rich.rule import Rule

sys.path.insert(0, str(Path(__file__).parent))
from config import POSTGRES_DSN, GEMINI_API_KEY, GEMINI_EMBEDDING_MODEL

console = Console(force_terminal=True)

CONFIDENCE_COLOR = {"HIGH": "bold green", "MEDIUM": "bold yellow", "LOW": "bold red"}
TIER_EMOJI = {"GOLD": "🌟", "SILVER": "🥈", "KB": "📘"}
SOURCE_LABEL = {
    "OFFICIAL_KB": "📘 Base de Conhecimento Oficial",
    "VALIDATED_TICKET": "🌟 Ticket GOLD (Solução Validada)",
    "PROBABLE_TICKET": "🥈 Ticket SILVER (Procedimento Provável)",
}


def get_query_embedding(text: str) -> list[float] | None:
    """Gera embedding da query usando Gemini (task_type=RETRIEVAL_QUERY)."""
    url = f"https://generativelanguage.googleapis.com/v1beta/{GEMINI_EMBEDDING_MODEL}:embedContent?key={GEMINI_API_KEY}"
    body = json.dumps({
        "model": GEMINI_EMBEDDING_MODEL,
        "content": {"parts": [{"text": text}]},
        "taskType": "RETRIEVAL_QUERY"  # Diferente do RETRIEVAL_DOCUMENT!
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            return data["embedding"]["values"]
    except Exception as e:
        console.print(f"[red]❌ Erro ao gerar embedding da query: {e}[/]")
        return None


def semantic_search(conn, embedding: list[float], top_k: int = 5) -> list[dict]:
    """Busca semântica pura por similaridade de cosseno."""
    vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
    query = f"""
        SELECT
            id, source_type, source_external_id, title,
            problem, solution, tier, confidence, source_authority,
            final_score, validation_evidence,
            1 - (embedding <=> '{vec_str}'::vector) AS similarity
        FROM ticket_knowledge_nodes
        WHERE embedding_generated = TRUE
        ORDER BY embedding <=> '{vec_str}'::vector
        LIMIT {top_k};
    """
    return conn.execute(query).fetchall()


def fts_search(conn, query_text: str, top_k: int = 5) -> list[dict]:
    """Busca lexical (Full-Text Search) em português."""
    # Preparar termos de busca
    terms = " | ".join(query_text.split())
    sql = f"""
        SELECT
            id, source_type, source_external_id, title,
            problem, solution, tier, confidence, source_authority,
            final_score, validation_evidence,
            ts_rank(
                to_tsvector('portuguese', COALESCE(title,'') || ' ' || COALESCE(problem,'') || ' ' || COALESCE(solution,'')),
                plainto_tsquery('portuguese', %s)
            ) AS similarity
        FROM ticket_knowledge_nodes
        WHERE to_tsvector('portuguese', COALESCE(title,'') || ' ' || COALESCE(problem,'') || ' ' || COALESCE(solution,''))
              @@ plainto_tsquery('portuguese', %s)
        ORDER BY similarity DESC
        LIMIT {top_k};
    """
    return conn.execute(sql, (query_text, query_text)).fetchall()


def hybrid_search(conn, query_text: str, embedding: list[float], top_k: int = 5) -> list[dict]:
    """
    Busca Híbrida: combina FTS lexical + similaridade semântica + autoridade da fonte.
    Score Final = (0.4 * semantic_score) + (0.3 * fts_score) + (0.3 * authority_score)
    """
    vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
    sql = f"""
        WITH semantic AS (
            SELECT
                id,
                1 - (embedding <=> '{vec_str}'::vector) AS semantic_score
            FROM ticket_knowledge_nodes
            WHERE embedding_generated = TRUE
        ),
        lexical AS (
            SELECT
                id,
                LEAST(1.0, ts_rank(
                    to_tsvector('portuguese', COALESCE(title,'') || ' ' || COALESCE(problem,'') || ' ' || COALESCE(solution,'')),
                    plainto_tsquery('portuguese', %s)
                ) * 10.0) AS fts_score
            FROM ticket_knowledge_nodes
        )
        SELECT
            kn.id,
            kn.source_type,
            kn.source_external_id,
            kn.title,
            kn.problem,
            kn.solution,
            kn.tier,
            kn.confidence,
            kn.source_authority,
            kn.final_score,
            kn.validation_evidence,
            ROUND(CAST(
                (COALESCE(s.semantic_score, 0) * 0.50) +
                (COALESCE(l.fts_score, 0) * 0.20) +
                ((kn.source_authority / 100.0) * 0.30)
            AS NUMERIC), 4) AS similarity
        FROM ticket_knowledge_nodes kn
        LEFT JOIN semantic s ON s.id = kn.id
        LEFT JOIN lexical l ON l.id = kn.id
        WHERE kn.embedding_generated = TRUE
        ORDER BY similarity DESC
        LIMIT {top_k};
    """
    return conn.execute(sql, (query_text,)).fetchall()


def display_results(results: list[dict], search_type: str, query: str, elapsed: float):
    """Exibe os resultados de busca formatados."""
    console.print()
    console.rule(f"[bold cyan]{search_type} — Query: \"{query}\" ({elapsed*1000:.0f}ms)")

    if not results:
        console.print("  [yellow]⚠️ Nenhum resultado encontrado.[/]")
        return

    for i, r in enumerate(results, 1):
        source_label = SOURCE_LABEL.get(r["source_type"], r["source_type"])
        tier_emoji = TIER_EMOJI.get(r["tier"], "❓")
        conf_color = CONFIDENCE_COLOR.get(r["confidence"], "white")
        similarity = float(r["similarity"])
        ext_id = r.get("source_external_id")
        ref = f"#{ext_id}" if ext_id else r.get("source_type", "KB")

        evidence = r.get("validation_evidence") or []
        ev_str = ", ".join(evidence) if evidence else "—"

        title = r.get("title") or "Sem título"
        problem = (r.get("problem") or "")[:200]
        solution = (r.get("solution") or "")[:300]

        content = (
            f"[dim]{source_label}[/] | [{conf_color}]{r['confidence']}[/] | "
            f"Autoridade: {r['source_authority']} | Score: {r['final_score']} | "
            f"Evidência: {ev_str}\n\n"
            f"[bold yellow]Problema:[/]\n{problem}{'...' if len(problem)==200 else ''}\n\n"
            f"[bold green]Solução:[/]\n{solution}{'...' if len(solution)==300 else ''}"
        )

        similarity_bar = "█" * int(similarity * 20) + "░" * (20 - int(similarity * 20))
        panel_title = f"#{i} {tier_emoji} {ref} — {title[:55]} | [{similarity_bar}] {similarity:.3f}"

        border = "green" if similarity > 0.75 else ("yellow" if similarity > 0.60 else "red")
        console.print(Panel(content, title=panel_title, border_style=border))


def run_test_suite(conn):
    """Executa a suíte completa de consultas de validação."""
    test_queries = [
        "erro ao emitir nota fiscal NF-e rejeição",
        "produto não sincroniza estoque Anymarket",
        "configurar boleto bancário CNAB remessa",
        "erro integração mercado livre pedido",
        "como configurar certificado digital A1",
        "Dynamic SQL error coluna desconhecida banco Firebird",
    ]

    console.print(Panel.fit(
        "[bold white on blue] DeskCortex — Validação de Busca Semântica (POC 60 nodes) [/]",
        border_style="blue"
    ))

    for query in test_queries:
        console.print(f"\n[bold white]⏳ Gerando embedding para query:[/] \"{query}\"")
        t0 = time.perf_counter()
        embedding = get_query_embedding(query)
        embed_time = time.perf_counter() - t0

        if not embedding:
            console.print("[red]❌ Falha ao gerar embedding. Pulando query.[/]")
            continue

        console.print(f"  ✅ Embedding gerado em {embed_time*1000:.0f}ms (3072 dims)")
        time.sleep(0.7)  # Rate limiting

        # Busca Híbrida (o método principal)
        t0 = time.perf_counter()
        results = hybrid_search(conn, query, embedding, top_k=3)
        hybrid_time = time.perf_counter() - t0
        display_results(results, "🔀 BUSCA HÍBRIDA", query, hybrid_time)

    console.print("\n")
    console.rule("[bold green]✅ Validação POC Concluída!")
    console.print("\n[bold]Interpretação dos Scores:[/]")
    console.print("  [bold green]> 0.75[/] → Alta relevância semântica (excelente!)")
    console.print("  [bold yellow]0.60–0.75[/] → Relevância moderada")
    console.print("  [bold red]< 0.60[/] → Baixa relevância (revisar scoring ou dados)")


def main():
    parser = argparse.ArgumentParser(description="DeskCortex — Teste de Busca Semântica")
    parser.add_argument("--query", type=str, help="Query específica para testar")
    parser.add_argument("--top", type=int, default=3, help="Número de resultados")
    args = parser.parse_args()

    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as conn:
        if args.query:
            embedding = get_query_embedding(args.query)
            if embedding:
                results = hybrid_search(conn, args.query, embedding, args.top)
                display_results(results, "🔀 BUSCA HÍBRIDA", args.query, 0)
        else:
            run_test_suite(conn)


if __name__ == "__main__":
    main()
