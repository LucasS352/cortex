"""Read-only evidence retrieval for the internal, single-tenant technician desk.

Knowledge-node summaries are never represented as original ticket actions.
No model call or remote Movidesk request is needed to inspect an example.
"""
import hashlib
import json
import re
from html.parser import HTMLParser


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("p", "div", "br", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value):
    parser = PlainText()
    parser.feed(str(value or ""))
    return "".join(parser.parts).strip()


def revision(session):
    """Any conversation change invalidates an in-flight human approval."""
    data = [(t.role, t.content, t.created_at.isoformat()) for t in session.turns]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()


def latest_references(session):
    turn = next((t for t in reversed(session.turns) if t.role == "assistant"), None)
    ids = []
    for item in (turn.evidence_classified if turn else []):
        value = item.get("node_id", item.get("id"))
        if isinstance(value, int) and value > 0 and value not in ids:
            ids.append(value)
    return ids[:30]


def ensure_single_tenant(conn):
    # Current knowledge nodes do not carry tenant identity. Fail closed if the
    # deployment stops being single-tenant rather than joining ambiguous IDs.
    rows = conn.execute("SELECT id FROM tenants ORDER BY id LIMIT 2").fetchall()
    if len(rows) != 1:
        raise ValueError("A base de evidências exige uma única organização configurada.")
    return rows[0]["id"]


def cards(rows, provided_ids=()):
    result, seen = [], set()
    provided = set(provided_ids)
    for row in rows:
        ticket = row.get("source_external_id") if row.get("source_type") not in ("OFFICIAL_KB", "MANUAL", "KB") else None
        key = ("ticket", str(ticket)) if ticket else ("node", str(row["id"]))
        if key in seen:
            continue
        seen.add(key)
        result.append({
            "node_id": row["id"], "ticket_id": ticket,
            "title": row.get("title") or "Sem assunto",
            "source_type": row.get("source_type"),
            "relationship": "provided_to_model" if row["id"] in provided else "similar",
            "solution_excerpt": plain_text(row.get("solution"))[:600],
            "modules": row.get("modules") or [],
            "version_reference": row.get("version_reference"),
            "error_codes": row.get("detected_error_codes") or [],
            "validation_state": "recorded_unverified",
            "answer_use": row.get("answer_use", "supporting_reference"),
            "quality_flags": row.get("quality_flags", []),
            "detail_url": f"/api/tech/evidence/{row['id']}",
        })
    return result


def retrieve(conn, query, provided_ids=(), offset=0, limit=5):
    from knowledge_access import search, assessment
    ensure_single_tenant(conn)
    cited = conn.execute("SELECT * FROM ticket_knowledge_nodes WHERE id=ANY(%s) ORDER BY array_position(%s::bigint[],id)",
                         (list(provided_ids), list(provided_ids))).fetchall() if provided_ids else []
    rows = search(conn, query, top_k=50) if query.strip() else []
    cited_ids = {r["id"] for r in cited}
    rows = [r for r in rows if r["id"] not in cited_ids]
    return {"sources": cards(cited, provided_ids), "items": cards(rows[offset:offset+limit]),
            "next_offset": offset+limit if len(rows)>offset+limit else None,
            "retrieval_status": "complete", "query": query, "evidence_assessment": assessment(rows),
            "candidate_limit": 50}


def _legacy_retrieve(conn, query, provided_ids=(), offset=0, limit=5):
    ensure_single_tenant(conn)
    columns = "id, source_type, source_external_id, title, solution, modules, detected_error_codes, version_reference"
    cited = conn.execute(
        f"SELECT {columns} FROM ticket_knowledge_nodes WHERE id = ANY(%s) ORDER BY array_position(%s::bigint[], id)",
        (list(provided_ids), list(provided_ids)),
    ).fetchall() if provided_ids else []
    # FTS is independent of Gemini. Exact protocol works even without embedding.
    protocol = re.fullmatch(r"#?(\d+)", query.strip())
    protocol_id = int(protocol[1]) if protocol and len(protocol[1]) <= 10 else -1
    excluded_tickets = [row["source_external_id"] for row in cited if row.get("source_external_id")]
    rows = conn.execute(f"""
        WITH ranked AS (
            SELECT {columns},
                ts_rank_cd(to_tsvector('portuguese', coalesce(title,'') || ' ' || coalesce(problem,'') || ' ' || coalesce(solution,'')),
                    websearch_to_tsquery('portuguese', %s)) AS score,
                row_number() OVER (PARTITION BY source_external_id ORDER BY id DESC) AS position
            FROM ticket_knowledge_nodes
            WHERE source_external_id IS NOT NULL
              AND source_external_id <> ALL(%s::bigint[])
              AND (source_external_id = %s OR
                to_tsvector('portuguese', coalesce(title,'') || ' ' || coalesce(problem,'') || ' ' || coalesce(solution,''))
                    @@ websearch_to_tsquery('portuguese', %s))
        )
        SELECT * FROM ranked WHERE position = 1
        ORDER BY (source_external_id = %s) DESC, score DESC, id DESC
        LIMIT %s OFFSET %s
    """, (query, excluded_tickets, protocol_id, query, protocol_id, limit + 1, offset)).fetchall() if query.strip() else []
    return {
        "sources": cards(cited, provided_ids), "items": cards(rows[:limit]),
        "next_offset": offset + limit if len(rows) > limit else None,
        "retrieval_status": "complete", "query": query,
    }


def detail(conn, node_id):
    tenant = ensure_single_tenant(conn)
    node = conn.execute("""SELECT id, source_external_id, source_type, title, problem,
        solution, result, modules, detected_error_codes, version_reference, metadata, source_id,
        content_for_embedding FROM ticket_knowledge_nodes WHERE id = %s""", (node_id,)).fetchone()
    if not node:
        return None
    if node.get("source_type") in ("OFFICIAL_KB", "KB"):
        external = (node.get("metadata") or {}).get("external_id")
        article = conn.execute("SELECT external_id,title,content_text,content_html,updated_at_source FROM raw_kb_articles WHERE tenant_id=%s AND external_id=%s",(tenant,external)).fetchone()
        text = (article.get("content_text") or plain_text(article.get("content_html"))) if article else ""
        return {**cards([node])[0], "summary": {"problem": node.get("problem"), "solution": node.get("solution"), "result": node.get("result")},
                "original_status": "available" if text else "unavailable", "status": "Artigo oficial importado", "created_at": None,
                "resolved_at": None, "snapshot_at": str(article.get("updated_at_source")) if article else None,
                "source_revision": hashlib.sha256(text.encode()).hexdigest() if text else None,
                "source_url": f"https://helpdesk.alternativasistemas.com.br/kb/article/{external}" if article else None,
                "actions": [{"id":external,"created_at":str(article.get("updated_at_source")),"author":"Base de conhecimento", "text":text,"is_internal":False}] if text else [],
                "notice":"Artigo original importado. Conteúdo em vídeo pode exigir transcrição; conferir versão e aplicabilidade."}
    ticket_id = node.get("source_external_id")
    row = conn.execute("""SELECT payload, ingested_at FROM raw_tickets
        WHERE tenant_id = %s AND source_provider = 'movidesk' AND external_id = %s""",
        (tenant, ticket_id)).fetchone() if ticket_id else None
    payload = row["payload"] if row else {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    actions = []
    for action in payload.get("actions", []) or []:
        if action.get("isDeleted"):
            continue
        creator = action.get("createdBy") or {}
        actions.append({
            "id": action.get("id"), "created_at": action.get("createdDate"),
            "author": creator.get("businessName") or creator.get("name") or "Não informado",
            "text": str(action["description"]) if action.get("description") else plain_text(action.get("htmlDescription")),
            "is_internal": action.get("type") == 1,
        })
    actions.sort(key=lambda a: (a["created_at"] or "", str(a["id"])))
    return {
        **cards([node])[0],
        "summary": {"problem": plain_text(node.get("problem")), "solution": plain_text(node.get("solution")), "result": plain_text(node.get("result"))},
        "original_status": "available" if actions else "unavailable",
        "status": payload.get("status") or "Não informado",
        "created_at": payload.get("createdDate"), "resolved_at": payload.get("resolvedIn"),
        "snapshot_at": str(row["ingested_at"]) if row else None,
        "source_revision": hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest() if payload else None,
        "actions": actions,
        "notice": "Histórico importado. Encerramento não comprova correção. A ação exata da solução não foi marcada na ingestão; confira a conversa original.",
    }
