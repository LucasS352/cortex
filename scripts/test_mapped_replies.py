import html
import os
import unittest
from unittest.mock import Mock, MagicMock, patch

import mapped_replies as mapped
from movidesk_note_worker import render_note
from movidesk_note_worker import generate_note, Settings
from technician_workbench import build_reply, evidence_for_company


class MappedReplyTests(unittest.TestCase):
    def test_worker_carries_recipe_history_and_images_into_note(self):
        ticket = {'id': 131661, 'subject': 'Teste', 'clients': [], 'actions': [
            {'id': 10, 'type': 2, 'description': 'Forma de pagamento não integrada',
             'createdBy': {'id': 'requester', 'profileType': 2}}]}
        connection = MagicMock()
        connection.__enter__.return_value.execute.return_value.fetchone.return_value = {'name': None}
        with patch('technician_workbench.connection', return_value=connection), \
             patch('tech_evidence.ensure_single_tenant', return_value='tenant'), \
             patch('agent_control.organization', return_value=('', '', None)), \
             patch.dict(os.environ, {'CORTEX_PUBLIC_URL': 'https://cortex.example'}), \
             patch('technician_workbench.llm_client.generate') as model:
            result = generate_note(ticket, Settings(), Mock())
        model.assert_not_called()
        self.assertEqual(result['mode'], 'reviewed_procedure')
        self.assertEqual(result['similar_tickets'][0]['ticket'], 131288)
        self.assertEqual(len(result['images']), 2)
        self.assertIn('Ctrl+R', render_note(result, 'marker'))

    def test_worker_carries_three_confirmed_state_registration_cases_into_note(self):
        ticket = {'id': 131726, 'subject': 'Erro nota', 'clients': [], 'actions': [
            {'id': 10, 'type': 2, 'description': 'IE do destinatário inválida',
             'createdBy': {'id': 'requester', 'profileType': 2}}]}
        connection = MagicMock()
        connection.__enter__.return_value.execute.return_value.fetchone.return_value = {'name': None}
        with patch('technician_workbench.connection', return_value=connection), \
             patch('tech_evidence.ensure_single_tenant', return_value='tenant'), \
             patch('agent_control.organization', return_value=('', '', None)), \
             patch.dict(os.environ, {'CORTEX_PUBLIC_URL': 'https://cortex.example'}), \
             patch('technician_workbench.llm_client.generate') as model:
            result = generate_note(ticket, Settings(), Mock())
        model.assert_not_called()
        self.assertEqual(result['mode'], 'reviewed_procedure')
        self.assertGreaterEqual(len(result['similar_tickets']), 3)
        note = render_note(result, 'marker')
        for ticket_id in (129530, 118832, 99143):
            self.assertIn('/Ticket/Edit/' + str(ticket_id), note)
        self.assertIn('Recarregue os dados do destinatário', note)

    def test_ticket_131661_gets_complete_procedure_without_model_omitting_shortcut(self):
        query = ('Pedido LU-1558670119374371 (INTEGRACOMMERCE - CineMagiaMagalu08) '
                 'com Forma de Pagamento não integrada efd67646-5229-49d0-90fa-44a3f7241704.')
        with patch('technician_workbench.company_similar_cases', return_value=[]), \
             patch('technician_workbench.llm_client.generate') as model:
            sources, cases, scope = evidence_for_company(None, 'tenant', 'company', query)
            reply, meta = build_reply({}, '', [], [], sources, query, cases, 'customer_draft')
        model.assert_not_called()
        self.assertEqual(scope, 'reviewed_procedure')
        self.assertTrue(meta['used'])
        for term in ('Cadastros → Formas de Pagamento', 'Ctrl+R', 'Código de fora', 'reprocesse'):
            self.assertIn(term, reply)
        self.assertNotIn('128116', reply)
        self.assertNotIn('efd67646', reply)
        self.assertNotIn('CineMagia', reply)

    def test_named_payment_and_error_without_uuid_is_enough(self):
        self.assertIsNotNone(mapped.match('anymarket: forma de pagamento "cartao elo" nao integrada'))

    def test_payment_not_configured_uses_reviewed_ctrl_r_procedure(self):
        query = ('Erro na baixa do pedido 404021225: Forma de pagamento "CARTÃO DE CRÉDITO" '
                 '/ [Shopee PayLater] não está configurada. Código do erro: CEF94536')
        source = mapped.match(query)
        self.assertIsNotNone(source)
        self.assertEqual(source['id'], 'reviewed-payment-relation-v1')
        self.assertEqual([case['ticket'] for case in source['resolved_examples']], [131288, 128116])
        self.assertEqual([item['after_step'] for item in source['images']], [2, 3])
        for term in ('Cadastros → Formas de Pagamento', 'Ctrl+R', 'Relação - Site',
                     'Código de fora', 'Shopee PayLater'):
            self.assertIn(term, source['reviewed_reply'])
        images, notices = mapped.note_images(source)
        self.assertFalse(notices)
        note = render_note({'reply': source['reviewed_reply'], 'images': images,
                            'similar_tickets': source['resolved_examples']}, 'marker')
        self.assertLess(note.index('2. Com essa forma'), note.index(images[0]['data_url']))
        self.assertLess(note.index(images[0]['data_url']), note.index('3. Confira'))
        self.assertLess(note.index('3. Confira'), note.index(images[1]['data_url']))
        self.assertIn('/Ticket/Edit/131288', note)

    def test_different_scenarios_and_failed_attempts_do_not_repeat_recipe(self):
        for query in ('meu pedido não caiu no master', 'produto não integrado',
                      'Pedidos Anymarket integram sem forma de pagamento e à vista',
                      'ANY_PAGAMENTO_STATUS: forma de pagamento não integrada',
                      'Forma de pagamento não integrada, já fiz Ctrl+R e não resolveu',
                      'Forma de pagamento não integrada. Já relacionei, continua igual'):
            with self.subTest(query=query):
                self.assertIsNone(mapped.match(query))

    def test_note_links_verified_history_and_bundled_images(self):
        source = mapped.match('Forma de pagamento não integrada')
        with patch.dict(os.environ, {'CORTEX_PUBLIC_URL': 'https://cortex.example'}):
            images, notices = mapped.note_images(source)
        self.assertEqual(len(images), 2)
        self.assertFalse(notices)
        self.assertTrue(images[0]['data_url'].startswith('data:image/png;base64,'))
        self.assertEqual(mapped.safe_image_data(images[0]['data_url']), images[0]['data_url'])
        note = render_note({'reply': source['reviewed_reply'], 'images': images,
                            'similar_tickets': source['resolved_examples']}, 'test-marker')
        self.assertIn('/Ticket/Edit/128116', note)
        self.assertIn('Resolução registrada:', note)
        self.assertIn('/procedure-images/payment-selection.png', note)
        self.assertIn('<img src="data:image/png;base64,', note)
        self.assertIn('max-width:100%', note)

    def test_product_not_integrated_uses_resolved_ticket_131672_recipe(self):
        source = mapped.match('Produto "229880" (código do integrador) - "5773950.362939.0-Água" (descrição) não integrado')
        self.assertIsNotNone(source)
        self.assertEqual(source['id'], 'reviewed-product-relation-v1')
        self.assertEqual(source['resolved_examples'][0]['ticket'], 131672)
        for term in ('produto_id', 'cod_estoque', 'Código de fora', 'Adicional', 'Ctrl+R'):
            self.assertIn(term, source['reviewed_reply'])

    def test_state_registration_reply_has_official_link_steps_and_images(self):
        source = mapped.match('Ao transmitir a nota aparece Inscrição Estadual inválida.')
        self.assertIsNotNone(source)
        self.assertEqual(source['id'], 'reviewed-state-registration-v1')
        self.assertEqual(source['source_url'], 'https://dfe-portal.svrs.rs.gov.br/Nfe/Ccc')
        self.assertEqual(len(source['images']), 3)
        self.assertGreaterEqual(len(source['resolved_examples']), 3)
        self.assertEqual([case['ticket'] for case in source['resolved_examples'][:3]],
                         [129530, 118832, 99143])
        self.assertTrue(all(not case['active'] and case['resolution']
                            for case in source['resolved_examples']))
        for term in ('Dados de Entrega', 'RG/IE', 'Pesquisar por CNPJ', 'Situação IE', 'Habilitado'):
            self.assertIn(term, source['reviewed_reply'])
        with patch.dict(os.environ, {'CORTEX_PUBLIC_URL': ''}):
            images, notices = mapped.note_images(source)
        self.assertEqual(len(images), 3)
        self.assertFalse(notices)
        self.assertFalse(images[0]['url'])
        note = render_note({'reply': source['reviewed_reply'], 'images': images,
                            'sources': [{'title': source['title'], 'url': source['source_url']}],
                            'similar_tickets': source['resolved_examples']}, 'marker')
        self.assertEqual(note.count('<img src="data:image/png;base64,'), 3)
        for ticket_id in (129530, 118832, 99143, 104696):
            self.assertIn('/Ticket/Edit/' + str(ticket_id), note)
        self.assertGreaterEqual(note.count('Resolução registrada:'), 4)
        exact_link = '<a href="https://dfe-portal.svrs.rs.gov.br/Nfe/Ccc">https://dfe-portal.svrs.rs.gov.br/Nfe/Ccc</a>'
        self.assertIn(exact_link, note)
        self.assertNotIn('href="https://dfe-portal.svrs.rs.gov.br/Nfe/Ccc)"', note)
        self.assertLess(note.index('1. Abra a nota fiscal'), note.index('state-registration-delivery.png')
                        if 'state-registration-delivery.png' in note else note.index(images[0]['data_url']))
        first_image = note.index(images[0]['data_url'])
        second_step = note.index('2. Acesse <a href=')
        second_image = note.index(images[1]['data_url'])
        third_step = note.index('3. No resultado')
        third_image = note.index(images[2]['data_url'])
        self.assertLess(first_image, second_step)
        self.assertLess(second_step, second_image)
        self.assertLess(second_image, third_step)
        self.assertLess(third_step, third_image)

    def test_state_registration_reply_matches_master_abbreviation_from_screenshot(self):
        source = mapped.match('Ocorreram os seguintes erros ao enviar a NFe: IE do destinatário inválida')
        self.assertIsNotNone(source)
        self.assertEqual(source['id'], 'reviewed-state-registration-v1')

    def test_duplicate_nfe_reply_has_official_queries_image_and_confirmed_cases(self):
        source = mapped.match('Rejeição: Duplicidade de NF-e com diferença na Chave de Acesso [chNFe: 123]')
        self.assertIsNotNone(source)
        self.assertEqual(source['id'], 'reviewed-duplicate-nfe-key-v1')
        self.assertEqual(len(source['resolved_examples']), 3)
        self.assertEqual([case['ticket'] for case in source['resolved_examples']],
                         [127706, 117855, 115902])
        self.assertEqual(source['images'][0]['after_step'], 1)
        with patch.dict(os.environ, {'CORTEX_PUBLIC_URL': ''}):
            images, notices = mapped.note_images(source)
        self.assertEqual(len(images), 1)
        self.assertFalse(notices)
        note = render_note({'reply': source['reviewed_reply'], 'images': images,
                            'sources': [{'title': source['title'], 'url': source['source_url']}],
                            'similar_tickets': source['resolved_examples']}, 'marker')
        for url in (mapped.NFE_INUTILIZATION_URL, mapped.NFE_QUERY_URL):
            escaped_url = html.escape(url, quote=True)
            self.assertIn(f'<a href="{escaped_url}">{escaped_url}</a>', note)
        self.assertEqual(note.count('<img src="data:image/png;base64,'), 1)
        self.assertLess(note.index('1. Abra a nota fiscal'), note.index(images[0]['data_url']))
        self.assertLess(note.index(images[0]['data_url']), note.index('2. Copie a chave'))
        for ticket_id in (127706, 117855, 115902):
            self.assertIn('/Ticket/Edit/' + str(ticket_id), note)

    def test_exact_recipe_wins_even_when_company_history_exists(self):
        ticket = {'id': 140000, 'subject': 'Pedido não integrado',
                  'clients': [{'id': 'requester', 'email': 'cliente@example.test',
                               'organization': {'id': 'company', 'businessName': 'Empresa'}}],
                  'actions': [{'id': 15, 'type': 2,
                    'description': 'Produto "229880" (código do integrador) - "5773950.362939.0-Água" (descrição) não integrado',
                    'createdBy': {'id': 'requester', 'profileType': 2}}]}
        connection = MagicMock()
        def execute(query, *_args):
            name = 'movidesk_company_tickets' if 'movidesk_company_tickets' in query else None
            return MagicMock(fetchone=Mock(return_value={'name': name}))
        connection.__enter__.return_value.execute.side_effect = execute
        unrelated = {'source_type': 'VALIDATED_TICKET', 'applicable': True,
                     'answer_use': 'procedure_reference', 'title': 'Outro caso'}
        with patch('technician_workbench.connection', return_value=connection), \
             patch('tech_evidence.ensure_single_tenant', return_value='tenant'), \
             patch('agent_control.organization', return_value=('company', 'Empresa', None)), \
             patch('technician_workbench.evidence_for_company',
                   return_value=([unrelated], [], 'same_company')) as company_search, \
             patch.dict(os.environ, {'CORTEX_PUBLIC_URL': 'https://cortex.example'}), \
             patch('technician_workbench.llm_client.generate') as model:
            result = generate_note(ticket, Settings(), Mock())
        company_search.assert_called_once()
        model.assert_not_called()
        self.assertEqual(result['mode'], 'reviewed_procedure')
        self.assertIn('cod_estoque', result['reply'])

    def test_missing_origin_keeps_embedded_images_without_broken_relative_links(self):
        with patch.dict(os.environ, {'CORTEX_PUBLIC_URL': ''}):
            images, notices = mapped.note_images(mapped.match('forma de pagamento não integrada'))
        self.assertEqual(len(images), 2)
        self.assertFalse(notices)
        self.assertTrue(all(not image['url'] for image in images))

    def test_sources_keep_kb_links_and_ticket_ids_are_not_confused_with_articles(self):
        note = render_note({'reply': 'Teste', 'sources': [
            {'title': 'Artigo', 'url': 'https://helpdesk.alternativasistemas.com.br/kb/article/123'},
            {'title': 'Histórico', 'ticket': 128116},
            {'title': '<script>', 'url': 'javascript:alert(1)'},
        ]}, 'marker')
        self.assertIn('/kb/article/123', note)
        self.assertIn('/Ticket/Edit/128116', note)
        self.assertNotIn('javascript:', note)
        self.assertNotIn('<script>', note)

    def test_urls_reject_credentials_tokens_and_markup(self):
        for value in ('javascript:alert(1)', 'https://user:pass@example.com/x',
                      'https://example.com/x?token=secret', '//example.com/x',
                      'https://example.com/"onclick="x'):
            self.assertEqual(mapped.safe_url(value), '')

    def test_only_exact_official_nfe_query_urls_may_keep_a_query_string(self):
        self.assertEqual(mapped.safe_url(mapped.NFE_INUTILIZATION_URL), mapped.NFE_INUTILIZATION_URL)
        self.assertEqual(mapped.safe_url(mapped.NFE_QUERY_URL), mapped.NFE_QUERY_URL)
        self.assertEqual(mapped.safe_url(mapped.NFE_INUTILIZATION_URL + '&redirect=https://example.com'), '')

    def test_untrusted_inline_image_data_is_rejected(self):
        for value in ('data:text/html;base64,PHNjcmlwdD4=',
                      'data:image/png;base64,PHNjcmlwdD4=',
                      'data:image/svg+xml;base64,PHN2Zz4='):
            self.assertEqual(mapped.safe_image_data(value), '')

    def test_anymarket_carrier_reply_includes_ctrl_r_image_and_no_duplicate_caption(self):
        for query in (
            'cara como faço pra relacionar a transportadora, esta dando um erro no pedido da anymarket.',
            'estou com erro de transportadora dentro da anymarket, como faço para relacionar a transportadora ??',
            'Carrier retornado pela plataforma: FedEx.O pedido não foi integrado porque não existe relacionamento da transportadora no ERP Master.',
        ):
            with self.subTest(query=query):
                source = mapped.match(query)
                self.assertIsNotNone(source)
                self.assertEqual(source['id'], 'reviewed-anymarket-carrier-relation-v1')
                images, notices = mapped.note_images(source)
                self.assertEqual(len(images), 1)
                self.assertFalse(notices)
                note = render_note({'reply': source['reviewed_reply'], 'images': images}, 'marker')
                self.assertIn('Ctrl+R', note)
                self.assertIn('Janela Relação - Site (Ctrl+R)', note)
                self.assertEqual(note.count(images[0]['caption']), 2)  # once in alt="", once in <small>

    def test_image_without_distinct_name_does_not_duplicate_caption_in_html(self):
        source = mapped.match('Forma de pagamento não integrada')
        images, _ = mapped.note_images(source)
        note = render_note({'reply': source['reviewed_reply'], 'images': images}, 'marker')
        self.assertNotIn('<p><strong>' + html.escape(images[0]['caption']) + '</strong></p>', note)

    def test_synchronizer_transportadora_log_and_target_code_10(self):
        from technician_workbench import search_context_query
        log = ('16/01/2025 09:45:44 ------------------------------Forçando Pedido 871'
               'Não será possível baixar o pedido [871]: Transportadora "Mini Envios Kangu via Ag Correios" '
               'não integrada.Código do erro: [3F638290-EDEE-474F-BCFA-E66547F56B61]')
        source_log = mapped.match(log)
        self.assertIsNotNone(source_log)
        self.assertIn('Mini Envios Kangu via Ag Correios', source_log['reviewed_reply'])
        self.assertIn('Ctrl+R', source_log['reviewed_reply'])
        self.assertNotIn('AnyMarket', source_log['reviewed_reply'])

        recent = [
            {'role': 'technician', 'content': log},
            {'role': 'technician', 'content': 'Carrier retornado pela plataforma: FedEx. O pedido não foi integrado porque não existe relacionamento da transportadora no ERP Master.'},
        ]
        contextual = search_context_query('quero cadastrar na transportadora 10', recent)
        self.assertNotIn('FedEx', contextual)
        source_10 = mapped.match(contextual)
        self.assertIsNotNone(source_10)
        self.assertIn('código 10', source_10['reviewed_reply'])
        self.assertIn('Mini Envios Kangu via Ag Correios', source_10['reviewed_reply'])
        self.assertNotIn('AnyMarket', source_10['reviewed_reply'])
        self.assertNotIn('FedEx', source_10['reviewed_reply'])

    def test_api_failure_does_not_authorize_carrier_mapping(self):
        queries = (
            'AnyMarket: pedido não integrado porque a API da transportadora retornou HTTP 503 Service Unavailable.',
            'Pedido não integrado: timeout na API da transportadora FedEx.',
            'Transportadora FedEx: API não integrada por HTTP 401 Unauthorized.',
        )
        for query in queries:
            with self.subTest(query=query):
                self.assertIsNone(mapped.match(query))

    def test_other_entity_failure_does_not_select_carrier_recipe(self):
        source = mapped.match('AnyMarket pedido não integrado: forma de pagamento não integrada. Transportadora FedEx.')
        self.assertEqual(source['id'], 'reviewed-payment-relation-v1')
        self.assertIsNone(mapped.match('Produto não integrado. Transportadora FedEx.'))
        self.assertFalse(mapped.carrier_mapping_failure(
            'Transportadora FedEx, forma de pagamento não integrada.'))

    def test_order_external_and_error_codes_are_not_carrier_destination(self):
        for detail in ('Código 871 do pedido.', 'Código 123 externo recebido da plataforma.',
                       'Código 503 do erro.', 'Transportadora 123 externa da plataforma.'):
            with self.subTest(detail=detail):
                source = mapped.match('Transportadora "FedEx" não integrada. ' + detail)
                self.assertIsNotNone(source)
                self.assertNotRegex(source['reviewed_reply'], r'transportadora (?:de código )?\d+')
                self.assertNotIn('cadastre a transportadora primeiro', source['reviewed_reply'])

    def test_only_explicit_internal_carrier_code_is_bound(self):
        for detail in ('Quero relacionar à transportadora 10.',
                       'Usar transportadora de código 10 no Master.',
                       'Código 871 do pedido. Quero relacionar à transportadora 10.'):
            with self.subTest(detail=detail):
                source = mapped.match('Transportadora "FedEx" não integrada. ' + detail)
                self.assertIn('transportadora de código 10', source['reviewed_reply'])
                self.assertNotIn('transportadora de código 871', source['reviewed_reply'])

    def test_conflicting_destinations_are_not_silently_selected(self):
        self.assertEqual(mapped.carrier_target_code('transportadora 10 ou transportadora 20'), '')

    def test_turn_aware_destination_correction_and_same_turn_ambiguity(self):
        from technician_workbench import search_context_query
        log = 'Transportadora "Mini Envios Kangu via Ag Correios" não integrada.'
        recent_corrected = [
            {'role': 'technician', 'content': 'quero cadastrar na transportadora 10'},
            {'role': 'technician', 'content': log},
        ]
        corrected_query = search_context_query('perdão, quero cadastrar na transportadora 20', recent_corrected)
        self.assertEqual(mapped.carrier_target_code(corrected_query), '20')
        source = mapped.match(corrected_query)
        self.assertIsNotNone(source)
        self.assertIn('transportadora de código 20', source['reviewed_reply'])
        self.assertNotIn('transportadora de código 10', source['reviewed_reply'])

        ambiguous_query = search_context_query('ou melhor, na transportadora 10 ou transportadora 20', recent_corrected)
        self.assertEqual(mapped.carrier_target_code(ambiguous_query), '')
        ambiguous_source = mapped.match(ambiguous_query)
        self.assertIsNotNone(ambiguous_source)
        self.assertNotRegex(ambiguous_source['reviewed_reply'], r'transportadora de código \d+')

    def test_order_qualified_carrier_phrases_select_recipe_without_false_positives(self):
        valid_queries = (
            'A transportadora do pedido não está relacionada no Master.',
            'Transportadora deste pedido não integrada na AnyMarket.',
            'Não existe relacionamento para o pedido na transportadora FedEx.',
            'O frete do pedido não está relacionado no Master.',
        )
        for query in valid_queries:
            with self.subTest(query=query):
                source = mapped.match(query)
                self.assertIsNotNone(source)
                self.assertEqual(source['id'], 'reviewed-anymarket-carrier-relation-v1')

        invalid_queries = (
            'Transportadora FedEx: pedido não integrado.',
            'Transportadora FedEx no pedido não integrado.',
            'API da transportadora FedEx não integrada (HTTP 503).',
        )
        for query in invalid_queries:
            with self.subTest(query=query):
                self.assertIsNone(mapped.match(query))


if __name__ == '__main__':
    unittest.main()


