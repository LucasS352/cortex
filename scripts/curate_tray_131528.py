"""Refresh and promote ticket 131528 as a client-safe Tray status-mapping reference.

Run from the project root: python scripts/curate_tray_131528.py
The Movidesk ticket remains the source of truth; this only creates the local
knowledge reference and does not change the customer ERP or Tray account.
"""

import json

import httpx
import psycopg
from psycopg.rows import dict_row

from config import MOVIDESK_BASE_URL, MOVIDESK_TOKEN, POSTGRES_DSN, GEMINI_API_KEY, GEMINI_EMBEDDING_MODEL
from refine_tickets_v2 import process_ticket_v2
from sync_current_tickets import EXPAND, REFINED_COLUMNS, refined_sql


TICKET_ID = 131528
SOURCE_ID = f"ticket-{TICKET_ID}"
TITLE = "Tray: pedido não integra quando o status da plataforma não possui de-para"
PROBLEM = (
    "No Sincronizador Tray, o pedido não foi integrado porque o status recebido da plataforma "
    "\"A ENVIAR ELO\" não possuía relação cadastrada no Master. "
    "O log retornou F31D63FA-7AEC-4722-BD59-D4A085244236."
)
DIAGNOSIS = (
    "O status externo da Tray estava sem vínculo na rotina Cadastro - Integração Relação. "
    "A equivalência deve ser definida conforme o fluxo real da loja; o nome do status, sozinho, "
    "não determina qual status interno deve ser usado."
)
SOLUTION = (
    "Abrir Cadastro - Integração Relação (atalho Ctrl+N), selecionar a empresa e a integração Tray "
    "corretas e conferir uma relação funcional semelhante. Em STATUS_INTEGRACAO, cadastrar o "
    "protocolo do integrador informado no log e relacioná-lo ao status interno aprovado para o fluxo "
    "da loja. Salvar, forçar o reprocessamento do pedido e validar o retorno no Sincronizador e a "
    "criação da venda no Master. Não reutilizar os códigos, empresa ou relação externa do exemplo "
    "documentado em outro cliente."
)
RESULT = (
    "No chamado, após cadastrar o de-para, o pedido 31 foi reprocessado com sucesso e a venda foi "
    "integrada. O atendimento foi encerrado como resolvido."
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

    source_text = "\n".join(str(action.get("description") or "") for action in ticket.get("actions") or [])
    required = ("A ENVIAR ELO", "F31D63FA-7AEC-4722-BD59-D4A085244236", "Pedido 31", "processada com sucesso")
    if any(fragment not in source_text for fragment in required):
        raise RuntimeError("A causa, o procedimento ou o resultado documentado não foram encontrados no chamado")

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
        # The deterministic classifier treats this single long action as SILVER.
        # This editorial promotion is supported by the documented root cause,
        # precise correction and successful reprocessing in the same action.
        conn.execute("""
            UPDATE refined_tickets
            SET quality_tier='GOLD', technical_score=92, solution_score=93,
                validation_score=95, final_score=93, evidence_level='STRONG',
                hard_block_reason=NULL, is_blocked_gold=FALSE,
                evidence_data=%s::jsonb, discard_reason=NULL, refined_at=NOW()
            WHERE tenant_id=%s AND external_id=%s
        """, (json.dumps({
            "curation_override": "GOLD",
            "reason": "causa, correção e reprocessamento bem-sucedido documentados no atendimento resolvido",
            "validation": ["RESOLVED_TICKET", "TECHNICIAN_REPORTED_SUCCESS", "REPROCESSING_CONFIRMED"],
        }, ensure_ascii=False), tenant_id, TICKET_ID))

        metadata = {
            "tenant_id": str(tenant_id),
            "source_url": f"https://helpdesk.alternativasistemas.com.br/Ticket/Edit/{TICKET_ID}",
            "scope": "Tray; a equivalência de status deve ser confirmada para cada loja",
            "validation": "Pedido 31 reprocessado com sucesso e venda integrada, conforme atendimento resolvido",
            "example_values_excluded_from_procedure": [
                "Empresa 1 INDINUTRI", "Integração 30 - TRAY", "Relação externa 2 - TRAY INDINUTRI",
                "Código do integrador 1", "Status EZ 11 - A ENVIAR",
            ],
            "source_last_update": ticket.get("lastUpdate"),
        }
        node = conn.execute("""
            INSERT INTO ticket_knowledge_nodes (
                source_type,source_id,source_external_id,title,content_for_embedding,
                problem,diagnosis,solution,result,product,modules,detected_error_codes,
                tier,confidence,technical_score,solution_score,validation_score,final_score,
                validation_evidence,source_authority,metadata,embedding,embedding_generated,embedding_model,
                screen_path,shortcut_keys,created_at_source
            ) VALUES (
                'VALIDATED_TICKET',%s,%s,%s,%s,%s,%s,%s,%s,'Master ERP',%s,%s,
                'GOLD','HIGH',92,93,95,93,%s,85,%s::jsonb,%s,%s,%s,%s,%s,%s
            ) ON CONFLICT (source_id) DO UPDATE SET
                title=EXCLUDED.title,content_for_embedding=EXCLUDED.content_for_embedding,
                problem=EXCLUDED.problem,diagnosis=EXCLUDED.diagnosis,solution=EXCLUDED.solution,
                result=EXCLUDED.result,modules=EXCLUDED.modules,detected_error_codes=EXCLUDED.detected_error_codes,
                tier=EXCLUDED.tier,confidence=EXCLUDED.confidence,technical_score=EXCLUDED.technical_score,
                solution_score=EXCLUDED.solution_score,validation_score=EXCLUDED.validation_score,
                final_score=EXCLUDED.final_score,validation_evidence=EXCLUDED.validation_evidence,
                metadata=EXCLUDED.metadata,screen_path=EXCLUDED.screen_path,shortcut_keys=EXCLUDED.shortcut_keys,
                created_at_source=EXCLUDED.created_at_source,
                embedding=COALESCE(EXCLUDED.embedding,ticket_knowledge_nodes.embedding),
                embedding_generated=EXCLUDED.embedding_generated OR ticket_knowledge_nodes.embedding_generated,
                updated_at=NOW()
            RETURNING id,source_type,tier,confidence,embedding_generated
        """, (
            SOURCE_ID, TICKET_ID, TITLE, CONTENT, PROBLEM, DIAGNOSIS, SOLUTION, RESULT,
            ["Integrações", "Tray", "Sincronizador", "Status"],
            ["F31D63FA-7AEC-4722-BD59-D4A085244236"],
            ["RESOLVED_TICKET", "TECHNICIAN_REPORTED_SUCCESS", "REPROCESSING_CONFIRMED"],
            json.dumps(metadata, ensure_ascii=False), vector, bool(vector),
            GEMINI_EMBEDDING_MODEL if vector else None,
            "Cadastro - Integração Relação", ["Ctrl+N"], ticket.get("createdDate"),
        )).fetchone()
        conn.commit()

    print(json.dumps({"ticket": TICKET_ID, "status": ticket["baseStatus"], **node}, ensure_ascii=False))


if __name__ == "__main__":
    main()
