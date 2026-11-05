"""Report-retrieval acceptance against the local restored support corpus (opt-in)."""
import os
import unittest
from unittest.mock import patch

from support_policy import decide
from technician_workbench import connection, evidence_for_company, report_usage_case_source


class ReportTopicUnit(unittest.TestCase):
    def test_extractor_case_cannot_supply_on_screen_report_steps(self):
        case = {'external_id': 125035, 'base_status': 'Closed', 'quality_tier': 'SILVER',
                'hard_block_reason': None, 'service_full': 'Dúvidas de Relatórios',
                'subject': 'EXTRATOR REL VENDAS POR PRODUTOS',
                'problem_statement': 'Configuração por e-mail apresenta erro',
                'solution_statement': 'Obrigado por entrar em contato conosco por este canal de atendimento.'}
        self.assertIsNone(report_usage_case_source(
            case, 'No Master tem algum relatório para ver venda por produto?'))


@unittest.skipUnless(os.getenv('REPORT_TEST_POSTGRES') == '1', 'replay local opt-in')
class ReportTopicReplay(unittest.TestCase):
    def test_ticket_132784_rejects_report_and_positive_control_can_use_it(self):
        import knowledge_access
        from movidesk_note_worker import Settings, generate_note, render_note
        from unittest.mock import Mock
        request = ('Bom dia Marcus. Tudo bem? Conforme sua orientação ao Vini da Stockfy estamos '
                   'abrindo o ticket para solicitar os acessos a API/TOKEN para início do projeto e implantação.')
        action = dict(id=1, type=2, createdBy={'id': 'client', 'profileType': 2}, description=request)
        ticket = dict(id=132784, subject='API/Token Alternativa e Stockfy', category='Solicitação de Serviço',
                      serviceFull=['Comercial', 'Contratar Módulo Novo'], actions=[action], clients=[{'id': 'client'}])
        with connection() as conn:
            self.assertEqual(knowledge_access.approved_procedures(conn, request), [])
        result = generate_note(ticket, Settings(), Mock(spec=[]))
        self.assertEqual(result['evidence_scope'], 'access_provisioning')
        self.assertEqual(result['decision']['state'], 'INVESTIGAR')
        self.assertEqual(result['sources'], [])
        self.assertNotIn('relatório', render_note(result, 'qa').casefold())
        ticket.update(subject='Relatório de vendas', serviceFull=['Dúvidas de Relatórios'])
        action['description'] = 'No Master tem algum relatório que eu possa ver venda por produto?'
        with patch('knowledge_access.knowledge_index.ensure_coverage'), patch('knowledge_access.curation.ensure_schema'):
            result = generate_note(ticket, Settings(), Mock(spec=[]))
        self.assertTrue(result['sources'])
        self.assertRegex(render_note(result, 'qa').lower(), r'vendas? por produtos?')

    def historical_corpus(self, conn):
        # These two fixtures exercise the historical fallback as it existed
        # before the reviewed procedure for 132273 was published.
        conn.execute("""CREATE TEMP VIEW ticket_knowledge_nodes AS
            SELECT * FROM public.ticket_knowledge_nodes WHERE NOT
            (coalesce(source_id, '') LIKE 'canon:%' AND coalesce(source_external_id, 0) = 132273)""")

    def test_eight_report_questions_keep_their_own_evidence(self):
        scenarios = (
            ('Onde vejo vendas por produto em um período?', '107436', 'RASCUNHO_SUPERVISIONADO'),
            ('Como consultar estatística de vendas por categoria?', '542', 'INVESTIGAR'),
            ('Onde vejo a curva ABC dos produtos vendidos?', '106', 'INVESTIGAR'),
            ('Como consultar a posição de estoque por empresa?', None, 'INVESTIGAR'),
            ('Qual relatório mostra a margem das vendas do mês?', 'reviewed-sales-margin-report-v1', 'PROCEDIMENTO_CONFIRMADO'),
            ('Como vejo comissão de vendas por vendedor?', None, 'INVESTIGAR'),
            ('Como consultar projeção de compras por produto?', '1368', 'RASCUNHO_SUPERVISIONADO'),
            ('Onde encontro o demonstrativo de venda analítico DVA?', '601', 'INVESTIGAR'),
        )
        with patch('knowledge_access.knowledge_index.ensure_coverage'), \
             patch('knowledge_access.curation.ensure_schema'), connection() as conn:
            self.historical_corpus(conn)
            tenant = conn.execute('SELECT id FROM tenants LIMIT 1').fetchone()['id']
            for question, source_id, state in scenarios:
                with self.subTest(question=question):
                    sources, cases, _ = evidence_for_company(
                        conn, tenant, '', question, service_hint='Dúvidas de Relatórios')
                    selected = sources[0] if sources else {}
                    self.assertEqual(decide(question, sources)['state'], state)
                    if source_id:
                        self.assertEqual(str(selected.get('source_external_id') or selected.get('id')), source_id)
                    if 'vendas por produto' in question.lower():
                        self.assertIn(125478, [case['external_id'] for case in cases])

    def test_ticket_132273_question_uses_screen_report_not_email_extractor(self):
        question = 'no master tem algum relatorio que eu possa ver venda por produto?'
        with patch('knowledge_access.knowledge_index.ensure_coverage'), \
             patch('knowledge_access.curation.ensure_schema'), connection() as conn:
            self.historical_corpus(conn)
            tenant = conn.execute('SELECT id FROM tenants LIMIT 1').fetchone()['id']
            sources, cases, _ = evidence_for_company(
                conn, tenant, '578851081', question, exclude_ticket_id=132273,
                service_hint='Dúvidas de Relatórios')
        self.assertEqual(sources[0]['source_external_id'], 107436)
        self.assertNotIn(125035, [case['external_id'] for case in cases
                                  if case.get('match') == 'report_topic'])


if __name__ == '__main__':
    unittest.main()
