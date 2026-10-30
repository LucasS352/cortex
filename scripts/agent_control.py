"""Read-only pilot eligibility and the actual Cortex capability catalog."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from html import unescape
import hashlib
import re

import httpx
import psycopg
from psycopg.rows import dict_row
from fastapi import HTTPException

from config import POSTGRES_DSN, MOVIDESK_BASE_URL, MOVIDESK_TOKEN
from skills.registry import get_global_registry
from ticket_dashboard import _organizations, INTERNAL_ORGANIZATION_IDS


def organization(clients: object) -> tuple[str | None, str | None, str | None]:
    """One external organization only; uncertain ownership is never eligible."""
    found = _organizations(clients)
    external = {key: name for key, name in found.items() if key not in INTERNAL_ORGANIZATION_IDS}
    if len(external) == 1:
        key, name = next(iter(external.items()))
        return key, name, None
    if len(external) > 1:
        return None, None, "O chamado está associado a mais de uma empresa."
    return None, None, "A empresa do chamado não está identificada."


def eligibility(ticket: dict | None, settings: dict, *, now: datetime | None = None) -> dict:
    reasons: list[str] = []
    if not ticket:
        return {"eligible": False, "reasons": ["Chamado não encontrado na fotografia atual de abertos."],
                "organization_id": None, "organization_name": None}
    org_id, org_name, issue = organization(ticket.get("clients"))
    if issue:
        reasons.append(issue)
    elif org_id not in settings.get("pilot_organization_ids", []):
        reasons.append("Empresa fora da lista do piloto.")
    authorized_emails = {str(value).strip().casefold()
                         for value in settings.get("pilot_requester_emails", [])
                         if str(value).strip()}
    client_emails = {str(client.get("email") or "").strip().casefold()
                     for client in (ticket.get("clients") or [])
                     if not client.get("isDeleted") and str(client.get("profileType")) != "1"}
    if authorized_emails and not (authorized_emails & client_emails):
        reasons.append("Solicitante fora da lista do piloto.")
    if str(ticket.get("type")) != "2":
        reasons.append("A origem precisa ser um chamado público.")
    if str(ticket.get("base_status") or "").casefold() in {"resolved", "closed", "canceled"}:
        reasons.append("O chamado já foi resolvido, fechado ou cancelado.")
    as_of = ticket.get("current_as_of")
    current = now or datetime.now(timezone.utc)
    if as_of is None or (current - as_of).total_seconds() > 48 * 3600:
        reasons.append("A fotografia do chamado está desatualizada; atualize os chamados.")
    if settings.get("pilot_paused", True):
        reasons.append("O piloto está pausado.")
    from automation_scope import evaluate
    scope = evaluate(ticket, settings, org_id)
    if not scope['allowed']:
        scope_messages = {'fora_da_triagem':'O chamado não está em Novo ou Triagem.',
            'status_bloqueado_desenvolvimento':'Status ou equipe de Desenvolvimento: agente bloqueado.',
            'status_bloqueado_comercial':'Status ou equipe Comercial: agente bloqueado.',
            'chamado_finalizado':'O chamado já foi finalizado.', 'status_indisponivel':'Status do chamado indisponível.',
            'automacao_pausada':'O piloto está pausado.',
            'liberacao_sem_data_registrada':'Confirme a liberação para registrar sua data.',
            'data_de_abertura_indisponivel':'A data de abertura do chamado não está disponível.',
            'chamado_anterior_a_liberacao':'O chamado foi criado antes da liberação ou autorização da empresa.'}
        message = scope_messages[scope['reason']]
        if message not in reasons: reasons.append(message)
    return {"eligible": not reasons, "reasons": reasons,
            "organization_id": org_id, "organization_name": org_name,
            "snapshot_at": as_of.isoformat() if as_of else None}


def company_catalog(search: str, page: int, limit: int, selected: list[str]) -> dict:
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row, connect_timeout=5,
                         options="-c statement_timeout=10000 -c default_transaction_read_only=on") as conn:
        people = conn.execute('''SELECT d.company_id AS external_id,d.company_name AS business_name,
            COALESCE(person.active,true) AS active
            FROM movidesk_company_directory d
            LEFT JOIN LATERAL (
              SELECT COALESCE((p.payload->>'isActive')::boolean,true) AS active
              FROM raw_persons p WHERE p.external_id=d.company_id
                AND p.person_type=2 AND p.profile_type IN (2,3)
              ORDER BY p.ingested_at DESC LIMIT 1
            ) person ON true
            ORDER BY d.company_name''').fetchall()
        open_rows = conn.execute("SELECT dashboard_data->'clients' AS clients FROM current_open_ticket_facts").fetchall()
    counts = Counter()
    for row in open_rows:
        org_id, _name, issue = organization(row['clients'])
        if not issue:
            counts[org_id] += 1
    term = search.casefold().strip()
    items = [{"id": row['external_id'], "name": row['business_name'] or f"Empresa {row['external_id']}",
              "open_tickets": counts[row['external_id']], "active": bool(row['active']),
              "selected": row['external_id'] in selected} for row in people]
    items = [item for item in items if not term or term in item['name'].casefold() or term in item['id'].casefold()]
    items.sort(key=lambda item: (not item['selected'], -item['open_tickets'], item['name'].casefold()))
    start = (page - 1) * limit
    return {"items": items[start:start + limit], "total": len(items), "page": page,
            "limit": limit, "registered": len(people), "selected": len(selected)}


def selectable_company(organization_id: str) -> dict:
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row, connect_timeout=5,
                         options="-c statement_timeout=8000 -c default_transaction_read_only=on") as conn:
        row = conn.execute('''SELECT d.company_name AS business_name,
            COALESCE(person.active,true) AS active
            FROM movidesk_company_directory d
            LEFT JOIN LATERAL (
              SELECT COALESCE((p.payload->>'isActive')::boolean,true) AS active
              FROM raw_persons p WHERE p.external_id=d.company_id
                AND p.person_type=2 AND p.profile_type IN (2,3)
              ORDER BY p.ingested_at DESC LIMIT 1
            ) person ON true
            WHERE d.company_id=%s LIMIT 1''', (organization_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Empresa não encontrada no cadastro local.")
    if not row['active']:
        raise HTTPException(409, "Empresa inativa no cadastro local.")
    return {"id": organization_id, "name": row['business_name'] or organization_id}


def all_selectable_company_ids() -> list[str]:
    """Snapshot of active external companies; independent of search and pagination."""
    with psycopg.connect(POSTGRES_DSN, connect_timeout=5,
                         options='-c statement_timeout=10000 -c default_transaction_read_only=on') as conn:
        rows = conn.execute('''SELECT DISTINCT d.company_id FROM movidesk_company_directory d
            LEFT JOIN LATERAL (
                SELECT COALESCE((p.payload->>'isActive')::boolean,true) AS active
                FROM raw_persons p WHERE p.external_id=d.company_id
                  AND p.person_type=2 AND p.profile_type IN (2,3)
                ORDER BY p.ingested_at DESC LIMIT 1
            ) person ON true WHERE COALESCE(person.active,true)''').fetchall()
    return sorted({str(row[0]) for row in rows if str(row[0]) not in INTERNAL_ORGANIZATION_IDS})


def ticket_snapshot(ticket_id: int) -> dict | None:
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row, connect_timeout=5,
                         options="-c statement_timeout=8000 -c default_transaction_read_only=on") as conn:
        row = conn.execute('''SELECT f.external_id,f.dashboard_data,f.current_as_of,
            (SELECT w.payload->>'type' FROM raw_tickets w WHERE w.external_id=f.external_id
             ORDER BY w.ingested_at DESC LIMIT 1) AS ticket_type
            FROM current_open_ticket_facts f WHERE f.external_id=%s''', (ticket_id,)).fetchone()
    if not row:
        return None
    data = row['dashboard_data'] or {}
    return {"ticket_id": row['external_id'], "subject": data.get('subject') or '',
            "status": data.get('status') or '', "base_status": data.get('base_status'),
            "clients": data.get('clients'), "type": row['ticket_type'],
            "current_as_of": row['current_as_of'], "created_date": data.get('created_date')}


def catalog() -> dict:
    skills = get_global_registry().list_all()
    for skill in skills:
        skill['used_in'] = 'Fluxo interativo do simulador'
        skill['draft_flow'] = False
    return {"skills": skills, "agents": [
        {"name": "Triagem", "status": "ativo", "detail": "Taxonomia cruzada e sinais do chamado; não confirma causa raiz."},
        {"name": "Busca e fontes", "status": "ativo", "detail": "RAG, curadoria e tipos de fonte permitidos."},
        {"name": "Pré-resposta", "status": "ativo", "detail": "Texto interno vinculado ao ID do chamado e às referências."},
        {"name": "Revisão humana", "status": "ativo", "detail": "Avaliação, correção e vínculo com chamado interno."},
        {"name": "Publicação Movidesk", "status": "piloto controlado", "detail": "O worker só adiciona ação privada (type 1) após as travas do servidor e da empresa; a conta real ainda exige validação ponta a ponta."},
    ]}


def fetch_movidesk_ticket(ticket_id: int) -> dict:
    """One explicit, user-triggered GET. Never log the token-bearing URL."""
    if not MOVIDESK_TOKEN:
        raise HTTPException(503, "Token Movidesk não configurado.")
    try:
        response = httpx.get(f"{MOVIDESK_BASE_URL}/tickets",
                             params={"token": MOVIDESK_TOKEN, "id": ticket_id,
                                     "$expand": "clients,parentTickets,childrenTickets,actions"},
                             timeout=8)
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Não foi possível consultar o chamado no Movidesk.") from exc
    if response.status_code == 429:
        raise HTTPException(429, "Limite da API Movidesk; tente novamente mais tarde.",
                            headers={"Retry-After": response.headers.get("retry-after", "60")})
    if response.status_code == 404:
        raise HTTPException(404, "Chamado não encontrado no Movidesk.")
    if response.status_code != 200:
        raise HTTPException(503, "Movidesk não confirmou o chamado.")
    try:
        ticket = response.json()
    except ValueError as exc:
        raise HTTPException(503, "Resposta inválida do Movidesk.") from exc
    if not isinstance(ticket, dict) or str(ticket.get('id')) != str(ticket_id):
        raise HTTPException(503, "Movidesk retornou um chamado inesperado.")
    return ticket


def verify_internal_relation(public_id: int, internal_id: int) -> dict:
    if public_id == internal_id:
        raise HTTPException(422, "O chamado interno deve ser diferente do chamado público.")
    internal = fetch_movidesk_ticket(internal_id)
    if str(internal.get('type')) != '1':
        raise HTTPException(422, "O ID informado não corresponde a um chamado interno.")
    related = {str(x.get('id')) for field in ('parentTickets', 'childrenTickets')
               for x in (internal.get(field) or []) if isinstance(x, dict)}
    if str(public_id) not in related:
        public = fetch_movidesk_ticket(public_id)
        related = {str(x.get('id')) for field in ('parentTickets', 'childrenTickets')
                   for x in (public.get(field) or []) if isinstance(x, dict)}
        if str(internal_id) not in related:
            raise HTTPException(422, "Os chamados não estão relacionados no Movidesk. Relacione-os antes de vincular.")
    return {"public_ticket_id": public_id, "internal_ticket_id": internal_id,
            "internal_subject": internal.get('subject') or '', "verified_at": datetime.now(timezone.utc).isoformat()}


def approval_marker(run_id: str, exact_text: str) -> str:
    digest = hashlib.sha256(exact_text.strip().encode('utf-8')).hexdigest()
    return f"CORTEX-APROVADO RUN:{run_id} SHA256:{digest}"


def verify_internal_approval(internal_id: int, run_id: str, exact_text: str) -> dict:
    marker = approval_marker(run_id, exact_text)
    ticket = fetch_movidesk_ticket(internal_id)
    if str(ticket.get('type')) != '1':
        raise HTTPException(409, 'O vínculo deixou de apontar para um chamado interno.')
    for action in ticket.get('actions') or []:
        if not isinstance(action, dict) or str(action.get('type')) != '1':
            continue
        plain = unescape(re.sub(r'<[^>]*>', ' ', str(action.get('description') or '')))
        if marker in re.sub(r'\s+', ' ', plain):
            return {'marker': marker, 'action_id': action.get('id'),
                    'created_date': action.get('createdDate')}
    raise HTTPException(409, 'A aprovação deste texto ainda não consta como ação interna no chamado relacionado. Registre o selo exibido no Cortex e tente novamente.')
