"""Workbench contract. DB test is opt-in and removes only its own records."""
import os
import unittest
import uuid
from unittest.mock import Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from technician_workbench import (safe_text, build_reply, company_case_source, connection,
                                  company_cases, evidence_for_company, environment_card,
                                  search_context_query)


class WorkbenchUnitTests(unittest.TestCase):
    def test_company_resolved_evidence_keeps_document_search(self):
        case = {'external_id': 131549, 'subject': 'Erro ao emitir etiqueta',
                'status': 'Resolvido', 'base_status': 'Resolved', 'quality_tier': 'SILVER',
                'hard_block_reason': None, 'problem_statement': 'Nenhuma venda selecionada',
                'technical_narrative': 'Gerado pelo Site estava marcado.',
                'solution_statement': 'O campo Gerado pelo Site foi alterado para Não e a etiqueta foi emitida.'}
        with patch('technician_workbench.company_similar_cases', return_value=[case]), \
             patch('technician_workbench.knowledge_access.search', return_value=[{'id': 'doc', 'title': 'Manual'}]) as general:
            sources, cases, scope = evidence_for_company(Mock(), 'tenant', '464', 'erro de etiqueta')
        general.assert_called_once()
        self.assertEqual(scope, 'company_history_with_documents')
        self.assertEqual(sources[1]['id'], 'doc')
        self.assertEqual(sources[0]['source_external_id'], 131549)
        self.assertEqual(cases[0]['external_id'], 131549)

    def test_active_company_case_is_recurrence_only_and_general_search_runs(self):
        case = {'external_id': 140000, 'subject': 'Erro ao emitir etiqueta',
                'status': 'Em atendimento', 'base_status': 'InAttendance', 'quality_tier': 'GOLD',
                'hard_block_reason': None, 'problem_statement': 'Nenhuma venda selecionada',
                'technical_narrative': 'Ainda em análise',
                'solution_statement': 'Uma alteração longa ainda não confirmada pelo cliente.',
                'is_active': True}
        general_source = {'id': 7, 'title': 'Manual de etiqueta'}
        with patch('technician_workbench.company_similar_cases', return_value=[case]), \
             patch('technician_workbench.knowledge_access.search', return_value=[general_source]) as general:
            sources, cases, scope = evidence_for_company(Mock(), 'tenant', '464', 'erro de etiqueta')
        general.assert_called_once()
        self.assertIsNone(company_case_source(case))
        self.assertEqual(scope, 'general_knowledge')
        self.assertEqual(sources, [general_source])
        self.assertTrue(cases[0]['is_active'])

    def test_search_carries_a_short_followup_but_not_a_new_issue(self):
        recent = [{'role': 'assistant', 'content': 'Confira o cadastro.'},
                  {'role': 'technician', 'content': 'Rejeição de inscrição estadual ao emitir a NF-e'}]
        self.assertIn('inscrição estadual', search_context_query('Verifiquei e continua igual', recent))
        self.assertNotIn('inscrição estadual', search_context_query('Agora estou com outro erro na etiqueta', recent))
        self.assertNotIn('inscrição estadual', search_context_query('Erro na etiqueta', recent))

    def test_credentials_are_rejected_from_manual_context(self):
        with self.assertRaises(HTTPException):
            safe_text('senha=super-secret')
        with self.assertRaises(HTTPException):
            safe_text('https://user:password@example.test/db')

    def test_incomplete_primary_source_allows_investigation_without_copying_weaker_procedure(self):
        rows = [{'id': 1, 'applicable': False, 'answer_use': 'investigation_only'},
                {'id': 2, 'title': 'Outra rejeição', 'solution': 'Não aplicar este ajuste',
                 'applicable': True, 'answer_use': 'supporting_reference'}]
        with patch('technician_workbench.llm_client.generate', return_value={
            'ok': True, 'text': 'Qual é o código da rejeição?', 'error': ''}) as generate:
            reply, meta = build_reply({}, '', [], [], rows, 'erro na nota')
        self.assertEqual(reply, 'Qual é o código da rejeição?')
        self.assertEqual(meta['mode'], 'investigation')
        prompt = generate.call_args.args[0]
        self.assertIn('Fonte principal aplicável: nenhuma', prompt)
        self.assertIn('Procedimento da fonte principal: não disponível', prompt)
        self.assertNotIn('Não aplicar este ajuste', prompt)

    def test_new_fiscal_subject_is_prioritized_over_old_label_history(self):
        row = {'subject': 'Etiqueta', 'contact_name': 'Fernanda', 'company_name': 'Medjet',
               'movidesk_company_id': '464'}
        with patch('technician_workbench.llm_client.generate', return_value={
            'ok': True, 'text': 'Confira a rejeição da NF-e.', 'error': ''}) as generate:
            reply, meta = build_reply(row, 'technician: erro de etiqueta resolvido', [], [], [],
                                      'erro de inscrição estadual do destinatio invalida ao emitir a nota')
        self.assertEqual(meta['mode'], 'investigation')
        prompt = generate.call_args.args[0]
        self.assertIn('mensagem ATUAL define o problema', prompt)
        self.assertIn('ao emitir a nota', prompt)
        self.assertNotIn('Fonte principal aplicável: Etiqueta', prompt)

    def test_usable_source_gets_sufficient_output_budget_and_grounded_fallback(self):
        source = {'id': 30897, 'source_external_id': 131549, 'source_type': 'PROBABLE_TICKET',
                  'title': 'Erro ao emitir etiqueta', 'applicable': True,
                  'answer_use': 'supporting_reference', 'problem': 'Nenhuma venda selecionada',
                  'diagnosis': 'O campo Gerado pelo Site estava marcado na venda.',
                  'solution': 'O campo Gerado pelo Site foi alterado para Não e a etiqueta foi emitida.'}
        row = {'movidesk_company_id': '464', 'company_name': 'Medjet',
               'subject': 'Etiqueta', 'contact_name': 'Fernanda'}
        cases = [{'external_id': 131549}]
        with patch('technician_workbench.llm_client.generate', return_value={
            'ok': False, 'error': 'Resposta incompleta.', 'text': ''}) as generate:
            reply, meta = build_reply(row, '', [], [], [source], 'Erro ao emitir etiqueta', cases)
        self.assertEqual(generate.call_args.kwargs['max_tokens'], 4096)
        self.assertIn('Número do chamado Movidesk para citar: 131549', generate.call_args.args[0])
        self.assertNotIn('30897', generate.call_args.args[0])
        self.assertIn('Gerado pelo Site', reply)
        self.assertIn('mesma empresa', reply)
        self.assertFalse(meta['used'])

    def test_environment_only_shows_active_connection(self):
        row = {'id': 'example', 'latest_observed_at': None, 'last_received_at': None,
               'snapshot': {'empresa': 'Exemplo', 'active_connection_occurrences': [3],
                            'config_sections': [
                                {'occurrence': 2, 'values': {'descricao': 'Cliente A', 'sincronizar': 'PRODUTO'}},
                                {'occurrence': 3, 'values': {'descricao': 'Cliente B', 'sincronizar': 'ESTOQUE',
                                                              'senha': 'should-not-be-exposed'}}]}}
        card = environment_card(row)
        self.assertEqual(card['active_connections'], [{'descricao': 'Cliente B', 'sincronizar': 'ESTOQUE'}])
        self.assertNotIn('senha', str(card))


@unittest.skipUnless(os.getenv('WORKBENCH_TEST_POSTGRES') == '1', 'Teste transacional de DB opt-in')
class WorkbenchDatabaseTests(unittest.TestCase):
    def test_medjet_case_can_be_found_before_knowledge_indexing(self):
        with connection() as conn:
            tenant = conn.execute('SELECT id FROM tenants LIMIT 1').fetchone()['id']
            cases = company_cases(conn, tenant, '464',
                                  'Nenhuma venda selecionada ou não foi possível gerar nenhuma da etiqueta das vendas selecionadas', 20)
        self.assertTrue(cases)
        self.assertEqual(cases[0]['external_id'], 131549)
        with patch('technician_workbench.llm_client.generate', return_value={
            'ok': False, 'error': 'Indisponível', 'text': ''}):
            reply, meta = build_reply({}, '', [], [], [], 'erro de etiqueta', cases)
        self.assertIn('código ou texto completo', reply)
        self.assertFalse(meta['used'])

    def test_company_fact_versioning_and_secret_filter(self):
        from simulator_app import app
        with connection() as conn:
            tenant = conn.execute('SELECT id FROM tenants LIMIT 1').fetchone()['id']
            company_id = 'qa-' + uuid.uuid4().hex
            conn.execute('INSERT INTO movidesk_company_directory(tenant_id,company_id,company_name) VALUES(%s,%s,%s)',
                         (tenant, company_id, 'Empresa fictícia de teste'))
        try:
            with patch.dict(os.environ, {'CORTEX_AUTH_REQUIRED': 'false'}), TestClient(app) as client:
                initial = client.get(f'/api/workbench/companies/{company_id}')
                self.assertEqual(initial.json()['version'], 0)
                saved = client.put(f'/api/workbench/companies/{company_id}', json={
                    'version': 0, 'facts': [{'category': 'integracao', 'text': 'Etiqueta emitida pelo Master',
                                            'verified': True, 'source_ticket': 131549}]})
                self.assertEqual(saved.status_code, 200, saved.text)
                self.assertEqual(client.get(f'/api/workbench/companies/{company_id}').json()['facts'][0]['source_ticket'], 131549)
                stale = client.put(f'/api/workbench/companies/{company_id}', json={'version': 0, 'facts': []})
                self.assertEqual(stale.status_code, 409)
                secret = client.put(f'/api/workbench/companies/{company_id}', json={
                    'version': saved.json()['version'], 'facts': [{'category': 'ambiente', 'text': 'senha=never-store'}]})
                self.assertEqual(secret.status_code, 422)
                self.assertNotIn('never-store', secret.text)
        finally:
            with connection() as conn:
                conn.execute('DELETE FROM company_context_profiles WHERE tenant_id=%s AND movidesk_company_id=%s',
                             (tenant, company_id))
                conn.execute('DELETE FROM movidesk_company_directory WHERE tenant_id=%s AND company_id=%s',
                             (tenant, company_id))

    def test_conversation_and_draft_keep_confirmed_actions(self):
        from simulator_app import app
        company_id = '464'  # Existing organization; test data is removed below.
        with connection() as conn:
            tenant = conn.execute('SELECT id FROM tenants LIMIT 1').fetchone()['id']
            existed = bool(conn.execute('SELECT 1 FROM company_context_profiles WHERE tenant_id=%s AND movidesk_company_id=%s',
                                        (tenant, company_id)).fetchone())
        cid = None
        try:
            with patch.dict(os.environ, {'CORTEX_AUTH_REQUIRED': 'false'}), TestClient(app) as client:
                found = client.get('/api/workbench/companies', params={'q': 'Medjet'})
                self.assertEqual(found.status_code, 200)
                self.assertTrue(any(x['id'] == company_id for x in found.json()['items']))
                created = client.post('/api/workbench/conversations', json={
                    'company_id': company_id, 'contact_name': 'Contato QA',
                    'subject': 'Teste temporário de etiqueta no gerenciamento'})
                self.assertEqual(created.status_code, 200, created.text)
                cid = created.json()['id']
                source = {'id': 1, 'source_external_id': 107776, 'title': 'Etiqueta',
                          'source_type': 'PROBABLE_TICKET', 'applicable': True,
                          'answer_use': 'supporting_reference', 'problem': 'Erro na etiqueta',
                          'diagnosis': 'Gerado pelo Site marcado', 'solution': 'Conferir contexto de frete'}
                with patch('technician_workbench.evidence_for_company',
                           return_value=([source], [], 'general_knowledge')), \
                     patch('technician_workbench.llm_client.generate', return_value={
                         'ok': True, 'text': 'Verifique Gerado pelo Site antes de alterar.',
                         'error': '', 'latency_ms': 1}):
                    message = client.post(f'/api/workbench/conversations/{cid}/messages', json={
                        'content': 'Fernanda relatou falha ao emitir etiqueta.'})
                self.assertEqual(message.status_code, 200, message.text)
                self.assertEqual(message.json()['sources'][0]['ticket_id'], 107776)
                confirmed = client.put(f'/api/workbench/conversations/{cid}/outcome', json={
                    'action': 'Conferi o campo Gerado pelo Site e alterei para Não.',
                    'result': 'A etiqueta foi emitida normalmente.', 'resolved': True})
                self.assertEqual(confirmed.status_code, 200, confirmed.text)
                draft = client.post(f'/api/workbench/conversations/{cid}/draft', json={'kind': 'SUP'})
                self.assertEqual(draft.status_code, 200, draft.text)
                self.assertEqual(draft.json()['status'], 'draft_local')
                self.assertIn('A etiqueta foi emitida normalmente.', draft.json()['body'])
                self.assertIsNone(draft.json()['movidesk_ticket_id'])
                reopened = client.get(f'/api/workbench/conversations/{cid}')
                self.assertEqual(reopened.json()['drafts'][0]['kind'], 'SUP')
                self.assertEqual(reopened.json()['preparation']['company_name'].split(' - ')[0], 'Medjet')
        finally:
            if cid:
                with connection() as conn:
                    conn.execute('DELETE FROM technician_ticket_drafts WHERE conversation_id=%s', (cid,))
                    conn.execute('DELETE FROM technician_conversation_messages WHERE conversation_id=%s', (cid,))
                    conn.execute('DELETE FROM technician_conversations WHERE id=%s', (cid,))
                    if not existed:
                        conn.execute('DELETE FROM company_context_profiles WHERE tenant_id=%s AND movidesk_company_id=%s AND updated_by=%s',
                                     (tenant, company_id, 'importacao_movidesk'))


if __name__ == '__main__':
    unittest.main()
