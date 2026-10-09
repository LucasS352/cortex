"""Index the reviewed local source for ticket 130962 without changing Movidesk.

Run: python scripts/curate_sync_130962.py
The source must contain the log and resolution checked during review. No
success confirmation or applicability to Loja Integrada is inferred.
"""
import json
import psycopg
from psycopg.rows import dict_row
from config import POSTGRES_DSN
from knowledge_access import normalize

TICKET_ID = 130962
ERROR_ID = "443E8D74-8F2F-4D7B-B9D1-DEC98F59B75B"
PROBLEM = (
    "Sincronizador UAPPI/WELL: erro no envio de produtos.\n"
    "ATENÇÃO! A descrição curta tem mais que 230 caracteres e foi ajustado.\n"
    "Erro no envio do Produto (536): HTTP/1.1 400 Bad Request. Requisição inválida.\n"
    f"Código do erro: [{ERROR_ID}]"
)
DIAGNOSIS = "o suporte identificou que o erro estava relacionado à descrição curta dos produtos."
SOLUTION = (
    "ajustar as descrições curtas dos produtos. O atendimento registra a retirada dos produtos "
    "da fila para alteração e reenvio. O aviso do log informa que a descrição curta ultrapassa "
    "230 caracteres. A fonte não detalha o menu para retirar ou reenviar os produtos."
)


def main():
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as conn:
        tenants = conn.execute("SELECT id FROM tenants LIMIT 2").fetchall()
        if len(tenants) != 1:
            raise RuntimeError("Curadoria exige corpus de uma única organização")
        row = conn.execute("SELECT payload FROM raw_tickets WHERE tenant_id=%s AND external_id=%s",
                           (tenants[0]['id'], TICKET_ID)).fetchone()
        if not row:
            raise RuntimeError("Chamado original ausente")
        payload = row['payload']
        text = normalize('\n'.join(a.get('description') or '' for a in payload.get('actions', [])))
        expected = (ERROR_ID.lower(), '230 caracteres', 'sincronizador uappi/well',
                    'erro se tratava da descricao curta', 'retirados produtos da fila para alteracao e reenvio')
        if any(part not in text for part in expected):
            raise RuntimeError("Fonte diverge do conteúdo revisado; conferir antes de indexar")
        title = payload['subject']
        metadata = {'tenant_id': str(tenants[0]['id']), 'reviewed_summary': True,
                    'source_url': f'https://helpdesk.alternativasistemas.com.br/Ticket/Edit/{TICKET_ID}',
                    'source_last_update': payload.get('lastUpdate'),
                    'scope': 'UAPPI/WELL; confirmar integrador antes de aplicar em outro ambiente',
                    'validation': 'Orientação registrada; sucesso após reenvio não confirmado na fonte'}
        node = conn.execute("""INSERT INTO ticket_knowledge_nodes
            (source_type,source_id,source_external_id,title,content_for_embedding,problem,diagnosis,
             solution,result,modules,detected_error_codes,tier,confidence,metadata,created_at_source)
            VALUES ('PROBABLE_TICKET',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'SILVER','MEDIUM',%s::jsonb,%s)
            ON CONFLICT (source_id) DO UPDATE SET
              title=EXCLUDED.title,content_for_embedding=EXCLUDED.content_for_embedding,
              problem=EXCLUDED.problem,diagnosis=EXCLUDED.diagnosis,solution=EXCLUDED.solution,
              result=EXCLUDED.result,modules=EXCLUDED.modules,detected_error_codes=EXCLUDED.detected_error_codes,
              metadata=EXCLUDED.metadata,embedding=NULL,embedding_generated=false,updated_at=NOW()
            RETURNING id""", (f'ticket-{TICKET_ID}', TICKET_ID, title,
                '\n'.join((title, PROBLEM, DIAGNOSIS, SOLUTION)), PROBLEM, DIAGNOSIS, SOLUTION,
                'Orientação registrada; resultado após reenvio não confirmado.',
                ['Integrações', 'UAPPI/WELL'], ['HTTP 400', ERROR_ID],
                json.dumps(metadata, ensure_ascii=False), payload.get('createdDate'))).fetchone()
        print(json.dumps({'ticket': TICKET_ID, 'knowledge_node': node['id'], 'indexed': True}))


if __name__ == '__main__':
    main()
