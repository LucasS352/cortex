import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import movidesk_webhook as receiver


class MovideskWebhookTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(receiver, 'WEBHOOK_DB', Path(self.temp.name) / 'events.sqlite3')
        self.secret_patch = patch.object(receiver, 'MOVIDESK_WEBHOOK_SECRET', 'x' * 40)
        self.db_patch.start()
        self.secret_patch.start()
        self.client = TestClient(receiver.app)
        self.url = '/webhooks/movidesk/tickets/' + 'x' * 40

    def tearDown(self):
        self.secret_patch.stop()
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


if __name__ == '__main__':
    unittest.main()
