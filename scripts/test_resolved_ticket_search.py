import unittest
from unittest.mock import MagicMock

import mapped_replies
from technician_workbench import extract_resolution, resolved_support_cases

ERROR = ('Erro na baixa do pedido "208581527" (id do pedido na AnyMarket) "LU-1409770438645014" '
         '(código no market place), cliente "AGOSTINHA". Produto (316977845) Sku (95949071) '
         '[id AnyMarket] - (Tela para Cadeira de Praia Verde 45 cm x 01 Metro) não integrada.')
SIGNATURE = '\nObrigado por entrar em contato conosco por este canal de atendimento.'


class AnyMarketProductRecipeTest(unittest.TestCase):
    def test_plain_language_reply_uses_codes_of_the_current_error(self):
        source = mapped_replies.match(ERROR)
        self.assertEqual(source['id'], 'reviewed-anymarket-product-relation-v1')
        reply = source['reviewed_reply']
        self.assertIn('316977845 com o Tipo P', reply)
        self.assertIn('95949071 com o Tipo F', reply)
        self.assertIn('Tela para Cadeira de Praia Verde 45 cm x 01 Metro', reply)
        self.assertIn('Ctrl+R', reply)
        for jargon in ('log', 'produto_id', 'cod_estoque', 'UUID', 'payload'):
            self.assertNotRegex(reply, r'\b' + jargon + r'\b')
        self.assertEqual(source['resolved_examples'], [])

    def test_carrier_error_is_not_taken_as_product(self):
        source = mapped_replies.match('Erro na baixa do pedido "1" (id do pedido na AnyMarket): '
                                      'Carrier "Expresso Aéreo" não integrado.')
        self.assertNotEqual((source or {}).get('id'), 'reviewed-anymarket-product-relation-v1')


class ResolutionExtractionTest(unittest.TestCase):
    def test_resolution_written_in_opening_post_is_recovered(self):
        case = {'problem_statement': 'Assunto/erro abordado: pedido não integrava.\nResolução: a relação do item '
                                     'estava diferente; feita nova relação pelo ctrl+r e o pedido integrou.' + SIGNATURE,
                'solution_statement': SIGNATURE}
        self.assertTrue(extract_resolution(case).startswith('a relação do item'))
        self.assertNotIn('Obrigado', extract_resolution(case))

    def test_signature_or_escalation_is_not_a_resolution(self):
        self.assertEqual(extract_resolution({'solution_statement': '__\nLuiz Supervisor\nalternativasistemas.com.br'}), '')
        self.assertEqual(extract_resolution({'solution_statement': 'Seu chamado foi encaminhado para o setor de Desenvolvimento.'}), '')


class SignatureSearchTest(unittest.TestCase):
    def row(self, ticket, subject, problem, same_company=False):
        return {'external_id': ticket, 'subject': subject, 'status': 'Fechado', 'base_status': 'Closed',
                'problem_statement': problem, 'technical_narrative': '', 'solution_statement': SIGNATURE,
                'quality_tier': 'REVIEW', 'hard_block_reason': None, 'same_company': same_company}

    def test_same_error_ranks_first_and_project_tickets_are_ignored(self):
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = [
            self.row(1, 'DSM - Implantação', 'Sku (1) [id AnyMarket]\nResolução: relação ajustada no ctrl+r.'),
            self.row(2, 'Pedido sem integrar', 'Produto (111) Sku (222) [id AnyMarket]\nResolução: relacionado no ctrl+r como Tipo F.'),
            self.row(3, 'Nova relação no item', ERROR + '\nResolução: feita nova relação do item; pedido integrado.'),
        ]
        result = resolved_support_cases(db, 'tenant', ERROR, 132240, company_id='99')
        self.assertEqual([r['external_id'] for r in result], [3, 2])
        self.assertTrue(all(r['resolution_verified'] for r in result))
        self.assertIn('ILIKE ANY', db.execute.call_args.args[0])

    def test_same_company_comes_first(self):
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = [
            self.row(3, 'Outra empresa', ERROR + '\nResolução: feita nova relação pelo ctrl+r.'),
            self.row(4, 'Mesma empresa', 'Produto (5) Sku (6) [id AnyMarket]\nResolução: relação criada no produto e pedido integrado.', True),
        ]
        result = resolved_support_cases(db, 'tenant', ERROR, company_id='453')
        self.assertEqual(result[0]['external_id'], 4)


if __name__ == '__main__':
    unittest.main()
