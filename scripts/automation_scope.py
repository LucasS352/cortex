"""Mandatory admission scope for automatic ticket assistance."""
from datetime import datetime, timezone
import unicodedata
import json
import sqlite3
import re
from pathlib import Path
from contextlib import closing


def restore_release_dates(state, audit_path):
    """Recover legacy authorization times only from the existing server audit."""
    selected = set(state.get('pilot_organization_ids') or [])
    dates = dict(state.get('company_authorized_at') or {})
    release = state.get('automation_released_at')
    missing = selected - dates.keys()
    path = Path(audit_path)
    if (release and not missing) or not path.is_file():
        return state
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            for stamp, detail in db.execute("SELECT created,detail FROM audit WHERE action=? ORDER BY id DESC",
                                            ('Configuração do agente',)):
                try:
                    entry = json.loads(detail)
                    before, after = entry.get('before') or {}, entry.get('after') or {}
                    if not isinstance(before,dict) or not isinstance(after,dict): continue
                    active = not after.get('pilot_paused',True)
                    started = before.get('pilot_paused',True) or (
                        after.get('auto_internal_note',False) and not before.get('auto_internal_note',False))
                    if not release and active and started: release = stamp
                    added = set(after.get('pilot_organization_ids') or []) - set(before.get('pilot_organization_ids') or [])
                    for company in added & missing: dates[company] = stamp
                    missing -= added
                    if release and not missing: break
                except (ValueError,TypeError,AttributeError):
                    continue
    except sqlite3.Error:
        return state
    return {**state, 'automation_released_at':release, 'company_authorized_at':dates}


def status_block(ticket):
    def normalized(value):
        if isinstance(value,dict): value = value.get('name') or value.get('businessName') or ''
        return ''.join(c for c in unicodedata.normalize('NFKD',str(value or '').casefold())
                       if not unicodedata.combining(c)).strip()
    status = normalized(ticket.get('status'))
    base = normalized(ticket.get('baseStatus') or ticket.get('base_status'))
    if base in {'resolved','closed','canceled','cancelled'} or status in {'resolvido','fechado','cancelado','resolved','closed','canceled','cancelled'}:
        return 'chamado_finalizado'
    if not status: return 'status_indisponivel'
    team = normalized(ticket.get('ownerTeam') or ticket.get('owner_team'))
    labels = status + ' ' + team
    if re.search(r'\b(?:dev|desenvolvimento|development|coding|codificacao)\b',labels):
        return 'status_bloqueado_desenvolvimento'
    if re.search(r'\b(?:comercial|comerciais|commercial|vendas?|sales)\b',labels):
        return 'status_bloqueado_comercial'
    return ''


def evaluate(ticket, state, organization_id):
    if state.get('pilot_paused', True):
        return {'allowed':False, 'reason':'automacao_pausada'}
    reason = status_block(ticket)
    if reason: return {'allowed':False,'reason':reason}
    start = state.get('automation_released_at')
    company = (state.get('company_authorized_at') or {}).get(str(organization_id))
    if not isinstance(start, (float,int)) or not isinstance(company, (float,int)) or start <= 0 or company <= 0:
        return {'allowed':False, 'reason':'liberacao_sem_data_registrada'}
    cutoff = max(start, company)
    raw = ticket.get('createdDate') or ticket.get('created_date')
    try:
        created = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).replace('Z','+00:00'))
        if created.tzinfo is None:
            # Movidesk documents createdDate as UTC and returns it without Z.
            created = created.replace(tzinfo=timezone.utc)
        allowed = created.timestamp() > cutoff
    except (ValueError, TypeError, OverflowError):
        return {'allowed':False,'reason':'data_de_abertura_indisponivel'}
    return {'allowed':allowed, 'reason':'' if allowed else 'chamado_anterior_a_liberacao',
            'released_at':datetime.fromtimestamp(cutoff,timezone.utc).isoformat()}
