"""
DeskCortex -- Migracao: Colunas para Manuais Internos (Fase 2)
==============================================================
Adiciona colunas e indices necessarios para source_type=MANUAL.
Execucao idempotente -- pode rodar multiplas vezes sem erro.

Uso:
    python migrate_manual_columns.py
    python migrate_manual_columns.py --check
"""

import argparse, os, sys
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

sys.path.insert(0, str(Path(__file__).parent))
from config import POSTGRES_DSN

console = Console(force_terminal=True)

MIGRATIONS = [
    ("Coluna media_path",
     "ALTER TABLE ticket_knowledge_nodes ADD COLUMN IF NOT EXISTS media_path TEXT;"),
    ("Coluna media_type",
     "ALTER TABLE ticket_knowledge_nodes ADD COLUMN IF NOT EXISTS media_type TEXT;"),
    ("Coluna manual_category",
     "ALTER TABLE ticket_knowledge_nodes ADD COLUMN IF NOT EXISTS manual_category TEXT;"),
    ("Coluna shortcut_keys",
     "ALTER TABLE ticket_knowledge_nodes ADD COLUMN IF NOT EXISTS shortcut_keys TEXT[];"),
    ("Coluna screen_path",
     "ALTER TABLE ticket_knowledge_nodes ADD COLUMN IF NOT EXISTS screen_path TEXT;"),
    ("Coluna media_metadata",
     "ALTER TABLE ticket_knowledge_nodes ADD COLUMN IF NOT EXISTS media_metadata JSONB;"),
    ("Indice B-tree manual_category",
     "CREATE INDEX IF NOT EXISTS idx_tkn_manual_category ON ticket_knowledge_nodes (manual_category) WHERE manual_category IS NOT NULL;"),
    ("Indice GIN media_metadata",
     "CREATE INDEX IF NOT EXISTS idx_tkn_media_metadata ON ticket_knowledge_nodes USING GIN (media_metadata) WHERE media_metadata IS NOT NULL;"),
    ("Indice GIN shortcut_keys",
     "CREATE INDEX IF NOT EXISTS idx_tkn_shortcut_keys ON ticket_knowledge_nodes USING GIN (shortcut_keys) WHERE shortcut_keys IS NOT NULL;"),
    ("Indice partial source_type=MANUAL",
     "CREATE INDEX IF NOT EXISTS idx_tkn_source_manual ON ticket_knowledge_nodes (source_type) WHERE source_type = 'MANUAL';"),
    ("Indice composto category+authority",
     "CREATE INDEX IF NOT EXISTS idx_tkn_category_authority ON ticket_knowledge_nodes (manual_category, source_authority DESC) WHERE source_type = 'MANUAL';"),
    ("Constraint source_type permite MANUAL",
     "ALTER TABLE ticket_knowledge_nodes DROP CONSTRAINT IF EXISTS ticket_knowledge_nodes_source_type_check; "
     "ALTER TABLE ticket_knowledge_nodes ADD CONSTRAINT ticket_knowledge_nodes_source_type_check CHECK (source_type IN ('OFFICIAL_KB', 'VALIDATED_TICKET', 'PROBABLE_TICKET', 'INVESTIGATIVE', 'MANUAL'));"),
    ("Constraint tier permite MANUAL",
     "ALTER TABLE ticket_knowledge_nodes DROP CONSTRAINT IF EXISTS ticket_knowledge_nodes_tier_check; "
     "ALTER TABLE ticket_knowledge_nodes ADD CONSTRAINT ticket_knowledge_nodes_tier_check CHECK (tier IN ('GOLD', 'SILVER', 'KB', 'MANUAL'));"),
]

NEW_COLS = ["media_path", "media_type", "manual_category", "shortcut_keys", "screen_path", "media_metadata"]
NEW_IDX  = ["idx_tkn_manual_category","idx_tkn_media_metadata","idx_tkn_shortcut_keys",
            "idx_tkn_source_manual","idx_tkn_category_authority"]


def get_columns(conn):
    with conn.cursor(row_factory=dict_row) as c:
        c.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='ticket_knowledge_nodes' ORDER BY ordinal_position;")
        return {r["column_name"]: r["data_type"] for r in c.fetchall()}

def get_indexes(conn):
    with conn.cursor() as c:
        c.execute("SELECT indexname FROM pg_indexes WHERE tablename='ticket_knowledge_nodes';")
        return {r[0] for r in c.fetchall()}

def get_counts(conn):
    with conn.cursor(row_factory=dict_row) as c:
        c.execute("SELECT source_type, COUNT(*) total FROM ticket_knowledge_nodes GROUP BY 1 ORDER BY 2 DESC;")
        return {r["source_type"]: r["total"] for r in c.fetchall()}

def print_status(conn):
    cols = get_columns(conn)
    idxs = get_indexes(conn)
    counts = get_counts(conn)

    t = Table(title="Colunas Fase 2", show_header=True, header_style="bold cyan")
    t.add_column("Coluna"); t.add_column("Tipo"); t.add_column("Status", justify="center")
    for col in NEW_COLS:
        ok = "[green]OK[/green]" if col in cols else "[red]MISSING[/red]"
        t.add_row(col, cols.get(col,"--"), ok)
    console.print(t)

    t2 = Table(title="Indices Fase 2", show_header=True, header_style="bold cyan")
    t2.add_column("Indice"); t2.add_column("Status", justify="center")
    for idx in NEW_IDX:
        ok = "[green]OK[/green]" if idx in idxs else "[red]MISSING[/red]"
        t2.add_row(idx, ok)
    console.print(t2)

    authority = {"MANUAL":"100 (max)","KB":"90","GOLD":"85","SILVER":"70"}
    t3 = Table(title="Conhecimentos no Banco", header_style="bold cyan")
    t3.add_column("source_type"); t3.add_column("Total", justify="right"); t3.add_column("Autoridade")
    for src, tot in counts.items():
        t3.add_row(src, f"{tot:,}", authority.get(src,"--"))
    console.print(t3)

def run_migrations(conn, dry_run=False):
    ok = fail = 0
    for name, sql in MIGRATIONS:
        try:
            if dry_run:
                console.print(f"  [dim][DRY RUN][/dim] {name}")
            else:
                with conn.cursor() as c:
                    c.execute(sql)
                conn.commit()
                console.print(f"  [green]OK[/green] {name}")
            ok += 1
        except Exception as e:
            conn.rollback()
            console.print(f"  [red]FAIL[/red] {name}: {e}")
            fail += 1
    return ok, fail

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Apenas verifica, sem alterar")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    console.print(Panel("[bold cyan]DeskCortex -- Migracao Fase 2[/bold cyan]\nAdicionando colunas para Manuais Internos (source_type='MANUAL')", border_style="cyan"))

    try:
        with psycopg.connect(POSTGRES_DSN) as conn:
            if args.check:
                print_status(conn)
                return
            ok, fail = run_migrations(conn, dry_run=args.dry_run)
            console.print(f"\n[bold]Resultado: [green]{ok} OK[/green] / [red]{fail} falhas[/red][/bold]")
            if not args.dry_run:
                print_status(conn)
            if fail == 0:
                console.print(Panel("[bold green]Migracao concluida! Proximo: python ingest_manuals.py --dry-run --limit 5[/bold green]", border_style="green"))
            else:
                sys.exit(1)
    except psycopg.OperationalError as e:
        console.print(f"[red]Erro de conexao: {e}[/red]")
        console.print("[dim]Verifique: docker ps | grep postgres[/dim]")
        sys.exit(1)

if __name__ == "__main__":
    main()
