"""Human-labeled local evaluation set; imported solutions are references, never gold labels."""
from __future__ import annotations

import json
import time

import psycopg

import agent_operations
import knowledge_curation as curation
import triage_router
from config import POSTGRES_DSN


def _schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS evaluation_cases (
        ticket_id INTEGER PRIMARY KEY, subject TEXT NOT NULL, problem TEXT NOT NULL,
        historical_solution TEXT NOT NULL, suggested_modules TEXT NOT NULL,
        expected_modules TEXT NOT NULL DEFAULT '[]', expected_answer TEXT NOT NULL DEFAULT '',
        labeled_at REAL, created_at REAL NOT NULL)''')


def seed(limit=24):
    limit = max(1, min(int(limit), 40))
    with psycopg.connect(POSTGRES_DSN, connect_timeout=5) as conn:
        rows = conn.execute('''SELECT external_id,subject,problem_statement,solution_statement
            FROM refined_tickets
            WHERE quality_tier IN ('GOLD','SILVER') AND subject IS NOT NULL
              AND length(problem_statement)>30
            ORDER BY external_id DESC LIMIT 2500''').fetchall()
    buckets = {}
    for ticket_id, subject, problem, solution in rows:
        suggestion = triage_router.route(subject, problem)
        module = suggestion['modules'][0]
        if module == 'unclassified':
            continue
        bucket = buckets.setdefault(module, [])
        if len(bucket) < 4:
            bucket.append((ticket_id, subject, problem, solution or '', suggestion['modules']))
    selected = []
    for index in range(4):
        for module in curation.MODULE_NAMES:
            bucket = buckets.get(module, [])
            if len(bucket) > index:
                selected.append(bucket[index])
            if len(selected) >= limit:
                break
        if len(selected) >= limit:
            break
    with agent_operations.connect() as db:
        _schema(db)
        before = db.execute('SELECT COUNT(*) FROM evaluation_cases').fetchone()[0]
        for ticket_id, subject, problem, solution, modules in selected:
            db.execute('''INSERT OR IGNORE INTO evaluation_cases
                (ticket_id,subject,problem,historical_solution,suggested_modules,created_at)
                VALUES (?,?,?,?,?,?)''', (ticket_id, subject[:500], problem[:12000],
                                          solution[:8000], json.dumps(modules), time.time()))
        after = db.execute('SELECT COUNT(*) FROM evaluation_cases').fetchone()[0]
    return {'added': after-before, 'total': after}


def list_cases():
    with agent_operations.connect() as db:
        _schema(db)
        rows = [dict(row) for row in db.execute('''SELECT * FROM evaluation_cases
            ORDER BY labeled_at IS NOT NULL, ticket_id DESC''')]
    for row in rows:
        row['suggested_modules'] = json.loads(row['suggested_modules'])
        row['expected_modules'] = json.loads(row['expected_modules'])
        row['current_route'] = triage_router.route(row['subject'], row['problem'])
    labeled = [row for row in rows if row['labeled_at'] is not None]
    primary_correct = sum(row['current_route']['modules'][0] in row['expected_modules'] for row in labeled)
    coverage_correct = sum(set(row['expected_modules']).issubset(row['current_route']['modules']) for row in labeled)
    return {'items': rows, 'total': len(rows), 'labeled': len(labeled),
            'primary_correct': primary_correct, 'coverage_correct': coverage_correct,
            'module_options': [{'key': key, 'label': label} for key, label, _ in curation.MODULES
                               if key != 'unclassified']}


def label(ticket_id, expected_modules, expected_answer):
    allowed = set(curation.MODULE_NAMES) - {'unclassified'}
    if not isinstance(expected_modules, list) or not expected_modules or not set(expected_modules) <= allowed:
        raise ValueError('Selecione ao menos um assunto válido.')
    expected_answer = (expected_answer or '').strip()
    if len(expected_answer) < 15 or len(expected_answer) > 8000:
        raise ValueError('Descreva os critérios da resposta esperada (15 a 8000 caracteres).')
    with agent_operations.connect() as db:
        _schema(db)
        result = db.execute('''UPDATE evaluation_cases SET expected_modules=?,expected_answer=?,labeled_at=?
            WHERE ticket_id=?''', (json.dumps(sorted(set(expected_modules))), expected_answer,
                                  time.time(), ticket_id))
        if result.rowcount != 1:
            raise KeyError(ticket_id)
    agent_operations.audit('Caso de avaliação revisado', {'ticket_id': ticket_id,
        'expected_modules': sorted(set(expected_modules))})
    return {'ticket_id': ticket_id, 'saved': True}
