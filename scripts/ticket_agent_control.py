"""Durable per-ticket suspension, shared by the UI and the note worker."""
from __future__ import annotations

import os
import time
from contextlib import contextmanager

import movidesk_webhook as receiver


@contextmanager
def _ticket_lock(ticket_id):
    # File locks span app/worker processes without holding SQLite's write lock
    # during a Movidesk request. Both containers mount the same output volume.
    root = receiver.WEBHOOK_DB.parent / 'ticket-agent-locks'
    root.mkdir(parents=True, exist_ok=True)
    with (root / f'{int(ticket_id)}.lock').open('a+b') as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b'0')
            handle.flush()
        deadline = time.monotonic() + 60
        while True:
            handle.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Há um envio em andamento. Aguarde e tente novamente.') from None
                time.sleep(.05)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def status(ticket_id):
    with receiver._connect() as db:
        row = db.execute('SELECT paused,actor,updated_at FROM ticket_agent_state WHERE ticket_id=?',
                         (int(ticket_id),)).fetchone()
    return {'ticket_id': int(ticket_id), 'paused': bool(row['paused']) if row else False,
            'actor': row['actor'] if row else None, 'updated_at': row['updated_at'] if row else None}


def set_paused(ticket_id, paused, actor):
    with _ticket_lock(ticket_id):
        now = time.time()
        with receiver._connect() as db:
            db.execute('''INSERT INTO ticket_agent_state(ticket_id,paused,actor,updated_at) VALUES (?,?,?,?)
                ON CONFLICT(ticket_id) DO UPDATE SET paused=excluded.paused,actor=excluded.actor,
                updated_at=excluded.updated_at''', (int(ticket_id), int(paused), actor, now))
            db.execute('''INSERT INTO ticket_agent_audit(ticket_id,paused,actor,created_at)
                VALUES (?,?,?,?)''', (int(ticket_id), int(paused), actor, now))
        return {'ticket_id': int(ticket_id), 'paused': bool(paused), 'actor': actor, 'updated_at': now}


@contextmanager
def publication_guard(ticket_id):
    # A pause confirmed before this guard is acquired always prevents sending.
    # A PATCH already in progress completes before a pause can be confirmed.
    with _ticket_lock(ticket_id):
        yield not status(ticket_id)['paused']
