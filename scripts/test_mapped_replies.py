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
        self.assertEqual(result['similar_tickets'][0]['ticket'], 128116)
        self.assertEqual(len(result['images']), 2)
        self.assertIn('Ctrl+R', render_note(result, 'marker'))

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

    def test_missing_origin_is_explicit_and_never_emits_broken_relative_links(self):
        with patch.dict(os.environ, {'CORTEX_PUBLIC_URL': ''}):
            images, notices = mapped.note_images(mapped.match('forma de pagamento não integrada'))
        self.assertFalse(images)
        self.assertIn('CORTEX_PUBLIC_URL', notices[0])

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

    def test_untrusted_inline_image_data_is_rejected(self):
        for value in ('data:text/html;base64,PHNjcmlwdD4=',
                      'data:image/png;base64,PHNjcmlwdD4=',
                      'data:image/svg+xml;base64,PHN2Zz4='):
            self.assertEqual(mapped.safe_image_data(value), '')


if __name__ == '__main__':
    unittest.main()
