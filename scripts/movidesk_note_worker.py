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
from urllib.parse import urljoin, urlsplit

import httpx
import mapped_replies

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
    requester_emails: frozenset = frozenset()
    automatic_enabled: bool = False
    manual_internal_note: bool = False

    @classmethod
    def load(cls):
        ids = lambda name: frozenset(x.strip() for x in os.getenv(name, '').split(',') if x.strip())
        controlled = os.getenv('CORTEX_RUNTIME_PILOT_CONTROL', 'true').strip().casefold() in {'1', 'true', 'yes', 'on'}
        value = cls(mode=os.getenv('CORTEX_AUTONOTE_MODE', 'disabled'),
                    agent_id=os.getenv('MOVIDESK_BOT_PERSON_ID', '').strip(),
                    organizations=ids('CORTEX_PILOT_ORGANIZATION_IDS'),
                    tickets=ids('CORTEX_PILOT_TICKET_IDS'),
                    interval=max(6.1, float(os.getenv('CORTEX_MOVIDESK_INTERVAL_SECONDS', '7'))),
                    max_attempts=5, runtime_control=controlled,
                    requester_emails=frozenset(x.casefold() for x in ids('CORTEX_PILOT_REQUESTER_EMAILS')))
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


def effective_settings(settings, state_file=None):
    """Apply the UI policy without ever exceeding the environment ceiling."""
    if not settings.runtime_control:
        return settings
    state_file = ADMIN_STATE_FILE if state_file is None else state_file
    try:
        state = json.loads(Path(state_file).read_text(encoding='utf-8'))
        organizations = frozenset(str(value) for value in state.get('pilot_organization_ids', [])
                                  if re.fullmatch(r'[A-Za-z0-9_-]{1,80}', str(value)))
        requester_emails = frozenset(str(value).strip().casefold()
                                     for value in state.get('pilot_requester_emails', [])
                                     if re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', str(value).strip()))
    except (OSError, ValueError, TypeError):
        organizations, requester_emails = frozenset(), frozenset()
        pilot_ready, automatic_enabled = False, False
    else:
        pilot_ready = (not bool(state.get('pilot_paused', True)) and
                       bool(organizations) and bool(requester_emails))
        automatic_enabled = bool(state.get('auto_internal_note', False))
    enabled = pilot_ready and automatic_enabled
    if settings.mode == 'disabled':
        mode = 'disabled'
    elif settings.mode == 'internal_note' and enabled:
        mode = 'internal_note'
    else:
        # Invalid/missing UI state and a dry-run ceiling can generate previews,
        # but neither is allowed to write to Movidesk.
        mode = 'dry_run'
    return replace(settings, mode=mode, organizations=organizations, tickets=frozenset(),
                   requester_emails=requester_emails, automatic_enabled=automatic_enabled,
                   manual_internal_note=(settings.mode == 'internal_note' and pilot_ready))


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
        path = storage_id(attachment.get('path'))
        if not path:
            return None
        # Hash from the API, never an arbitrary URL supplied by the customer.
        time.sleep(max(0, self.settings.interval - (time.monotonic() - self.last_call)))
        self.last_call = time.monotonic()
        url, params = MOVIDESK_BASE_URL + '/storage/download', {'token': MOVIDESK_TOKEN, 'id': path}
        for _ in range(4):
            with self.client.stream('GET', url, params=params) as response:
                params = None
                if response.status_code in {301, 302, 303, 307, 308}:
                    target = urljoin(url, response.headers.get('location', ''))
                    if not safe_storage_url(target):
                        return None
                    url = target
                    continue
                if response.status_code != 200:
                    return None
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 4 * 1024 * 1024:
                        return None
                return bytes(raw)
        return None


def plain(value):
    return html.unescape(re.sub(r'<[^>]+>', ' ', str(value or ''))).strip()


def storage_id(value):
    """Return only a Movidesk storage id, never an arbitrary customer URL."""
    value = html.unescape(str(value or '')).strip()
    if re.fullmatch(r'[A-Za-z0-9_-]{8,200}', value):
        return value
    match = re.fullmatch(
        r'https://s3\.amazonaws\.com/movidesk-files/([A-Fa-f0-9]{32})(?:\?[^\s<>"\']+)?',
        value)
    return match.group(1) if match else ''


def safe_storage_url(value):
    """Allow only the documented Movidesk tenant hop and its S3 object hop."""
    try:
        url = urlsplit(str(value or ''))
    except ValueError:
        return False
    host = (url.hostname or '').casefold()
    return (url.scheme == 'https' and not url.username and not url.password and
            (host.endswith('.movidesk.com') or host == 's3.amazonaws.com' or
             host.endswith('.s3.amazonaws.com')))


def action_attachments(action):
    """Combine API attachments with prints pasted into Movidesk's rich editor."""
    found, seen = [], set()
    for attachment in action.get('attachments') or []:
        path = storage_id(attachment.get('path'))
        if not path or path in seen:
            continue
        seen.add(path)
        found.append({**attachment, 'path': path})
    rich = str(action.get('htmlDescription') or '')
    for source in re.findall(r'<img\b[^>]*\bsrc\s*=\s*["\']([^"\']+)', rich, re.I):
        path = storage_id(source)
        if not path or path in seen:
            continue
        seen.add(path)
        found.append({'path': path, 'fileName': f'print_inline_{len(found) + 1}.png',
                      'inline': True})
    return found


def image_mime(name, raw):
    """Detect supported image bytes; an inline print has no trustworthy suffix."""
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if raw.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if len(raw) >= 12 and raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        return 'image/webp'
    import mimetypes
    return mimetypes.guess_type(str(name or ''))[0]


def bot_action(action, settings):
    return ((str(action.get('type')) == '1' and PREFIX in str(action.get('description') or '')) or
            bool(settings.agent_id and str((action.get('createdBy') or {}).get('id')) == settings.agent_id))


def automatic_receipt_action(action):
    """Identify Movidesk's fixed opening acknowledgement, not a technician reply."""
    if str(action.get('type')) != '2':
        return False
    text = plain(action.get('description')).casefold()
    return ('seu chamado foi recebido' in text and
            'iremos realizar a triagem' in text and
            'atendente do suporte' in text)


def actions(ticket, settings):
    return sorted((a for a in ticket.get('actions', []) if not a.get('isDeleted') and
                   not bot_action(a, settings) and not automatic_receipt_action(a) and
                   (plain(a.get('description')) or action_attachments(a))),
                  key=lambda a: (str(a.get('createdDate') or ''), int(a.get('id') or 0)))


def customer_action(action, ticket):
    author = action.get('createdBy') or {}
    client_ids = {str(c.get('id')) for c in ticket.get('clients', []) if not c.get('isDeleted')}
    return str(action.get('type')) == '2' and (str(author.get('profileType')) == '2' or
            (str(author.get('id')) in client_ids and str(author.get('profileType')) != '1'))


def requester_email(action, ticket):
    """Return the email of the client who authored this exact action."""
    author = action.get('createdBy') or {}
    author_id = str(author.get('id') or '')
    if str(author.get('profileType')) == '1':
        return ''
    email = str(author.get('email') or '').strip().casefold()
    if email:
        return email
    for client in ticket.get('clients', []):
        if client.get('isDeleted') or str(client.get('id') or '') != author_id:
            continue
        email = str(client.get('email') or '').strip().casefold()
        if email:
            return email
    return ''


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
    if requester_email(history[-1], ticket) not in settings.requester_emails:
        return False, 'solicitante_fora_do_piloto'
    return True, ''


def identity(ticket, settings):
    latest = actions(ticket, settings)[-1]
    # Metadata updates and our own notes don't change the customer-message key.
    data = {'ticket': ticket['id'], 'action': latest.get('id'),
            'content': latest.get('description'), 'attachments': action_attachments(latest)}
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


def persist_operational_ticket(ticket):
    """Apply one fresh Movidesk ticket to the operational PostgreSQL model.

    This is the lightweight webhook path. It deliberately does not create a
    full snapshot run and does not promote the ticket to approved knowledge.
    """
    import psycopg
    from psycopg.rows import dict_row
    from config import POSTGRES_DSN
    import sync_current_tickets
    from tech_evidence import ensure_single_tenant

    try:
        with psycopg.connect(POSTGRES_DSN, row_factory=dict_row, connect_timeout=5,
                             options='-c statement_timeout=15000 -c lock_timeout=5000') as conn:
            tenant = ensure_single_tenant(conn)
            if not sync_current_tickets.upsert_ticket(conn, tenant, ticket):
                raise RetryJob('Movidesk retornou um identificador inválido')
    except RetryJob:
        raise
    except Exception as exc:
        raise RetryJob('Falha temporária ao atualizar o chamado na base operacional') from exc


def generate_note(ticket, settings, api):
    """Use the same grounded assistant, with current issue and company context."""
    import base64
    import ticket_image
    from agent_control import organization
    from technician_workbench import (connection, build_reply, evidence_for_company, environment_card,
                                      environment_rows, search_context_query, resolved_support_cases, capability_evidence_cases,
                                      company_case_source)
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
    current_attachments = action_attachments(latest)
    outcome = classify_outcome(question)
    if outcome and not current_attachments:
        return {'reply': RESOLVED_REPLY if outcome == 'resolved' else ACKNOWLEDGED_REPLY,
                'sources': [], 'mode': 'outcome', 'attachments': []}
    attachment_notices = []
    for attachment in current_attachments[:2]:
        name = str(attachment.get('fileName') or '')
        raw = api.image(attachment)
        mime = image_mime(name, raw or b'')
        if mime not in {'image/png', 'image/jpeg', 'image/webp'}:
            attachment_notices.append('Anexo não analisado automaticamente: ' + name[:150])
            continue
        try:
            result = ticket_image.transcribe('data:' + mime + ';base64,' + base64.b64encode(raw).decode()) if raw else {}
        except ValueError:
            result = {}
        if result.get('text'):
            question += '\nTexto extraído do print (conferir visualmente): ' + result['text']
        else:
            attachment_notices.append('Print não pôde ser lido automaticamente: ' + name[:150])
    if len(current_attachments) > 2:
        attachment_notices.append('Há outros anexos pendentes de leitura pelo técnico.')
    question += '\n' + '\n'.join(attachment_notices) if attachment_notices else ''
    query = search_context_query(question, list(reversed(messages[:-1])))
    from support_triage import intake
    triage = intake(question, query, messages[:-1])
    if triage:
        triage['attachments'].extend(attachment_notices)
        return triage
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
        reviewed = mapped_replies.match(query)
        sources, cases, evidence_scope = [], [], 'general_knowledge'
        has_directory = conn.execute("SELECT to_regclass('movidesk_company_tickets') AS name").fetchone()['name']
        if org_id and has_directory:
            sources, cases, evidence_scope = evidence_for_company(
                conn, tenant, org_id, query, exclude_ticket_id=ticket.get('id'))
        elif not reviewed:
            import knowledge_access
            sources = knowledge_access.search(conn, query, top_k=5)
        import knowledge_access
        approved = knowledge_access.approved_procedures(conn, query)
        if approved:
            sources = approved
            evidence_scope = 'human_approved_procedure'
        elif reviewed:
            # A reviewed exact-error recipe wins over semantic retrieval, while
            # company cases are still retained for the internal audit trail.
            sources = [reviewed]
            evidence_scope = 'reviewed_procedure'
        company_cases = list(cases)
        capability_leads = capability_evidence_cases(conn, tenant, query, ticket.get('id'))
        # History attached to a solution must describe a completed procedure.
        cases = [case for case in cases if company_case_source(case)]
        known = {str(case['external_id']) for case in cases}
        for case in resolved_support_cases(conn, tenant, query, ticket.get('id')):
            if str(case['external_id']) not in known:
                cases.append(case)
                known.add(str(case['external_id']))
    context = '\n'.join(f"{m['role']}: {m['content'][:1000]}" for m in messages[:-1])
    row = {'movidesk_company_id': org_id, 'company_name': org_name,
           'subject': ticket.get('subject'), 'contact_name': (latest.get('createdBy') or {}).get('businessName')}
    reply, meta = build_reply(row, context, facts, envs, sources, question, company_cases,
                              audience='customer_draft', capability_leads=capability_leads)
    if not meta.get('used'):
        raise RetryJob('Gemini indisponível; nenhuma nota de contingência publicada')
    reference = sources[:1] if sources and sources[0].get('applicable') and sources[0].get('answer_use') != 'investigation_only' else []
    tutorials = [s for s in sources[:5] if s.get('applicable') and s.get('videos')]
    similar = [{'ticket': case.get('external_id'), 'title': case.get('subject'),
                'status': case.get('status') or case.get('base_status'),
                'active': bool(case.get('is_active')),
                'resolution': case.get('solution_statement') if not case.get('is_active') and
                    not case.get('hard_block_reason') and case.get('quality_tier') in ('GOLD', 'SILVER') else None}
               for case in cases[:5]]
    images, media_notices = [], []
    if reference:
        images, media_notices = mapped_replies.note_images(reference[0])
        existing_ids = {str(case['ticket']) for case in similar} | {str(ticket['id'])}
        similar.extend(case for case in reference[0].get('resolved_examples', [])
                       if str(case['ticket']) not in existing_ids)
    else:
        similar = []
    # Capability observations are useful to the reviewing technician even when
    # they are not complete enough to be published as a validated procedure.
    # Keep this distinction explicit: they appear as related tickets without a
    # resolution copied into the customer-facing answer.
    existing_ids = {str(case.get('ticket')) for case in similar} | {str(ticket['id'])}
    for case in capability_leads:
        ticket_id = str(case.get('external_id') or '')
        if ticket_id and ticket_id not in existing_ids:
            similar.append({'ticket': ticket_id, 'title': case.get('subject'),
                            'status': case.get('status') or case.get('base_status'),
                            'active': (case.get('base_status') not in ('Resolved', 'Closed')),
                            'resolution': None})
            existing_ids.add(ticket_id)
    similar = similar[:3]
    return {'reply': reply,
            'sources': [{'ticket': s.get('source_external_id') if s.get('source_type') in
                         ('VALIDATED_TICKET', 'PROBABLE_TICKET') else None,
                         'title': s.get('title'), 'url': s.get('source_url') or (s.get('metadata') or {}).get('source_url'),
                         'provenance': s.get('provenance')} for s in reference + [s for s in tutorials if s not in reference]],
            'videos': [video for source in tutorials for video in source['videos']],
            'similar_tickets': similar, 'evidence_scope': evidence_scope,
            'mode': meta.get('mode'), 'attachments': attachment_notices + media_notices, 'images': images,
            'history_notice': ('Foram localizados menos de três chamados resolvidos com procedimento aplicável.'
                               if reference and len(similar) < 3 else
                               'Tutorial localizado na base; vídeo sem transcrição validada para gerar passos escritos.' if tutorials and not reference else
                               'Há evidências internas da funcionalidade, mas ainda não existe um procedimento completo aprovado.'
                               if capability_leads and not reference else
                               'Diagnóstico pendente: nenhuma solução comprovada foi selecionada.' if not reference else '')}


def render_note(result, marker):
    # Generated/source text is escaped. Only allowlisted reviewed images can
    # become img tags, and their URLs are built by mapped_replies.note_images.
    def inline_text(value):
        chunks, start = [], 0
        # Keep sentence punctuation outside the anchor so the visible address
        # and href are exactly the official URL.
        for found in re.finditer(r'https?://[^\s<>()\[\]{},;]+', value):
            chunks.append(html.escape(value[start:found.start()]))
            url = mapped_replies.safe_url(found.group(0))
            chunks.append('<a href="' + html.escape(url, quote=True) + '">' + html.escape(url) + '</a>'
                          if url else html.escape(found.group(0)))
            start = found.end()
        chunks.append(html.escape(value[start:]))
        rendered = ''.join(chunks)
        rendered = re.sub(r'\*\*([^*\n]+)\*\*', r'<strong>\1</strong>', rendered)
        return re.sub(r'`([^`\n]+)`', r'<code>\1</code>', rendered)

    def image_block(item):
        url = mapped_replies.safe_url(item.get('url'))
        image_src = mapped_replies.safe_image_data(item.get('data_url')) or url
        if not image_src:
            return ''
        name = html.escape(item.get('name') or 'Imagem de apoio')
        caption = html.escape(item.get('caption') or 'Tela de referência do procedimento')
        safe_src = html.escape(image_src, quote=True)
        image = '<img src="' + safe_src + '" alt="' + caption + \
                '" width="900" style="max-width:100%;height:auto">'
        if url:
            image = '<a href="' + html.escape(url, quote=True) + '">' + image + '</a>'
        return '<p><strong>' + name + '</strong></p><p>' + image + '</p><p><small>' + caption + '</small></p>'

    images = result.get('images') or []
    answer_images = [item for item in images if item.get('placement') == 'answer']
    positioned = {}
    for item in images:
        if item.get('placement') == 'answer':
            continue
        step = item.get('after_step')
        if isinstance(step, int) and step > 0:
            positioned.setdefault(step, []).append(item)
    lines = ['<p><strong>Cortex — resposta sugerida ao cliente (nota interna)</strong></p>']
    answer_images_emitted = False
    for raw_line in str(result['reply']).splitlines():
        if not raw_line.strip():
            continue
        numbered = re.match(r'^(\d+)\.\s', raw_line.strip())
        if numbered and not answer_images_emitted:
            for item in answer_images:
                lines.append(image_block(item))
            answer_images_emitted = True
        lines.append('<p>' + inline_text(raw_line.strip()) + '</p>')
        if numbered:
            for item in positioned.get(int(numbered.group(1)), []):
                lines.append(image_block(item))
    if not answer_images_emitted:
        for item in answer_images:
            lines.append(image_block(item))
    for source in result.get('sources', []):
        label = str(source.get('title') or 'Referência')
        if source.get('ticket'):
            label += ' — chamado ' + str(source['ticket'])
        url = mapped_replies.safe_url(source.get('url'))
        if str(source.get('ticket') or '').isdigit():
            url = mapped_replies.safe_url(TICKET_UI_BASE + '/' + str(source['ticket']))
        link = '<a href="' + html.escape(url, quote=True) + '">' + html.escape(label) + '</a>' if url else html.escape(label)
        lines.append('<p>Fonte: ' + link + '</p>')
        if source.get('provenance'):
            lines.append('<p>' + html.escape(source['provenance']) + '</p>')
    similar = result.get('similar_tickets') or []
    for video in result.get('videos') or []:
        url = mapped_replies.safe_url(video.get('url'))
        if url and re.fullmatch(r'https://youtu\.be/[A-Za-z0-9_-]{11}', url):
            lines.append('<p>Tutorial: <a href="' + html.escape(url, quote=True) + '">' +
                         html.escape(str(video.get('title') or 'Vídeo')) + '</a></p>')
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
            if case.get('active'):
                lines.append('<li>Em andamento: indica recorrência; não comprova solução.</li>')
            elif case.get('resolution'):
                lines.append('<li>Resolução registrada: ' + html.escape(plain(case['resolution'])[:700]) + '</li>')
        lines.append('</ul>')
    if result.get('history_notice'):
        lines.append('<p><strong>Observação interna:</strong> ' + html.escape(result['history_notice']) + '</p>')
    remaining_images = [item for item in images if item.get('placement') != 'answer' and not item.get('after_step')]
    if remaining_images:
        lines.append('<p><strong>Imagens do procedimento:</strong></p>')
        for item in remaining_images:
            lines.append(image_block(item))
    for notice in result.get('attachments', []):
        lines.append('<p>' + html.escape(notice) + '</p>')
    origin = mapped_replies.safe_url(os.getenv('CORTEX_PUBLIC_URL', '').rstrip('/'))
    if origin and str(result.get('feedback_ticket') or '').isdigit():
        from urllib.parse import urlencode
        # O técnico recebe um editor dedicado e espaçoso. A fila, os módulos
        # detalhados e as decisões de publicação permanecem no ambiente admin.
        review_url = origin + '/treinamento/tecnico?' + urlencode({
            'ticket': result['feedback_ticket'], 'marker': marker,
        })
        lines.append('<p><a href="' + html.escape(review_url, quote=True) + '">Preparar resolução técnica e enviar à curadoria</a> (uso interno)</p>')
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


def manual_pull_event(event):
    try:
        return bool(json.loads(event['payload_json']).get('CortexManualPull'))
    except (KeyError, TypeError, ValueError):
        return False


def process(event, settings, api, generator=generate_note, sync_only_reason=''):
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
    # Make the exact ticket visible to dashboards and retrieval before the AI
    # reasons about it. A database outage retries the event instead of allowing
    # a response based on an older nightly snapshot.
    persist_operational_ticket(ticket)
    if sync_only_reason:
        finish(event, 'skipped', sync_only_reason)
        return
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
        result['feedback_ticket'] = ticket['id']
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
    persist_operational_ticket(confirmed)
    finish(event, 'completed', 'nota_interna_publicada')


def run_once(settings, api, generator=generate_note):
    ceiling = settings
    settings = effective_settings(ceiling)
    with _connect() as db:
        db.execute('INSERT OR REPLACE INTO note_worker_status VALUES (1,?,?)', (time.time(), settings.mode))
    if settings.mode == 'disabled':
        return False
    event = claim(settings)
    if not event:
        return False
    manual = manual_pull_event(event)
    if ceiling.runtime_control and ceiling.mode == 'internal_note' and not manual and not settings.automatic_enabled:
        # The AI toggle controls note generation, not operational freshness.
        # Consume the webhook and upsert this exact ticket without spending
        # Gemini quota or publishing anything.
        process(event, settings, api, generator, sync_only_reason='automatico_desativado')
        return True
    if manual:
        if not settings.manual_internal_note:
            finish(event, 'skipped', 'processamento_manual_indisponivel')
            return True
        settings = replace(settings, mode='internal_note')
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
