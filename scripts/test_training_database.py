"""Opt-in transactional integration test; all rows and DDL are rolled back."""
import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch
import knowledge_training as kt
import knowledge_access as ka


@unittest.skipUnless(os.getenv('CORTEX_TEST_DATABASE') == '1', 'requires local PostgreSQL corpus')
class TrainingDatabaseTests(unittest.TestCase):
    def test_review_publish_revision_and_retirement(self):
        conn = kt.connect()
        @contextmanager
        def shared():
            yield conn
        try:
            conn.execute(kt.MIGRATION.read_text(encoding='utf-8'))
            ticket={'external_id':2147480000,'subject':'Teste procedimento único','base_status':'Resolved',
                    'solution_statement':'1. Conferir o cadastro correto. 2. Validar resultado.'}
            actions=[{'id':1,'type':1,'description':'CORTEX-AI-NOTE:test 1. Conferir o cadastro correto. 2. Validar resultado.'}]
            extraction=kt.parse_extraction({'title':'Teste procedimento único','symptom':'Cadastro de teste incorreto',
                'root_cause':'Parâmetro de teste incorreto','procedure':'1. Conferir o cadastro correto. 2. Validar resultado.',
                'module_hint':'registration','confidence':'MEDIUM'})
            with patch.object(kt,'connect',shared), patch.object(kt,'_schema_ready',True), \
                 patch.object(kt,'load_ticket',return_value=(ticket,actions)), \
                 patch.object(kt,'extract',return_value=(extraction,'Texto de teste','heuristic')), patch.object(kt,'_embed',return_value=None):
                options=kt.save_module('qa_training','Módulo QA','Teste transacional','tester')
                self.assertTrue(any(m['key']=='qa_training' for m in options))
                kt.save_module('qa_training','Módulo QA renomeado','Teste transacional','tester')
                example_ticket_id = ticket['external_id'] - 1
                review=kt.review_answer(ticket['external_id'],'1',-1,'qa_training','Usar o procedimento do exemplo',
                    'tester',example_ticket_id,'Procedimento de teste validado',
                    'Sim, utilize o procedimento de teste validado abaixo.',
                    '1. Conferir o cadastro correto.\n2. Validar o resultado.',[])
                cand=kt.get_candidate(review['candidate_id'])
                self.assertEqual(cand['ticket_external_id'],example_ticket_id)
                self.assertIsNone(cand['source_action_id'])
                self.assertEqual(cand['status'],'pending')
                kt.request_revision(cand['id'],'curador','Detalhar a validação do resultado')
                returned = kt.get_candidate(cand['id'])
                self.assertEqual(returned['status'],'pending')
                self.assertIn('review_request',returned['extraction'])
                self.assertEqual(conn.execute('SELECT count(*) n FROM cortex_kb.audit WHERE entity_id=%s AND action=%s',
                    (cand['id'],'revision:requested')).fetchone()['n'],1)
                content={**extraction,'module_key':'qa_training','subtopic_key':'general','expected_result':'Cadastro validado'}
                item=kt.approve(cand['id'],'new',content,'curador','Procedimento validado')
                node=dict(conn.execute('SELECT * FROM ticket_knowledge_nodes WHERE source_id=%s',(f"canon:{item['id']}",)).fetchone())
                node['quality_flags']=[]
                ka.canonical_reference(conn,node)
                self.assertEqual(node['answer_use'],'procedure_reference')
                self.assertTrue(ka.approved_procedures(conn,'Cadastro de teste incorreto'))
                recovered = ka.search(conn,'Cadastro de teste incorreto',maintain_index=False)
                self.assertEqual(recovered[0]['source_id'],f"canon:{item['id']}")
                self.assertEqual(recovered[0]['metadata']['approved_by'],'curador')
                kt.revise_item(item['id'],content,'curador','Revisão de teste')
                ka.canonical_reference(conn,node)
                self.assertEqual(node['answer_use'],'investigation_only')
                kt.deprecate(item['id'],'curador','Retirado após teste')
                current=conn.execute('SELECT count(*) AS n FROM ticket_knowledge_nodes WHERE source_id=%s',(f"canon:{item['id']}",)).fetchone()
                self.assertEqual(current['n'],0)
                release=kt.save_master_release({'version_label':'16/08/2026','title':'Falha conhecida de teste',
                    'narrative':'Nesta versão o pedido de teste não é importado.','impact':'Pedido fica pendente.',
                    'workaround':'1. Confirme a versão 16/08/2026.\n2. Reprocesse o pedido.',
                    'status':'active','modules':['qa_training'],'error_messages':['Pedido não importado'],
                    'evidence':[{'kind':'ticket','value':str(ticket['external_id']),'label':'Caso confirmado'}]},'curador')
                release_node=dict(conn.execute('SELECT * FROM ticket_knowledge_nodes WHERE source_id=%s',
                                               (f"master-release:{release['id']}",)).fetchone())
                self.assertEqual(release_node['version_reference'],'16/08/2026')
                release_node['quality_flags']=[]
                ka.canonical_reference(conn,release_node)
                self.assertEqual(release_node['answer_use'],'procedure_reference')
                self.assertTrue(ka.approved_procedures(conn,'pedido não importado versão 16/08/2026'))
        finally:
            conn.rollback()
            conn.close()

    def test_staging_approval_is_idempotent_and_revision_preserves_history(self):
        import knowledge_staging as staging
        conn = kt.connect()
        @contextmanager
        def shared():
            yield conn
        try:
            conn.execute(kt.MIGRATION.read_text(encoding='utf-8'))
            card = {'sha256':'a'*64,'card_id':'qa:isolated-staging','source_images':[],
                    'source_manual':None,'evidence':[{'type':'analyst','ref':'Validação transacional de teste'}]}
            content = {'title':'Procedimento único de homologação QA staging',
                'module_key':'registration','subtopic_key':'general','symptom':'Conferência isolada do cadastro QA staging',
                'root_cause':'Configuração de teste isolado para verificar a publicação.',
                'procedure':'1. Conferir o cadastro de homologação.\n2. Validar o resultado do teste.',
                'images':[],'applicability':{}}
            with patch.object(kt,'connect',shared), patch.object(kt,'_schema_ready',True), \
                 patch.object(staging,'detail',return_value=card):
                result = staging.approve('qa-staging-card','a'*64,content,'curador','Validado em teste transacional')
                duplicate = staging.approve('qa-staging-card','a'*64,content,'curador','Repetição da aprovação')
                self.assertEqual(result['id'],duplicate['id'])
                self.assertTrue(duplicate['already_imported'])
                node = conn.execute('SELECT metadata FROM ticket_knowledge_nodes WHERE source_id=%s',
                                    ('canon:'+str(result['id']),)).fetchone()
                self.assertEqual(node['metadata']['approved_by'],'curador')
                self.assertTrue(node['metadata']['source_references'])
                card['sha256']='b'*64
                revised = staging.approve('qa-staging-card','b'*64,content,'curador','Revisão de homologação',result['id'])
                self.assertEqual(revised['version'],2)
                versions=conn.execute('SELECT version_no,status FROM cortex_kb.versions WHERE item_id=%s ORDER BY version_no',
                                      (result['id'],)).fetchall()
                self.assertEqual([v['status'] for v in versions],['superseded','published'])
        finally:
            conn.rollback()
            conn.close()

    def test_score_0_to_10_draft_and_curation_submission_in_db(self):
        conn = kt.connect()
        @contextmanager
        def shared():
            yield conn
        try:
            conn.execute(kt.MIGRATION.read_text(encoding='utf-8'))
            ticket = {'external_id': 2147480099, 'subject': 'Relatório de produtos por fornecedor', 'base_status': 'Closed',
                      'solution_statement': '1. Acesse Relatórios > Vendas > Histórico. 2. Abra Produtos por Fornecedor.'}
            actions = [
                {'id': 1, 'type': 2, 'createdDate': '2026-04-17T11:36:00',
                 'createdBy': {'profileType': 2, 'businessName': 'Cliente'},
                 'description': 'Bom dia, onde tiro o relatório de produtos por fornecedor?'},
                {'id': 2, 'type': 1, 'createdDate': '2026-04-17T11:37:00',
                 'createdBy': {'profileType': 1, 'businessName': 'Cortex'},
                 'description': 'CORTEX-AI-NOTE:v1:2147480099:1 1. Acesse Relatórios > Vendas.'},
                {'id': 3, 'type': 2, 'createdDate': '2026-04-17T11:45:00',
                 'createdBy': {'profileType': 1, 'businessName': 'Lucas'},
                 'description': '<p>1. Acesse Relatórios > Vendas > Histórico.<br>2. Abra Produtos por Fabricante/Fornecedor.</p>'},
            ]
            extraction = kt.parse_extraction({
                'title': 'Relatório de produtos por fornecedor',
                'symptom': 'Dúvida sobre relatório de produtos por fornecedor',
                'root_cause': 'Funcionalidade localizada no menu de Histórico de Vendas',
                'procedure': '1. Acesse Relatórios > Vendas > Histórico.\n2. Abra Produtos por Fabricante/Fornecedor.',
                'module_hint': 'registration',
                'confidence': 'HIGH',
            })
            with patch.object(kt, 'connect', shared), patch.object(kt, '_schema_ready', True), \
                 patch.object(kt, 'load_ticket', return_value=(ticket, actions)), \
                 patch.object(kt, 'extract', return_value=(extraction, 'Texto de teste', 'heuristic')), \
                 patch.object(kt, '_embed', return_value=None):
                kt.save_module('relatorios', 'Relatórios', 'Módulo de relatórios', 'tester')
                # 1. Persistir nota 0 a 10 e atualizá-la mantendo auditoria
                s1 = kt.save_answer_score(ticket['external_id'], '2', 8, 'Lucas', 'relatorios')
                self.assertEqual(s1['score'], 8)
                s2 = kt.save_answer_score(ticket['external_id'], '2', 5, 'Lucas', 'relatorios', 'Faltou o caminho completo')
                self.assertEqual(s2['score'], 5)
                # 2. Salvar rascunho
                draft = kt.save_review_draft(
                    ticket['external_id'], '2', 'Lucas', score=5, module_key='relatorios',
                    subtopic_key='geral', keywords=['fornecedor', 'historico'],
                    proposed_title='Relatório de produtos por fornecedor',
                    proposed_answer='Sim, acesse Relatórios > Vendas > Histórico.',
                    proposed_procedure='1. Acesse Relatórios > Vendas > Histórico.\n2. Clique na aba Fornecedor.',
                    validated_html='<p>Sim, acesse Relatórios &gt; Vendas &gt; Histórico.</p>',
                    question_action_id='1', source_answer_action_id='3'
                )
                self.assertTrue(draft['is_draft'])
                saved = kt.ticket_saved_reviews(ticket['external_id'], actor='Lucas')
                self.assertEqual(len(saved), 1)
                self.assertEqual(saved[0]['score'], 5)
                self.assertTrue(saved[0]['is_draft'])
                self.assertEqual(saved[0]['keywords'], ['fornecedor', 'historico'])
                # 3. Enviar para curadoria administrativa (cria candidato pending, não publica automaticamente)
                submitted = kt.review_answer(
                    ticket['external_id'], '2', -1, 'relatorios', 'Validado pelo técnico', 'Lucas',
                    ticket['external_id'], 'Relatório de produtos por fornecedor',
                    'Sim, acesse Relatórios > Vendas > Histórico e abra Produtos por Fornecedor.',
                    '1. Acesse Relatórios > Vendas > Histórico.\n2. Abra Produtos por Fabricante/Fornecedor.',
                    [], '1', '3', subtopic_key='geral', keywords=['fornecedor', 'historico'],
                    score=5, validated_html='<p>Sim, acesse Relatórios.</p>'
                )
                self.assertIsNotNone(submitted['candidate_id'])
                cand = kt.get_candidate(submitted['candidate_id'])
                self.assertEqual(cand['status'], 'pending')
                self.assertEqual(cand['suggested_subtopic'], 'geral')
                self.assertIn('fornecedor', cand['extraction']['keywords'])
                # Garante que NÃO foi publicado automaticamente em cortex_kb.items nem em ticket_knowledge_nodes
                published_count = conn.execute(
                    'SELECT count(*) AS n FROM cortex_kb.items WHERE title=%s',
                    ('Relatório de produtos por fornecedor',)
                ).fetchone()['n']
                self.assertEqual(published_count, 0)
        finally:
            conn.rollback()
            conn.close()

if __name__ == '__main__': unittest.main()
