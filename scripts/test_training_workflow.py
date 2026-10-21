"""Human approval boundary and exact-answer regression checks."""
import unittest
import base64
import io
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from PIL import Image
import knowledge_training as kt
import knowledge_access as ka
from fastapi import FastAPI
from fastapi.testclient import TestClient
from knowledge_training_api import router, _technician_failures
import test_knowledge_training as legacy


class TrainingWorkflowTests(unittest.TestCase):
    def test_existing_training_rules(self):
        for name in dir(legacy):
            if name.startswith('test_'):
                with self.subTest(name=name):
                    getattr(legacy, name)()

    def test_answer_identity_cannot_use_customer_message(self):
        actions = [{'id': 1, 'description': 'Olá'}, {'id': 2, 'description': 'CORTEX-AI-NOTE:abc resposta'}]
        self.assertEqual(kt.select_answer(actions, '2')['id'], 2)
        for action in ('1','3'):
            with self.assertRaises(ValueError): kt.select_answer(actions, action)

    def test_clone_source_lists_only_human_technician_answers(self):
        actions = [
            {'id':1,'type':2,'description':'Mensagem longa enviada pelo cliente para solicitar ajuda.'},
            {'id':2,'type':1,'description':'Resposta humana com o caminho correto para abrir o relatório.'},
            {'id':3,'type':1,'description':'CORTEX-AI-NOTE:abc resposta automática que não pode ser clonada.'},
        ]
        with patch.object(kt,'ensure_schema'), patch.object(kt,'connect') as connect, \
             patch.object(kt,'load_ticket',return_value=({'subject':'Relatório por fornecedor'},actions)):
            result = kt.ticket_technician_answers(132084)
        self.assertEqual([a['action_id'] for a in result['answers']],['2'])
        self.assertEqual([q['action_id'] for q in result['questions']],['1'])
        self.assertEqual(result['answers'][0]['suggested_question_action_id'],'1')
        self.assertEqual(result['subject'],'Relatório por fornecedor')

    def test_authoring_screen_lists_customer_question_and_suggests_it_for_cortex_answer(self):
        actions = [
            {'id':3,'type':2,'description':'Onde vejo os produtos comprados por fornecedor?'},
            {'id':4,'type':2,'description':'Seu chamado foi recebido e iremos realizar a triagem por um atendente do suporte.'},
            {'id':5,'type':1,'description':'CORTEX-AI-NOTE:abc resposta sugerida para o técnico revisar.'},
        ]
        with patch.object(kt,'ensure_schema'), patch.object(kt,'connect'), \
             patch.object(kt,'load_ticket',return_value=({'subject':'Relatório por fornecedor'},actions)):
            result = kt.ticket_answers(132084)
        self.assertEqual([q['action_id'] for q in result['questions']],['3'])
        self.assertEqual(result['answers'][0]['suggested_question_action_id'],'3')

    def test_unclassified_cannot_be_published(self):
        with self.assertRaisesRegex(ValueError, 'módulo'):
            kt._validate_content(dict(title='Titulo completo',symptom='Sintoma',root_cause='Causa confirmada',
                procedure='1. Procedimento',module_key='unclassified',subtopic_key='general'))

    def test_negative_feedback_requires_example_ticket(self):
        with patch.object(kt,'taxonomy_options',return_value=[{'key':'fiscal'}]):
            with self.assertRaisesRegex(ValueError, 'ticket-exemplo'):
                kt.review_answer(131726,'2',-1,'fiscal','Passo incorreto','tecnico')

    def test_negative_feedback_creates_candidate_from_example_ticket(self):
        db = MagicMock(); db.execute.return_value.fetchone.return_value = {'id': 4}
        proposal = dict(proposed_title='Consultar relatório por fornecedor',
                        proposed_answer='Sim, o relatório está disponível no menu de vendas.',
                        proposed_procedure='1. Acesse Relatórios > Vendas.\n2. Selecione a aba Fornecedor.')
        with patch.object(kt,'taxonomy_options',return_value=[{'key':'fiscal'}]), \
             patch.object(kt,'connect') as connect, patch.object(kt,'load_ticket',return_value=({},[{'id':2,'description':'CORTEX-AI-NOTE:abc'}])), \
             patch.object(kt,'create_candidate',return_value={'id':8}) as create, patch.object(kt,'_publish') as publish:
            connect.return_value.__enter__.return_value = db
            result = kt.review_answer(131726,'2',-1,'fiscal','Passo incorreto','tecnico',98765,
                                      proposal['proposed_title'],proposal['proposed_answer'],proposal['proposed_procedure'],[])
            create.assert_called_once_with(98765,'tecnico',True,None,'fiscal',{
                'title':proposal['proposed_title'],'answer':proposal['proposed_answer'],
                'procedure':proposal['proposed_procedure'],'images':[],
                'question':None,'question_action_id':None,'answer_action_id':None})
            publish.assert_not_called()
            self.assertEqual(result['candidate_id'],8)
            self.assertEqual(result['example_ticket_id'],98765)

    def test_positive_feedback_only_records_confirmation(self):
        db = MagicMock(); db.execute.return_value.fetchone.return_value = {'id': 4}
        with patch.object(kt,'taxonomy_options',return_value=[{'key':'fiscal'}]), \
             patch.object(kt,'connect') as connect, patch.object(kt,'load_ticket',return_value=({},[{'id':2,'description':'CORTEX-AI-NOTE:abc'}])), \
             patch.object(kt,'create_candidate') as create, patch.object(kt,'_publish') as publish:
            connect.return_value.__enter__.return_value = db
            result = kt.review_answer(131726,'2',1,'fiscal','Validado no atendimento','tecnico')
            create.assert_not_called(); publish.assert_not_called()
            self.assertIsNone(result['candidate_id'])

    def test_canonical_original_and_stale_version(self):
        db=MagicMock(); db.execute.return_value.fetchone.return_value={'id':1,'version_no':2,'approved_by':'curador','applicability':{}}
        row={'source_id':'canon:1','metadata':{'version_no':2},'quality_flags':[]}
        ka.canonical_reference(db,row)
        self.assertEqual(row['answer_use'],'procedure_reference')
        row['metadata']['version_no']=1
        ka.canonical_reference(db,row)
        self.assertEqual(row['answer_use'],'investigation_only')

    def test_taxonomy_invalid_keys_rejected_before_database(self):
        for key in ('unclassified','../fiscal','Fiscal','x'):
            with self.assertRaises(ValueError): kt.save_module(key,'Nome','', 'tester')

    def test_canonical_scope_requires_confirmation(self):
        source={'title':'Cadastro de produto','problem':'Cadastro de produto', 'solution':'1. Cadastrar produto.',
                'evidence_status':'human_approved_procedure','metadata':{'applicability':{'ufs':['SP']}}}
        self.assertFalse(ka.applicability(source,'como cadastrar produto')['applicable'])
        self.assertIn('canonical_scope_requires_confirmation:ufs',ka.applicability(source,'como cadastrar produto')['applicability_reasons'])

    def test_approved_reply_keeps_steps_without_model_rewriting(self):
        from technician_workbench import build_reply
        source={'title':'Cadastro de produto','problem':'Cadastro de produto', 'solution':'1. Passo aprovado.\n2. Conferir resultado.',
                'applicable':True,'answer_use':'procedure_reference','evidence_status':'human_approved_procedure'}
        with patch('llm_client.generate') as generate:
            reply,meta=build_reply({},'',[],[],[source],'como cadastrar produto')
        generate.assert_not_called()
        self.assertIn(source['solution'],reply)
        self.assertEqual(meta['mode'],'human_approved_procedure')

    def test_images_must_match_existing_steps(self):
        content=dict(title='Titulo completo',symptom='Sintoma',root_cause='Causa confirmada',
            procedure='1. Conferir resultado.',module_key='fiscal',subtopic_key='general',
            images=[{'file':'state-registration-delivery.png','after_step':2,'caption':'Campo IE'}])
        with self.assertRaises(ValueError): kt._validate_content(content)
        content['images'][0]['after_step']=1
        self.assertEqual(kt._validate_content(content)['images'][0]['after_step'],1)

    def test_technician_print_is_reencoded_and_bound_to_a_numbered_step(self):
        raw = io.BytesIO(); Image.new('RGB',(20,10),'white').save(raw,'JPEG')
        data_url = 'data:image/jpeg;base64,' + base64.b64encode(raw.getvalue()).decode()
        with tempfile.TemporaryDirectory() as folder, patch.object(kt,'TRAINING_MEDIA',Path(folder)):
            saved = kt._save_proposal_images([{'data_url':data_url,'name':'Aba Fornecedor','caption':'Aba Fornecedor','placement':'procedure','after_step':2}],
                                             '1. Abra o relatório.\n2. Clique na aba Fornecedor.')
            self.assertRegex(saved[0]['file'],r'^[0-9a-f]{32}\.png$')
            self.assertTrue((Path(folder)/saved[0]['file']).is_file())
            with self.assertRaisesRegex(ValueError,'passo numerado'):
                kt._save_proposal_images([{'data_url':data_url,'name':'Aba Fornecedor','caption':'Aba Fornecedor','placement':'procedure','after_step':3}],
                                         '1. Abra o relatório.\n2. Clique na aba Fornecedor.')

    def test_technician_print_can_be_placed_after_customer_answer(self):
        raw = io.BytesIO(); Image.new('RGB',(20,10),'white').save(raw,'PNG')
        data_url = 'data:image/png;base64,' + base64.b64encode(raw.getvalue()).decode()
        with tempfile.TemporaryDirectory() as folder, patch.object(kt,'TRAINING_MEDIA',Path(folder)):
            saved = kt._save_proposal_images([{'data_url':data_url,'name':'Visão geral do relatório',
                'caption':'Tela inicial do relatório','placement':'answer','after_step':None}], '1. Abra o relatório.')
        self.assertEqual(saved[0]['placement'],'answer')
        self.assertIsNone(saved[0]['after_step'])

    def test_feedback_link_targets_ticket_and_exact_marker(self):
        from movidesk_note_worker import render_note
        import os
        with patch.dict(os.environ, {'CORTEX_PUBLIC_URL':'https://cortex.example'}):
            note=render_note({'reply':'Resposta','feedback_ticket':131726},'CORTEX-AI-NOTE:abc')
        self.assertIn('/treinamento/tecnico?ticket=131726&amp;marker=CORTEX-AI-NOTE%3Aabc',note)
        self.assertIn('Preparar resolução técnica',note)

    def test_review_api_rejects_invalid_rating(self):
        app=FastAPI(); app.include_router(router)
        with TestClient(app) as client, patch.object(kt,'review_answer') as review:
            result=client.post('/api/treinamento/tickets/131726/avaliar',json={'action_id':'1','module_key':'fiscal','rating':0})
            self.assertEqual(result.status_code,422); review.assert_not_called()

    def test_review_api_forwards_example_ticket(self):
        app=FastAPI(); app.include_router(router)
        with TestClient(app) as client, patch.object(kt,'review_answer',return_value={'id':4,'candidate_id':8}) as review:
            result=client.post('/api/treinamento/tickets/131726/avaliar',json={
                'action_id':'1','module_key':'fiscal','rating':-1,'comment':'Passo incorreto',
                'example_ticket_id':98765,'proposed_title':'Consultar relatório por fornecedor',
                'proposed_answer':'Sim, o relatório está disponível no menu de vendas.',
                'proposed_procedure':'1. Acesse Relatórios > Vendas.\n2. Selecione a aba Fornecedor.'})
            self.assertEqual(result.status_code,200)
            review.assert_called_once_with(131726,'1',-1,'fiscal','Passo incorreto','tecnico_local',98765,
                'Consultar relatório por fornecedor','Sim, o relatório está disponível no menu de vendas.',
                '1. Acesse Relatórios > Vendas.\n2. Selecione a aba Fornecedor.',[],None,None)

    def test_technician_pin_identifies_actor_and_forwards_named_images(self):
        app=FastAPI(); app.include_router(router); _technician_failures.clear()
        context={'subject':'Relatório','questions':[{'action_id':'3','text':'Pergunta'}],
                 'answers':[{'action_id':'5','text':'Resposta'}]}
        body={'action_id':'5','question_action_id':'3','module_key':'fiscal',
              'title':'Relatório por fornecedor','answer':'Sim, o relatório está disponível no Master.',
              'procedure':'1. Abra o relatório no menu de vendas.', 'comment':'Validado',
              'images':[]}
        with patch.dict('os.environ',{'CORTEX_PUBLIC_URL':'https://desk.cortexalter.tech',
                                      'CORTEX_TECHNICIAN_PINS':'1234:Lucas,0000:Marília'}), \
             TestClient(app,base_url='https://desk.cortexalter.tech') as client, \
             patch.object(kt,'ticket_answers',return_value=context), \
             patch.object(kt,'taxonomy_options',return_value=[{'key':'fiscal','label':'Fiscal'}]), \
             patch.object(kt,'review_answer',return_value={'candidate_id':8}) as review:
            opened=client.get('/api/treinamento/tecnico/tickets/132084',headers={'X-Cortex-Technician-Pin':'1234'})
            self.assertEqual(opened.status_code,200); self.assertEqual(opened.json()['technician'],'Lucas')
            sent=client.post('/api/treinamento/tecnico/tickets/132084/propostas',json=body,
                             headers={'X-Cortex-Technician-Pin':'1234','Origin':'https://desk.cortexalter.tech'})
        self.assertEqual(sent.status_code,200)
        self.assertEqual(review.call_args.args[5],'Lucas')

    def test_technician_pin_rejects_unknown_code(self):
        app=FastAPI(); app.include_router(router); _technician_failures.clear()
        with patch.dict('os.environ',{'CORTEX_TECHNICIAN_PINS':'1234:Lucas'}), TestClient(app) as client:
            result=client.get('/api/treinamento/tecnico/tickets/132084',headers={'X-Cortex-Technician-Pin':'9999'})
        self.assertEqual(result.status_code,401)

    def test_admin_can_override_question_answer_pair(self):
        db = MagicMock(); db.execute.return_value.fetchone.return_value = {'id': 4}
        notes = {'questions':[{'action_id':'3','text':'Onde encontro o relatório por fornecedor?'}],
                 'answers':[{'action_id':'5','text':'Acesse Relatórios > Vendas > Histórico.'}]}
        with patch.object(kt,'taxonomy_options',return_value=[{'key':'fiscal'}]), \
             patch.object(kt,'ticket_technician_answers',return_value=notes), \
             patch.object(kt,'connect') as connect, \
             patch.object(kt,'load_ticket',return_value=({},[{'id':2,'description':'CORTEX-AI-NOTE:abc'}])), \
             patch.object(kt,'create_candidate',return_value={'id':8}) as create:
            connect.return_value.__enter__.return_value = db
            kt.review_answer(131726,'2',-1,'fiscal','Pareamento corrigido','tecnico',98765,
                'Relatório por fornecedor','Sim, existe esse relatório no Master.',
                '1. Acesse Relatórios > Vendas.\n2. Abra o relatório por fornecedor.',[], '3','5')
        proposal = create.call_args.args[-1]
        self.assertEqual(proposal['question'],'Onde encontro o relatório por fornecedor?')
        self.assertEqual(proposal['question_action_id'],'3')
        self.assertEqual(proposal['answer_action_id'],'5')

    def test_technician_can_write_resolution_without_existing_human_answer(self):
        db = MagicMock(); db.execute.return_value.fetchone.return_value = {'id': 4}
        notes = {'questions':[{'action_id':'3','text':'Onde encontro o relatório por fornecedor?'}],
                 'answers':[]}
        with patch.object(kt,'taxonomy_options',return_value=[{'key':'fiscal'}]), \
             patch.object(kt,'ticket_technician_answers',return_value=notes), \
             patch.object(kt,'connect') as connect, \
             patch.object(kt,'load_ticket',return_value=({},[{'id':9,'description':'CORTEX-AI-NOTE:abc'}])), \
             patch.object(kt,'create_candidate',return_value={'id':8}) as create:
            connect.return_value.__enter__.return_value = db
            result = kt.review_answer(132084,'9',-1,'fiscal','Validado no Master','tecnico',132084,
                'Relatório por fornecedor','Sim, existe esse relatório no Master.',
                '1. Acesse Relatórios > Vendas.\n2. Abra o relatório por fornecedor.',[], '3',None)
        proposal = create.call_args.args[-1]
        self.assertEqual(proposal['question'],'Onde encontro o relatório por fornecedor?')
        self.assertIsNone(proposal['answer_action_id'])
        self.assertEqual(result['candidate_id'],8)

    def test_retrieval_test_uses_query_embedding_and_reports_rank(self):
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = {'id':7,'title':'Relatório por fornecedor','status':'active'}
        rows = [{'source_id':'canon:9','title':'Outro','combined_score':.7,'retrieval_method':'text_and_optional_vector'},
                {'source_id':'canon:7','title':'Relatório por fornecedor','combined_score':.66,'retrieval_method':'text_and_optional_vector'}]
        with patch.object(kt,'ensure_schema'), patch.object(kt,'connect') as connect, \
             patch.object(kt,'_embed',return_value=[.1,.2]) as embed, \
             patch.object(kt.knowledge_access,'search',return_value=rows) as search:
            connect.return_value.__enter__.return_value = db
            result = kt.test_item_retrieval(7,'Onde vejo os produtos por fornecedor?')
        embed.assert_called_once_with('Onde vejo os produtos por fornecedor?', 'RETRIEVAL_QUERY')
        self.assertTrue(result['found']); self.assertEqual(result['rank'],2)
        self.assertEqual(search.call_args.kwargs['source_types'],('OFFICIAL_KB',))

    def test_retrieval_api_delegates_to_production_search_test(self):
        app=FastAPI(); app.include_router(router)
        expected={'found':True,'rank':1,'query_embedding_ok':True,'results':[]}
        with TestClient(app) as client, patch.object(kt,'test_item_retrieval',return_value=expected) as test:
            result=client.get('/api/treinamento/itens/7/testar',params={'q':'Onde vejo por fornecedor?'})
        self.assertEqual(result.status_code,200); self.assertEqual(result.json(),expected)
        test.assert_called_once_with(7,'Onde vejo por fornecedor?')

    def test_master_release_requires_valid_evidence_and_is_sanitized(self):
        data = {'version_label':'16/08/2026','title':'Falha na importação','narrative':'O pedido não entra no Master.',
                'status':'active','modules':['integrations'],'error_messages':['Erro de integração'],
                'evidence':[{'kind':'ticket','value':'131726','label':'Confirmado'}]}
        with patch.object(kt, 'taxonomy_options', return_value=[{'key':'integrations'}]):
            clean = kt._validate_release(data)
            self.assertEqual(clean['evidence'][0]['value'],'131726')
            data['evidence'][0]['value'] = 'ticket inválido'
            with self.assertRaisesRegex(ValueError, 'número'):
                kt._validate_release(data)

    def test_master_release_is_a_current_human_approved_source(self):
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = {
            'id': 7, 'version_label': '16/08/2026', 'status': 'active', 'approved_by': 'Lucas'}
        row = {'source_id':'master-release:7','metadata':{'knowledge_kind':'master_release'},'quality_flags':[]}
        ka.canonical_reference(db, row)
        self.assertEqual(row['answer_use'],'procedure_reference')
        self.assertIn('16/08/2026', row['provenance'])

    def test_master_release_api_delegates_human_actor(self):
        app=FastAPI(); app.include_router(router)
        payload={'version_label':'16/08/2026','title':'Falha conhecida','narrative':'Falha ao importar pedido.',
                 'status':'active','modules':['integrations'],'error_messages':[],'evidence':[]}
        with TestClient(app) as client, patch.object(kt,'save_master_release',return_value={'id':9}) as save:
            result=client.post('/api/treinamento/versoes-master',json=payload)
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['id'],9)
        save.assert_called_once()

if __name__ == '__main__': unittest.main()
