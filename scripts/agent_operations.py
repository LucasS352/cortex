"""Persistent operational history and admission limits for local draft generation."""
import json
import hashlib
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException

OPERATIONS_DB = Path(__file__).parent / "output" / "agent_operations.sqlite3"


@contextmanager
def connect():
    OPERATIONS_DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(OPERATIONS_DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, subject TEXT NOT NULL, problem TEXT NOT NULL,
            started REAL NOT NULL, finished REAL, duration REAL, status TEXT NOT NULL,
            draft TEXT, sources TEXT NOT NULL, error TEXT,
            verdict TEXT, review_note TEXT, reviewed REAL
        );
        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY, created REAL NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS runs_started ON runs(started);
        CREATE TABLE IF NOT EXISTS run_workflow (
            run_id TEXT PRIMARY KEY, internal_ticket_id INTEGER NOT NULL,
            relation_verified_at REAL NOT NULL, approved_text TEXT,
            approved_by TEXT, approved_at REAL, approval_note TEXT,
            approved_sha256 TEXT, delivery_state TEXT NOT NULL DEFAULT 'awaiting_review'
        );
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
    if 'ticket_id' not in columns:
        db.execute("ALTER TABLE runs ADD COLUMN ticket_id INTEGER")
    if 'decision' not in columns:
        db.execute("ALTER TABLE runs ADD COLUMN decision TEXT")
    workflow_columns = {row[1] for row in db.execute("PRAGMA table_info(run_workflow)")}
    if 'approval_action_id' not in workflow_columns:
        db.execute("ALTER TABLE run_workflow ADD COLUMN approval_action_id TEXT")
    try:
        with db:
            yield db
    finally:
        db.close()


def audit(action, detail):
    with connect() as db:
        db.execute("INSERT INTO audit(created,action,detail) VALUES(?,?,?)", (time.time(), action, json.dumps(detail, ensure_ascii=False)))


def begin(subject, problem, sources, controls, ticket_id=None, decision=None):
    now = time.time()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # A synchronous request is bounded by the upstream timeout. Expired leases
        # after interruption must not block the next application session.
        db.execute("UPDATE runs SET status='interrupted', finished=?, error='Processamento interrompido; resultado não registrado.' WHERE status='running' AND started<?", (now, now - 300))
        active = db.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0]
        recent = db.execute("SELECT COUNT(*) FROM runs WHERE started>?", (now - 60,)).fetchone()[0]
        last = db.execute("SELECT MAX(started) FROM runs").fetchone()[0]
        wait = max(0, math.ceil(controls['min_interval_seconds'] - (now - (last or 0))))
        if active >= controls['max_concurrent'] or recent >= controls['max_per_minute'] or wait:
            raise HTTPException(429, "Limite de processamento atingido. Aguarde e tente novamente.", headers={"Retry-After": str(max(1, wait, 60 if recent >= controls['max_per_minute'] else 1))})
        run_id = uuid.uuid4().hex
        db.execute("INSERT INTO runs(id,subject,problem,started,status,sources,ticket_id,decision) VALUES(?,?,?,?,?,?,?,?)",
                   (run_id, subject[:500], problem[:12000], now, 'running',
                    json.dumps(sources, ensure_ascii=False), ticket_id,
                    json.dumps(decision or {}, ensure_ascii=False)))
    return run_id


def finish(run_id, draft=None, error=None):
    now = time.time()
    with connect() as db:
        db.execute("UPDATE runs SET finished=?,duration=?-started,status=?,draft=?,error=? WHERE id=?", (now, now, 'failed' if error else 'completed', draft, error, run_id))


def review(run_id, verdict, note):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Resposta não encontrada.")
        if row['status'] != 'completed':
            raise HTTPException(409, "Somente respostas concluídas podem ser avaliadas.")
        approved = db.execute("SELECT approved_at FROM run_workflow WHERE run_id=?", (run_id,)).fetchone()
        if approved and approved['approved_at'] is not None:
            raise HTTPException(409, "Aprovação do piloto já registrada; esta avaliação não pode mais mudar.")
        db.execute("UPDATE runs SET verdict=?,review_note=?,reviewed=? WHERE id=?", (verdict, note, time.time(), run_id))
        db.execute("INSERT INTO audit(created,action,detail) VALUES(?,?,?)", (time.time(), 'Avaliação de resposta', json.dumps({'run_id': run_id, 'verdict': verdict, 'note': note}, ensure_ascii=False)))


def run_detail(run_id):
    with connect() as db:
        row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Resposta não encontrada.")
        workflow = db.execute("SELECT * FROM run_workflow WHERE run_id=?", (run_id,)).fetchone()
    result = dict(row)
    result['workflow'] = dict(workflow) if workflow else None
    return result


def link_internal(run_id: str, internal_id: int, verified_at: float):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        run = db.execute("SELECT ticket_id,status FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise HTTPException(404, "Resposta não encontrada.")
        if run['status'] != 'completed' or run['ticket_id'] is None:
            raise HTTPException(409, "Somente respostas concluídas e vinculadas ao chamado público podem ter revisão interna.")
        current = db.execute("SELECT * FROM run_workflow WHERE run_id=?", (run_id,)).fetchone()
        if current and current['approved_at'] is not None:
            raise HTTPException(409, "Resposta já aprovada. O vínculo não pode mais mudar.")
        db.execute("INSERT INTO run_workflow(run_id,internal_ticket_id,relation_verified_at) VALUES(?,?,?) "
                   "ON CONFLICT(run_id) DO UPDATE SET internal_ticket_id=excluded.internal_ticket_id,relation_verified_at=excluded.relation_verified_at",
                   (run_id, internal_id, verified_at))
        db.execute("INSERT INTO audit(created,action,detail) VALUES(?,?,?)", (time.time(), 'Vínculo com chamado interno',
                   json.dumps({'run_id': run_id, 'public_ticket_id': run['ticket_id'], 'internal_ticket_id': internal_id})))
    return run_detail(run_id)['workflow']


def approve_for_pilot(run_id: str, text: str, note: str, reviewer: str, approval_action_id=None):
    exact = text.strip()
    if len(exact) < 30 or len(exact) > 8000:
        raise HTTPException(422, "A resposta aprovada precisa ter entre 30 e 8.000 caracteres.")
    if len(note.strip()) < 8:
        raise HTTPException(422, "Registre o que foi conferido na revisão.")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        run = db.execute("SELECT status,ticket_id,verdict FROM runs WHERE id=?", (run_id,)).fetchone()
        workflow = db.execute("SELECT * FROM run_workflow WHERE run_id=?", (run_id,)).fetchone()
        if not run or not workflow:
            raise HTTPException(409, "Vincule primeiro um chamado interno relacionado.")
        if run['status'] != 'completed' or run['ticket_id'] is None or run['verdict'] != 'approved':
            raise HTTPException(409, "Avalie a resposta como aprovada antes de liberar o piloto.")
        digest = hashlib.sha256(exact.encode('utf-8')).hexdigest()
        if workflow['approved_at'] is not None:
            if workflow['approved_sha256'] == digest:
                return dict(workflow)
            raise HTTPException(409, "Já existe um texto aprovado para esta geração.")
        now = time.time()
        db.execute("UPDATE run_workflow SET approved_text=?,approved_by=?,approved_at=?,approval_note=?,approved_sha256=?,approval_action_id=?,delivery_state='blocked_until_movidesk_test' WHERE run_id=?",
                   (exact, reviewer, now, note.strip(), digest, str(approval_action_id) if approval_action_id is not None else None, run_id))
        db.execute("INSERT INTO audit(created,action,detail) VALUES(?,?,?)", (now, 'Aprovação do piloto',
                   json.dumps({'run_id': run_id, 'public_ticket_id': run['ticket_id'],
                               'internal_ticket_id': workflow['internal_ticket_id'], 'approved_sha256': digest,
                               'reviewer': reviewer, 'approval_action_id': approval_action_id,
                               'note': note.strip()}, ensure_ascii=False)))
        updated = db.execute("SELECT * FROM run_workflow WHERE run_id=?", (run_id,)).fetchone()
    return dict(updated)


def performance(days):
    with connect() as db:
        rows = [dict(r) for r in db.execute("SELECT id,ticket_id,started,duration,status,verdict FROM runs WHERE started>=? ORDER BY started DESC", (time.time() - days * 86400,))]
    completed = [r for r in rows if r['status'] == 'completed']
    reviewed = [r for r in completed if r['verdict']]
    linked = [r for r in completed if r['ticket_id'] is not None]
    linked_reviewed = [r for r in linked if r['verdict']]
    counts = {v: sum(r['verdict'] == v for r in reviewed) for v in ['approved', 'corrected', 'rejected']}
    durations = sorted(r['duration'] for r in completed if r['duration'] is not None)
    daily = {}
    from datetime import datetime, timezone
    for r in rows:
        day = datetime.fromtimestamp(r['started'], timezone.utc).strftime('%Y-%m-%d')
        bucket = daily.setdefault(day, {'date': day, 'completed': 0, 'failed': 0})
        if r['status'] == 'completed': bucket['completed'] += 1
        if r['status'] in ('failed', 'interrupted'): bucket['failed'] += 1
    return {'total': len(rows), 'completed': len(completed), 'linked_completed': len(linked),
            'linked_reviewed': len(linked_reviewed),
            'failed': sum(r['status'] in ('failed', 'interrupted') for r in rows),
            'reviewed': len(reviewed), 'pending_review': len(completed)-len(reviewed), **counts,
            'approval_rate': round(counts['approved'] / len(reviewed)*100, 1) if reviewed else None,
            'review_coverage': round(len(reviewed)/len(completed)*100, 1) if completed else None,
            'mean_seconds': round(sum(durations)/len(durations), 1) if durations else None,
            'p95_seconds': round(durations[math.ceil(len(durations)*.95)-1], 1) if durations else None,
            'daily': sorted(daily.values(), key=lambda r:r['date']), 'days': days}


def runs(days, verdict, page, limit):
    clause = "started>=?"
    args = [time.time() - days*86400]
    if verdict == 'pending':
        clause += " AND status='completed' AND verdict IS NULL"
    elif verdict:
        clause += " AND verdict=?"
        args.append(verdict)
    with connect() as db:
        total = db.execute(f"SELECT COUNT(*) FROM runs WHERE {clause}", args).fetchone()[0]
        items = [dict(r) for r in db.execute(f"SELECT * FROM runs WHERE {clause} ORDER BY started DESC LIMIT ? OFFSET ?", [*args, limit, (page-1)*limit])]
        workflows = {r['run_id']: dict(r) for r in db.execute("SELECT run_id,internal_ticket_id,approved_at,delivery_state FROM run_workflow WHERE run_id IN (" + ",".join("?" for _ in items) + ")", [item['id'] for item in items])} if items else {}
    for item in items:
        item['sources'] = json.loads(item['sources'])
        item['decision'] = json.loads(item['decision'] or '{}')
        item['workflow'] = workflows.get(item['id'])
    return {'items': items, 'total': total, 'page': page, 'limit': limit}


def audit_log():
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 100")]
