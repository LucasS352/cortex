"""Daily ticket flow with explicit provenance and backlog snapshots."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg

from config import POSTGRES_DSN


ZONE = ZoneInfo("America/Sao_Paulo")


def _local_day(value):
    return value.astimezone(ZONE).date().isoformat()


def report(days: int = 30, ticket_type: str = "public") -> dict:
    if days not in (7, 30, 90):
        raise ValueError("Período permitido: 7, 30 ou 90 dias.")
    if ticket_type not in ("public", "internal", "all"):
        raise ValueError("Tipo de chamado inválido.")
    type_value = {"public": "2", "internal": "1", "all": None}[ticket_type]
    today = datetime.now(ZONE).date()
    first = today - timedelta(days=days - 1)
    start = datetime.combine(first, time.min, ZONE)
    end = datetime.combine(today + timedelta(days=1), time.min, ZONE)
    series = { (first + timedelta(days=i)).isoformat():
              {"date": (first + timedelta(days=i)).isoformat(),
               "entered": 0, "resolved": 0, "closed": 0, "reopened": 0,
               "backlog": None, "backlog_as_of": None}
              for i in range(days) }

    with psycopg.connect(POSTGRES_DSN, connect_timeout=5) as conn:
        for field, label in (("created_date", "entered"),
                             ("resolved_date", "resolved"),
                             ("closed_date", "closed")):
            rows = conn.execute(f"""
                SELECT (r.{field} AT TIME ZONE 'America/Sao_Paulo')::date AS day,
                       COUNT(*) AS n FROM refined_tickets r
                JOIN raw_tickets w ON w.id=r.raw_ticket_id
                WHERE r.{field} >= %s AND r.{field} < %s
                  AND (%s::text IS NULL OR w.payload->>'type'=%s)
                GROUP BY 1
            """, (start, end, type_value, type_value)).fetchall()
            for day, count in rows:
                series[day.isoformat()][label] = count

        # reopenedIn holds only the latest reopening, so this is explicitly a
        # lower bound rather than a complete lifecycle event history.
        reopened = conn.execute("""
            SELECT (NULLIF(w.payload->>'reopenedIn','')::timestamptz
                    AT TIME ZONE 'America/Sao_Paulo')::date AS day, COUNT(*)
            FROM raw_tickets w
            WHERE w.payload->>'reopenedIn' IS NOT NULL
              AND NULLIF(w.payload->>'reopenedIn','')::timestamptz >= %s
              AND NULLIF(w.payload->>'reopenedIn','')::timestamptz < %s
              AND (%s::text IS NULL OR w.payload->>'type'=%s)
            GROUP BY 1
        """, (start, end, type_value, type_value)).fetchall()
        for day, count in reopened:
            series[day.isoformat()]["reopened"] = count

        snapshots = conn.execute("""
            SELECT run.id, run.completed_at, COUNT(s.external_id) AS backlog
            FROM movidesk_sync_runs run
            JOIN movidesk_open_snapshot s ON s.run_id=run.id
            JOIN raw_tickets w ON w.tenant_id=s.tenant_id AND w.external_id=s.external_id
              AND w.source_provider='movidesk'
            WHERE run.status='completed' AND run.coverage_version>=2
              AND run.completed_at >= %s
              AND run.completed_at < %s
              AND (%s::text IS NULL OR w.payload->>'type'=%s)
            GROUP BY run.id,run.completed_at ORDER BY run.completed_at
        """, (start, end, type_value, type_value)).fetchall()
        for _, stamp, count in snapshots:
            day = _local_day(stamp)
            series[day]["backlog"] = count
            series[day]["backlog_as_of"] = stamp.isoformat()

        latest = conn.execute("""
            SELECT run.id,run.completed_at,COUNT(s.external_id)
            FROM movidesk_sync_runs run JOIN movidesk_open_snapshot s ON s.run_id=run.id
            JOIN raw_tickets w ON w.tenant_id=s.tenant_id AND w.external_id=s.external_id
              AND w.source_provider='movidesk'
            WHERE run.status='completed' AND run.coverage_version>=2
              AND (%s::text IS NULL OR w.payload->>'type'=%s)
            GROUP BY run.id,run.completed_at
            ORDER BY run.completed_at DESC,run.id DESC LIMIT 1
        """, (type_value, type_value)).fetchone()
        stale_open = conn.execute("""
            SELECT COUNT(*) FROM refined_tickets r
            JOIN raw_tickets w ON w.id=r.raw_ticket_id
            LEFT JOIN current_open_ticket_facts f ON f.external_id=r.external_id
            WHERE r.base_status NOT IN ('Resolved','Closed','Canceled')
              AND f.external_id IS NULL
              AND NOT EXISTS (SELECT 1 FROM movidesk_sync_unavailable u
                  WHERE u.run_id=%s AND u.external_id=r.external_id)
              AND (%s::text IS NULL OR w.payload->>'type'=%s)
        """, (latest[0] if latest else 0, type_value, type_value)).fetchone()[0]
        unavailable_open = conn.execute('''SELECT COUNT(*) FROM movidesk_sync_unavailable u
            JOIN raw_tickets w ON w.tenant_id=u.tenant_id AND w.external_id=u.external_id
                AND w.source_provider='movidesk'
            WHERE u.run_id=%s AND (%s::text IS NULL OR w.payload->>'type'=%s)''',
            (latest[0] if latest else 0, type_value, type_value)).fetchone()[0]
        sync_status = conn.execute("SELECT status FROM movidesk_sync_runs ORDER BY id DESC LIMIT 1").fetchone()

    items = list(series.values())
    totals = {key: sum(item[key] for item in items)
              for key in ("entered", "resolved", "closed", "reopened")}
    totals["rate"] = round(100 * totals["resolved"] / totals["entered"], 1) if totals["entered"] else None
    totals["simple_balance"] = totals["entered"] - totals["resolved"]
    measured = [item["backlog"] for item in items if item["backlog"] is not None]
    totals["backlog_change_observed"] = measured[-1] - measured[0] if len(measured) >= 2 else None
    return {"from": first.isoformat(), "to": today.isoformat(), "days": days,
            "ticket_type": ticket_type,
            "totals": totals, "series": items,
            "latest_backlog": latest[2] if latest else None,
            "latest_backlog_at": latest[1].isoformat() if latest else None,
            "stale_open_records": stale_open,
            "unavailable_open_records": unavailable_open,
            "sync_in_progress": bool(sync_status and sync_status[0] == "running"),
            "historical_complete": False,
            "method": "Abertura, resolução e fechamento usam suas datas de evento em Brasília. "
                      "Backlog usa a última fotografia completa de cada dia com cobertura das rotas "
                      "recente e histórica do Movidesk. "
                      "Datas históricas de resolução e fechamento ainda podem estar incompletas; "
                      "reaberturas mostram apenas a última data registrada por chamado. "
                      "Chamados indisponíveis por 404 são listados separadamente, sem fechamento presumido."}


def tickets_for_day(day: date, event: str, ticket_type: str = "public", limit: int = 100) -> dict:
    field = {"entered": "created_date", "resolved": "resolved_date",
             "closed": "closed_date"}.get(event)
    if field is None:
        raise ValueError("Evento inválido.")
    if ticket_type not in ("public", "internal", "all"):
        raise ValueError("Tipo de chamado inválido.")
    type_value = {"public": "2", "internal": "1", "all": None}[ticket_type]
    if day > datetime.now(ZONE).date() or day < date(2015, 1, 1):
        raise ValueError("Data fora do intervalo disponível.")
    start = datetime.combine(day, time.min, ZONE)
    end = start + timedelta(days=1)
    with psycopg.connect(POSTGRES_DSN, connect_timeout=5) as conn:
        rows = conn.execute(f"""
            SELECT r.external_id,r.subject,r.base_status,r.{field} AS event_at
            FROM refined_tickets r JOIN raw_tickets w ON w.id=r.raw_ticket_id
            WHERE r.{field} >= %s AND r.{field} < %s
              AND (%s::text IS NULL OR w.payload->>'type'=%s)
            ORDER BY r.{field} DESC, r.external_id DESC LIMIT %s
        """, (start, end, type_value, type_value, limit)).fetchall()
        total = conn.execute(f"""SELECT COUNT(*) FROM refined_tickets r
            JOIN raw_tickets w ON w.id=r.raw_ticket_id
            WHERE r.{field} >= %s AND r.{field} < %s
              AND (%s::text IS NULL OR w.payload->>'type'=%s)""",
                             (start, end, type_value, type_value)).fetchone()[0]
    return {"date": day.isoformat(), "event": event, "total": total,
            "items": [{"id": row[0], "subject": row[1], "status": row[2],
                       "event_at": row[3].isoformat()} for row in rows]}

