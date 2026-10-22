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

if __name__ == '__main__': unittest.main()
