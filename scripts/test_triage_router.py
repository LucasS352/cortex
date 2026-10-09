import unittest

from triage_router import route


class TriageRouterTest(unittest.TestCase):
    def test_shipping_label_routes_across_logistics_and_integration(self):
        result = route('Etiqueta do Mercado Livre não baixa', 'O pedido está pronto para envio.')
        self.assertIn('integrations', result['modules'])
        self.assertIn('logistics', result['modules'])
        self.assertTrue(result['needs_review'])

    def test_fastreport_print_problem_does_not_force_marketplace(self):
        result = route('Etiqueta de gôndola cortada', 'No FastReport FR3, a Zebra imprime fora da margem.')
        self.assertIn('products', result['modules'])
        self.assertNotIn('integrations', result['modules'])

    def test_cross_domain_label_is_not_forced_to_one_specialist(self):
        result = route('Etiqueta Shopee desalinhada', 'O ZPL sai cortado na Zebra após baixar pelo marketplace.')
        self.assertIn('integrations', result['modules'])
        self.assertIn('products', result['modules'])
        self.assertTrue(result['needs_review'])

    def test_unknown_topic_abstains(self):
        result = route('Dúvida', 'Preciso de ajuda.')
        self.assertEqual(result['modules'], ['unclassified'])
        self.assertTrue(result['needs_review'])

    def test_short_api_signal_requires_whole_word(self):
        result = route('Atendimento rápido no caixa', 'Uma dúvida simples.')
        self.assertNotIn('integrations', result['modules'])


if __name__ == '__main__':
    unittest.main()
