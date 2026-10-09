import sys, unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import simulator_app as module

class TicketCaptureTests(unittest.TestCase):
    def test_capture_does_not_call_ai_or_create_assistant_turn(self):
        with patch.object(module.RomarioDialogueEngine, 'generate_response', side_effect=AssertionError('AI invoked')), patch.object(module.triage_engine,'analyze_first_message',side_effect=AssertionError('triage invoked')):
            client=TestClient(module.app)
            response=client.post('/api/client/submit',json={'subject':'Teste','description':'Erro ao emitir nota','module':'Fiscal','urgency':'Alta'})
            self.assertEqual(response.status_code,200)
            sid=response.json()['session_id']
            self.assertNotIn('reply',response.json())
            self.assertEqual([t.role for t in module.session_store.get(sid).turns],['user'])
            self.assertTrue(any(row['session_id']==sid for row in client.get('/api/tech/queue').json()))
    def test_chat_is_disabled(self):
        client=TestClient(module.app)
        self.assertEqual(client.post('/api/chat',json={}).status_code,410)
        self.assertEqual(client.post('/api/chat/image',json={}).status_code,410)
