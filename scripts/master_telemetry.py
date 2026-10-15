"""Daily Master inventory; internal collector only, durable PostgreSQL history."""
import json
import uuid

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, HTTPException, Query

from config import POSTGRES_DSN
from master_config import MasterConfigRequest, normalize, changed_fields
from tech_evidence import ensure_single_tenant

router = APIRouter(prefix='/api/v1/master', tags=['Inventário diário ERP Master'])


def connection():
    return psycopg.connect(POSTGRES_DSN, row_factory=dict_row, connect_timeout=4,
                           options='-c statement_timeout=8000 -c lock_timeout=5000')


def save_report(conn, report):
    tenant = ensure_single_tenant(conn)
    env_id = str(uuid.uuid4())
    conn.execute('''INSERT INTO master_environments(id,tenant_id,installation_key,company_key)
        VALUES(%s,%s,%s,%s) ON CONFLICT (tenant_id,installation_key) DO NOTHING''',
        (env_id, tenant, report['installation_key'], report['company_key']))
    env = conn.execute('''SELECT * FROM master_environments
        WHERE tenant_id=%s AND installation_key=%s FOR UPDATE''', (tenant, report['installation_key'])).fetchone()
    if env['company_key'] != report['company_key']:
        raise HTTPException(409, 'Identificador da instalação já associado a outra empresa. Requer conciliação explícita.')
    prior = conn.execute('''SELECT id,payload_hash,promoted FROM master_environment_snapshots
        WHERE environment_id=%s AND request_key=%s''', (env['id'], report['request_key'])).fetchone()
    if prior:
        if prior['payload_hash'] != report['payload_hash']:
            raise HTTPException(409, 'event_id já recebido com outro conteúdo.')
        return {'status': 'duplicate', 'environment_id': str(env['id']), 'snapshot_id': str(prior['id']),
                'promoted': prior['promoted'], 'is_beta': report['payload']['is_beta'], 'warnings': report['payload']['warnings']}
    previous = conn.execute('SELECT sanitized_payload FROM master_environment_snapshots WHERE id=%s',
                            (env['latest_snapshot_id'],)).fetchone() if env['latest_snapshot_id'] else None
    changes = changed_fields(previous['sanitized_payload'] if previous else None, report['payload'])
    promote = report['clock_trusted'] and (env['latest_observed_at'] is None or report['observed_at'] > env['latest_observed_at'])
    snapshot_id = str(uuid.uuid4())
    conn.execute('''INSERT INTO master_environment_snapshots
        (id,environment_id,request_key,payload_hash,observed_at,sanitized_payload,changed_fields,promoted)
        VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)''',
        (snapshot_id, env['id'], report['request_key'], report['payload_hash'], report['observed_at'],
         json.dumps(report['payload'], ensure_ascii=False), json.dumps(changes), promote))
    conn.execute('''UPDATE master_environments SET last_received_at=now(),
        latest_snapshot_id=CASE WHEN %s THEN %s::uuid ELSE latest_snapshot_id END,
        latest_observed_at=CASE WHEN %s THEN %s ELSE latest_observed_at END WHERE id=%s''',
        (promote, snapshot_id, promote, report['observed_at'], env['id']))
    warnings = list(report['payload']['warnings'])
    if not promote and report['clock_trusted']:
        warnings.append('older_or_same_timestamp_preserved_in_history_only')
    return {'status': 'stored', 'environment_id': str(env['id']), 'snapshot_id': snapshot_id,
            'promoted': promote, 'is_beta': report['payload']['is_beta'], 'changed_fields': changes, 'warnings': warnings}


@router.post('/telemetry/config/validate')
def validate_config(req: MasterConfigRequest):
    report = normalize(req)
    payload = report['payload']
    return {'status': 'valid', 'is_beta': payload['is_beta'], 'beta_ticket': payload['beta_ticket'],
            'version': payload['version'], 'build_local': payload['build_local'],
            'identity_status': payload['identity_status'], 'connection_count': payload['connection_count'],
            'active_connection_occurrences': payload['active_connection_occurrences'],
            'sanitization': payload['sanitization'], 'warnings': payload['warnings']}


@router.post('/telemetry/config')
def ingest_config(req: MasterConfigRequest):
    report = normalize(req)
    try:
        with connection() as conn:
            return save_report(conn, report)
    except (psycopg.Error, ValueError):
        raise HTTPException(503, 'Inventário indisponível. Reenvie o mesmo event_id após a recuperação.')


@router.get('/environments')
def environments(beta_only: bool = False, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=100000)):
    try:
        with connection() as conn:
            tenant = ensure_single_tenant(conn)
            rows = conn.execute('''SELECT e.id, e.latest_snapshot_id, e.last_received_at, e.latest_observed_at,
                s.sanitized_payload - 'config_sections' AS environment,
                e.latest_observed_at IS NULL OR e.latest_observed_at < now() - interval '36 hours' AS stale
                FROM master_environments e LEFT JOIN master_environment_snapshots s ON s.id=e.latest_snapshot_id
                WHERE e.tenant_id=%s AND (NOT %s OR s.sanitized_payload->>'is_beta'='true')
                ORDER BY e.last_received_at DESC,e.id LIMIT %s OFFSET %s''',
                (tenant, beta_only, limit + 1, offset)).fetchall()
            return {'items': rows[:limit], 'next_offset': offset + limit if len(rows) > limit else None}
    except (psycopg.Error, ValueError):
        raise HTTPException(503, 'Inventário indisponível ou migração ainda não aplicada.')


@router.get('/environments/{environment_id}/history')
def history(environment_id: uuid.UUID, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=100000)):
    try:
        with connection() as conn:
            tenant = ensure_single_tenant(conn)
            env = conn.execute('SELECT id FROM master_environments WHERE id=%s AND tenant_id=%s', (environment_id, tenant)).fetchone()
            if not env:
                raise HTTPException(404, 'Ambiente não encontrado.')
            rows = conn.execute('''SELECT id,observed_at,received_at,promoted,changed_fields,
                sanitized_payload - 'config_sections' AS environment FROM master_environment_snapshots
                WHERE environment_id=%s ORDER BY observed_at DESC, received_at DESC,id DESC LIMIT %s OFFSET %s''',
                (environment_id, limit + 1, offset)).fetchall()
            return {'items': rows[:limit], 'next_offset': offset + limit if len(rows) > limit else None}
    except (psycopg.Error, ValueError):
        raise HTTPException(503, 'Histórico indisponível.')


@router.get('/environments/{environment_id}/snapshots/{snapshot_id}')
def snapshot(environment_id: uuid.UUID, snapshot_id: uuid.UUID):
    try:
        with connection() as conn:
            tenant = ensure_single_tenant(conn)
            row = conn.execute('''SELECT s.id,s.observed_at,s.received_at,s.sanitized_payload AS environment
                FROM master_environment_snapshots s JOIN master_environments e ON e.id=s.environment_id
                WHERE e.tenant_id=%s AND e.id=%s AND s.id=%s''', (tenant, environment_id, snapshot_id)).fetchone()
            if not row:
                raise HTTPException(404, 'Registro não encontrado.')
            return row
    except (psycopg.Error, ValueError):
        raise HTTPException(503, 'Registro indisponível.')
