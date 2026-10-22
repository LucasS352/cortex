import unittest
from unittest.mock import patch

from support_policy import decide, evidence, response_violations
from technician_workbench import build_reply, cases_for_selected_evidence


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

    def test_applicable_official_report_document_can_answer_report_request(self):
        source = {'id': 203, 'source_type': 'OFFICIAL_KB', 'applicable': True,
                  'answer_use': 'procedure_reference', 'evidence_status': 'official_document',
                  'title': 'Gerenciamento de Markup de Vendas'}
        result = decide('Preciso de um relatório da margem das vendas', [source])
        self.assertEqual(result['state'], 'PROCEDIMENTO_CONFIRMADO')
        self.assertTrue(result['can_propose_procedure'])

    def test_official_document_does_not_bypass_review_outside_reports(self):
        source = {'id': 204, 'source_type': 'OFFICIAL_KB', 'applicable': True,
                  'answer_use': 'procedure_reference', 'evidence_status': 'official_document',
                  'title': 'Alteração fiscal'}
        result = decide('Preciso alterar a tributação deste produto', [source])
        self.assertFalse(result['can_propose_procedure'])
        self.assertTrue(result['can_draft_from_evidence'])
        self.assertEqual(result['state'], 'RASCUNHO_SUPERVISIONADO')

    def test_resolved_documented_ticket_feeds_reviewable_draft(self):
        source = {'id': 'company-ticket-132176', 'source_external_id': 132176,
                  'source_type': 'PROBABLE_TICKET', 'applicable': True,
                  'answer_use': 'supporting_reference',
                  'evidence_status': 'documented_resolved_case',
                  'title': 'Pedido sem produto relacionado',
                  'problem': 'Produto não relacionado ao pedido',
                  'solution': 'Abra o produto no Master, use Ctrl+R e relacione o código externo.'}
        with patch('technician_workbench.llm_client.generate', return_value={
            'ok': True, 'text': 'Olá! Abra o cadastro do produto e use Ctrl+R para conferir o relacionamento.',
            'error': ''}) as generate:
            reply, meta = build_reply({}, '', [], [], [source],
                                      'Produto não relacionado ao pedido')
        prompt = generate.call_args.args[0]
        self.assertIn('Procedimento da fonte principal:', prompt)
        self.assertIn('use Ctrl+R', prompt)
        self.assertIn('rascunho para revisão do técnico', prompt)
        self.assertIn('Ctrl+R', reply)
        self.assertEqual(meta['mode'], 'supervised_draft')
        self.assertFalse(meta['decision']['can_propose_procedure'])

    def test_unusable_or_conflicting_evidence_never_feeds_steps(self):
        source = {'id': 1, 'source_type': 'OFFICIAL_KB', 'applicable': False,
                  'answer_use': 'investigation_only', 'evidence_status': 'official_document',
                  'solution': 'Abra menu de outro integrador.'}
        with patch('technician_workbench.llm_client.generate', return_value={
            'ok': True, 'text': 'Vamos identificar o integrador.', 'error': ''}) as generate:
            _, meta = build_reply({}, '', [], [], [source], 'Erro na AnyMarket')
        self.assertNotIn('Abra menu de outro integrador.', generate.call_args.args[0])
        self.assertFalse(meta['decision']['can_draft_from_evidence'])

    def test_unrelated_ticket_is_not_attached_to_answer(self):
        cases = [{'external_id': 132176, 'subject': 'Produto sem relação'},
                 {'external_id': 132240, 'subject': 'Venda não integrada'},
                 {'external_id': 130230, 'match': 'error_signature'}]
        selected = cases_for_selected_evidence(cases, {'source_external_id': 132176})
        self.assertEqual([case['external_id'] for case in selected], [132176, 130230])

    def test_generic_marketplace_order_still_uses_occurrences_intake(self):
        from support_triage import intake
        for query in ('meu pedido ja ta certo no marketplace mas nao entrou no master',
                      'Temos uma venda na Magazord que não está integrando no nosso sistema.'):
            with self.subTest(query=query):
                result = intake(query, query, [])
                self.assertIsNotNone(result)
                self.assertEqual(result['mode'], 'triage')
                self.assertIn('Ocorrências', result['reply'])
                self.assertEqual(result['similar_tickets'], [])

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
