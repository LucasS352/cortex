"""Deterministic, snapshot-based operational triage. No inferred SLA or resolution."""
from collections import Counter
from datetime import datetime, timezone
import unicodedata

QUEUES = {'support': 'Ação do suporte', 'development': 'Desenvolvimento',
          'customer': 'Retorno do cliente', 'other': 'Outras dependências'}
SUPPORT_WAITING_DEVELOPMENT = 'Suporte aguardando desenvolvimento'

def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value or '')
                   if not unicodedata.combining(c)).casefold().strip()

def department_for(team):
    if normalized(team) in {'desenvolvimento', 'desenvolvimento - dev'}:
        return 'Desenvolvimento'
    return team or 'Sem equipe'


def responsibility_for(team, status):
    if normalized(team).startswith('suporte') and 'aguardando desenvolvimento' in normalized(status):
        return SUPPORT_WAITING_DEVELOPMENT
    return department_for(team)


def queue_for(status):
    value = normalized(status)
    if value.startswith('dev -') or 'aguardando desenvolvimento' in value:
        return 'development'
    if 'aguardando retorno do cliente' in value:
        return 'customer'
    if value in {'novo', 'em atendimento', 'retorno recebido', 'recebido suporte',
                 'recebido desenvolvimento', 'atualizar sistema', 'analise de solicitacao',
                 'a ser distribuido'} or value.startswith('sup -'):
        return 'support'
    return 'other'

def urgent(value):
    return any(term in normalized(value) for term in ('urgent', 'alta', 'critic'))

def age_days(created, reference):
    if not created or not reference:
        return None
    def aware(d):
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    return max(0, (aware(reference) - aware(created)).days)

def enrich(rows, reference, topic_fn):
    return [{**r, 'queue': queue_for(r.get('status')), 'is_urgent': urgent(r.get('urgency')),
             'age_days': age_days(r.get('created_date'), reference),
             'topic': topic_fn(r.get('subject') or '', r.get('problem_statement') or '')}
            for r in rows]

def select(rows, queue='', age='', high=False):
    def matches_age(r):
        n = r['age_days']
        if not age: return True
        if n is None: return age == 'unknown'
        return {'0-7': n <= 7, '8-30': 8 <= n <= 30, '31-90': 31 <= n <= 90,
                '91+': n > 90, '30+': n > 30}.get(age, False)
    return [r for r in rows if (not queue or r['queue'] == queue)
            and matches_age(r) and (not high or r['is_urgent'])]

def priority_key(r):
    return (not r['is_urgent'], normalized(r.get('status')) != 'retorno recebido',
            -(r['age_days'] if r['age_days'] is not None else -1), str(r['external_id']))

def public_ticket(r):
    return {'id': r['external_id'], 'subject': r.get('subject') or 'Sem assunto',
            'organization': r.get('organization') or 'Não identificada',
            'status': r.get('status') or 'Sem status', 'urgency': r.get('urgency') or 'Normal',
            'topic': r['topic'], 'queue': r['queue'], 'age_days': r['age_days'],
            'created_at': r['created_date'].isoformat() if r.get('created_date') else None,
            'reason': ('Prioridade alta / urgente' if r['is_urgent'] else
                       'Cliente retornou' if normalized(r.get('status')) == 'retorno recebido'
                       else 'Mais antigo na fila')}

def summarize(rows, queue=''):
    selected = select(rows, queue)
    topics = []
    for name in sorted({r['topic'] for r in selected}):
        group = [r for r in selected if r['topic'] == name]
        topics.append({'name': name, 'count': len(group),
                       'urgent': sum(r['is_urgent'] for r in group),
                       'aged': sum(r['age_days'] is not None and r['age_days'] > 30 for r in group),
                       'clients': len({r['organization'] for r in group if r.get('organization')})})
    topics.sort(key=lambda r: (-r['count'], r['name']))
    ages = [r['age_days'] for r in selected if r['age_days'] is not None]
    clients = Counter(r['organization'] for r in selected if r.get('organization'))
    # Global view highlights support cases; selecting a queue examines that queue instead.
    candidates = selected if queue else select(rows, 'support')
    return {'departments': [{'name': name, 'count': count} for name, count in Counter(responsibility_for(r.get('owner_team'), r.get('status')) for r in rows).most_common()], 'scope': queue, 'total': len(selected), 'urgent': sum(r['is_urgent'] for r in selected),
            'aged': sum(n > 30 for n in ages), 'missing_dates': len(selected) - len(ages),
            'queues': [{'key': key, 'name': name, 'count': sum(r['queue'] == key for r in rows)}
                       for key, name in QUEUES.items()],
            'topics': topics,
            'ages': [{'key': key, 'name': label, 'count': len(select(selected, age=key))}
                     for key, label in [('0-7', 'Até 7 dias'), ('8-30', '8 a 30 dias'),
                                        ('31-90', '31 a 90 dias'), ('91+', 'Mais de 90 dias'),
                                        ('unknown', 'Sem data')]],
            'clients_total': len(clients),
            'clients': [{'name': name, 'count': count} for name, count in clients.most_common(5)],
            'unidentified_clients': sum(r.get('company_id') == '__unidentified__' for r in selected),
            'ambiguous_clients': sum(r.get('company_id') == '__ambiguous__' for r in selected),
            'without_client': sum(r.get('company_issue') == 'no_client' for r in selected),
            'person_without_company': sum(r.get('company_issue') == 'person_without_company' for r in selected),
            'priority': [public_ticket(r) for r in sorted(candidates, key=priority_key)[:8]],
            'priority_scope': queue or 'support'}
