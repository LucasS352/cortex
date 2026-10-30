import tempfile
import threading
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import movidesk_webhook as receiver
import ticket_agent_control as control
import knowledge_training_api as training


class TicketAgentControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'webhooks.sqlite3'
        self.patch = patch.object(receiver, 'WEBHOOK_DB', self.db)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_state_survives_new_connections_and_audit_records_both_actions(self):
        control.set_paused(42, True, 'Ana')
        self.assertTrue(control.status(42)['paused'])
        # A fresh interpreter reads the same state without any in-memory cache.
        result = subprocess.run([sys.executable, '-c',
            'from pathlib import Path; import sys; import movidesk_webhook as r; '
            'r.WEBHOOK_DB=Path(sys.argv[1]); import ticket_agent_control as c; print(c.status(42)["paused"])',
            str(self.db)], capture_output=True, text=True, check=True, timeout=15)
        self.assertEqual(result.stdout.strip(), 'True')
        self.assertFalse(control.status(43)['paused'])
        control.set_paused(42, False, 'Bruno')
        self.assertFalse(control.status(42)['paused'])
        with receiver._connect() as db:
            rows = db.execute('SELECT ticket_id,paused,actor,created_at FROM ticket_agent_audit ORDER BY id').fetchall()
        self.assertEqual([(r[0], r[1], r[2]) for r in rows], [(42, 1, 'Ana'), (42, 0, 'Bruno')])
        self.assertTrue(all(r[3] > 0 for r in rows))

    def test_pause_cannot_confirm_while_a_publication_is_in_flight(self):
        started, confirmed = threading.Event(), threading.Event()
        failures = []
        def pause():
            started.set()
            try:
                control.set_paused(42, True, 'Ana')
                confirmed.set()
            except Exception as exc:
                failures.append(exc)
        with control.publication_guard(42) as allowed:
            self.assertTrue(allowed)
            thread = threading.Thread(target=pause)
            thread.start()
            self.assertTrue(started.wait(2))
            self.assertFalse(confirmed.wait(.15))
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertFalse(failures)
        self.assertTrue(confirmed.is_set())
        with control.publication_guard(42) as allowed:
            self.assertFalse(allowed)

    def test_backend_requires_correct_pin_and_same_origin(self):
        app = FastAPI()
        app.include_router(training.router)
        training._technician_failures.clear()
        with TestClient(app, base_url='https://testserver') as client, \
             patch.dict('os.environ', {'CORTEX_PUBLIC_URL': 'https://testserver', 'CORTEX_TECHNICIAN_PINS': '7531:Ana'}), \
             patch.object(training, '_lookup_db_pin', return_value=None), \
             patch.object(training.kt, 'connect', return_value=MagicMock()), \
             patch.object(training.kt, 'load_ticket', return_value=({}, [])):
            url = '/api/treinamento/tecnico/tickets/42/agente'
            bad = client.put(url, json={'paused': True}, headers={'Origin':'https://testserver','X-Cortex-Technician-Pin':'9999'})
            self.assertEqual(bad.status_code, 401)
            self.assertFalse(control.status(42)['paused'])
            cross = client.put(url, json={'paused':True}, headers={'Origin':'https://other.test','X-Cortex-Technician-Pin':'7531'})
            self.assertEqual(cross.status_code, 403)
            headers = {'Origin':'https://testserver','X-Cortex-Technician-Pin':'7531'}
            self.assertEqual(client.put(url, json={'paused':'false'}, headers=headers).status_code, 422)
            response = client.put(url, json={'paused':True}, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['actor'], 'Ana')
            self.assertTrue(control.status(42)['paused'])
            self.assertEqual(client.put(url, json={'paused':False}, headers=headers).status_code, 200)
            self.assertFalse(control.status(42)['paused'])
