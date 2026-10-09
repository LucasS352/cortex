"""Regression checks against an isolated operational database; no provider calls."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException
from fastapi.testclient import TestClient
import admin_console
import agent_operations as operations
import simulator_app
import psycopg
from config import POSTGRES_DSN


class AgentOperationsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(operations, 'OPERATIONS_DB', Path(self.temp.name) / 'runs.sqlite')
        self.state_patch = patch.object(admin_console, 'STATE_FILE', Path(self.temp.name) / 'settings.json')
        self.db_patch.start()
        self.state_patch.start()
        self.client = TestClient(simulator_app.app)
        self.controls = {**admin_console.DEFAULT_STATE, 'min_interval_seconds': 0, 'max_per_minute': 60, 'max_concurrent': 5}

    def tearDown(self):
        self.db_patch.stop()
        self.state_patch.stop()
        self.temp.cleanup()

    def test_review_denominator_and_edits(self):
        ids = []
        for i in range(4):
            run = operations.begin('Teste', 'Contexto', [], self.controls)
            operations.finish(run, draft='Resposta')
            ids.append(run)
        operations.review(ids[0], 'approved', '')
        operations.review(ids[1], 'corrected', 'Ajustar procedimento')
        result = operations.performance(30)
        self.assertEqual((result['approval_rate'], result['review_coverage'], result['pending_review']), (50, 50, 2))
        operations.review(ids[1], 'rejected', 'Procedimento inaplicável')
        result = operations.performance(30)
        self.assertEqual((result['reviewed'], result['corrected'], result['rejected']), (2, 0, 1))

    def test_concurrent_admission_is_atomic(self):
        controls = {**self.controls, 'max_concurrent': 1}
        def start(_):
            try:
                return operations.begin('Teste', '', [], controls)
            except HTTPException as exc:
                self.assertEqual(exc.status_code, 429)
                return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(start, range(4)))
        self.assertEqual(len([r for r in ids if r]), 1)

    def test_interval_and_minute_limits(self):
        run = operations.begin('Teste', '', [], self.controls)
        operations.finish(run, draft='Resposta')
        for control in ({'min_interval_seconds': 30}, {'max_per_minute': 1}):
            with self.assertRaises(HTTPException) as error:
                operations.begin('Teste', '', [], {**self.controls, **control})
            self.assertEqual(error.exception.status_code, 429)

    def test_route_records_generation_and_review(self):
        with psycopg.connect(POSTGRES_DSN) as conn:
            source_id = conn.execute("SELECT id FROM ticket_knowledge_nodes WHERE source_type='OFFICIAL_KB' LIMIT 1").fetchone()[0]
        request = {'subject': 'Teste', 'problem': 'Contexto', 'top_results': [{'id': source_id, 'source_type': 'OFFICIAL_KB', 'title': 'Artigo'}]}
        with patch.object(simulator_app, '_generate_draft', return_value={'draft': 'Resposta de teste'}):
            response = self.client.post('/api/draft', json=request)
        self.assertEqual(response.status_code, 200)
        run_id = response.json()['run_id']
        review_url = f'/api/admin/runs/{run_id}/review'
        self.assertEqual(self.client.put(review_url, json={'verdict': 'rejected'}).status_code, 422)
        self.assertEqual(self.client.put(review_url, json={'verdict': 'approved'}).status_code, 200)
        self.assertEqual(self.client.get('/api/admin/performance').json()['approval_rate'], 100)
        self.assertEqual(self.client.get('/api/admin/runs').json()['items'][0]['draft'], 'Resposta de teste')

    def test_linked_ticket_records_trace_without_external_write(self):
        with psycopg.connect(POSTGRES_DSN) as conn:
            ticket_id, subject = conn.execute('''SELECT external_id,subject FROM refined_tickets
                WHERE subject IS NOT NULL ORDER BY external_id DESC LIMIT 1''').fetchone()
            source_id = conn.execute("SELECT id FROM ticket_knowledge_nodes WHERE source_type='OFFICIAL_KB' LIMIT 1").fetchone()[0]
        context = self.client.get(f'/api/admin/ticket-context/{ticket_id}')
        self.assertEqual(context.status_code, 200)
        self.assertEqual(context.json()['subject'], subject)
        request = {'ticket_id': ticket_id, 'subject': subject, 'problem': 'Como verificar este caso?',
                   'top_results': [{'id': source_id}]}
        with patch.object(simulator_app, '_generate_draft', return_value={'draft': 'Resposta de teste'}):
            response = self.client.post('/api/draft', json=request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['decision']['ticket_id'], ticket_id)
        self.assertEqual(self.client.get('/api/admin/runs').json()['items'][0]['ticket_id'], ticket_id)
        self.assertEqual(self.client.get('/api/admin/performance').json()['linked_completed'], 1)
        self.assertEqual(self.client.post('/api/draft', json={**request, 'subject': 'Outro chamado'}).status_code, 422)
        self.assertEqual(self.client.post('/api/draft', json={**request, 'ticket_id': 999999999}).status_code, 404)

    def test_settings_pause_and_validation(self):
        self.assertEqual(self.client.put('/api/admin/settings', json={'max_concurrent': 0}).status_code, 422)
        response = self.client.put('/api/admin/settings', json={'draft_generation_enabled': False, 'max_per_minute': 3})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['auto_send_enabled'])
        self.assertEqual(self.client.post('/api/draft', json={}).status_code, 423)
        self.assertTrue(self.client.get('/api/admin/audit').json())

    def test_empty_metrics_and_failed_runs(self):
        self.assertIsNone(operations.performance(30)['approval_rate'])
        run = operations.begin('Teste', '', [], self.controls)
        operations.finish(run, error='Falha')
        result = operations.performance(30)
        self.assertEqual((result['failed'], result['completed']), (1, 0))
        self.assertIsNone(result['mean_seconds'])
        with self.assertRaises(HTTPException):
            operations.review(run, 'approved', '')


if __name__ == '__main__':
    unittest.main()
