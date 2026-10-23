"""Administrative quality observations from persisted evidence, without schema writes."""
import knowledge_training as kt
import support_events


def snapshot():
    with kt.connect() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        columns = {r['column_name'] for r in conn.execute("""SELECT column_name FROM information_schema.columns
            WHERE table_schema='cortex_kb' AND table_name='answer_reviews'""")}
        correction = 'count(*) FILTER (WHERE rating=-1 AND proposed_answer IS NOT NULL)' if 'proposed_answer' in columns else 'NULL::bigint'
        rejection = 'count(*) FILTER (WHERE rating=-1 AND proposed_answer IS NULL)' if 'proposed_answer' in columns else 'count(*) FILTER (WHERE rating=-1)'
        avg_score = 'round(avg(score)::numeric, 2)::float' if 'score' in columns else 'NULL::float'
        scored_count = 'count(*) FILTER (WHERE score IS NOT NULL)' if 'score' in columns else '0::bigint'
        reviews = conn.execute(f'''SELECT count(*) total,
            count(*) FILTER (WHERE rating=1) approved,
            {correction} corrected, {rejection} rejected,
            {avg_score} average_score, {scored_count} scored_count
            FROM cortex_kb.answer_reviews''').fetchone()
        approval = conn.execute('''SELECT count(*) decided,
            avg(extract(epoch FROM (decided_at-created_at))) mean_seconds
            FROM cortex_kb.candidates WHERE status IN ('approved','merged') AND decided_at IS NOT NULL''').fetchone()
        negative = conn.execute('''SELECT f.item_id,f.version_id,i.title,count(*) negative_reviews
            FROM cortex_kb.feedback f JOIN cortex_kb.items i ON i.id=f.item_id WHERE f.rating=-1
            GROUP BY f.item_id,f.version_id,i.title ORDER BY negative_reviews DESC LIMIT 20''').fetchall()
        coverage = conn.execute('''SELECT module_key,count(*) active_procedures FROM cortex_kb.items
            WHERE status='active' GROUP BY module_key ORDER BY active_procedures DESC''').fetchall()
        usage = conn.execute('''SELECT s->>'source_id' source_id,s->>'version' version,
            s->>'title' title,count(*) citations FROM technician_conversation_messages m,
            LATERAL jsonb_array_elements(m.sources) s WHERE m.role='assistant'
            AND s->>'source_id' LIKE 'canon:%%' GROUP BY 1,2,3 ORDER BY citations DESC LIMIT 20''').fetchall()
        gaps = conn.execute('''SELECT count(*) assistant_messages,
            count(*) FILTER (WHERE sources='[]'::jsonb) messages_without_attached_source
            FROM technician_conversation_messages WHERE role='assistant' ''').fetchone()
        companies = conn.execute('''SELECT movidesk_company_id,count(*) conversations
            FROM technician_conversations GROUP BY movidesk_company_id
            HAVING count(*)>1 ORDER BY conversations DESC LIMIT 20''').fetchall()
    return {'answer_reviews': dict(reviews), 'approval_time': dict(approval),
            'negative_procedures': negative, 'module_coverage': coverage,
            'procedure_citations': usage, 'evidence_coverage': dict(gaps),
            'company_recurrence': companies,
            'operational': support_events.snapshot(),
            'limitations': [
                'Citações medem fontes anexadas ao assistente do técnico; não representam todos os usos no Movidesk.',
                'Mensagem sem fonte também pode ser confirmação de resultado; não equivale a erro ou investigação.',
                'A telemetria operacional começa nesta versão; replays históricos não alimentam os eventos.',
                'Avaliações técnicas humanas são distintas das verificações automatizadas do replay.']}
