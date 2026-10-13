import unittest
from unittest.mock import MagicMock, Mock, patch

from support_triage import intake
from movidesk_note_worker import generate_note, render_note, Settings
from technician_workbench import resolved_support_cases


class SupportTriageTests(unittest.TestCase):
    def test_generic_order_uses_images_before_any_database_or_model(self):
        ticket = {'id': 42, 'clients': [], 'actions': [
            {'id': 1, 'type': 2, 'description': 'Meu pedido 123 não caiu no Master',
             'createdBy': {'id': 'client', 'profileType': 2}}]}
        with patch('technician_workbench.connection') as db, patch('technician_workbench.llm_client.generate') as model:
            result = generate_note(ticket, Settings(), Mock())
        db.assert_not_called()
        model.assert_not_called()
        self.assertEqual(result['mode'], 'triage')
        self.assertEqual(result['similar_tickets'], [])
        note = render_note(result, 'test')
        self.assertEqual(note.count('<img '), 3)
        for step in (1, 2, 3):
            self.assertLess(note.index(f'{step}. '), note.index(result['images'][step-1]['data_url']))
            self.assertLess(note.index(result['images'][step-1]['data_url']), note.index(f'{step+1}. '))

    def test_known_error_in_text_or_print_bypasses_intake(self):
        for error in ('Forma de pagamento não integrada', 'HTTP 401 Unauthorized',
                      'Texto extraído do print: IE do destinatário inválida'):
            question = 'Meu pedido não caiu no Master. ' + error
            self.assertIsNone(intake(question, question, []))

    def test_followup_does_not_repeat_intake(self):
        question = 'Meu pedido não caiu no Master'
        self.assertIsNone(intake(question, question, [{'content': 'Na tela Ocorrências de integrações'}]))
        question += '. Já pesquisei e não apareceu nenhuma ocorrência'
        self.assertIsNone(intake(question, question, []))

    def test_unrelated_issue_does_not_use_order_intake(self):
        self.assertIsNone(intake('Erro ao emitir nota fiscal', 'Erro ao emitir nota fiscal', []))

    def test_historical_search_rejects_active_blocked_and_wrong_problem(self):
        base = {'external_id': 1, 'subject': 'Forma de pagamento não integrada',
                'base_status': 'Closed', 'quality_tier': 'GOLD', 'hard_block_reason': '',
                'solution_statement': 'Foi cadastrada a relação da forma de pagamento e o pedido integrou.',
                'problem_statement': 'Forma de pagamento não integrada', 'technical_narrative': ''}
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = [
            dict(base, external_id=2, base_status='InAttendance'),
            dict(base, external_id=3, hard_block_reason='review'),
            dict(base, external_id=4, solution_statement='Resolvido'), base]
        result = resolved_support_cases(db, 'tenant', 'Forma de pagamento não integrada', 42)
        self.assertEqual([row['external_id'] for row in result], [1])
        self.assertIn("base_status IN ('Resolved','Closed')", db.execute.call_args.args[0])
        self.assertIn('42', db.execute.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
