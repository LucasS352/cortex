import copy
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
import automation_rules as rules
import test_movidesk_note_worker as fixtures
import movidesk_note_worker as worker
import movidesk_webhook as receiver
import automation_monitor


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.WorkerTests()
        self.f.setUp()
        self.f.api.data['status'] = 'Novo'
        worker.runtime_state.return_value['automation_rules'] = {
            'enabled': True, 'events': ['customer_message', 'status_changed'], 'statuses': ['Novo', 'Triagem'], 'companies': {}}

    def tearDown(self):
        self.f.tearDown()

    def process(self, **payload):
        self.f.enqueue(**payload)
        worker.run_once(self.f.settings, self.f.api, self.f.generate)
        return receiver.receipt_summary()['recent'][0]

    def test_creation_publishes_private_note(self):
        self.assertEqual(self.process()['outcome'], 'nota_interna_publicada')
        self.assertEqual(len(self.f.api.writes), 1)

    def test_creation_wrong_status_is_logged_without_generation(self):
        self.f.api.data['status'] = 'DEV - Em coding'
        self.assertEqual(self.process()['outcome'], 'status_bloqueado_desenvolvimento')
        self.f.generate.assert_not_called()

    def test_unauthorized_company_never_generates(self):
        self.f.api.data['clients'][0]['organization']['id'] = 'other'
        self.assertEqual(self.process()['outcome'], 'fora_do_piloto')
        self.f.generate.assert_not_called()

    def test_transition_to_triagem_processes_once_and_same_status_does_not(self):
        self.process(Version=1)
        self.f.api.data['status'] = 'Triagem'
        self.assertEqual(self.process(Version=2)['outcome'], 'nota_interna_publicada')
        self.assertEqual(self.process(Version=3)['outcome'], 'evento_nao_elegivel')
        self.assertEqual(len(self.f.api.writes), 2)

    def test_same_webhook_is_idempotent(self):
        self.process()
        self.f.enqueue()
        self.assertFalse(worker.run_once(self.f.settings, self.f.api, self.f.generate))
        self.assertEqual(len(self.f.api.writes), 1)

    def test_company_disable_does_not_remove_authorization(self):
        worker.runtime_state.return_value['automation_rules']['companies'] = {'464': {'enabled': False}}
        self.assertEqual(self.process()['outcome'], 'automacao_empresa_desativada')
        self.f.generate.assert_not_called()

    def test_preview_never_calls_writer(self):
        self.f.settings = worker.replace(self.f.settings, mode='dry_run')
        self.assertEqual(self.process()['state'], 'preview')
        self.assertFalse(self.f.api.writes)

    def test_generation_failure_has_retry_and_timeline(self):
        self.f.generate.side_effect = RuntimeError('private secret')
        self.assertEqual(self.process()['state'], 'retry')
        with patch.object(automation_monitor.Settings, 'load', return_value=self.f.settings):
            result = automation_monitor.report({}, 42)
        self.assertTrue(result['items'][0]['error'])
        self.assertNotIn('private secret', str(result))
        self.assertIn('generation', [x['stage'] for x in result['items'][0]['trace']])

    def test_insufficient_evidence_retains_draft_without_publication(self):
        self.f.generate.return_value['decision'] = {'state': 'ENCAMINHAR', 'can_propose_procedure': False}
        self.assertEqual(self.process()['outcome'], 'contexto_insuficiente')
        self.assertFalse(self.f.api.writes)

    def test_rejected_publication_is_not_blindly_repeated(self):
        from types import SimpleNamespace
        self.f.api.internal_note = Mock(return_value=SimpleNamespace(status_code=403))
        self.assertEqual(self.process()['outcome'], 'publicacao_rejeitada')
        self.assertEqual(self.process(Version=2)['outcome'], 'evento_nao_elegivel')
        self.f.api.internal_note.assert_called_once()

    def test_no_worker_delivery_stays_queued(self):
        self.f.enqueue()
        with patch.object(automation_monitor.Settings, 'load', return_value=self.f.settings):
            result = automation_monitor.report({}, 42)
        self.assertFalse(result['worker_alive'])
        self.assertEqual(result['items'][0]['state'], 'received')

    def test_validation_rejects_unknown_event_status_and_malformed_company(self):
        base = worker.runtime_state.return_value['automation_rules']
        for key, value in [('events', ['created']), ('statuses', ['Inventado']), ('companies', {'464': []})]:
            with self.subTest(key=key), self.assertRaises(HTTPException):
                rules.validate({**base, key: value}, {'Novo', 'Triagem'})

    def test_first_snapshot_does_not_claim_status_transition(self):
        t = self.f.api.data
        rule = {'enabled': True, 'events': ['status_changed'], 'statuses': [], 'companies': {}}
        self.assertFalse(rules.evaluate(t, {}, None, rule, '464')['allowed'])
        old = copy.deepcopy(t)
        old['status'] = 'Novo'
        t['status'] = 'Triagem'
        self.assertTrue(rules.evaluate(t, {}, old, rule, '464')['allowed'])


if __name__ == '__main__':
    unittest.main()
