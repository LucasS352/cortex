import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import movidesk_webhook as receiver


class MovideskWebhookTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(receiver, 'WEBHOOK_DB', Path(self.temp.name) / 'events.sqlite3')
        self.secret_patch = patch.object(receiver, 'MOVIDESK_WEBHOOK_SECRET', 'x' * 40)
        self.master_secret_patch = patch.object(receiver, 'MASTER_CONFIG_WEBHOOK_SECRET', 'm' * 40)
        self.db_patch.start()
        self.secret_patch.start()
        self.master_secret_patch.start()
        self.client = TestClient(receiver.app)
        self.url = '/webhooks/movidesk/tickets/' + 'x' * 40
        self.master_url = '/webhooks/master/config/' + 'm' * 40

    def tearDown(self):
        self.secret_patch.stop()
        self.master_secret_patch.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def test_authenticated_delivery_is_deduplicated_and_does_not_process(self):
        event = {'Id': 131140, 'Subject': 'Teste', 'Actions': [{'Id': 4, 'Type': 1}],
                 'WebhookEvents': [{'Criteria': 1, 'Operation': 2}]}
        self.assertEqual(self.client.post(self.url, json=event).text, 'Ok')
        self.assertEqual(self.client.post(self.url, json=event).status_code, 200)
        result = receiver.receipt_summary()
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['recent'][0]['ticket_id'], 131140)
        self.assertEqual(result['recent'][0]['state'], 'received')

    def test_internal_enqueue_uses_the_same_validation_and_deduplication(self):
        event = {'Id': 131661, 'Actions': [{'Id': 7}], 'CortexManualPull': 1}
        self.assertTrue(receiver.enqueue_payload(event))
        self.assertFalse(receiver.enqueue_payload(event))
        self.assertEqual(receiver.receipt_summary()['total'], 1)
        with self.assertRaises(ValueError):
            receiver.enqueue_payload({'Id': 0})

    def test_invalid_secret_and_payload_are_rejected(self):
        self.assertEqual(self.client.post('/webhooks/movidesk/tickets/wrong', json={'Id': 1}).status_code, 404)
        self.assertEqual(self.client.post(self.url, content=b'not-json').status_code, 400)
        self.assertEqual(self.client.post(self.url, json={'Id': True}).status_code, 400)
        self.assertEqual(self.client.post(self.url, json={'Id': 0}).status_code, 400)
        self.assertEqual(receiver.receipt_summary()['total'], 0)

    def test_new_action_is_a_distinct_event_and_oversized_body_fails(self):
        self.client.post(self.url, json={'Id': 131140, 'Actions': [{'Id': 1}]})
        self.client.post(self.url, json={'Id': 131140, 'Actions': [{'Id': 2}]})
        self.assertEqual(receiver.receipt_summary()['total'], 2)
        self.assertEqual(self.client.post(self.url, content=b'a' * (receiver.MAX_BODY_BYTES + 1)).status_code, 413)

    def master_payload(self, **updates):
        value = {
            'empresa': 'EMPRESA TESTE', 'usuario': 'Operador',
            'versao': 'v3.0 - 15/10/2026 08:00:00', 'ticket': '',
            'servidor': 'db-test', 'banco': '/firebird/test.fdb',
            'datahora': '15/10/2026 09:00:00', 'codigo_empresa': 'empresa-test',
            'instalacao_id': 'instalacao-test', 'event_id': 'evento-test',
            'config': '[geral]\nn_bancos=1\n[conexao_1]\ndescricao=SINC TESTE\natualizador=S\nservidor=db-test\nbanco=/firebird/test.fdb\nsenha=nao-pode-vazar'
        }
        value.update(updates)
        return value

    def test_master_config_webhook_authenticates_normalizes_and_saves(self):
        saved = {'status': 'stored', 'environment_id': 'env', 'snapshot_id': 'snap',
                 'promoted': True, 'is_beta': False, 'warnings': []}
        with patch.object(receiver, 'master_connection', return_value=nullcontext(object())), \
                patch.object(receiver, 'save_report', return_value=saved) as save:
            response = self.client.post(self.master_url, json=self.master_payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'stored')
        report = save.call_args.args[1]
        self.assertNotIn('nao-pode-vazar', str(report))
        self.assertEqual(report['payload']['integration_summary'][0]['descricao'], 'SINC TESTE')

    def test_master_config_webhook_rejects_secret_invalid_body_and_oversize(self):
        self.assertEqual(self.client.post('/webhooks/master/config/wrong', json=self.master_payload()).status_code, 404)
        response = self.client.post(self.master_url, json=self.master_payload(datahora='invalid'))
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('nao-pode-vazar', response.text)
        self.assertEqual(self.client.post(self.master_url, content=b'a' * (receiver.MAX_BODY_BYTES + 1)).status_code, 413)


if __name__ == '__main__':
    unittest.main()
