"""Regression: evidence gaps must not silence eligible private support notes."""
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import movidesk_note_worker as worker
import movidesk_webhook as receiver
import test_movidesk_note_worker as fixtures


class NotePublicationTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.WorkerTests()
        self.f.setUp()
        self.f.settings = worker.replace(self.f.settings, manual_internal_note=True)
        self.stack = ExitStack()
        self.db = self.stack.enter_context(patch('technician_workbench.connection'))
        self.db.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = {'name': None}
        self.stack.enter_context(patch('tech_evidence.ensure_single_tenant', return_value='tenant'))
        for target in ('knowledge_access.search', 'knowledge_access.approved_procedures',
                       'technician_workbench.capability_evidence_cases',
                       'technician_workbench.resolved_support_cases'):
            self.stack.enter_context(patch(target, return_value=[]))
        self.stack.enter_context(patch('support_events.record'))
        self.model = self.stack.enter_context(patch('technician_workbench.llm_client.generate',
            return_value={'ok': True, 'text': 'Olá! Qual tipo de nota você deseja cancelar?', 'error': ''}))
        self.f.api.data['actions'][0]['description'] = 'Como faço pra cancelar a nota fiscal?'

    def tearDown(self):
        self.stack.close()
        self.f.tearDown()

    def run_note(self, **payload):
        self.f.enqueue(**payload)
        worker.run_once(self.f.settings, self.f.api)
        return receiver.receipt_summary()['recent'][0]

    def legacy_hold(self, outcome='contexto_insuficiente', outbox_state='review'):
        self.f.enqueue()
        key, action = worker.identity(self.f.api.data, self.f.settings)
        marker = worker.PREFIX + key
        with receiver._connect() as db:
            db.execute("UPDATE webhook_events SET state='needs_review',outcome=?,attempts=5", (outcome,))
            db.execute('INSERT INTO internal_note_outbox VALUES (?,?,?,?,?,?,?,?)',
                       (key, 42, action, marker, '<p>HTML antigo não deve ser enviado</p>', outbox_state, 1, 1))
        return key

    def test_new_authorized_howto_without_source_posts_clarification_once(self):
        self.assertEqual(self.run_note()['outcome'], 'nota_interna_publicada')
        self.model.assert_called_once()
        self.assertEqual(len(self.f.api.writes), 1)
        content = self.f.api.writes[0][1]
        self.assertIn('Qual tipo de nota', content)
        self.assertIn('INVESTIGAR', content)
        self.assertEqual(self.run_note(CortexManualPull=1)['state'], 'completed')
        self.assertEqual(len(self.f.api.writes), 1)
        self.model.assert_called_once()

    def test_unsupported_model_steps_are_replaced_before_private_publication(self):
        unsafe = 'Abra o menu inventado e clique no botão Apagar. DROP TABLE notas;'
        self.model.return_value['text'] = unsafe
        self.assertEqual(self.run_note()['outcome'], 'nota_interna_publicada')
        content = self.f.api.writes[0][1]
        self.assertNotIn(unsafe, content)
        self.assertNotIn('DROP TABLE', content)
        self.assertNotIn('menu inventado', content)
        self.assertIn('revisão técnica', content)

    def test_failed_attempt_posts_escalation_without_repeating_procedure(self):
        self.f.api.data['actions'][0]['description'] = 'Já relacionei a transportadora e não resolveu'
        self.assertEqual(self.run_note()['outcome'], 'nota_interna_publicada')
        self.model.assert_not_called()
        self.assertIn('revisão técnica', self.f.api.writes[0][1])

    def test_repository_intake_is_posted_even_without_selected_procedure(self):
        self.f.api.data['actions'][0]['description'] = 'Meu pedido não caiu no Master'
        self.assertEqual(self.run_note()['outcome'], 'nota_interna_publicada')
        self.db.assert_not_called()
        self.model.assert_not_called()
        content = self.f.api.writes[0][1]
        self.assertIn('Ocorrências de integrações', content)
        self.assertEqual(content.count('<img '), 3)

    def test_legacy_hold_is_automatically_regenerated_and_sent_only_once(self):
        key = self.legacy_hold()
        self.assertTrue(worker.run_once(self.f.settings, self.f.api))
        self.assertEqual(self.f.states(), ['completed'])
        self.assertEqual(len(self.f.api.writes), 1)
        self.assertNotIn('HTML antigo', self.f.api.writes[0][1])
        self.assertIn('Qual tipo de nota', self.f.api.writes[0][1])
        with receiver._connect() as db:
            self.assertEqual(db.execute('SELECT state FROM internal_note_outbox WHERE key=?', (key,)).fetchone()[0], 'sent')
        self.assertFalse(worker.run_once(self.f.settings, self.f.api))
        self.assertEqual(self.run_note(CortexManualPull=1)['state'], 'completed')
        self.assertEqual(len(self.f.api.writes), 1)

    def test_manual_pull_regenerates_legacy_review_instead_of_reusing_it(self):
        self.legacy_hold(outcome='historical')
        self.assertEqual(self.run_note(CortexManualPull=1)['outcome'], 'nota_interna_publicada')
        self.assertNotIn('HTML antigo', self.f.api.writes[0][1])

    def test_recovery_checks_current_scope_company_and_pause_before_generation(self):
        for block in ('company', 'scope', 'pause'):
            with self.subTest(block=block):
                with receiver._connect() as db:
                    db.execute('DELETE FROM webhook_events')
                    db.execute('DELETE FROM internal_note_outbox')
                self.f.api.data = fixtures.ticket()
                self.legacy_hold()
                if block == 'company':
                    self.f.api.data['clients'][0]['organization']['id'] = 'unauthorized'
                elif block == 'pause':
                    import ticket_agent_control
                    ticket_agent_control.set_paused(42, True, 'QA')
                with patch.object(worker, 'new_ticket_scope', return_value={
                        'allowed': block != 'scope', 'reason': 'chamado_anterior_a_liberacao'}):
                    self.assertTrue(worker.run_once(self.f.settings, self.f.api))
                self.assertEqual(self.f.states(), ['skipped'])
                self.assertFalse(self.f.api.writes)
                self.model.assert_not_called()

    def test_recovery_never_requeues_uncertain_or_exhausted_delivery(self):
        self.legacy_hold(outcome='tentativas_esgotadas', outbox_state='uncertain')
        self.assertFalse(worker.run_once(self.f.settings, self.f.api))
        self.model.assert_not_called()
        self.assertFalse(self.f.api.writes)

    def test_generation_failure_during_recovery_keeps_review_html_unsent(self):
        self.legacy_hold()
        self.model.return_value = {'ok': False, 'error': 'unavailable'}
        self.assertTrue(worker.run_once(self.f.settings, self.f.api))
        self.assertEqual(self.f.states(), ['retry'])
        self.assertFalse(self.f.api.writes)
        with receiver._connect() as db:
            self.assertEqual(db.execute('SELECT state FROM internal_note_outbox').fetchone()[0], 'review')


if __name__ == '__main__':
    unittest.main()
