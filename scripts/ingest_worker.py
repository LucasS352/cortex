"""
DeskCortex — Worker de Ingestão Contínua de Tickets Movidesk
==============================================================
Pipeline ELT que extrai tickets da API do Movidesk e grava o JSON bruto
na tabela raw_tickets (PostgreSQL + JSONB).

Características:
  - Extração dual: /tickets/past (>90d) → /tickets (<90d) → incremental
  - Rate limiting adaptativo: comercial (9 req/min) vs turbo noturno (multi-worker async)
  - Checkpoints no PostgreSQL: retomada exata sem duplicidade
  - UPSERT idempotente (ON CONFLICT)
  - Retry com backoff exponencial
  - Modo turbo com asyncio e semáforo de concorrência

Uso:
    # Teste com 5 páginas
    python ingest_worker.py --max-pages 5

    # Ingestão completa (histórico + recente)
    python ingest_worker.py

    # Forçar modo turbo (ignorar detecção horária)
    python ingest_worker.py --force-turbo

    # Forçar modo comercial (safe)
    python ingest_worker.py --force-safe

    # Resetar checkpoints e recomeçar do zero
    python ingest_worker.py --reset
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Forçar UTF-8 no console Windows para suporte a emojis
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import httpx
import psycopg
from psycopg.rows import dict_row
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    MOVIDESK_TOKEN,
    MOVIDESK_BASE_URL,
    ODATA_SELECT_FIELDS,
    ODATA_EXPAND,
    ODATA_TOP,
    POSTGRES_DSN,
    MAX_RETRIES,
    BACKOFF_BASE_SECONDS,
    BACKOFF_MAX_SECONDS,
    get_rate_config,
    is_turbo_window,
    RateConfig,
    RATE_COMERCIAL,
    RATE_TURBO,
)

console = Console(force_terminal=True)

# ---------------------------------------------------------------------------
# Estatísticas globais
# ---------------------------------------------------------------------------
class Stats:
    def __init__(self):
        self.total_tickets = 0
        self.total_pages = 0
        self.total_requests = 0
        self.total_errors = 0
        self.total_429s = 0
        self.total_upserted = 0
        self.total_duplicates = 0
        self.start_time = time.perf_counter()
        self.current_endpoint = ""
        self.current_skip = 0
        self.last_ticket_id = 0

    @property
    def elapsed_seconds(self) -> float:
        return time.perf_counter() - self.start_time

    @property
    def tickets_per_minute(self) -> float:
        elapsed = self.elapsed_seconds
        if elapsed < 1:
            return 0
        return (self.total_tickets / elapsed) * 60

    def summary(self) -> str:
        elapsed = self.elapsed_seconds
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)
        return (
            f"📊 Resumo: {self.total_tickets:,} tickets | "
            f"{self.total_pages} páginas | "
            f"{self.total_requests} requests | "
            f"{self.total_errors} erros | "
            f"{self.total_429s} rate-limits | "
            f"⏱ {minutes}m{seconds}s | "
            f"🚀 {self.tickets_per_minute:.0f} tickets/min"
        )

stats = Stats()


# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------
def get_db_connection() -> psycopg.Connection:
    """Cria conexão com PostgreSQL."""
    return psycopg.connect(POSTGRES_DSN, row_factory=dict_row)


def get_connector(conn: psycopg.Connection) -> dict:
    """Busca o conector Movidesk da Alternativa."""
    row = conn.execute("""
        SELECT c.id, c.tenant_id, c.sync_status, c.sync_phase,
               c.checkpoint_endpoint, c.checkpoint_skip,
               c.last_synced_ticket_id, c.total_tickets_ingested
        FROM connectors c
        JOIN tenants t ON c.tenant_id = t.id
        WHERE t.slug = 'alternativa' AND c.provider = 'movidesk'
        LIMIT 1
    """).fetchone()
    if not row:
        console.print("[bold red]❌ Conector Movidesk não encontrado! Execute o init.sql primeiro.[/]")
        sys.exit(1)
    return dict(row)


def update_checkpoint(conn: psycopg.Connection, connector_id: str,
                       endpoint: str, skip: int, last_ticket_id: int,
                       total_ingested: int, phase: str = None,
                       status: str = "in_progress", error: str = None):
    """Atualiza o checkpoint de paginação no banco."""
    conn.execute("""
        UPDATE connectors SET
            sync_status = %(status)s,
            sync_phase = COALESCE(%(phase)s, sync_phase),
            checkpoint_endpoint = %(endpoint)s,
            checkpoint_skip = %(skip)s,
            last_synced_ticket_id = %(last_ticket_id)s,
            total_tickets_ingested = %(total_ingested)s,
            last_synced_at = NOW(),
            last_error_message = %(error)s,
            updated_at = NOW()
        WHERE id = %(connector_id)s
    """, {
        "connector_id": connector_id,
        "status": status,
        "phase": phase,
        "endpoint": endpoint,
        "skip": skip,
        "last_ticket_id": last_ticket_id,
        "total_ingested": total_ingested,
        "error": error,
    })
    conn.commit()


def upsert_tickets(conn: psycopg.Connection, tenant_id: str,
                    tickets: list[dict]) -> int:
    """Insere tickets no raw_tickets com UPSERT (ON CONFLICT atualiza)."""
    if not tickets:
        return 0

    upserted = 0
    for ticket in tickets:
        external_id = ticket.get("id")
        if external_id is None:
            continue

        created_date = ticket.get("createdDate")

        conn.execute("""
            INSERT INTO raw_tickets (tenant_id, external_id, payload, created_date)
            VALUES (%(tenant_id)s, %(external_id)s, %(payload)s, %(created_date)s)
            ON CONFLICT (tenant_id, source_provider, external_id)
            DO UPDATE SET
                payload = EXCLUDED.payload,
                ingested_at = NOW()
        """, {
            "tenant_id": tenant_id,
            "external_id": external_id,
            "payload": json.dumps(ticket, ensure_ascii=False, default=str),
            "created_date": created_date,
        })
        upserted += 1

    conn.commit()
    return upserted


def log_ingestion(conn: psycopg.Connection, connector_id: str,
                   endpoint: str, skip: int, tickets_fetched: int,
                   status: str, http_status: int = None,
                   error_message: str = None, duration_ms: int = 0):
    """Registra cada request no log de auditoria."""
    conn.execute("""
        INSERT INTO ingestion_log
            (connector_id, endpoint, page_skip, tickets_fetched, status, http_status, error_message, duration_ms)
        VALUES (%(connector_id)s, %(endpoint)s, %(skip)s, %(tickets_fetched)s,
                %(status)s, %(http_status)s, %(error_message)s, %(duration_ms)s)
    """, {
        "connector_id": connector_id,
        "endpoint": endpoint,
        "skip": skip,
        "tickets_fetched": tickets_fetched,
        "status": status,
        "http_status": http_status,
        "error_message": error_message,
        "duration_ms": duration_ms,
    })
    conn.commit()


def reset_checkpoint(conn: psycopg.Connection, connector_id: str):
    """Reseta o checkpoint para recomeçar do zero."""
    conn.execute("""
        UPDATE connectors SET
            sync_status = 'idle',
            sync_phase = 'past',
            checkpoint_endpoint = '/tickets/past',
            checkpoint_skip = 0,
            last_synced_ticket_id = 0,
            total_tickets_ingested = 0,
            last_error_message = NULL,
            updated_at = NOW()
        WHERE id = %(connector_id)s
    """, {"connector_id": connector_id})
    conn.commit()
    console.print("[bold yellow]🔄 Checkpoint resetado para o início![/]")


# ---------------------------------------------------------------------------
def build_ticket_url(endpoint: str, skip: int = 0, after_id: int = 0) -> str:
    """Constrói URL OData completa para uma página de tickets com cursor opcional."""
    url = (
        f"{MOVIDESK_BASE_URL}{endpoint}"
        f"?token={MOVIDESK_TOKEN}"
        f"&$top={ODATA_TOP}"
        f"&$select={ODATA_SELECT_FIELDS}"
        f"&$expand={ODATA_EXPAND}"
        f"&$orderby=id asc"
    )
    if after_id > 0:
        url += f"&$filter=id gt {after_id}"
    elif skip > 0:
        url += f"&$skip={skip}"
    return url


def fetch_page_sync(client: httpx.Client, endpoint: str, skip: int = 0,
                    after_id: int = 0) -> tuple[list[dict], int]:
    """Faz request síncrono de uma página de tickets via cursor ou skip."""
    url = build_ticket_url(endpoint, skip, after_id)
    start = time.perf_counter()

    for attempt in range(MAX_RETRIES):
        try:
            resp = client.get(url)
            duration_ms = int((time.perf_counter() - start) * 1000)

            if resp.status_code == 200:
                data = resp.json()
                tickets = data if isinstance(data, list) else []
                return tickets, resp.status_code

            elif resp.status_code == 429:
                stats.total_429s += 1
                retry_after = int(resp.headers.get("Retry-After", "60"))
                console.print(f"  ⏳ Rate limited (429)! Aguardando {retry_after}s... "
                               f"(tentativa {attempt + 1}/{MAX_RETRIES})")
                time.sleep(retry_after + 1)
                continue

            elif resp.status_code >= 500:
                delay = min(BACKOFF_BASE_SECONDS * (2 ** attempt), BACKOFF_MAX_SECONDS)
                console.print(f"  ⚠️ Erro {resp.status_code} do servidor. "
                               f"Retry em {delay}s (tentativa {attempt + 1}/{MAX_RETRIES})")
                time.sleep(delay)
                continue

            else:
                console.print(f"  ❌ Erro {resp.status_code}: {resp.text[:200]}")
                return [], resp.status_code

        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as e:
            delay = min(BACKOFF_BASE_SECONDS * (2 ** attempt), BACKOFF_MAX_SECONDS)
            console.print(f"  🔌 Erro de rede: {e}. Retry em {delay}s "
                           f"(tentativa {attempt + 1}/{MAX_RETRIES})")
            time.sleep(delay)
            continue

    console.print(f"  ❌ Máximo de retries ({MAX_RETRIES}) atingido para {endpoint}")
    return [], 0


async def fetch_page_async(client: httpx.AsyncClient, endpoint: str,
                            skip: int = 0, after_id: int = 0,
                            semaphore: asyncio.Semaphore = None) -> tuple[list[dict], int]:
    """Faz request assíncrono de uma página de tickets via cursor."""
    url = build_ticket_url(endpoint, skip, after_id)

    for attempt in range(MAX_RETRIES):
        async with (semaphore if semaphore else asyncio.Semaphore(1)):
            try:
                resp = await client.get(url)

                if resp.status_code == 200:
                    data = resp.json()
                    tickets = data if isinstance(data, list) else []
                    return tickets, resp.status_code

                elif resp.status_code == 429:
                    stats.total_429s += 1
                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    console.print(f"  ⏳ Rate limited! Aguardando {retry_after}s...")
                    await asyncio.sleep(retry_after + 1)
                    continue

                elif resp.status_code >= 500:
                    delay = min(BACKOFF_BASE_SECONDS * (2 ** attempt), BACKOFF_MAX_SECONDS)
                    await asyncio.sleep(delay)
                    continue

                else:
                    return [], resp.status_code

            except (httpx.ConnectError, httpx.ReadTimeout) as e:
                delay = min(BACKOFF_BASE_SECONDS * (2 ** attempt), BACKOFF_MAX_SECONDS)
                await asyncio.sleep(delay)
                continue

    return [], 0


# ---------------------------------------------------------------------------
# Ingestão Síncrona (Modo Comercial)
# ---------------------------------------------------------------------------
def ingest_cursor(endpoint: str, connector: dict, max_pages: int = 0,
                  rate_config: RateConfig = None) -> str:
    """Ingestão contínua com cursor-based pagination (id gt {after_id})."""
    if rate_config is None:
        rate_config = get_rate_config()

    connector_id = str(connector["id"])
    tenant_id = str(connector["tenant_id"])
    after_id = connector.get("last_synced_ticket_id", 0)

    # Se o checkpoint está em outro endpoint, resetar cursor
    if connector.get("checkpoint_endpoint") != endpoint:
        after_id = 0

    page_count = 0

    console.print(f"\n🚀 Iniciando ingestão CURSOR ({rate_config.mode})")
    console.print(f"   Endpoint: {endpoint}")
    console.print(f"   Cursor inicial: #{after_id}")
    if max_pages > 0:
        console.print(f"   Limite de páginas: {max_pages}")

    with (
        httpx.Client(timeout=30.0, follow_redirects=True) as client,
        get_db_connection() as conn,
    ):
        update_checkpoint(conn, connector_id, endpoint, 0,
                          after_id,
                          connector.get("total_tickets_ingested", 0),
                          status="in_progress")

        while True:
            if max_pages > 0 and page_count >= max_pages:
                console.print(f"\n📋 Limite de {max_pages} páginas atingido. Parando.")
                break

            start = time.perf_counter()
            tickets, http_status = fetch_page_sync(client, endpoint, after_id=after_id)
            duration_ms = int((time.perf_counter() - start) * 1000)
            stats.total_requests += 1

            if not tickets:
                if http_status == 200:
                    console.print(f"\n✅ Endpoint {endpoint} esgotado (0 tickets após #{after_id})")
                    log_ingestion(conn, connector_id, endpoint, 0, 0,
                                  "completed", http_status, duration_ms=duration_ms)
                    break
                else:
                    stats.total_errors += 1
                    log_ingestion(conn, connector_id, endpoint, 0, 0,
                                  "error", http_status,
                                  f"HTTP {http_status}", duration_ms)
                    update_checkpoint(conn, connector_id, endpoint, 0,
                                      after_id, stats.total_tickets,
                                      status="error", error=f"HTTP {http_status} em #{after_id}")
                    break

            count = len(tickets)
            upserted = upsert_tickets(conn, tenant_id, tickets)

            page_count += 1
            stats.total_pages += 1
            stats.total_tickets += count
            stats.total_upserted += upserted
            prev_id = after_id
            after_id = max(t.get("id", 0) for t in tickets)
            stats.last_ticket_id = after_id

            log_ingestion(conn, connector_id, endpoint, 0, count,
                          "success", http_status, duration_ms=duration_ms)

            update_checkpoint(conn, connector_id, endpoint, 0,
                              after_id, stats.total_tickets)

            console.print(
                f"  📦 Página {page_count:>4} | "
                f"cursor=#{prev_id:>6} -> #{after_id:>6} | "
                f"{count:>3} tickets | "
                f"{duration_ms:>4}ms | "
                f"Total: {stats.total_tickets:,} | "
                f"{stats.tickets_per_minute:.0f} t/min"
            )

            if count < ODATA_TOP:
                console.print(f"\n✅ Endpoint {endpoint} esgotado ({count} < {ODATA_TOP} na última página)")
                break

            time.sleep(rate_config.delay_seconds)

        update_checkpoint(conn, connector_id, endpoint, 0,
                          after_id, stats.total_tickets,
                          status="completed")

    return "completed"


# ---------------------------------------------------------------------------
# Orquestrador principal
# ---------------------------------------------------------------------------
def run_ingestion(max_pages: int = 0, force_mode: str = None):
    """Orquestra a ingestão completa: /tickets/past → /tickets → incremental."""

    console.print("\n" + "=" * 70)
    console.print("[bold white on blue] DeskCortex — Worker de Ingestão Movidesk [/]")
    console.print("=" * 70)

    # Determinar rate config
    if force_mode == "turbo":
        rate_config = RATE_TURBO
        console.print("⚡ Modo TURBO forçado pelo usuário")
    elif force_mode == "safe":
        rate_config = RATE_COMERCIAL
        console.print("🏢 Modo COMERCIAL forçado pelo usuário")
    else:
        rate_config = get_rate_config()

    console.print(f"⏰ Hora atual: {datetime.now().strftime('%H:%M:%S')}")
    console.print(f"📡 Modo: {rate_config.mode.upper()} ({rate_config.max_concurrent} workers)")
    console.print(f"🔑 Token: {MOVIDESK_TOKEN[:8]}...{MOVIDESK_TOKEN[-4:]}")
    if max_pages > 0:
        console.print(f"📋 Limite: {max_pages} páginas ({max_pages * ODATA_TOP} tickets máx)")

    # Buscar estado do conector
    with get_db_connection() as conn:
        connector = get_connector(conn)

    console.print(f"\n📊 Estado atual do conector:")
    console.print(f"   Fase: {connector.get('sync_phase', 'past')}")
    console.print(f"   Endpoint: {connector.get('checkpoint_endpoint', '/tickets/past')}")
    console.print(f"   Skip: {connector.get('checkpoint_skip', 0)}")
    console.print(f"   Último ticket: #{connector.get('last_synced_ticket_id', 0)}")
    console.print(f"   Total ingerido: {connector.get('total_tickets_ingested', 0):,}")

    # Determinar fase e endpoint
    phase = connector.get("sync_phase", "past")
    endpoint = connector.get("checkpoint_endpoint", "/tickets/past")

    phases = []
    if phase == "past":
        phases = [("/tickets/past", "past"), ("/tickets", "recent")]
    elif phase == "recent":
        phases = [("/tickets", "recent")]
    elif phase == "incremental":
        phases = [("/tickets", "incremental")]
    else:
        phases = [("/tickets/past", "past"), ("/tickets", "recent")]

    for ep, ph in phases:
        console.print(f"\n{'=' * 50}")
        console.print(f"🔄 Fase: {ph.upper()} — Endpoint: {ep}")
        console.print(f"{'=' * 50}")

        # Atualizar conector para a fase atual
        with get_db_connection() as conn:
            connector = get_connector(conn)
            # Se estamos mudando de fase, resetar skip
            if connector.get("checkpoint_endpoint") != ep:
                connector["checkpoint_skip"] = 0
                connector["checkpoint_endpoint"] = ep

        # Executar ingestão via cursor
        result = ingest_cursor(ep, connector, max_pages, rate_config)

        # Avançar para próxima fase
        if result == "completed" and ph == "past":
            with get_db_connection() as conn:
                update_checkpoint(conn, str(connector["id"]), "/tickets", 0,
                                  0, stats.total_tickets,
                                  phase="recent", status="idle")

    # Resumo final
    console.print("\n" + "=" * 70)
    console.print("[bold green]" + stats.summary() + "[/]")
    console.print("=" * 70)

    # Verificação final no banco
    with get_db_connection() as conn:
        row = conn.execute("""
            SELECT COUNT(*) as total,
                   MIN(external_id) as min_id,
                   MAX(external_id) as max_id,
                   MIN(ingested_at) as first_ingested,
                   MAX(ingested_at) as last_ingested
            FROM raw_tickets
        """).fetchone()
        console.print(f"\n📊 Estado do banco raw_tickets:")
        console.print(f"   Total de tickets: {row['total']:,}")
        console.print(f"   Range de IDs: #{row['min_id']} – #{row['max_id']}")
        console.print(f"   Primeira ingestão: {row['first_ingested']}")
        console.print(f"   Última ingestão: {row['last_ingested']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="DeskCortex — Worker de Ingestão Contínua de Tickets Movidesk"
    )
    parser.add_argument(
        "--max-pages", type=int, default=0,
        help="Número máximo de páginas a buscar (0 = ilimitado)"
    )
    parser.add_argument(
        "--force-turbo", action="store_true",
        help="Forçar modo turbo (ignorar detecção horária)"
    )
    parser.add_argument(
        "--force-safe", action="store_true",
        help="Forçar modo comercial seguro (1 worker)"
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Resetar checkpoints e recomeçar do zero"
    )

    args = parser.parse_args()

    if not MOVIDESK_TOKEN:
        console.print("[bold red]❌ MOVIDESK_TOKEN não configurado no .env![/]")
        sys.exit(1)

    # Reset de checkpoints
    if args.reset:
        with get_db_connection() as conn:
            connector = get_connector(conn)
            reset_checkpoint(conn, str(connector["id"]))

    # Determinar modo
    force_mode = None
    if args.force_turbo:
        force_mode = "turbo"
    elif args.force_safe:
        force_mode = "safe"

    try:
        run_ingestion(max_pages=args.max_pages, force_mode=force_mode)
    except KeyboardInterrupt:
        console.print("\n\n⏹ Interrompido pelo usuário. Checkpoint salvo!")
        console.print(stats.summary())
    except Exception as e:
        console.print(f"\n❌ Erro fatal: {e}")
        # Salvar checkpoint de emergência
        try:
            with get_db_connection() as conn:
                connector = get_connector(conn)
                update_checkpoint(
                    conn, str(connector["id"]),
                    connector.get("checkpoint_endpoint", "/tickets/past"),
                    connector.get("checkpoint_skip", 0),
                    stats.last_ticket_id,
                    stats.total_tickets,
                    status="error",
                    error=str(e)[:500],
                )
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
