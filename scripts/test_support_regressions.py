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

    def test_four_turn_ticket_132084_kangu_log_does_not_inherit_anymarket_or_fedex(self):
        from technician_workbench import active_issue_messages, search_context_query
        turn_1 = 'cara como faço pra relacionar a transportadora, esta dando um erro no pedido da anymarket.'
        turn_2 = ('Carrier retornado pela plataforma: FedEx. O pedido não foi integrado '
                  'porque não existe relacionamento da transportadora no ERP Master.')
        turn_3 = ('16/01/2025 09:45:44 ------------------------------Forçando Pedido 871 '
                  'Não será possível baixar o pedido [871]: Transportadora "Mini Envios Kangu via Ag Correios" '
                  'não integrada. Código do erro: [3F638290-EDEE-474F-BCFA-E66547F56B61]')
        turn_4 = 'quero cadastrar na transportadora 10'
        recent = [
            {'role': 'assistant', 'content': 'Identifiquei a transportadora Mini Envios Kangu.'},
            {'role': 'technician', 'content': turn_3},
            {'role': 'assistant', 'content': 'Para vincular a FedEx na AnyMarket, use o Ctrl+R.'},
            {'role': 'technician', 'content': turn_2},
            {'role': 'assistant', 'content': 'Envie o erro exato.'},
            {'role': 'technician', 'content': turn_1},
        ]
        active_msgs = active_issue_messages(turn_4, recent)
        active_blob = '\n'.join(m['content'] for m in active_msgs)
        self.assertIn('Mini Envios Kangu via Ag Correios', active_blob)
        self.assertNotIn('anymarket', active_blob.lower())
        self.assertNotIn('fedex', active_blob.lower())

        query = search_context_query(turn_4, recent)
        self.assertNotIn('anymarket', query.lower())
        self.assertNotIn('fedex', query.lower())
        source = mapped_replies.match(query)
        self.assertIsNotNone(source)
        self.assertIn('Mini Envios Kangu via Ag Correios', source['reviewed_reply'])
        self.assertIn('transportadora de código 10', source['reviewed_reply'])
        self.assertNotIn('AnyMarket', source['reviewed_reply'])
        self.assertNotIn('FedEx', source['reviewed_reply'])

    def test_repeated_same_log_does_not_drop_opening_integrator(self):
        from technician_workbench import search_context_query
        turn_1 = 'Estou com erro de transportadora na AnyMarket'
        turn_2 = 'Transportadora "FedEx" não integrada.'
        recent = [
            {'role': 'technician', 'content': turn_2},
            {'role': 'technician', 'content': turn_1},
        ]
        query = search_context_query(turn_2, recent)
        self.assertIn('anymarket', query.lower())
        source = mapped_replies.match(query)
        self.assertIsNotNone(source)
        self.assertIn('AnyMarket', source['reviewed_reply'])
        self.assertIn('FedEx', source['reviewed_reply'])

    def test_worker_and_workbench_llm_prompts_exclude_previous_issue_segment(self):
        import uuid
        from unittest.mock import MagicMock, Mock, patch
        from movidesk_note_worker import Settings, generate_note
        import technician_workbench as tw

        old_turn_1 = 'Como relacionar transportadora com erro na AnyMarket?'
        old_turn_2 = 'Carrier: FedEx. Não existe relacionamento da transportadora no ERP Master.'
        new_turn_3 = ('16/01/2025 09:45:44 ------------------------------Forçando Pedido 871 '
                      'Não será possível baixar o pedido [871]: Erro desconhecido XYZ999 no sincronizador.')
        new_turn_4 = 'esse mesmo retorno aparece no pedido 871'

        # 1) Verify Worker prompt sent to llm_client.generate
        ticket = {
            'id': 132084,
            'subject': 'Dúvida sincronizador',
            'clients': [{'id': 'req', 'email': 'c@example.test',
                         'organization': {'id': 'org-1', 'businessName': 'Cliente Teste'}}],
            'actions': [
                {'id': 1, 'type': 2, 'description': old_turn_1, 'createdBy': {'id': 'req', 'profileType': 2}},
                {'id': 2, 'type': 2, 'description': old_turn_2, 'createdBy': {'id': 'req', 'profileType': 2}},
                {'id': 3, 'type': 2, 'description': new_turn_3, 'createdBy': {'id': 'req', 'profileType': 2}},
                {'id': 4, 'type': 2, 'description': new_turn_4, 'createdBy': {'id': 'req', 'profileType': 2}},
            ],
        }
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value = {'name': None}
        conn.__enter__.return_value.execute.return_value.fetchall.return_value = []
        llm_ok = {'ok': True, 'text': 'Orientação de investigação sem procedimentos.', 'model': 'gemini-test'}
        with patch('technician_workbench.connection', return_value=conn), \
             patch('tech_evidence.ensure_single_tenant', return_value='tenant'), \
             patch('agent_control.organization', return_value=('org-1', 'Cliente Teste', None)), \
             patch('knowledge_access.search', return_value=[]), \
             patch('knowledge_access.approved_procedures', return_value=[]), \
             patch('technician_workbench.llm_client.generate', return_value=llm_ok) as worker_llm:
            generate_note(ticket, Settings(), Mock())
        worker_llm.assert_called_once()
        worker_prompt = worker_llm.call_args[0][0]
        self.assertIn('XYZ999', worker_prompt)
        self.assertNotIn('AnyMarket', worker_prompt)
        self.assertNotIn('FedEx', worker_prompt)

        # 2) Verify Workbench add_message prompt sent to llm_client.generate
        existing_desc = [
            {'role': 'technician', 'content': new_turn_3},
            {'role': 'technician', 'content': old_turn_2},
            {'role': 'technician', 'content': old_turn_1},
        ]
        wb_conn = MagicMock()
        wb_conn.__enter__.return_value.execute.return_value.fetchall.return_value = existing_desc
        wb_conn.__enter__.return_value.execute.return_value.fetchone.return_value = {'id': 99}
        with patch.object(tw, 'connection', return_value=wb_conn), \
             patch.object(tw, 'ensure_single_tenant', return_value='tenant'), \
             patch.object(tw, 'conversation', return_value={'id': uuid.uuid4(), 'status': 'open', 'movidesk_company_id': 'org-1', 'subject': 'Teste'}), \
             patch.object(tw, 'profile', return_value={'company_name': 'Cliente Teste', 'facts': []}), \
             patch.object(tw, 'environment_rows', return_value=[]), \
             patch.object(tw, 'evidence_for_company', return_value=([], [], 'general_knowledge')), \
             patch.object(tw, 'capability_evidence_cases', return_value=[]), \
             patch('support_events.record'), \
             patch('technician_workbench.llm_client.generate', return_value=llm_ok) as wb_llm:
            tw.add_message(uuid.uuid4(), tw.NewMessage(content=new_turn_4))
        wb_llm.assert_called_once()
        wb_prompt = wb_llm.call_args[0][0]
        self.assertIn('XYZ999', wb_prompt)
        self.assertNotIn('AnyMarket', wb_prompt)
        self.assertNotIn('FedEx', wb_prompt)


if __name__ == '__main__':
    unittest.main()

