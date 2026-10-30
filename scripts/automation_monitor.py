"""Operational diagnostics from actual local receipts, never inferred success."""
import json
import re
import time

from movidesk_webhook import _connect
from movidesk_note_worker import Settings, effective_settings, eligible, identity
from agent_control import organization
from automation_rules import evaluate

LABELS = {
    'fora_da_triagem':'Chamado fora de Triagem',
    'automacao_pausada':'Automação pausada',
    'liberacao_sem_data_registrada':'Liberação ainda não confirmada',
    'data_de_abertura_indisponivel':'Data de abertura indisponível',
    'chamado_anterior_a_liberacao':'Chamado criado antes da liberação',
    'limite_operacional': 'Limite operacional de geração atingido',
    'fora_do_piloto': 'Empresa não autorizada', 'solicitante_fora_do_piloto': 'Solicitante não autorizado',
    'automatico_desativado': 'Publicação automática desabilitada',
    'sem_nova_mensagem_do_cliente': 'Última ação não é uma nova mensagem do cliente',
    'status_nao_elegivel': 'Status fora da regra', 'evento_nao_elegivel': 'Evento fora da regra',
    'automacao_empresa_desativada': 'Automação desabilitada para a empresa',
    'chamado_finalizado': 'Chamado finalizado', 'nota_interna_publicada': 'Nota interna publicada',
    'mensagem_ja_processada': 'Mensagem já processada', 'nota_gerada_sem_publicacao': 'Prévia gerada sem publicação',
    'nota_confirmada_no_movidesk': 'Publicação confirmada', 'publicacao_rejeitada': 'Publicação rejeitada',
    'tentativas_esgotadas': 'Tentativas esgotadas; revisão necessária',
    'contexto_insuficiente': 'Evidência insuficiente; minuta retida para revisão',
    'politica_alterada_durante_analise': 'Autorização ou publicação desabilitada durante a análise',
}


def safe_error(value):
    # Worker errors are intentionally operational messages, never raw upstream
    # bodies. Redact defensively when reading older stored diagnostics.
    text = re.sub(r'https?://\S+', '[URL omitida]', str(value or ''))
    text = re.sub(r'(?i)(token|password|senha|secret|api_key)\s*[=:]\s*\S+', r'\1=[omitido]', text)
    return text[:400]


def report(state, ticket_id=None):
    summary = {}
    with _connect() as db:
        worker = db.execute('SELECT heartbeat,mode FROM note_worker_status WHERE id=1').fetchone()
        rows = db.execute('SELECT * FROM webhook_events ' + ('WHERE ticket_id=? ' if ticket_id else '') +
                          'ORDER BY received_at DESC LIMIT 100', (ticket_id,) if ticket_id else ()).fetchall()
        snapshots = {r['ticket_id']: json.loads(r['payload_json']) for r in db.execute(
            'SELECT * FROM ticket_webhook_snapshots ' + ('WHERE ticket_id=?' if ticket_id else ''),
            (ticket_id,) if ticket_id else ())}
        for row in db.execute('SELECT state,count(*) AS n FROM webhook_events GROUP BY state'):
            summary[row['state']] = row['n']
        sent = {r[0] for r in db.execute("SELECT key FROM internal_note_outbox WHERE state='sent'")}
    items = []
    for row in rows:
        ticket = snapshots.get(row['ticket_id'], {})
        org, name, _ = organization(ticket.get('clients'))
        context = json.loads(row['context_json'] or '{}')
        org, name = context.get('company_id', org), context.get('company', name)
        d = json.loads(row['decision_json'] or '{}')
        items.append({'ticket_id': row['ticket_id'], 'company_id': org, 'company': name,
                      'status': context.get('status', ticket.get('status')), 'received_at': row['received_at'], 'state': row['state'],
                      'event': d.get('event', 'Não classificado'), 'rule': d.get('rule'),
                      'outcome': row['outcome'], 'reason': LABELS.get(row['outcome'], row['outcome']) or row['state'],
                      'error': bool(row['last_error']), 'attempts': row['attempts'],
                      'error_detail': safe_error(row['last_error']),
                      'duration': round(row['finished_at'] - row['received_at'], 2) if row['finished_at'] else None,
                      'trace': json.loads(row['trace_json'] or '[]')})
    alive = bool(worker and time.time() - worker['heartbeat'] < 900)
    try:
        settings = effective_settings(Settings.load())
        ceiling = Settings.load().mode
    except ValueError:
        settings, ceiling = Settings(), 'invalid'
    result = {'items': items, 'counts': summary, 'worker_alive': alive,
              'worker': dict(worker) if worker else None, 'mode': settings.mode, 'ceiling': ceiling,
              'rules': state.get('automation_rules', {}), 'labels': LABELS,
              'checks': [{'label': 'Governança habilitada', 'ok': not state.get('pilot_paused', True)},
                         {'label': 'Empresas autorizadas', 'ok': bool(state.get('pilot_organization_ids'))},
                         {'label': 'Publicação automática habilitada', 'ok': bool(state.get('auto_internal_note'))},
                         {'label': 'Servidor permite nota privada', 'ok': ceiling == 'internal_note'},
                         {'label': 'Worker com atividade recente', 'ok': alive}]}
    if ticket_id:
        ticket = snapshots.get(ticket_id)
        if ticket:
            ok, reason = eligible(ticket, settings)
            from automation_scope import evaluate as scope_evaluate
            scope = scope_evaluate(ticket, state, organization(ticket.get('clients'))[0])
            if not scope['allowed']: ok, reason = False, scope['reason']
            key = identity(ticket, settings)[0] if ok else None
            result['ticket'] = {'eligible': ok, 'reason': LABELS.get(reason, reason),
                                'published_message': key in sent, 'snapshot_only': True,
                                'snapshot_notice': 'Verificação sobre a última fotografia recebida. Evento/status avaliados conforme histórico registrado.'}
        else:
            result['ticket'] = {'eligible': False, 'reason': 'Nenhuma fotografia recebida. Confira o gatilho e a entrega no Movidesk.'}
    return result
