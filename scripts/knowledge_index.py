"""Maintain lexical coverage of eligible historical tickets without an LLM.

Existing nodes and editorial exclusions are never overwritten. Closed status
and tier only select candidates; they do not establish diagnostic confidence.
Called on demand by shared retrieval, at most once per minute per process.
"""
import threading
import time
import psycopg
from psycopg.rows import dict_row
from config import POSTGRES_DSN

_lock = threading.Lock()
_checked_at = 0.0


def sync_missing():
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row, connect_timeout=5,
                         options='-c statement_timeout=30000') as conn:
        conn.execute("SELECT pg_advisory_xact_lock(734099)")
        tenants = conn.execute('SELECT id FROM tenants LIMIT 2').fetchall()
        if len(tenants) != 1:
            raise ValueError('Indexação exige corpus de uma única organização')
        rows = conn.execute("""INSERT INTO ticket_knowledge_nodes
            (source_type,source_id,source_external_id,title,content_for_embedding,
             problem,diagnosis,solution,modules,detected_error_codes,tier,confidence,
             source_authority,metadata,created_at_source)
            SELECT 'PROBABLE_TICKET','ticket-'||r.external_id::text,r.external_id,
                coalesce(r.subject,'Chamado sem título'),
                concat_ws(E'\\n',r.subject,r.problem_statement,r.technical_narrative,r.solution_statement),
                r.problem_statement,r.technical_narrative,r.solution_statement,
                ARRAY(SELECT jsonb_array_elements_text(CASE WHEN jsonb_typeof(r.detected_modules)='array' THEN r.detected_modules ELSE '[]'::jsonb END)),
                ARRAY(SELECT jsonb_array_elements_text(CASE WHEN jsonb_typeof(r.detected_error_codes)='array' THEN r.detected_error_codes ELSE '[]'::jsonb END)),
                r.quality_tier,'MEDIUM',60,
                jsonb_build_object('tenant_id',r.tenant_id,'index_origin','lexical_coverage',
                    'source_quality_tier',r.quality_tier,'reviewed_summary',false,
                    'source_url','https://helpdesk.alternativasistemas.com.br/Ticket/Edit/'||r.external_id::text),
                r.created_date
            FROM refined_tickets r
            WHERE r.tenant_id=%s AND r.quality_tier IN ('GOLD','SILVER')
                AND r.base_status IN ('Resolved','Closed')
                AND r.hard_block_reason IS NULL
                AND length(trim(coalesce(r.solution_statement,''))) >= 45
                AND EXISTS(SELECT 1 FROM raw_tickets raw WHERE raw.tenant_id=r.tenant_id AND raw.external_id=r.external_id)
                AND r.external_id NOT IN (SELECT source_external_id FROM ticket_knowledge_nodes
                    WHERE source_external_id IS NOT NULL)
                AND NOT EXISTS(SELECT 1 FROM ticket_knowledge_nodes n WHERE n.source_id='ticket-'||r.external_id::text)
            ON CONFLICT DO NOTHING RETURNING source_external_id""", (tenants[0]['id'],)).fetchall()
        return {'inserted': len(rows), 'ticket_ids': [r['source_external_id'] for r in rows]}


def ensure_coverage():
    global _checked_at
    with _lock:
        if time.monotonic() - _checked_at < 60:
            return
        result = sync_missing()
        _checked_at = time.monotonic()
        return result


if __name__ == '__main__':
    import json
    print(json.dumps(sync_missing()))
