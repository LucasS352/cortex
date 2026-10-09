"""Durable webhook -> fresh ticket -> Gemini -> INTERNAL action pilot.

Run one worker per shared output volume. No public action writer exists here.
An ambiguous PATCH is reconciled by its marker and is never blindly repeated.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path

import httpx

from config import MOVIDESK_BASE_URL, MOVIDESK_TOKEN
from movidesk_webhook import _connect


PREFIX = 'CORTEX-AI-NOTE:'
TERMINAL = {'resolved', 'closed', 'canceled'}
TICKET_UI_BASE = os.getenv('MOVIDESK_TICKET_UI_BASE',
                           'https://helpdesk.alternativasistemas.com.br/Ticket/Edit').rstrip('/')
ADMIN_STATE_FILE = Path(__file__).parent / 'output' / 'agent_admin_state.json'


@dataclass(frozen=True)
class Settings:
    mode: str = 'disabled'
    agent_id: str = ''
    organizations: frozenset = frozenset()
    tickets: frozenset = frozenset()
    interval: float = 7.0
    max_attempts: int = 5
    runtime_control: bool = False

    @classmethod
    def load(cls):
        ids = lambda name: frozenset(x.strip() for x in os.getenv(name, '').split(',') if x.strip())
        controlled = os.getenv('CORTEX_RUNTIME_PILOT_CONTROL', 'true').strip().casefold() in {'1', 'true', 'yes', 'on'}
        value = cls(os.getenv('CORTEX_AUTONOTE_MODE', 'disabled'),
                    os.getenv('MOVIDESK_BOT_PERSON_ID', '').strip(),
                    ids('CORTEX_PILOT_ORGANIZATION_IDS'), ids('CORTEX_PILOT_TICKET_IDS'),
                    max(6.1, float(os.getenv('CORTEX_MOVIDESK_INTERVAL_SECONDS', '7'))), 5, controlled)
        if value.mode not in {'disabled', 'dry_run', 'internal_note'}:
            raise ValueError('CORTEX_AUTONOTE_MODE: use disabled, dry_run ou internal_note')
        if value.mode != 'disabled' and not MOVIDESK_TOKEN:
            raise ValueError('Configure o token do Movidesk')
        if value.mode != 'disabled' and not value.runtime_control and not (value.organizations or value.tickets):
            raise ValueError('Configure pelo menos uma empresa ou chamado do piloto')
        if value.mode == 'internal_note' and not value.agent_id:
            raise ValueError('Configure MOVIDESK_BOT_PERSON_ID para assinar as notas')
        if value.mode != 'disabled' and not os.getenv('GEMINI_API_KEY'):
            raise ValueError('Configure GEMINI_API_KEY para gerar as notas')
        return value


def effective_settings(settings, state_file=ADMIN_STATE_FILE):
    """Apply the UI policy without ever exceeding the environment ceiling."""
    if not settings.runtime_control:
        return settings
    try:
        state = json.loads(Path(state_file).read_text(encoding='utf-8'))
        organizations = frozenset(str(value) for value in state.get('pilot_organization_ids', [])
                                  if re.fullmatch(r'[A-Za-z0-9_-]{1,80}', str(value)))
        enabled = (not bool(state.get('pilot_paused', True)) and
                   bool(state.get('auto_internal_note', False)) and bool(organizations))
    except (OSError, ValueError, TypeError):
        organizations, enabled = frozenset(), False
    if settings.mode == 'disabled':
        mode = 'disabled'
    elif settings.mode == 'internal_note' and enabled:
        mode = 'internal_note'
    else:
        # Invalid/missing UI state and a dry-run ceiling can generate previews,
        # but neither is allowed to write to Movidesk.
        mode = 'dry_run'
    return replace(settings, mode=mode, organizations=organizations, tickets=frozenset())


class RetryJob(Exception):
    pass


class Api:
    def __init__(self, settings):
        self.settings = settings
        self.last_call = 0.0
        self.client = httpx.Client(timeout=25, follow_redirects=False)

    def request(self, method, path, **kwargs):
        time.sleep(max(0, self.settings.interval - (time.monotonic() - self.last_call)))
        self.last_call = time.monotonic()
        params = {'token': MOVIDESK_TOKEN, **kwargs.pop('params', {})}
        # Never include token-bearing exceptions/URLs or response bodies in logs.
        return self.client.request(method, MOVIDESK_BASE_URL + path, params=params, **kwargs)

    def ticket(self, ticket_id):
        for path in ('/tickets', '/tickets/past'):
            response = self.request('GET', path, params={'id': ticket_id})
            if response.status_code == 404:
                continue
            if response.status_code != 200:
                raise RetryJob(f'Leitura Movidesk HTTP {response.status_code}')
            data = response.json()
            if not isinstance(data, dict) or str(data.get('id')) != str(ticket_id):
                raise RetryJob('Resposta Movidesk não corresponde ao chamado solicitado')
            if not isinstance(data.get('actions'), list):
                raise RetryJob('Movidesk não retornou o histórico de ações')
            return data
        raise RetryJob('Chamado não localizado na API atual nem no histórico')

    def internal_note(self, ticket_id, content):
        # New action: omit id. Do not send clients, status, notes or old actions.
        payload = {'actions': [{'type': 1, 'description': content,
                                'createdBy': {'id': self.settings.agent_id}}]}
        return self.request('PATCH', '/tickets', params={'id': ticket_id}, json=payload)

    def image(self, attachment):
        path = str(attachment.get('path') or '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,200}', path):
            return None
        # Hash from the API, never an arbitrary URL supplied by the customer.
        time.sleep(max(0, self.settings.interval - (time.monotonic() - self.last_call)))
        self.last_call = time.monotonic()
        with self.client.stream('GET', MOVIDESK_BASE_URL + '/storage/download',
                                params={'token': MOVIDESK_TOKEN, 'id': path}) as response:
            if response.status_code != 200:
                return None
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > 4 * 1024 * 1024:
                    return None
            return bytes(raw)


def plain(value):
    return html.unescape(re.sub(r'<[^>]+>', ' ', str(value or ''))).strip()


def bot_action(action, settings):
    return ((str(action.get('type')) == '1' and PREFIX in str(action.get('description') or '')) or
            bool(settings.agent_id and str((action.get('createdBy') or {}).get('id')) == settings.agent_id))


def actions(ticket, settings):
    return sorted((a for a in ticket.get('actions', []) if not a.get('isDeleted') and
                   not bot_action(a, settings) and (plain(a.get('description')) or a.get('attachments'))),
                  key=lambda a: (str(a.get('createdDate') or ''), int(a.get('id') or 0)))


def customer_action(action, ticket):
    author = action.get('createdBy') or {}
    client_ids = {str(c.get('id')) for c in ticket.get('clients', []) if not c.get('isDeleted')}
    return str(action.get('type')) == '2' and (str(author.get('profileType')) == '2' or
            (str(author.get('id')) in client_ids and str(author.get('profileType')) != '1'))


def eligible(ticket, settings):
    from agent_control import organization
    org_id, _name, _issue = organization(ticket.get('clients'))
    if str(ticket.get('id')) not in settings.tickets and org_id not in settings.organizations:
        return False, 'fora_do_piloto'
    if ticket.get('isDeleted') or str(ticket.get('type')) != '2':
        return False, 'chamado_interno_ou_excluido'
    if str(ticket.get('baseStatus', '')).casefold() in TERMINAL:
        return False, 'chamado_finalizado'
    history = actions(ticket, settings)
    if not history or not customer_action(history[-1], ticket):
        return False, 'sem_nova_mensagem_do_cliente'
    return True, ''


def identity(ticket, settings):
    latest = actions(ticket, settings)[-1]
    # Metadata updates and our own notes don't change the customer-message key.
    data = {'ticket': ticket['id'], 'action': latest.get('id'),
            'content': latest.get('description'), 'attachments': latest.get('attachments')}
    key = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return key, str(latest.get('id'))


def marker_present(ticket, marker):
    return any(marker in str(a.get('description') or '') and str(a.get('type')) == '1'
               for a in ticket.get('actions', []) if not a.get('isDeleted'))


def persist_ticket(ticket):
    # Keep a fresh original for audit; this does not promote AI suggestions to KB.
    with _connect() as db:
        db.execute('''INSERT INTO ticket_webhook_snapshots VALUES (?,?,?)
            ON CONFLICT(ticket_id) DO UPDATE SET fetched_at=excluded.fetched_at,payload_json=excluded.payload_json''',
                   (ticket['id'], time.time(), json.dumps(ticket, ensure_ascii=False)))


def generate_note(ticket, settings, api):
    """Use the same grounded assistant, with current issue and company context."""
    import base64
    import mimetypes
    import ticket_image
    from agent_control import organization
    from technician_workbench import (connection, build_reply, evidence_for_company, environment_card,
                                      environment_rows, search_context_query)
    from tech_evidence import ensure_single_tenant
    from conversation_outcome import classify_outcome, RESOLVED_REPLY, ACKNOWLEDGED_REPLY

    history = actions(ticket, settings)
    latest = history[-1]
    def model_text(value):
        text = plain(value)
        text = re.sub(r'(?im)(\b(?:senha|password|passwd|token|secret|api[_ -]?key)\s*[=:]\s*)[^\r\n]+',
                      r'\1[credencial omitida]', text)
        return re.sub(r'(https?://)[^/\s]+@', r'\1[credencial omitida]@', text)
    messages = [{'role': 'technician' if customer_action(a, ticket) else 'assistant',
                 'content': model_text(a.get('description'))} for a in history[-24:]]
    question = messages[-1]['content']
    outcome = classify_outcome(question)
    if outcome and not latest.get('attachments'):
        return {'reply': RESOLVED_REPLY if outcome == 'resolved' else ACKNOWLEDGED_REPLY,
                'sources': [], 'mode': 'outcome', 'attachments': []}
    attachment_notices = []
    for attachment in (latest.get('attachments') or [])[:2]:
        name = str(attachment.get('fileName') or '')
        mime = mimetypes.guess_type(name)[0]
        if mime not in {'image/png', 'image/jpeg', 'image/webp'}:
            attachment_notices.append('Anexo não analisado automaticamente: ' + name[:150])
            continue
        raw = api.image(attachment)
        try:
            result = ticket_image.transcribe('data:' + mime + ';base64,' + base64.b64encode(raw).decode()) if raw else {}
        except ValueError:
            result = {}
        if result.get('text'):
            question += '\nTexto extraído do print (conferir visualmente): ' + result['text']
        else:
            attachment_notices.append('Print não pôde ser lido automaticamente: ' + name[:150])
    if len(latest.get('attachments') or []) > 2:
        attachment_notices.append('Há outros anexos pendentes de leitura pelo técnico.')
    question += '\n' + '\n'.join(attachment_notices) if attachment_notices else ''
    org_id, org_name, _ = organization(ticket.get('clients'))
    with connection() as conn:
        tenant = ensure_single_tenant(conn)
        # These optional profiles can be absent on an older restored database.
        facts, envs = [], []
        has_profiles = conn.execute("SELECT to_regclass('company_context_profiles') AS name").fetchone()['name']
        if org_id and has_profiles:
            profile = conn.execute('SELECT facts FROM company_context_profiles WHERE tenant_id=%s AND movidesk_company_id=%s',
                                   (tenant, org_id)).fetchone()
            facts = profile['facts'] if profile else []
            has_links = conn.execute("SELECT to_regclass('company_environment_links') AS name").fetchone()['name']
            if has_links:
                envs = [environment_card(e) for e in environment_rows(conn, tenant, org_id)]
        query = search_context_query(question, list(reversed(messages[:-1])))
        sources, cases, evidence_scope = [], [], 'general_knowledge'
        has_directory = conn.execute("SELECT to_regclass('movidesk_company_tickets') AS name").fetchone()['name']
        if org_id and has_directory:
            sources, cases, evidence_scope = evidence_for_company(
                conn, tenant, org_id, query, exclude_ticket_id=ticket.get('id'))
        else:
            import knowledge_access
            sources = knowledge_access.search(conn, query, top_k=5)
    context = '\n'.join(f"{m['role']}: {m['content'][:1000]}" for m in messages[:-1])
    row = {'movidesk_company_id': org_id, 'company_name': org_name,
           'subject': ticket.get('subject'), 'contact_name': (latest.get('createdBy') or {}).get('businessName')}
    reply, meta = build_reply(row, context, facts, envs, sources, question, cases, audience='customer_draft')
    if not meta.get('used'):
        raise RetryJob('Gemini indisponível; nenhuma nota de contingência publicada')
    reference = sources[:1] if sources and sources[0].get('applicable') and sources[0].get('answer_use') != 'investigation_only' else []
    similar = [{'ticket': case.get('external_id'), 'title': case.get('subject'),
                'status': case.get('status') or case.get('base_status'),
                'active': bool(case.get('is_active'))} for case in cases[:5]]
    return {'reply': reply,
            'sources': [{'ticket': s.get('source_external_id'), 'title': s.get('title')} for s in reference],
            'similar_tickets': similar, 'evidence_scope': evidence_scope,
            'mode': meta.get('mode'), 'attachments': attachment_notices}


def render_note(result, marker):
    # All generated/source text is escaped: no model-produced HTML or external images.
    reply = html.escape(result['reply'])
    reply = re.sub(r'\*\*([^*\n]+)\*\*', r'<strong>\1</strong>', reply)
    reply = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', reply)
    lines = ['<p><strong>Cortex — resposta sugerida ao cliente (nota interna)</strong></p>',
             '<p>' + reply.replace('\n', '<br>') + '</p>']
    for source in result.get('sources', []):
        label = str(source.get('title') or 'Referência')
        if source.get('ticket'):
            label += ' — chamado ' + str(source['ticket'])
        lines.append('<p>Fonte: ' + html.escape(label) + '</p>')
    similar = result.get('similar_tickets') or []
    if similar:
        lines.append('<p><strong>Observação interna — Tickets semelhantes:</strong></p><ul>')
        for case in similar:
            ticket_id = str(case.get('ticket') or '')
            status = str(case.get('status') or ('Em andamento' if case.get('active') else 'Concluído'))
            title = str(case.get('title') or 'Sem assunto')[:240]
            label = f'#{ticket_id} — {status} — {title}'
            if ticket_id.isdigit():
                lines.append('<li><a href="' + html.escape(TICKET_UI_BASE + '/' + ticket_id, quote=True) +
                             '">' + html.escape(label) + '</a></li>')
            else:
                lines.append('<li>' + html.escape(label) + '</li>')
        lines.append('</ul>')
    for notice in result.get('attachments', []):
        lines.append('<p>' + html.escape(notice) + '</p>')
    lines.append('<p><small>Confira a orientação antes de responder ao cliente. ' + marker + '</small></p>')
    return ''.join(lines)


def claim(settings):
    now = time.time()
    with _connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE webhook_events SET state='retry' WHERE state='processing' AND lease_until<?", (now,))
        db.execute("UPDATE webhook_events SET state='needs_review',outcome='tentativas_esgotadas' WHERE state='retry' AND attempts>=?", (settings.max_attempts,))
        # Serialize all jobs even if another worker is accidentally started.
        if db.execute("SELECT 1 FROM webhook_events WHERE state='processing' AND lease_until>? LIMIT 1", (now,)).fetchone():
            return None
        row = db.execute("""SELECT * FROM webhook_events WHERE state IN ('received','retry')
            AND available_at<=? AND attempts<? ORDER BY received_at LIMIT 1""", (now, settings.max_attempts)).fetchone()
        if not row:
            return None
        db.execute("UPDATE webhook_events SET state='processing',attempts=attempts+1,lease_until=? WHERE digest=?",
                   (now + 600, row['digest']))
        return dict(row)


def finish(event, state, outcome='', error=None):
    with _connect() as db:
        db.execute('''UPDATE webhook_events SET state=?,outcome=?,last_error=?,lease_until=0,available_at=? WHERE digest=?''',
                   (state, outcome, error, time.time() + 60, event['digest']))


def outbox_state(key, state):
    with _connect() as db:
        db.execute('UPDATE internal_note_outbox SET state=?,updated_at=? WHERE key=?', (state, time.time(), key))


def process(event, settings, api, generator=generate_note):
    ticket = api.ticket(event['ticket_id'])
    payload = json.loads(event['payload_json'])
    expected = max((int(a.get('Id') or 0) for a in payload.get('Actions', []) if isinstance(a, dict)), default=0)
    fetched = max((int(a.get('id') or 0) for a in ticket['actions']), default=0)
    if expected > fetched:
        raise RetryJob('API ainda não exibe a ação anunciada pelo webhook')
    current_actions = {str(a.get('id')): a for a in ticket['actions']}
    for announced in payload.get('Actions', []):
        if not isinstance(announced, dict) or not announced.get('Description'):
            continue
        current = current_actions.get(str(announced.get('Id')))
        if current and re.sub(r'\s+', ' ', plain(announced['Description'])) != re.sub(r'\s+', ' ', plain(current.get('description'))):
            raise RetryJob('Texto da ação na API difere do webhook; aguardando consistência')
    persist_ticket(ticket)
    ok, reason = eligible(ticket, settings)
    if not ok:
        finish(event, 'skipped', reason)
        return
    key, source_action = identity(ticket, settings)
    marker = PREFIX + key
    with _connect() as db:
        existing = db.execute('SELECT * FROM internal_note_outbox WHERE key=?', (key,)).fetchone()
    if marker_present(ticket, marker):
        outbox_state(key, 'sent')
        finish(event, 'completed', 'nota_confirmada_no_movidesk')
        return
    if existing and existing['state'] == 'sent':
        finish(event, 'completed', 'mensagem_ja_processada')
        return
    if existing and existing['state'] in {'sending', 'uncertain'}:
        raise RetryJob('Entrega incerta: aguardando confirmação do marcador; PATCH não será repetido')
    if existing and existing['state'] == 'rejected':
        finish(event, 'failed', 'publicacao_anterior_rejeitada')
        return
    if not existing:
        result = generator(ticket, settings, api)
        if not isinstance(result.get('reply'), str) or not result['reply'].strip():
            raise RetryJob('Modelo não gerou texto completo')
        content = render_note(result, marker)
        with _connect() as db:
            db.execute('INSERT OR IGNORE INTO internal_note_outbox VALUES (?,?,?,?,?,?,?,?)',
                       (key, ticket['id'], source_action, marker, content, 'prepared', time.time(), time.time()))
            existing = db.execute('SELECT * FROM internal_note_outbox WHERE key=?', (key,)).fetchone()
    if settings.mode == 'dry_run':
        outbox_state(key, 'preview')
        finish(event, 'preview', 'nota_gerada_sem_publicacao')
        return
    if settings.mode != 'internal_note':
        finish(event, 'skipped', 'publicacao_desativada')
        return
    # Re-read after generation: don't post an obsolete suggestion after a new reply.
    fresh = api.ticket(ticket['id'])
    if marker_present(fresh, marker):
        outbox_state(key, 'sent')
        finish(event, 'completed', 'nota_confirmada_no_movidesk')
        return
    ok, reason = eligible(fresh, settings)
    if not ok or identity(fresh, settings)[0] != key:
        finish(event, 'skipped', reason or 'mensagem_alterada_durante_analise')
        return
    # Commit intent before any network write. A crash afterwards requires reconciliation.
    outbox_state(key, 'sending')
    try:
        response = api.internal_note(ticket['id'], existing['content'])
    except (httpx.HTTPError, OSError):
        outbox_state(key, 'uncertain')
        raise RetryJob('Entrega incerta: falha de conexão durante PATCH') from None
    if response.status_code == 429:
        outbox_state(key, 'prepared')
        raise RetryJob('Movidesk limitou as requisições (429)')
    if response.status_code >= 500:
        outbox_state(key, 'uncertain')
        raise RetryJob('Entrega incerta: erro do Movidesk durante PATCH')
    if response.status_code not in (200, 201, 204):
        outbox_state(key, 'rejected')
        finish(event, 'failed', 'publicacao_rejeitada', f'PATCH HTTP {response.status_code}')
        return
    # HTTP success can precede visibility in GET. Reconcile rather than assuming delivery.
    outbox_state(key, 'uncertain')
    confirmed = api.ticket(ticket['id'])
    if not marker_present(confirmed, marker):
        raise RetryJob('PATCH aceito; aguardando a nota aparecer na consulta do Movidesk')
    outbox_state(key, 'sent')
    persist_ticket(confirmed)
    finish(event, 'completed', 'nota_interna_publicada')


def run_once(settings, api, generator=generate_note):
    settings = effective_settings(settings)
    with _connect() as db:
        db.execute('INSERT OR REPLACE INTO note_worker_status VALUES (1,?,?)', (time.time(), settings.mode))
    if settings.mode == 'disabled':
        return False
    event = claim(settings)
    if not event:
        return False
    try:
        process(event, settings, api, generator)
    except Exception as exc:
        message = str(exc) if isinstance(exc, RetryJob) else 'Falha no processamento (' + type(exc).__name__ + ')'
        exhausted = event['attempts'] + 1 >= settings.max_attempts
        finish(event, 'needs_review' if exhausted else 'retry', 'tentativas_esgotadas' if exhausted else '', message)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    settings = Settings.load()
    api = Api(settings)
    print('Cortex notas internas: modo=' + settings.mode, flush=True)
    try:
        while True:
            processed = run_once(settings, api)
            if args.once:
                break
            if not processed:
                time.sleep(3)
    finally:
        api.client.close()


if __name__ == '__main__':
    main()
