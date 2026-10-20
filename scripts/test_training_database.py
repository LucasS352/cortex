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
                content={**extraction,'module_key':'qa_training','subtopic_key':'general','expected_result':'Cadastro validado'}
                item=kt.approve(cand['id'],'new',content,'curador','Procedimento validado')
                node=dict(conn.execute('SELECT * FROM ticket_knowledge_nodes WHERE source_id=%s',(f"canon:{item['id']}",)).fetchone())
                node['quality_flags']=[]
                ka.canonical_reference(conn,node)
                self.assertEqual(node['answer_use'],'procedure_reference')
                self.assertTrue(ka.approved_procedures(conn,'Cadastro de teste incorreto'))
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

if __name__ == '__main__': unittest.main()
