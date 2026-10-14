"""Local manual and scheduled refresh of the Movidesk operational snapshot.

The refresh stays separate from knowledge ingestion: newly fetched tickets never
become RAG sources through this module.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from config import POSTGRES_DSN


OUTPUT = Path(__file__).parent / "output"
STATE_FILE = OUTPUT / "ticket_sync_schedule.json"
LOCAL_ZONE = ZoneInfo("America/Sao_Paulo")
SCHEDULE_VERSION = 2
DEFAULT = {"enabled": True, "time": "00:00", "last_auto_date": None,
           "version": SCHEDULE_VERSION}
_lock = threading.RLock()
_process: subprocess.Popen | None = None
_scheduler: threading.Thread | None = None
_stop = threading.Event()
_last_error: str | None = None
_log_path: Path | None = None


class SyncBusy(Exception):
    pass


def _read_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return dict(DEFAULT)
        state = {**DEFAULT, **data}
        # Version 1 shipped with the original 06:00 default. Move existing
        # installations to the requested midnight full refresh once, while
        # preserving the last-run marker. Later user choices are left intact.
        if int(data.get("version") or 1) < SCHEDULE_VERSION:
            state.update(enabled=True, time="00:00", version=SCHEDULE_VERSION)
            _write_state(state)
        return state
    except (OSError, ValueError):
        return dict(DEFAULT)


def _write_state(state: dict) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, STATE_FILE)


def schedule() -> dict:
    with _lock:
        state = _read_state()
    now = datetime.now(LOCAL_ZONE)
    hour, minute = map(int, state["time"].split(":"))
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due <= now or state.get("last_auto_date") == now.date().isoformat():
        due += timedelta(days=1)
    return {"enabled": bool(state["enabled"]), "time": state["time"],
            "timezone": "America/Sao_Paulo",
            "next_run_at": due.isoformat() if state["enabled"] else None}


def save_schedule(enabled: bool, run_time: str) -> dict:
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", run_time):
        raise ValueError("Informe um horário válido no formato HH:MM.")
    if type(enabled) is not bool:
        raise ValueError("Ativação da agenda inválida.")
    with _lock:
        state = _read_state()
        was_enabled = bool(state["enabled"])
        state.update(enabled=enabled, time=run_time)
        # Enabling after today's time schedules the first automatic run tomorrow.
        if enabled and not was_enabled:
            now = datetime.now(LOCAL_ZONE)
            if now.strftime("%H:%M") >= run_time:
                state["last_auto_date"] = now.date().isoformat()
        _write_state(state)
    return schedule()


def _latest_run() -> dict | None:
    with psycopg.connect(POSTGRES_DSN, connect_timeout=5, row_factory=dict_row,
                        options="-c statement_timeout=8000 -c default_transaction_read_only=on") as conn:
        return conn.execute("""
            SELECT id, started_at, completed_at, status, new_count, older_open_count
            FROM movidesk_sync_runs ORDER BY id DESC LIMIT 1
        """).fetchone()


def _orphaned_run(run: dict | None, process_running: bool,
                  now: datetime | None = None) -> bool:
    """Recognize a DB run left behind after the app/container stopped."""
    if not run or run.get('status') != 'running' or process_running:
        return False
    now = now or datetime.now(timezone.utc)
    started = run.get('started_at')
    return bool(started and started < now - timedelta(hours=2))


def status() -> dict:
    with _lock:
        process = _process
        running = process is not None and process.poll() is None
        pid = process.pid if running else None
        error = _last_error
        log_path = _log_path
    run = _latest_run()
    if _orphaned_run(run, running):
        # Keep the last completed snapshot available, but stop presenting an
        # abandoned process as if Movidesk had been answering for many hours.
        run = dict(run)
        run['status'] = 'failed'
        run['completed_at'] = datetime.now(timezone.utc)
        error = ('A atualização foi interrompida antes de concluir. A base mantém '
                 'a última carga válida; inicie uma nova atualização se necessário.')
    progress = None
    if run and run['status'] in ('running', 'failed'):
        if log_path is None or not log_path.is_file():
            candidates = sorted(OUTPUT.glob('ticket-sync-*.log'), key=lambda p: p.stat().st_mtime, reverse=True)
            log_path = next((p for p in candidates if abs(p.stat().st_mtime - run['started_at'].timestamp()) < 86400), None)
        if log_path and log_path.is_file():
            try:
                with log_path.open('rb') as log:
                    log.seek(max(0, log.seek(0, 2) - 8192))
                    lines = log.read().decode('utf-8', errors='replace').splitlines()
                page = next((re.match(r'(new|older_open|older_past) page=(\d+) records=(\d+)', line)
                             for line in reversed(lines)
                             if re.match(r'(new|older_open|older_past) page=', line)), None)
                reconciled = next((re.match(r'reconciled (\d+)/(\d+)', line)
                                   for line in reversed(lines) if line.startswith('reconciled ')), None)
                candidates = next((re.match(r'lifecycle reconciliation candidates=(\d+)', line)
                                   for line in reversed(lines) if line.startswith('lifecycle reconciliation candidates=')), None)
                retry = next((line for line in reversed(lines) if line.startswith('Movidesk ') and 'tentativa ' in line), None)
                progress = {'phase': 'reconciliation' if candidates else (page[1] if page else 'starting'),
                            'pages': int(page[2]) if page else 0,
                            'tickets_seen': (run['new_count'] or 0) + (run['older_open_count'] or 0),
                            'reconciled': int(reconciled[1]) if reconciled else 0,
                            'reconciliation_total': int(candidates[1]) if candidates else None,
                            'last_activity_at': datetime.fromtimestamp(log_path.stat().st_mtime, timezone.utc).isoformat(),
                            'retrying': bool(retry and lines and retry == lines[-1])}
                if run['status'] == 'failed' and not error:
                    if any('ReadTimeout' in line or 'ConnectTimeout' in line for line in lines):
                        error = 'Movidesk não respondeu no prazo; a atualização pode ser tentada novamente.'
                    elif any('Traceback' in line for line in lines):
                        error = 'A atualização falhou. Consulte o registro técnico e tente novamente.'
            except OSError:
                pass
    if run:
        run = {key: value.isoformat() if isinstance(value, datetime) else value
               for key, value in run.items()}
    return {"running": running or bool(run and run["status"] == "running"),
            "pid": pid, "last_run": run, "schedule": schedule(), "last_error": error,
            "progress": progress}


def start_sync(source: str = "manual") -> dict:
    global _process, _last_error, _log_path
    if source not in {"manual", "automatic"}:
        raise ValueError("Origem da sincronização inválida.")
    with _lock:
        if _process is not None and _process.poll() is None:
            raise SyncBusy("Já existe uma atualização em andamento.")
        latest = _latest_run()
        if latest and latest["status"] == "running":
            # A refresh started outside this server may still be running.
            if latest["started_at"] > datetime.now(timezone.utc) - timedelta(hours=2):
                raise SyncBusy("Já existe uma atualização em andamento.")
            with psycopg.connect(POSTGRES_DSN, connect_timeout=5) as conn:
                conn.execute("UPDATE movidesk_sync_runs SET status='failed', completed_at=NOW() "
                             "WHERE id=%s AND status='running'", (latest["id"],))
        OUTPUT.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_path = OUTPUT / f"ticket-sync-{stamp}.log"
        _log_path = log_path
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with log_path.open("w", encoding="utf-8") as log:
            _process = subprocess.Popen(
                [sys.executable, "-u", str(Path(__file__).parent / "sync_current_tickets.py")],
                cwd=Path(__file__).parent, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
            )
        _last_error = None
        if source == "automatic":
            state = _read_state()
            state["last_auto_date"] = datetime.now(LOCAL_ZONE).date().isoformat()
            _write_state(state)
        return {"started": True, "source": source, "pid": _process.pid,
                "status": "running"}


def _scheduler_loop() -> None:
    global _last_error
    while not _stop.wait(30):
        try:
            with _lock:
                state = _read_state()
            if not state["enabled"]:
                continue
            now = datetime.now(LOCAL_ZONE)
            today = now.date().isoformat()
            if now.strftime("%H:%M") < state["time"] or state.get("last_auto_date") == today:
                continue
            latest = _latest_run()
            if latest and latest["started_at"].astimezone(LOCAL_ZONE).date() == now.date():
                with _lock:
                    state = _read_state()
                    state["last_auto_date"] = today
                    _write_state(state)
                continue
            start_sync("automatic")
        except SyncBusy:
            continue
        except Exception as exc:
            _last_error = f"Falha ao iniciar atualização automática: {type(exc).__name__}."


def start_scheduler() -> None:
    global _scheduler
    with _lock:
        if _scheduler is None or not _scheduler.is_alive():
            _stop.clear()
            _scheduler = threading.Thread(target=_scheduler_loop, name="cortex-ticket-sync", daemon=True)
            _scheduler.start()


def stop_scheduler() -> None:
    _stop.set()
    with _lock:
        scheduler = _scheduler
    if scheduler is not None:
        scheduler.join(timeout=2)
