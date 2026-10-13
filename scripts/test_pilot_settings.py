"""Pilot account IDs stay explicit and do not enable external sending."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException

import admin_console


class PilotSettingsTests(unittest.TestCase):
    def test_save_ids_keeps_human_review_lock(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(admin_console, "STATE_FILE", Path(directory) / "state.json"), \
             patch.object(admin_console.operations, "audit"):
            state = admin_console.set_state({"pilot_person_ids": ["123", "123"],
                                             "pilot_organization_ids": ["company_1"],
                                             "pilot_requester_emails": ["Lucas.Teste@Alternativa.com.br"]})
            self.assertEqual(state["pilot_person_ids"], ["123"])
            self.assertEqual(state["pilot_organization_ids"], ["company_1"])
            self.assertEqual(state["pilot_requester_emails"], ["lucas.teste@alternativa.com.br"])
            self.assertTrue(state["human_review_required"])
            self.assertFalse(state["auto_send_enabled"])
            self.assertEqual(admin_console.get_state()["pilot_person_ids"], ["123"])

    def test_rejects_invalid_ids(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(admin_console, "STATE_FILE", Path(directory) / "state.json"), \
             patch.object(admin_console.operations, "audit"):
            with self.assertRaises(HTTPException):
                admin_console.set_state({"pilot_person_ids": ["unexpected value! "]})

    def test_rejects_invalid_requester_email(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(admin_console, "STATE_FILE", Path(directory) / "state.json"), \
             patch.object(admin_console.operations, "audit"):
            with self.assertRaises(HTTPException):
                admin_console.set_state({"pilot_requester_emails": ["nao-e-email"]})

    def test_webhook_status_reports_only_effective_internal_note_mode(self):
        request = SimpleNamespace(state=SimpleNamespace(cortex_authenticated=True), client=None)
        receipt = {'total': 0, 'recent': [], 'notes': [], 'worker': None}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(admin_console, "STATE_FILE", Path(directory) / "state.json"), \
             patch.object(admin_console.operations, "audit"), \
             patch.object(admin_console.movidesk_webhook, "receipt_summary", return_value=receipt), \
             patch.dict(admin_console.os.environ, {'CORTEX_AUTONOTE_MODE': 'internal_note'}):
            admin_console.set_state({'pilot_organization_ids': ['464'],
                                     'pilot_paused': False,
                                     'auto_internal_note': True})
            enabled = admin_console.webhook_receipts(request)
            self.assertEqual(enabled['effective_mode'], 'internal_note')
            self.assertEqual(enabled['selected_organizations'], 1)

            admin_console.set_state({'pilot_paused': True})
            paused = admin_console.webhook_receipts(request)
            self.assertEqual(paused['effective_mode'], 'dry_run')

    def test_server_ceiling_cannot_be_overridden_by_panel(self):
        request = SimpleNamespace(state=SimpleNamespace(cortex_authenticated=True), client=None)
        receipt = {'total': 0, 'recent': [], 'notes': [], 'worker': None}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(admin_console, "STATE_FILE", Path(directory) / "state.json"), \
             patch.object(admin_console.operations, "audit"), \
             patch.object(admin_console.movidesk_webhook, "receipt_summary", return_value=receipt), \
             patch.dict(admin_console.os.environ, {'CORTEX_AUTONOTE_MODE': 'disabled'}):
            admin_console.set_state({'pilot_organization_ids': ['464'],
                                     'pilot_paused': False,
                                     'auto_internal_note': True})
            status = admin_console.webhook_receipts(request)
            self.assertEqual(status['effective_mode'], 'disabled')

    def test_manual_pull_fetches_latest_authorized_customer_action_and_enqueues_it(self):
        request = SimpleNamespace(state=SimpleNamespace(cortex_authenticated=True), client=None)
        settings = admin_console.movidesk_note_worker.Settings(
            mode='dry_run', agent_id='bot', organizations=frozenset({'464'}),
            requester_emails=frozenset({'lucas.teste@alternativa.com.br'}),
            manual_internal_note=True)
        ticket = {
            'id': 131661, 'type': 2, 'baseStatus': 'New',
            'clients': [{'id': 'client', 'email': 'lucas.teste@alternativa.com.br',
                         'organization': {'id': '464', 'businessName': 'Cortex Teste'}}],
            'actions': [{'id': 7, 'type': 2, 'description': 'Mensagem atual do cliente.',
                         'createdDate': '2026-10-13T00:56:45Z',
                         'createdBy': {'id': 'client', 'profileType': 2,
                                       'email': 'lucas.teste@alternativa.com.br'}}],
        }
        fake_api = SimpleNamespace(ticket=Mock(return_value=ticket),
                                   client=SimpleNamespace(close=Mock()))
        with patch.object(admin_console.movidesk_note_worker.Settings, 'load', return_value=settings), \
             patch.object(admin_console.movidesk_note_worker, 'effective_settings', return_value=settings), \
             patch.object(admin_console.movidesk_note_worker, 'Api', return_value=fake_api), \
             patch.object(admin_console, 'persist_live_ticket') as persist, \
             patch.object(admin_console.movidesk_webhook, 'enqueue_payload', return_value=True) as enqueue:
            result = admin_console.pull_webhook_ticket(131661, request)
        self.assertEqual(result['status'], 'queued')
        self.assertEqual(result['action_id'], '7')
        payload = enqueue.call_args.args[0]
        self.assertEqual(payload['Actions'][0]['Id'], 7)
        self.assertIn('CortexManualPull', payload)
        persist.assert_called_once_with(ticket)
        self.assertTrue(result['ticket_refreshed'])

    def test_manual_pull_uses_customer_message_before_movidesk_automatic_receipt(self):
        request = SimpleNamespace(state=SimpleNamespace(cortex_authenticated=True), client=None)
        settings = admin_console.movidesk_note_worker.Settings(
            mode='internal_note', agent_id='bot', organizations=frozenset({'2021612901'}),
            requester_emails=frozenset({'lucas.teste@alternativa.com.br'}),
            manual_internal_note=True)
        ticket = {
            'id': '131662', 'type': 2, 'baseStatus': 'New',
            'clients': [{'id': 'client', 'email': 'lucas.teste@alternativa.com.br',
                         'organization': {'id': '2021612901', 'businessName': 'Cortex Teste'}}],
            'actions': [
                {'id': 1, 'type': 2, 'description': 'Inscrição estadual inválida.',
                 'createdDate': '2026-10-13T10:00:00Z',
                 'createdBy': {'id': 'client', 'profileType': 2,
                               'email': 'lucas.teste@alternativa.com.br'}},
                {'id': 2, 'type': 2,
                 'description': 'Olá! O seu chamado foi recebido, e iremos realizar a triagem para um atendente do suporte auxiliar no atendimento.',
                 'createdDate': '2026-10-13T10:00:01Z',
                 'createdBy': {'id': 'agent', 'profileType': 3}},
            ],
        }
        fake_api = SimpleNamespace(ticket=Mock(return_value=ticket),
                                   client=SimpleNamespace(close=Mock()))
        with patch.object(admin_console.movidesk_note_worker.Settings, 'load', return_value=settings), \
             patch.object(admin_console.movidesk_note_worker, 'effective_settings', return_value=settings), \
             patch.object(admin_console.movidesk_note_worker, 'Api', return_value=fake_api), \
             patch.object(admin_console, 'persist_live_ticket') as persist, \
             patch.object(admin_console.movidesk_webhook, 'enqueue_payload') as enqueue:
            result = admin_console.pull_webhook_ticket(131662, request)
        self.assertEqual(result['action_id'], '1')
        self.assertEqual(enqueue.call_args.args[0]['Actions'][0]['Id'], 1)
        persist.assert_called_once_with(ticket)


if __name__ == "__main__":
    unittest.main()
