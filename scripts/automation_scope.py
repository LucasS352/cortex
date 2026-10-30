"""Mandatory admission scope for automatic ticket assistance."""
from datetime import datetime, timezone
import unicodedata


def evaluate(ticket, state, organization_id):
    if state.get('pilot_paused', True):
        return {'allowed':False, 'reason':'automacao_pausada'}
    status = ''.join(c for c in unicodedata.normalize('NFKD', str(ticket.get('status') or '').strip().casefold())
                     if not unicodedata.combining(c))
    if status != 'triagem':
        return {'allowed':False, 'reason':'fora_da_triagem'}
    start = state.get('automation_released_at')
    company = (state.get('company_authorized_at') or {}).get(str(organization_id))
    if not isinstance(start, (float,int)) or not isinstance(company, (float,int)) or start <= 0 or company <= 0:
        return {'allowed':False, 'reason':'liberacao_sem_data_registrada'}
    cutoff = max(start, company)
    raw = ticket.get('createdDate') or ticket.get('created_date')
    try:
        created = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).replace('Z','+00:00'))
        if created.tzinfo is None:
            raise ValueError('Creation time must include timezone')
        allowed = created.timestamp() > cutoff
    except (ValueError, TypeError, OverflowError):
        return {'allowed':False,'reason':'data_de_abertura_indisponivel'}
    return {'allowed':allowed, 'reason':'' if allowed else 'chamado_anterior_a_liberacao',
            'released_at':datetime.fromtimestamp(cutoff,timezone.utc).isoformat()}
