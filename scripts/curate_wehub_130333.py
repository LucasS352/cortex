"""Refresh ticket 130333 and index its verified, client-scoped WeHub finding.

Run from the project root: python scripts/curate_wehub_130333.py
The source ticket remains the record of truth; this does not alter the ERP.
"""

import json

import httpx
import psycopg
from psycopg.rows import dict_row

from config import MOVIDESK_BASE_URL, MOVIDESK_TOKEN, POSTGRES_DSN, GEMINI_API_KEY, GEMINI_EMBEDDING_MODEL
from refine_tickets_v2 import process_ticket_v2
from sync_current_tickets import EXPAND, REFINED_COLUMNS, refined_sql


TICKET_ID = 130333
SOURCE_ID = f"ticket-{TICKET_ID}"
TITLE = "WeHub: status Enviado rejeitado quando número do objeto vai vazio — caso Shop Tons"
PROBLEM = (
    "Ao sincronizar pedidos pelo status Enviado, a WeHub retornou HTTP 400 e CodigoErro 405: "
    "o pedido precisa de código de rastreio. No caso documentado, o campo número do objeto "
    "era transmitido vazio."
)
DIAGNOSIS = (
    "No ambiente Shop Tons, a configuração WHUB_ENVIA_NF_NUM_OBJ_VAZIO estava em N. "
    "O suporte confirmou o comportamento com Desenvolvimento; outro banco do mesmo cliente "
    "conseguia comunicar o status sem número de rastreio real."
)
SOLUTION = (
    "Após validação do Desenvolvimento, ajustar WHUB_ENVIA_NF_NUM_OBJ_VAZIO para S fez a "
    "integração enviar o número da NF como identificador substituto quando o número do objeto "
    "está vazio. O técnico informou que o status voltou a comunicar. Antes de reproduzir, "
    "comparar as configurações dos bancos, confirmar a regra vigente da WeHub e validar o "
    "efeito da alteração com Desenvolvimento. Não inventar código de rastreio nem aplicar "
    "a configuração indiscriminadamente a outros clientes."
)
RESULT = (
    "Chamado resolvido em 29/09/2026; êxito da comunicação reportado pelo técnico. "
    "A mensagem final ao cliente solicitou novos testes; aceite do cliente não consta na fonte."
)
CONTENT = "\n".join((TITLE, PROBLEM, DIAGNOSIS, SOLUTION, RESULT))


def main():
    if not MOVIDESK_TOKEN:
        raise RuntimeError("MOVIDESK_TOKEN ausente")
    with httpx.Client(timeout=45) as client:
        response = client.get(
            MOVIDESK_BASE_URL + "/tickets",
            params={"token": MOVIDESK_TOKEN, "id": TICKET_ID, "$expand": EXPAND},
        )
        response.raise_for_status()
        ticket = response.json()
    if isinstance(ticket, list):
        if len(ticket) != 1:
            raise RuntimeError("Ticket não encontrado de forma inequívoca")
        ticket = ticket[0]
    if int(ticket.get("id", 0)) != TICKET_ID or ticket.get("baseStatus") not in ("Resolved", "Closed"):
        raise RuntimeError("Ticket ou estado diferente do esperado")
    final_text = "\n".join(str(a.get("description") or "") for a in ticket.get("actions") or [])
    if "405" not in final_text or "WeHub" not in final_text or "substituto" not in final_text:
        raise RuntimeError("A solução documentada não foi encontrada no chamado")

    # Embed only the short technical case, without order IDs, names or credentials.
    vector = None
    if GEMINI_API_KEY:
        from ingest_knowledge_nodes_v2 import call_gemini_embed
        vector = call_gemini_embed(CONTENT)

    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as conn:
        tenant = conn.execute("SELECT id FROM tenants WHERE slug='alternativa'").fetchone()
        if not tenant:
            raise RuntimeError("Tenant Alternativa ausente")
        tenant_id = tenant["id"]
        raw = conn.execute("""
            INSERT INTO raw_tickets (tenant_id,source_provider,external_id,payload,created_date,processing_status)
            VALUES (%s,'movidesk',%s,%s::jsonb,%s,'done')
            ON CONFLICT (tenant_id,source_provider,external_id) DO UPDATE SET
                payload=EXCLUDED.payload,created_date=EXCLUDED.created_date,
                ingested_at=NOW(),processing_status='done',processed_at=NOW()
            RETURNING id
        """, (tenant_id, TICKET_ID, json.dumps(ticket, ensure_ascii=False), ticket.get("createdDate"))).fetchone()
        refined = process_ticket_v2(str(raw["id"]), str(tenant_id), TICKET_ID, ticket)
        conn.execute(refined_sql(), tuple(refined[name] for name in REFINED_COLUMNS))
        metadata = {
            "tenant_id": str(tenant_id),
            "source_url": f"https://helpdesk.alternativasistemas.com.br/Ticket/Edit/{TICKET_ID}",
            "scope": "Shop Tons; não generalizar automaticamente",
            "validation": "Desenvolvimento consultado; sucesso relatado pelo técnico; cliente convidado a testar",
            "configuration": "WHUB_ENVIA_NF_NUM_OBJ_VAZIO: N -> S",
            "source_last_update": ticket.get("lastUpdate"),
        }
        row = conn.execute("""
            INSERT INTO ticket_knowledge_nodes (
                source_type,source_id,source_external_id,title,content_for_embedding,
                problem,diagnosis,solution,result,product,modules,detected_error_codes,
                tier,confidence,technical_score,solution_score,validation_score,final_score,
                validation_evidence,source_authority,metadata,embedding,embedding_generated,embedding_model
            ) VALUES (
                'VALIDATED_TICKET',%s,%s,%s,%s,%s,%s,%s,%s,'Master ERP',%s,%s,
                'GOLD','HIGH',90,90,85,88,%s,85,%s::jsonb,%s,%s,%s
            ) ON CONFLICT (source_id) DO UPDATE SET
                title=EXCLUDED.title,content_for_embedding=EXCLUDED.content_for_embedding,
                problem=EXCLUDED.problem,diagnosis=EXCLUDED.diagnosis,solution=EXCLUDED.solution,
                result=EXCLUDED.result,metadata=EXCLUDED.metadata,
                embedding=COALESCE(EXCLUDED.embedding,ticket_knowledge_nodes.embedding),
                embedding_generated=EXCLUDED.embedding_generated OR ticket_knowledge_nodes.embedding_generated,
                updated_at=NOW()
            RETURNING id,embedding_generated
        """, (
            SOURCE_ID,TICKET_ID,TITLE,CONTENT,PROBLEM,DIAGNOSIS,SOLUTION,RESULT,
            ["Integrações", "WeHub"],["405"],
            ["DEVELOPMENT_CONFIRMED", "TECHNICIAN_REPORTED_SUCCESS", "CUSTOMER_RETEST_PENDING"],
            json.dumps(metadata,ensure_ascii=False),vector,bool(vector),
            GEMINI_EMBEDDING_MODEL if vector else None,
        )).fetchone()
        conn.commit()
        print(json.dumps({"ticket": TICKET_ID,"status": ticket["baseStatus"],
                          "knowledge_node": row["id"],"embedding_generated":row["embedding_generated"]},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
