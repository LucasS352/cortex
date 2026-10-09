"""Incrementally refresh current Movidesk tickets without promoting them to knowledge.

Fetches tickets created after the last stored ID, then refreshes older open tickets.
Each API page is committed atomically to raw_tickets and refined_tickets, so the
command can be safely rerun after interruption. No customer replies are sent.
"""

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row
from psycopg import sql

sys.path.insert(0, str(Path(__file__).parent))
from config import MOVIDESK_BASE_URL, MOVIDESK_TOKEN, ODATA_SELECT_FIELDS, POSTGRES_DSN
from refine_tickets_v2 import process_ticket_v2


PAGE_SIZE = 100
RETRYABLE_HTTP = {429, 500, 502, 503, 504}
EXPAND = "actions($expand=attachments),clients($expand=organization),owner,customFieldValues($expand=items)"
JSON_COLUMNS = {"detected_modules", "detected_error_codes", "evidence_data"}
REFINED_COLUMNS = (
    "tenant_id", "raw_ticket_id", "external_id", "subject", "service_full",
    "service_first_level", "category", "urgency", "status", "base_status",
    "client_business_name", "client_organization_name", "client_email",
    "created_date", "resolved_date", "closed_date", "total_actions",
    "agent_actions_count", "client_actions_count", "problem_statement",
    "technical_narrative", "solution_statement", "detected_modules",
    "detected_error_codes", "technical_score", "solution_score",
    "validation_score", "noise_score", "final_score", "quality_tier",
    "evidence_level", "hard_block_reason", "is_blocked_gold",
    "evidence_data", "discard_reason",
)


def refined_sql():
    columns = sql.SQL(", ").join(map(sql.Identifier, REFINED_COLUMNS))
    values = sql.SQL(", ").join(
        sql.SQL("%s::jsonb") if name in JSON_COLUMNS else sql.SQL("%s")
        for name in REFINED_COLUMNS
    )
    updates = sql.SQL(", ").join(
        sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(name), sql.Identifier(name))
        for name in REFINED_COLUMNS if name not in ("tenant_id", "external_id")
    )
    return sql.SQL(
        "INSERT INTO refined_tickets ({}) VALUES ({}) "
        "ON CONFLICT (tenant_id, external_id) DO UPDATE SET {}, refined_at=NOW()"
    ).format(columns, values, updates)


def fetch_page(client, filter_text, descending, past=False, lightweight=False):
    params = {
        "token": MOVIDESK_TOKEN,
        "$top": PAGE_SIZE,
        "$select": "id,status,baseStatus,resolvedIn,closedIn,reopenedIn,lastUpdate" if lightweight else ODATA_SELECT_FIELDS,
        "$filter": filter_text,
        "$orderby": "id desc" if descending else "id asc",
    }
    if not lightweight:
        params["$expand"] = EXPAND
    response = request_with_retry(client, MOVIDESK_BASE_URL + ("/tickets/past" if past else "/tickets"), params)
    if response.status_code >= 400:
        raise RuntimeError(f"Movidesk list request failed with HTTP {response.status_code}")
    data = response.json()
    if not isinstance(data, list):
        raise ValueError("Expected an array of tickets from Movidesk")
    return data


def request_with_retry(client, url, params, attempts=4):
    for attempt in range(attempts):
        try:
            response = client.get(url, params=params)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt == attempts - 1:
                raise
            reason = type(exc).__name__
        else:
            if response.status_code not in RETRYABLE_HTTP or attempt == attempts - 1:
                return response
            reason = f"HTTP {response.status_code}"
        wait = min(40, 5 * 2 ** attempt)
        print(f"Movidesk {reason}; tentativa {attempt + 2}/{attempts} em {wait}s", flush=True)
        time.sleep(wait)


def fetch_ticket_by_id(client, ticket_id):
    """Refresh a ticket that left the open snapshot, including its terminal dates."""
    params = {"token": MOVIDESK_TOKEN, "id": ticket_id,
              "$select": ODATA_SELECT_FIELDS, "$expand": EXPAND}
    response = request_with_retry(client, MOVIDESK_BASE_URL + "/tickets", params)
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise RuntimeError(f"Movidesk ticket {ticket_id} request failed with HTTP {response.status_code}")
    ticket = response.json()
    if not isinstance(ticket, dict) or str(ticket.get("id")) != str(ticket_id):
        raise RuntimeError(f"Ticket {ticket_id} não retornou dados válidos")
    ticket["id"] = ticket_id
    return ticket


def refresh_lifecycle(conn, tenant_id, ticket):
    """Update status dates without replacing the stored conversation with a list result."""
    ticket_id = int(ticket["id"])
    values = {key: ticket.get(key) for key in
              ("status", "baseStatus", "resolvedIn", "closedIn", "reopenedIn", "lastUpdate")}
    conn.execute("""
        UPDATE raw_tickets SET payload=payload || %s::jsonb, ingested_at=NOW()
        WHERE tenant_id=%s AND source_provider='movidesk' AND external_id=%s
    """, (json.dumps(values, ensure_ascii=False), tenant_id, ticket_id))
    conn.execute("""
        UPDATE refined_tickets SET status=%s, base_status=%s,
          resolved_date=%s, closed_date=%s, refined_at=NOW()
        WHERE tenant_id=%s AND external_id=%s
    """, (ticket.get("status"), ticket.get("baseStatus"), ticket.get("resolvedIn"),
          ticket.get("closedIn"), tenant_id, ticket_id))
    conn.commit()


def upsert_company_links(conn, tenant_id, ticket):
    """Keep the company-first retrieval index aligned with every synchronized ticket."""
    ticket_id = int(ticket["id"])
    organizations = {}
    for client in ticket.get("clients") or []:
        organization = client.get("organization") or {}
        company_id = organization.get("id")
        company_name = organization.get("businessName")
        if not company_id and str(client.get("personType")) == "2":
            company_id, company_name = client.get("id"), client.get("businessName")
        if company_id and company_name:
            organizations[str(company_id)] = str(company_name)
    conn.execute("DELETE FROM movidesk_company_tickets WHERE tenant_id=%s AND ticket_external_id=%s",
                 (tenant_id, ticket_id))
    for company_id, company_name in organizations.items():
        conn.execute("""INSERT INTO movidesk_company_directory
            (tenant_id,company_id,company_name,last_seen_at) VALUES(%s,%s,%s,now())
            ON CONFLICT (tenant_id,company_id) DO UPDATE
            SET company_name=EXCLUDED.company_name,last_seen_at=now()""",
            (tenant_id, company_id, company_name))
        conn.execute("""INSERT INTO movidesk_company_tickets
            (tenant_id,company_id,ticket_external_id) VALUES(%s,%s,%s)
            ON CONFLICT DO NOTHING""", (tenant_id, company_id, ticket_id))


def upsert_page(conn, tenant_id, run_id, tickets):
    statement = refined_sql()
    changed = 0
    tiers = Counter()
    for ticket in tickets:
        external_id = ticket.get("id")
        if not isinstance(external_id, int):
            continue
        payload = json.dumps(ticket, ensure_ascii=False)
        row = conn.execute("""
            INSERT INTO raw_tickets
                (tenant_id, source_provider, external_id, payload, created_date, processing_status)
            VALUES (%s, 'movidesk', %s, %s::jsonb, %s, 'done')
            ON CONFLICT (tenant_id, source_provider, external_id)
            DO UPDATE SET payload=EXCLUDED.payload, created_date=EXCLUDED.created_date,
                ingested_at=NOW(), processing_status='done', processed_at=NOW()
            RETURNING id
        """, (tenant_id, external_id, payload, ticket.get("createdDate"))).fetchone()
        record = process_ticket_v2(str(row["id"]), str(tenant_id), external_id, ticket)
        conn.execute(statement, tuple(record[name] for name in REFINED_COLUMNS))
        upsert_company_links(conn, tenant_id, ticket)
        if ticket.get("baseStatus") not in ("Resolved", "Closed", "Canceled"):
            dashboard_data = {
                "subject": record["subject"], "category": record["category"],
                "urgency": record["urgency"], "status": record["status"],
                "base_status": record["base_status"], "created_date": record["created_date"],
                "problem_statement": record["problem_statement"],
                "technical_narrative": record["technical_narrative"],
                "client_business_name": record["client_business_name"],
                "clients": ticket.get("clients") or [], "owner": ticket.get("owner"),
                "owner_team": ticket.get("ownerTeam"),
            }
            conn.execute("""
                INSERT INTO movidesk_open_snapshot (run_id, tenant_id, external_id, dashboard_data)
                VALUES (%s, %s, %s, %s::jsonb) ON CONFLICT DO NOTHING
            """, (run_id, tenant_id, external_id, json.dumps(dashboard_data, ensure_ascii=False, default=str)))
        changed += 1
        tiers[record["quality_tier"]] += 1
    conn.commit()
    return changed, tiers


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=0, help="Limit pages for a trial run")
    parser.add_argument("--older-open-before", type=int, default=0,
                        help="Original cutoff when resuming a partial run")
    args = parser.parse_args()
    if not MOVIDESK_TOKEN:
        raise SystemExit("MOVIDESK_TOKEN is missing")

    totals = Counter()
    pages = 0
    started = datetime.now(timezone.utc)
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as conn:
        if not conn.execute("SELECT pg_try_advisory_lock(20261006) AS acquired").fetchone()["acquired"]:
            raise RuntimeError("Outra sincronização de chamados já está em andamento")
        conn.execute((Path(__file__).parent.parent / "infra" / "002_current_ticket_snapshot.sql").read_text(encoding="utf-8"))
        conn.commit()
        tenant = conn.execute("SELECT id FROM tenants WHERE slug='alternativa'").fetchone()
        if tenant is None:
            raise SystemExit("Alternativa tenant is missing")
        tenant_id = tenant["id"]
        previous_max = conn.execute(
            "SELECT COALESCE(MAX(external_id),0) AS n FROM raw_tickets WHERE tenant_id=%s",
            (tenant_id,),
        ).fetchone()["n"]
        run_id = conn.execute("""
            INSERT INTO movidesk_sync_runs (tenant_id, started_at, status, previous_max_id)
            VALUES (%s, %s, 'running', %s) RETURNING id
        """, (tenant_id, started, previous_max)).fetchone()["id"]
        conn.commit()
        print(f"Started {started.isoformat()} | previous_max={previous_max}", flush=True)

        try:
            with httpx.Client(timeout=httpx.Timeout(35, connect=10), headers={"User-Agent": "DeskCortex/1.0"}) as client:
                for lane in ("new", "older_open", "older_past"):
                    cursor = previous_max if lane == "new" else (args.older_open_before or previous_max + 1)
                    while True:
                        if args.max_pages and pages >= args.max_pages:
                            break
                        if lane == "new":
                            filter_text = f"id gt {cursor}"
                        else:
                            filter_text = (
                                f"id lt {cursor} and baseStatus ne 'Resolved' "
                                "and baseStatus ne 'Closed' and baseStatus ne 'Canceled'"
                            )
                        tickets = fetch_page(client, filter_text, lane != "new", lane == "older_past")
                        if not tickets:
                            break
                        page_size = len(tickets)
                        ids = [t["id"] for t in tickets if isinstance(t.get("id"), int)]
                        if not ids:
                            raise RuntimeError("Page has no usable ticket IDs")
                        next_cursor = max(ids) if lane == "new" else min(ids)
                        if next_cursor == cursor:
                            raise RuntimeError("Cursor did not advance")
                        if lane == "older_past":
                            existing = {r["external_id"] for r in conn.execute(
                                "SELECT external_id FROM movidesk_open_snapshot "
                                "WHERE run_id=%s AND external_id=ANY(%s)", (run_id, ids)).fetchall()}
                            tickets = [t for t in tickets if t["id"] not in existing]
                        count, tiers = upsert_page(conn, tenant_id, run_id, tickets)
                        totals[lane] += count
                        conn.execute("UPDATE movidesk_sync_runs SET new_count=%s, older_open_count=%s WHERE id=%s",
                                     (totals['new'], totals['older_open'] + totals['older_past'], run_id))
                        conn.commit()
                        for tier, value in tiers.items():
                            totals[f"tier_{tier}"] += value
                        pages += 1
                        cursor = next_cursor
                        print(f"{lane} page={pages} records={count} cursor={cursor} tiers={dict(tiers)}", flush=True)
                        if page_size < PAGE_SIZE:
                            break
                        time.sleep(6.7)  # Respect the daytime API budget.
                    if args.max_pages and pages >= args.max_pages:
                        break

                # Reconcile every stored-open ticket absent from this run. Some
                # have closed; others were missed by the recent-only endpoint.
                if not args.max_pages:
                    missing = [r["external_id"] for r in conn.execute("""
                        SELECT r.external_id FROM refined_tickets r
                        LEFT JOIN movidesk_open_snapshot fresh
                          ON fresh.run_id=%s AND fresh.external_id=r.external_id
                        WHERE r.tenant_id=%s AND r.base_status NOT IN
                          ('Resolved','Closed','Canceled') AND fresh.external_id IS NULL
                        ORDER BY r.external_id DESC
                    """, (run_id, tenant_id)).fetchall()]
                    print(f"lifecycle reconciliation candidates={len(missing)}", flush=True)
                    for offset in range(0, len(missing), 10):
                        batch = missing[offset:offset + 10]
                        found = {}
                        for past in (False, True):
                            remaining = [ticket_id for ticket_id in batch if ticket_id not in found]
                            if not remaining:
                                break
                            time.sleep(6.7)
                            filt = " or ".join(f"id eq {ticket_id}" for ticket_id in remaining)
                            for ticket in fetch_page(client, filt, True, past, lightweight=True):
                                ticket_id = int(ticket["id"])
                                if ticket_id in remaining:
                                    found[ticket_id] = ticket
                        for ticket_id in batch:
                            ticket = found.get(ticket_id)
                            if ticket is None:
                                time.sleep(6.7)
                                ticket = fetch_ticket_by_id(client, ticket_id)
                            if ticket is None:
                                conn.execute("""INSERT INTO movidesk_sync_unavailable
                                    (run_id, tenant_id, external_id, reason)
                                    VALUES (%s,%s,%s,'404 on direct lookup after both list routes')
                                    ON CONFLICT (run_id, external_id) DO NOTHING""",
                                    (run_id, tenant_id, ticket_id))
                                conn.commit()
                                totals["unavailable"] += 1
                                totals["reconciled"] += 1
                                continue
                            if ticket.get("baseStatus") in ("Resolved", "Closed", "Canceled"):
                                refresh_lifecycle(conn, tenant_id, ticket)
                            else:
                                # Individual fetch retains clients/actions for an open ticket.
                                time.sleep(6.7)
                                full_ticket = fetch_ticket_by_id(client, ticket_id)
                                if full_ticket is None:
                                    conn.execute("""INSERT INTO movidesk_sync_unavailable
                                        (run_id, tenant_id, external_id, reason)
                                        VALUES (%s,%s,%s,'404 on direct lookup after list match')
                                        ON CONFLICT (run_id, external_id) DO NOTHING""",
                                        (run_id, tenant_id, ticket_id))
                                    conn.commit()
                                    totals["unavailable"] += 1
                                else:
                                    upsert_page(conn, tenant_id, run_id, [full_ticket])
                            totals["reconciled"] += 1
                        print(f"reconciled {min(offset+10,len(missing))}/{len(missing)}", flush=True)
                    unresolved = conn.execute("""
                        SELECT COUNT(*) AS n FROM refined_tickets r
                        LEFT JOIN movidesk_open_snapshot fresh
                          ON fresh.run_id=%s AND fresh.external_id=r.external_id
                        WHERE r.tenant_id=%s AND r.base_status NOT IN
                          ('Resolved','Closed','Canceled') AND fresh.external_id IS NULL
                          AND NOT EXISTS (SELECT 1 FROM movidesk_sync_unavailable u
                              WHERE u.run_id=%s AND u.external_id=r.external_id)
                    """, (run_id, tenant_id, run_id)).fetchone()["n"]
                    if unresolved:
                        raise RuntimeError(f"Reconciliação incompleta: {unresolved} tickets")

            final_max = conn.execute(
                "SELECT MAX(external_id) AS n FROM raw_tickets WHERE tenant_id=%s", (tenant_id,)
            ).fetchone()["n"]
            complete = args.max_pages == 0
            conn.execute("""
                UPDATE movidesk_sync_runs SET completed_at=NOW(), status=%s,
                    coverage_version=%s,
                    final_max_id=%s, new_count=%s, older_open_count=%s WHERE id=%s
            """, ('completed' if complete else 'partial', 2 if complete else 1, final_max,
                  totals['new'], totals['older_open'] + totals['older_past'], run_id))
            conn.commit()
            print(json.dumps({
                "started_at": started.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "previous_max": previous_max,
                "final_max": final_max,
                "pages": pages,
                "totals": totals,
                "complete": complete,
                "run_id": run_id,
                "knowledge_nodes_created": 0,
            }, ensure_ascii=False, default=dict), flush=True)
        except BaseException:
            conn.rollback()
            conn.execute("UPDATE movidesk_sync_runs SET status='failed', completed_at=NOW() "
                         "WHERE id=%s AND status='running'", (run_id,))
            conn.commit()
            raise


if __name__ == "__main__":
    main()
