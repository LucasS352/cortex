import unittest
from unittest.mock import patch

from support_policy import decide, evidence, response_violations
from technician_workbench import build_reply


class SupportPolicyTests(unittest.TestCase):
    def test_exact_diagnostic_in_print_is_evidence(self):
        self.assertTrue(evidence('Texto extraído do print: HTTP 401 Unauthorized')['concrete_error'])

    def test_carrier_name_alone_does_not_authorize_mapping(self):
        import mapped_replies
        self.assertIsNone(mapped_replies.match('AnyMarket Carrier FedEx transportadora demora para entregar'))

    def test_current_attempt_failure_blocks_repeating_even_reviewed_recipe(self):
        source = {'id': 'approved', 'source_type': 'REVIEWED_PROCEDURE',
                  'applicable': True, 'answer_use': 'procedure_reference', 'solution': 'Repita o cadastro.'}
        with patch('technician_workbench.llm_client.generate') as generate:
            reply, meta = build_reply({}, '', [], [], [source], 'Já relacionei a transportadora e não resolveu')
        generate.assert_not_called()
        self.assertEqual(meta['decision']['state'], 'ENCAMINHAR')
        self.assertNotIn('Repita o cadastro', reply)

    def test_failed_attempt_in_recent_context_also_blocks_recipe_repetition(self):
        source = {'id':'reviewed', 'source_type':'REVIEWED_PROCEDURE','applicable':True,'answer_use':'procedure_reference'}
        _, meta = build_reply({},'Já relacionei e não resolveu',[],[],[source],'E agora?',
                             decision_query='AnyMarket transportadora sem vínculo. Já relacionei e não resolveu. E agora?')
        self.assertEqual(meta['decision']['state'],'ENCAMINHAR')

    def test_conflict_removes_confidence_and_procedural_permission(self):
        result = decide('Erro 539', [{'applicable': True, 'answer_use': 'procedure_reference',
                         'evidence_status': 'human_approved_procedure', 'quality_flags': ['source_conflict']}])
        self.assertEqual(result['state'], 'ENCAMINHAR')
        self.assertFalse(result['can_propose_procedure'])
        self.assertEqual(result['confidence'], 'BAIXA')

    def test_canonical_version_is_attached_to_decision(self):
        result = decide('Erro 539', [{'source_id': 'canon:2', 'applicable': True,
                         'answer_use': 'procedure_reference', 'evidence_status': 'human_approved_procedure',
                         'metadata': {'version_no': 3, 'approved_by': 'curador', 'approved_at': '2026-10-21'}}])
        self.assertEqual(result['state'], 'PROCEDIMENTO_CONFIRMADO')
        self.assertEqual(result['procedure']['version'], 3)
        self.assertEqual(result['procedure']['approved_by'], 'curador')

    def test_open_company_case_does_not_become_solution(self):
        result = decide('HTTP 401 Unauthorized', [], [{'external_id': 2, 'is_active': True}])
        self.assertFalse(result['can_propose_procedure'])
        self.assertEqual(result['related_open_tickets'], [2])

    def test_resolved_history_cannot_become_main_procedure(self):
        result = decide('Falha na integração', [{'id':'company-ticket-1','applicable':True,
            'source_type':'PROBABLE_TICKET','answer_use':'supporting_reference'}])
        self.assertEqual(result['state'],'INVESTIGAR')
        self.assertFalse(result['can_propose_procedure'])
        self.assertIsNone(result['procedure'])

    def test_howto_without_approved_source_does_not_require_an_error(self):
        from support_policy import investigation_reply
        query='Como consultar vendas por produto?'
        reply=investigation_reply(query,decide(query,[]))
        self.assertIn('dúvida de uso',reply)
        self.assertNotIn('Informe a mensagem',reply)
        self.assertNotIn('preciso do retorno',reply)

    def test_report_without_approved_source_does_not_request_error_message(self):
        from support_policy import investigation_reply
        query='Preciso de um relatório de vendas por período'
        reply=investigation_reply(query,decide(query,[]))
        self.assertIn('relatório solicitado',reply)
        self.assertIn('não é necessário apresentar uma mensagem de erro',reply)
        self.assertNotIn('mensagem exibida',reply)

    def test_model_navigation_without_source_is_blocked(self):
        with patch('technician_workbench.llm_client.generate', return_value={
            'ok': True, 'text': 'Abra a aba Inventada e pressione Ctrl+R.'}):
            reply, meta = build_reply({}, '', [], [], [], 'HTTP 401 Unauthorized na Tray')
        self.assertEqual(meta['mode'], 'guardrail')
        self.assertIn('unsupported_navigation', meta['blocked_output_reasons'])
        self.assertNotIn('Inventada', reply)

    def test_mutation_blocked_even_when_source_is_applicable(self):
        self.assertIn('database_mutation', response_violations('DELETE FROM vendas',
            {'applicable': True, 'answer_use': 'procedure_reference'}))


if __name__ == '__main__':
    unittest.main()
