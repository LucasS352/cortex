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


if __name__ == '__main__':
    unittest.main()

