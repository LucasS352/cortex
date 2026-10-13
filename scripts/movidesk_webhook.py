"""Isolated, receive-only Movidesk webhook service.

The endpoint acknowledges quickly and stores events. It never calls Movidesk,
generates an AI answer, or writes to a ticket while handling a request.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from config import MOVIDESK_WEBHOOK_SECRET


WEBHOOK_DB = Path(__file__).parent / 'output' / 'movidesk_webhooks.sqlite3'
MAX_BODY_BYTES = 2 * 1024 * 1024
app = FastAPI(title='Cortex Movidesk webhook receiver', docs_url=None,
              redoc_url=None, openapi_url=None)


@contextmanager
def _connect():
    WEBHOOK_DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(WEBHOOK_DB, timeout=2)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('''CREATE TABLE IF NOT EXISTS webhook_events (
        digest TEXT PRIMARY KEY, ticket_id INTEGER NOT NULL, received_at REAL NOT NULL,
        payload_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'received')''')
    db.execute('CREATE INDEX IF NOT EXISTS webhook_events_ticket ON webhook_events(ticket_id,received_at)')
    columns = {row[1] for row in db.execute('PRAGMA table_info(webhook_events)')}
    for name, definition in {'attempts': 'INTEGER NOT NULL DEFAULT 0',
                             'available_at': 'REAL NOT NULL DEFAULT 0',
                             'lease_until': 'REAL NOT NULL DEFAULT 0',
                             'outcome': 'TEXT', 'last_error': 'TEXT'}.items():
        if name not in columns:
            db.execute(f'ALTER TABLE webhook_events ADD COLUMN {name} {definition}')
    db.execute('''CREATE TABLE IF NOT EXISTS internal_note_outbox (
        key TEXT PRIMARY KEY, ticket_id INTEGER NOT NULL, source_action TEXT NOT NULL,
        marker TEXT NOT NULL UNIQUE, content TEXT NOT NULL, state TEXT NOT NULL,
        created_at REAL NOT NULL, updated_at REAL NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS note_worker_status (
        id INTEGER PRIMARY KEY CHECK(id=1), heartbeat REAL NOT NULL, mode TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS ticket_webhook_snapshots (
        ticket_id INTEGER PRIMARY KEY, fetched_at REAL NOT NULL, payload_json TEXT NOT NULL)''')
    try:
        with db:
            yield db
    finally:
        db.close()


def receipt_summary():
    with _connect() as db:
        total = db.execute('SELECT COUNT(*) FROM webhook_events').fetchone()[0]
        rows = db.execute('''SELECT ticket_id,received_at,state,outcome,last_error,attempts FROM webhook_events
            ORDER BY received_at DESC LIMIT 20''').fetchall()
        counts = dict(db.execute('SELECT state,count(*) FROM webhook_events GROUP BY state').fetchall())
        worker = db.execute('SELECT heartbeat,mode FROM note_worker_status WHERE id=1').fetchone()
        notes = [dict(row) for row in db.execute('''SELECT ticket_id,state,updated_at,content
            FROM internal_note_outbox ORDER BY updated_at DESC LIMIT 5''')]
        for note in notes:
            note['text'] = html.unescape(re.sub(r'<[^>]*>', '', note.pop('content').replace('<br>', '\n').replace('</p>', '\n')))
    return {'total': total, 'recent': [dict(row) for row in rows], 'counts': counts,
            'worker': dict(worker) if worker else None, 'notes': notes}


def enqueue_payload(payload: dict) -> bool:
    """Validate and durably enqueue one ticket snapshot.

    Returns True when a new event was inserted and False for an identical
    delivery that was already stored.
    """
    if not isinstance(payload, dict):
        raise ValueError('Invalid ticket payload')
    ticket_id = payload.get('Id')
    if isinstance(ticket_id, bool) or not isinstance(ticket_id, int) or ticket_id <= 0:
        raise ValueError('Invalid ticket ID')
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    with _connect() as db:
        result = db.execute('''INSERT OR IGNORE INTO webhook_events
            (digest,ticket_id,received_at,payload_json) VALUES (?,?,?,?)''',
            (digest, ticket_id, time.time(), canonical))
    return result.rowcount == 1


@app.get('/health')
def health():
    return {'ready': bool(MOVIDESK_WEBHOOK_SECRET and len(MOVIDESK_WEBHOOK_SECRET) >= 32)}


@app.post('/webhooks/movidesk/tickets/{secret}', response_class=PlainTextResponse)
async def receive_ticket(secret: str, request: Request):
    # Movidesk's trigger UI documents a URL field, not a signed-request header.
    # The unguessable URL is the bearer credential; expose this service over HTTPS only.
    if not MOVIDESK_WEBHOOK_SECRET or len(MOVIDESK_WEBHOOK_SECRET) < 32 or not secrets.compare_digest(
            secret, MOVIDESK_WEBHOOK_SECRET):
        return PlainTextResponse('Not found', status_code=404)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            return PlainTextResponse('Payload too large', status_code=413)
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return PlainTextResponse('Invalid JSON', status_code=400)
    try:
        enqueue_payload(payload)
    except ValueError as exc:
        return PlainTextResponse(str(exc), status_code=400)
    except sqlite3.Error:
        # A failed insert must trigger Movidesk's retry, never a false success.
        return PlainTextResponse('Storage unavailable', status_code=503)
    return PlainTextResponse('Ok', status_code=200)
