"""Validated, opt-in automation rules. Existing installations keep legacy behavior."""
from fastapi import HTTPException

EVENTS = {'status_changed': 'Entrada em status', 'customer_message': 'Nova mensagem do cliente'}
DEFAULT = {'enabled': False, 'events': ['customer_message'], 'statuses': [], 'companies': {}}


def validate(value, known_statuses):
    if not isinstance(value, dict) or set(value) - set(DEFAULT):
        raise HTTPException(422, 'Regra de automação inválida.')
    result = {**DEFAULT, **value}
    if type(result['enabled']) is not bool:
        raise HTTPException(422, 'Habilitação inválida.')
    for rule in [result, *result['companies'].values()] if isinstance(result['companies'], dict) else []:
        if not isinstance(rule, dict) or type(rule.get('enabled', True)) is not bool:
            raise HTTPException(422, 'Exceção de empresa inválida.')
        events, statuses = rule.get('events', result['events']), rule.get('statuses', result['statuses'])
        if not isinstance(events, list) or not events or any(e not in EVENTS for e in events):
            raise HTTPException(422, 'Selecione eventos implementados.')
        if not isinstance(statuses, list) or any(s not in known_statuses for s in statuses):
            raise HTTPException(422, 'Selecione status existentes no cadastro local.')
    if not isinstance(result['companies'], dict) or len(result['companies']) > 100:
        raise HTTPException(422, 'Exceções de empresas inválidas.')
    for key, rule in result['companies'].items():
        import re
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', key) or not isinstance(rule, dict) or set(rule) - {'enabled', 'events', 'statuses'}:
            raise HTTPException(422, 'Exceção de empresa inválida.')
    return result


def evaluate(ticket, payload, previous, rules, organization_id):
    from automation_scope import status_block
    blocked = status_block(ticket)
    if blocked:
        return {'allowed':False,'event':'snapshot','reason':blocked,'rule':'escopo_obrigatorio'}
    if not rules.get('enabled'):
        return {'allowed': True, 'event': 'legacy', 'reason': '', 'rule': 'compatibilidade'}
    rule = {**rules, **rules.get('companies', {}).get(organization_id, {})}
    status = ticket.get('status') or ''
    transition = bool(previous and previous.get('status') != status)
    observed = set()
    if transition:
        observed.add('status_changed')
    old_ids = {str(a.get('id')) + ':' + str(a.get('description')) for a in (previous or {}).get('actions', [])}
    from movidesk_note_worker import customer_action
    if any(customer_action(a, ticket) and str(a.get('id')) + ':' + str(a.get('description')) not in old_ids
           for a in ticket.get('actions', []) if not a.get('isDeleted')):
        observed.add('customer_message')
    matched = sorted(observed & set(rule.get('events', [])))
    reason = ('automacao_empresa_desativada' if not rule.get('enabled') else
              'evento_nao_elegivel' if not matched else '')
    return {'allowed': not reason, 'event': ', '.join(sorted(observed)) or 'snapshot',
            'reason': reason, 'rule': organization_id if organization_id in rules.get('companies', {}) else 'global',
            'transition': transition, 'matched': matched, 'status': status}
