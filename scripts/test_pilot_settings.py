"""Pilot account IDs stay explicit and do not enable external sending."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
