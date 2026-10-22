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

    def test_sales_margin_request_routes_to_reports_specialist(self):
        from triage_router import resolve_specialist
        specialist, result = resolve_specialist(
            'Relatório mensal', 'Preciso ver a margem das minhas vendas do mês.'
        )
        self.assertIn('reports', result['modules'])
        self.assertEqual(specialist['id'], 'reports')

    def test_fastreport_physical_margin_stays_with_fr3_specialist(self):
        from triage_router import resolve_specialist
        specialist, result = resolve_specialist(
            'Relatório FR3', 'A margem da etiqueta está cortada na impressora Zebra.'
        )
        self.assertNotIn('reports', result['modules'])
        self.assertEqual(specialist['id'], 'fr3_logistics')

    def test_reports_specialist_keeps_official_sources_with_portuguese_modules(self):
        from triage_router import resolve_specialist, filter_by_specialist
        specialist, route_info = resolve_specialist(
            'Relatório', 'Qual relatório mostra vendas por produto no período?'
        )
        sources = [
            {'id': 1, 'title': 'Estatística de vendas', 'modules': ['Vendas']},
            {'id': 2, 'title': 'Projeção de compras', 'modules': ['Compras']},
        ]
        filtered = filter_by_specialist(sources, specialist, route_info)
        self.assertEqual([source['id'] for source in filtered], [1, 2])

    def test_resolve_specialist_selects_configured_subagent_and_filters_domain(self):
        from triage_router import resolve_specialist, filter_by_specialist
        spec_fiscal, route_fiscal = resolve_specialist('Erro ao transmitir NF-e', 'Rejeição 694 da SEFAZ no CFOP')
        self.assertIsNotNone(spec_fiscal)
        self.assertEqual(spec_fiscal['id'], 'fiscal')
        self.assertEqual(spec_fiscal['name'], 'Fiscal & SEFAZ')

        spec_int, _ = resolve_specialist('Pedido não desceu', 'Erro no sincronizador da AnyMarket')
        self.assertIsNotNone(spec_int)
        self.assertEqual(spec_int['id'], 'integrations')
        self.assertEqual(spec_int['name'], 'Hubs & Integrações')

        items = [
            {'external_id': 101, 'subject': 'Rejeição 694 NF-e SEFAZ', 'problem_statement': 'Erro CFOP na nota fiscal',
             'solution_statement': 'Ajustada tributação e CFOP na nota fiscal.'},
            {'external_id': 202, 'subject': 'Impressora Zebra desalinhada', 'problem_statement': 'Etiqueta FR3 cortada na bobina',
             'solution_statement': 'Ajustada margem no FastReport.'},
        ]
        filtered = filter_by_specialist(items, spec_fiscal, route_fiscal)
        self.assertEqual([item['external_id'] for item in filtered], [101])

    def test_worker_routes_to_fiscal_specialist_compares_resolved_tickets_and_cites_them(self):
        from unittest.mock import MagicMock, Mock, patch
        from movidesk_note_worker import Settings, generate_note, render_note

        ticket = {
            'id': 150000,
            'subject': 'Erro ao transmitir NF-e',
            'clients': [{'id': 'req', 'email': 'c@example.test',
                         'organization': {'id': 'org-1', 'businessName': 'Empresa Fiscal'}}],
            'actions': [{
                'id': 1, 'type': 2,
                'description': 'Ao transmitir a NF-e apareceu Rejeição 694: Não informado o grupo de ICMS para a UF de destino.',
                'createdBy': {'id': 'req', 'profileType': 2},
            }],
        }
        resolved_cases = [
            {
                'external_id': 129901,
                'subject': 'SUP - NFE - Rejeição 694 DIFAL UF destino',
                'status': 'Resolvido',
                'base_status': 'Resolved',
                'is_active': False,
                'quality_tier': 'GOLD',
                'hard_block_reason': '',
                'problem_statement': 'Rejeição 694 ao transmitir NF-e interestadual para não contribuinte.',
                'technical_narrative': 'Aba de tributação interestadual/DIFAL estava sem preenchimento no cadastro.',
                'solution_statement': 'Acessada a nota fiscal na aba Tributação, preenchida a partilha ICMS UF destino (DIFAL) e nota transmitida.',
            },
            {
                'external_id': 128450,
                'subject': 'Rejeição 694 na emissão de nota fiscal',
                'status': 'Fechado',
                'base_status': 'Closed',
                'is_active': False,
                'quality_tier': 'SILVER',
                'hard_block_reason': '',
                'problem_statement': 'Erro 694 na SEFAZ.',
                'technical_narrative': 'Operação interestadual para consumidor final sem grupo ICMSUFDest.',
                'solution_statement': 'Configurado cálculo de DIFAL no CFOP e recarregados os impostos da NF-e.',
            },
        ]
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value = {'name': None}
        llm_reply = {
            'ok': True,
            'text': ('1. Acesse a nota fiscal e abra a aba **Tributação**.\n'
                     '2. Confira o preenchimento do grupo de ICMS para a UF de destino (DIFAL) no CFOP utilizado e retransmita a NF-e.'),
            'model': 'gemini-test',
        }
        with patch('technician_workbench.connection', return_value=conn), \
             patch('tech_evidence.ensure_single_tenant', return_value='tenant'), \
             patch('agent_control.organization', return_value=('org-1', 'Empresa Fiscal', None)), \
             patch('knowledge_access.search', return_value=[]), \
             patch('knowledge_access.approved_procedures', return_value=[]), \
             patch('technician_workbench.resolved_support_cases', return_value=resolved_cases), \
             patch('technician_workbench.llm_client.generate', return_value=llm_reply) as llm_gen:
            result = generate_note(ticket, Settings(), Mock())

        llm_gen.assert_called_once()
        prompt_sent = llm_gen.call_args[0][0]
        self.assertIn('Subagente Especialista acionado: Fiscal & SEFAZ', prompt_sent)
        self.assertIn('129901', prompt_sent)
        self.assertIn('partilha ICMS UF destino (DIFAL)', prompt_sent)
        self.assertEqual(result['specialist']['name'], 'Fiscal & SEFAZ')

        rendered = render_note(result, 'marker-1')
        self.assertIn('Especialista acionado:</strong> Fiscal &amp; SEFAZ', rendered)
        self.assertIn('/Ticket/Edit/129901', rendered)
        self.assertIn('/Ticket/Edit/128450', rendered)
        self.assertIn('Resolução registrada:', rendered)


if __name__ == '__main__':
    unittest.main()
