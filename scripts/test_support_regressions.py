"""Routing contract fixtures, separate from the real historical quality sample."""
import json
from pathlib import Path
import unittest

import mapped_replies
from support_policy import decide
from support_triage import intake


class SupportRegressionTests(unittest.TestCase):
    def test_fixed_domain_scenarios(self):
        path = Path(__file__).resolve().parent.parent/'docs/support-regressions.json'
        for case in json.loads(path.read_text(encoding='utf-8')):
            with self.subTest(case=case['id']):
                source = mapped_replies.match(case['question'])
                self.assertEqual((source or {}).get('id'), case['recipe'])
                decision = decide(case['question'], [source] if source else [])
                self.assertEqual(decision['state'], case['state'])
                if case.get('forbidden'):
                    self.assertIsNone(intake(case['question'], case['question'], []))
                    self.assertNotIn(case['forbidden'], source['reviewed_reply'])

    def test_new_question_does_not_reuse_old_integration_context(self):
        from technician_workbench import search_context_query
        query = search_context_query('Agora outra dúvida: como consultar vendas por produto?',
                                     [{'role':'technician','content':'AnyMarket: Carrier sem relacionamento'}])
        self.assertNotIn('Carrier', query)
        self.assertIsNone(mapped_replies.match(query))

    def test_ticket_132084_two_turn_anymarket_carrier_flow(self):
        from technician_workbench import search_context_query
        turn_1 = 'cara como faço pra relacionar a transportadora, esta dando um erro no pedido da anymarket.'
        turn_2 = ('Carrier retornado pela plataforma: FedEx.O pedido não foi integrado '
                  'porque não existe relacionamento da transportadora no ERP Master.')
        recent = [
            {'role': 'assistant', 'content': 'Poderia nos informar a mensagem de erro exata da transportadora?'},
            {'role': 'technician', 'content': turn_1},
        ]
        contextual_query = search_context_query(turn_2, recent)
        self.assertIn('anymarket', contextual_query.lower())
        self.assertIn('fedex', contextual_query.lower())
        source = mapped_replies.match(contextual_query)
        self.assertIsNotNone(source)
        self.assertEqual(source['id'], 'reviewed-anymarket-carrier-relation-v1')
        decision = decide(contextual_query, [source])
        self.assertEqual(decision['state'], 'PROCEDIMENTO_CONFIRMADO')
        self.assertTrue(decision['can_propose_procedure'])
        images, notices = mapped_replies.note_images(source)
        self.assertEqual(len(images), 1)
        self.assertFalse(notices)

    def test_short_new_requests_do_not_reuse_carrier_diagnosis(self):
        from technician_workbench import search_context_query
        recent = [{'role': 'assistant', 'content': 'Informe o Carrier.'},
                  {'role': 'technician', 'content': 'AnyMarket transportadora sem relacionamento'}]
        for question in ('Como cadastrar um produto?', 'Como configurar forma de pagamento?',
                         'Qual relatório de vendas?', 'Como cadastrar transportadora?'):
            with self.subTest(question=question):
                query = search_context_query(question, recent)
                self.assertEqual(query, question)
                self.assertIsNone(mapped_replies.match(query))

    def test_three_turn_followup_preserves_integrator_and_current_carrier(self):
        from technician_workbench import search_context_query
        recent = [
            {'role': 'assistant', 'content': 'Confira a relação no Ctrl+R.'},
            {'role': 'technician', 'content': 'Carrier: FedEx. Não existe relacionamento da transportadora no ERP Master.'},
            {'role': 'assistant', 'content': 'Envie o erro completo.'},
            {'role': 'technician', 'content': 'Como faço pra relacionar a transportadora com erro na AnyMarket?'},
        ]
        query = search_context_query('Agora quero relacionar na transportadora 10', recent)
        source = mapped_replies.match(query)
        self.assertIn('anymarket', query.lower())
        self.assertIn('transportadora de código 10', source['reviewed_reply'])
        self.assertIn('FedEx', source['reviewed_reply'])

    def test_new_diagnostic_does_not_keep_old_missing_relation(self):
        from technician_workbench import search_context_query
        recent = [{'role': 'technician', 'content': 'AnyMarket: transportadora sem relacionamento'}]
        query = search_context_query('HTTP 503 Service Unavailable na API da transportadora', recent)
        self.assertIn('anymarket', query.lower())
        self.assertNotIn('sem relacionamento', query)
        self.assertIsNone(mapped_replies.match(query))

    def test_replacement_log_does_not_inherit_old_carrier_name(self):
        from technician_workbench import search_context_query
        recent = [{'role': 'technician', 'content': 'AnyMarket: Transportadora "FedEx" não integrada'}]
        query = search_context_query('Transportadora "DHL" não integrada', recent)
        source = mapped_replies.match(query)
        self.assertIn('DHL', source['reviewed_reply'])
        self.assertNotIn('FedEx', source['reviewed_reply'])

    def test_short_followup_after_topic_change_does_not_resurrect_carrier(self):
        from technician_workbench import search_context_query
        query = search_context_query('Onde fica?', [
            {'role': 'technician', 'content': 'Como cadastrar um produto?'},
            {'role': 'technician', 'content': 'AnyMarket transportadora sem relacionamento'}])
        self.assertNotIn('transportadora', query)
        self.assertIsNone(mapped_replies.match(query))

    def test_latest_carrier_field_replaces_quoted_name_in_active_issue(self):
        from technician_workbench import search_context_query
        recent = [{'role': 'technician', 'content': 'Agora o Carrier é DHL'},
                  {'role': 'technician', 'content': 'AnyMarket: Transportadora "FedEx" não integrada'}]
        query = search_context_query('Quero relacionar na transportadora 10', recent)
        source = mapped_replies.match(query)
        self.assertIn('DHL', source['reviewed_reply'])
        self.assertNotIn('FedEx', source['reviewed_reply'])


if __name__ == '__main__':
    unittest.main()

