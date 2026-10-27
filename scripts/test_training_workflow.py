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
    def test_candidate_context_distinguishes_customer_question_and_technician_history(self):
        db=MagicMock()
        db.__enter__.return_value.execute.return_value.fetchone.return_value={
            'id': 7, 'ticket_external_id': 132176, 'source_question_action_id': 1}
        db.__enter__.return_value.execute.return_value.fetchall.return_value=[]
        actions=[
            {'id':1,'type':2,'createdDate':'2026-10-23T08:00:00',
             'createdBy':{'profileType':2,'businessName':'Cliente'},
             'description':'<p>Produto não integra. Qual cadastro devo abrir?</p>',
             'htmlDescription':'<p>Pergunta</p><img src="https://s3.amazonaws.com/movidesk-files/ABCDEF0123456789ABCDEF0123456789">'},
            {'id':2,'type':1,'createdDate':'2026-10-23T09:00:00',
             'createdBy':{'profileType':1,'businessName':'Técnico'},
             'description':'<p>Abra o cadastro e relacione o código externo.</p>'},
        ]
        with patch.object(kt,'connect',return_value=db), \
             patch.object(kt,'load_ticket',return_value=({'subject':'Produto não integra','status':'Fechado'},actions)):
            result=kt.get_candidate(7)
        context=result['ticket_context']
        self.assertIn('Qual cadastro',context['question'])
        self.assertEqual([a['role'] for a in context['history']],['customer','technician'])
        self.assertIn('código externo',context['history'][1]['text'])
        self.assertIn('código externo',context['technician_reply'])
        self.assertEqual(len(context['images']),1)
        self.assertEqual(len(context['history'][0]['images']),1)
        self.assertTrue(context['history'][0]['images'][0]['inline'])
        self.assertIn('/api/treinamento/candidatos/7/imagens/1/0',context['history'][0]['html'])
        self.assertEqual(context['history'][1]['images'],[])
        self.assertEqual(context['images'][0]['url'],'/api/treinamento/candidatos/7/imagens/1/0')

    def test_candidate_image_is_bound_to_original_ticket_action(self):
        db=MagicMock()
        db.__enter__.return_value.execute.return_value.fetchone.return_value={'ticket_external_id':132176}
        actions=[{'id':1,'type':2,'description':'Veja o print',
                  'htmlDescription':'<img src="https://s3.amazonaws.com/movidesk-files/ABCDEF0123456789ABCDEF0123456789">'}]
        with patch.object(kt,'connect',return_value=db), \
             patch.object(kt,'load_ticket',return_value=({},actions)), \
             patch('movidesk_note_worker.Api') as api:
            api.return_value.image.return_value=b'\x89PNG\r\n\x1a\nmock'
            raw,mime=kt.candidate_ticket_image(7,1,0)
            self.assertEqual(mime,'image/png')
            self.assertEqual(raw[:8],b'\x89PNG\r\n\x1a\n')
            with self.assertRaises(LookupError):
                kt.candidate_ticket_image(7,2,0)
        api.return_value.image.assert_called_once()

    def test_selected_ticket_print_is_prepared_without_publishing(self):
        raw = io.BytesIO(); Image.new('RGB', (20, 10), 'white').save(raw, 'PNG')
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(kt, 'TRAINING_MEDIA', Path(folder)), \
             patch.object(kt, 'candidate_ticket_image', return_value=(raw.getvalue(), 'image/png')), \
             patch.object(kt, 'connect') as connect, \
             patch.object(kt, 'audit') as audit:
            result = kt.prepare_candidate_image(7, 2, 0, 'Lucas')
            self.assertTrue((Path(folder) / result['file']).is_file())
            self.assertRegex(result['file'], r'^[0-9a-f]{32}\.png$')
            self.assertEqual(result['url'], '/training-media/' + result['file'])
            connect.assert_called_once()
            self.assertEqual(audit.call_args.args[3], 'image_prepared')
            self.assertEqual(audit.call_args.args[4], 'Lucas')

    def test_prepared_candidate_image_can_be_redacted_without_original_action(self):
        raw = io.BytesIO(); Image.new('RGB', (20, 10), 'white').save(raw, 'PNG')
        data_url = 'data:image/png;base64,' + base64.b64encode(raw.getvalue()).decode()
        source_file = 'a' * 32 + '.png'
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = {
            'extraction': {'images': [{'file': source_file}]}
        }
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(kt, 'TRAINING_MEDIA', Path(folder)), \
             patch.object(kt, 'ensure_schema'), \
             patch.object(kt, 'connect') as connect, \
             patch.object(kt, 'audit') as audit:
            connect.return_value.__enter__.return_value = db
            result = kt.save_candidate_prepared_image_redaction(
                7, source_file, data_url, 'Print tratado', True, 'Lucas'
            )
        self.assertTrue(result['file'].endswith('.png'))
        self.assertNotEqual(result['file'], source_file)
        self.assertEqual(audit.call_args.args[3], 'prepared_image_reviewed')

    def test_candidate_answer_prefers_validated_html_and_restores_missing_gallery_images(self):
        text = (Path(__file__).parent / 'static' / 'treinamento.html').read_text(encoding='utf-8')
        validated = text.index('if (content.validated_html)')
        source = text.index('if (source?.html)', validated)
        self.assertLess(validated, source)
        self.assertIn('function ensureCandidateImagesInAnswer(html, content)', text)
        self.assertIn('image.dataset.candidateFile = chosen.file', text)
        self.assertIn('/imagens-salvas/${encodeURIComponent(wrapper.dataset.file)}/revisar', text)

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

    def test_transform_refreshes_live_ticket_before_extraction(self):
        app=FastAPI(); app.include_router(router)
        with TestClient(app) as client, \
             patch.object(kt,'create_candidate',return_value={'id':12}) as create:
            result=client.post('/api/treinamento/candidatos',json={'ticket_external_id':132176,'force':True})
        self.assertEqual(result.status_code,200)
        create.assert_called_once_with(132176,'tecnico_local',True,refresh_live=True)

    def test_admin_ticket_entry_opens_exact_ticket_in_technician_curation(self):
        text = (Path(__file__).parent / 'static' / 'treinamento.html').read_text(encoding='utf-8')
        self.assertIn('function openTechnicalCuration(ticket)', text)
        self.assertIn("target.searchParams.set('ticket', String(ticket))", text)
        self.assertIn('openTechnicalCuration(n);', text)
        self.assertIn('>Preparar para curadoria</button>', text)
        self.assertNotIn('id="reviewAnswerBtn"', text)
        self.assertNotIn('data-tab="reviews"', text)

        technician = (Path(__file__).parent / 'static' / 'treinamento_tecnico.html').read_text(encoding='utf-8')
        self.assertIn('const manualActionId = `manual:${ticket}`', technician)
        self.assertIn('Modo <strong>preparação manual</strong>', technician)
        self.assertIn('applyTechnicianNoteToEditor(recommendedNote, false)', technician)

    def test_manual_technician_proposal_does_not_require_prior_cortex_answer(self):
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = {'id': 44}
        with patch.object(kt, 'taxonomy_options', return_value=[{'key': 'relatorios'}]), \
             patch.object(kt, 'connect') as connect, \
             patch.object(kt, 'load_ticket', return_value=({'subject': 'Relatório'}, [
                 {'id': 1, 'type': 2, 'description': 'Onde encontro o relatório por produto?'}
             ])), \
             patch.object(kt, 'create_candidate', return_value={'id': 81}) as create:
            connect.return_value.__enter__.return_value = db
            result = kt.review_answer(
                132384, 'manual:132384', -1, 'relatorios', 'Solução revisada', 'Lucas', 132384,
                'Relatório de vendas por produto',
                'O relatório está disponível no menu de vendas do Master.',
                '1. Acesse o menu Relatórios.\n2. Abra Vendas por produto.',
                [], None, None,
            )
        self.assertEqual(result['candidate_id'], 81)
        create.assert_called_once()

    def test_candidate_screen_does_not_mix_global_image_library(self):
        page=Path(__file__).parent/'static'/'treinamento.html'
        text=page.read_text(encoding='utf-8')
        self.assertIn('candidateForm(c, content, related, imported)',text)
        self.assertIn('(content.images || []).map(chosen => state.images.find(image => image.file === chosen.file))',text)
        self.assertIn('...imported.values()',text)
        self.assertIn('class="review-answer-editor" contenteditable="true"',text)
        self.assertIn('function candidateAnswerHtml(c, content, context)',text)
        self.assertIn('imagesForCandidateAction(context, source)',text)
        self.assertNotIn('Conversa original do chamado',text)
        self.assertIn("dlg.querySelectorAll('#f_answer img')",text)
        self.assertIn('Os prints aparecem automaticamente na resposta',text)
        self.assertIn('Incluído automaticamente',text)
        self.assertIn("['blur','Borrar'],['paint','Pintar']",text)
        self.assertIn('after_answer_paragraph',text)
        self.assertIn('/revisar',text)

    def test_candidate_subcategories_use_collapsed_parent_tree(self):
        page=Path(__file__).parent/'static'/'treinamento.html'
        text=page.read_text(encoding='utf-8')
        self.assertIn('<details class="subtree-group"><summary>',text)
        self.assertIn("if (open) menu.querySelectorAll('details').forEach(d => d.open = false)",text)
        self.assertIn('id="f_sub" type="hidden"',text)

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
             patch.object(kt,'revision_requests',return_value=[]), \
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

    def test_refactored_technician_curation_screen_layout_and_exclusions(self):
        html = (Path(__file__).parent / 'static' / 'treinamento_tecnico.html').read_text(encoding='utf-8')
        # 3 áreas principais presentes
        self.assertIn('Mensagem original do cliente', html)
        self.assertIn('O que a IA analisou', html)
        self.assertIn('Entendimento da IA', html)
        self.assertIn('Resposta sugerida pela IA', html)
        self.assertIn('Avaliação da resposta', html)
        self.assertIn('Solução validada pelo técnico', html)
        self.assertIn('Revise a resposta, faça ajustes se necessário e envie para a curadoria administrativa.', html)
        self.assertIn('Importar última resposta do ticket', html)
        self.assertIn('Salvar rascunho', html)
        self.assertIn('Enviar para curadoria', html)
        # Bloco descartado e elementos excluídos na imagem 2 NÃO devem existir
        self.assertNotIn('Contexto e informações do chamado', html)
        self.assertNotIn('Buscar chamados, conhecimento', html)
        self.assertNotIn('2 de 15', html)

    def test_parse_cortex_note_extracts_clean_reply_sources_and_confidence(self):
        raw_note = (
            '<div><b>CORTEX — Apoio automático ao atendimento</b></div>'
            '<div><b>Especialista acionado:</b> Fiscal · <b>Decisão interna:</b> respond</div>'
            '<div><b>Sugestão de resposta ao cliente:</b><br>'
            'Olá! Para consultar produtos por fornecedor:<br>'
            '1. Acesse Relatórios > Vendas > Histórico.<br>'
            '2. Abra Produtos por Fabricante/Fornecedor.<br>'
            '3. Selecione a aba Fornecedor.</div>'
            '<div><b>Observação interna:</b> Cliente deseja localizar relatório de produtos filtrando pelo fornecedor.</div>'
            '<div><b>Fontes usadas:</b> canon:14, #132084</div>'
            '<div><b>Chamados semelhantes:</b><br>#132084 — Relatório por fornecedor (0.86)</div>'
            '<!-- CORTEX-AI-NOTE:v1:132269:1 -->'
        )
        parsed = kt._parse_cortex_note(raw_note)
        self.assertIn('1. Acesse Relatórios > Vendas > Histórico.', parsed['clean_reply'])
        self.assertNotIn('CORTEX-AI-NOTE:', parsed['clean_reply'])
        self.assertEqual(parsed['specialist'], 'Fiscal')
        self.assertEqual(parsed['confidence'], 'Alta')
        self.assertGreaterEqual(parsed['sources_count'], 2)
        self.assertIn('Cliente deseja localizar relatório', parsed['internal_observation'])

    def test_save_and_update_answer_score_0_to_10_without_auto_publishing(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.side_effect = [
            None,
            {'id': 41, 'score': 8, 'rating': 1, 'module_key': 'fiscal', 'updated_at': '2026-10-22T22:00:00Z'},
            {'id': 41, 'score': 8, 'rating': 1, 'module_key': 'fiscal'},
            {'id': 41, 'score': 4, 'rating': -1, 'module_key': 'fiscal', 'updated_at': '2026-10-22T22:05:00Z'},
        ]
        actions = [{'id': 9, 'description': 'CORTEX-AI-NOTE:v1:132269:1 Sugestão'}]
        with patch.object(kt, 'ensure_schema'), patch.object(kt, 'connect') as connect, \
             patch.object(kt, 'load_ticket', return_value=({'subject': 'Teste'}, actions)), \
             patch.object(kt, 'taxonomy_options', return_value=[{'key': 'fiscal'}, {'key': 'geral'}]), \
             patch.object(kt, 'create_candidate') as create_cand, \
             patch.object(kt, 'audit') as audit_log:
            connect.return_value.__enter__.return_value = conn
            first = kt.save_answer_score(132269, '9', 8, 'Lucas', 'fiscal', 'Boa resposta inicial')
            second = kt.save_answer_score(132269, '9', 4, 'Lucas', 'fiscal', 'Faltou detalhar o passo 2')
            with self.assertRaisesRegex(ValueError, '0 a 10'):
                kt.save_answer_score(132269, '9', 11, 'Lucas')
        self.assertEqual(first['score'], 8)
        self.assertEqual(first['rating'], 1)
        self.assertEqual(second['score'], 4)
        self.assertEqual(second['rating'], -1)
        create_cand.assert_not_called()
        self.assertEqual(audit_log.call_count, 2)

    def test_import_last_technician_answer_supports_open_and_closed_tickets_and_prioritizes_public(self):
        closed_ticket = {'subject': 'Relatório por fornecedor', 'status': 'Fechado', 'baseStatus': 'Closed'}
        actions = [
            {'id': 1, 'type': 2, 'createdDate': '2026-04-17T11:36:00',
             'createdBy': {'profileType': 2, 'businessName': 'Cliente'},
             'description': 'Bom dia, tem algum relatório de produtos por fornecedor?'},
            {'id': 2, 'type': 1, 'createdDate': '2026-04-17T11:37:00',
             'createdBy': {'profileType': 1, 'businessName': 'Cortex'},
             'description': 'CORTEX-AI-NOTE:v1:132269:1 Resposta automática da IA que deve ser ignorada.'},
            {'id': 3, 'type': 2, 'createdDate': '2026-04-17T11:38:00',
             'createdBy': {'profileType': 1, 'businessName': 'Automação'},
             'description': 'Olá, recebemos sua solicitação e em breve um atendente irá responder.'},
            {'id': 4, 'type': 2, 'createdDate': '2026-04-17T11:45:00',
             'createdBy': {'profileType': 1, 'businessName': 'Marília'},
             'description': '<p><b>Sim!</b> Acesse 1. Relatórios > Vendas > Histórico. 2. Produtos por Fornecedor.</p>'},
            {'id': 5, 'type': 1, 'createdDate': '2026-04-17T11:50:00',
             'createdBy': {'profileType': 1, 'businessName': 'Lucas'},
             'description': 'Nota interna técnica: cliente usa versão atualizada do Master no servidor local.'},
        ]
        with patch.object(kt, 'ensure_schema'), patch.object(kt, 'connect'), \
             patch.object(kt, 'load_ticket', return_value=(closed_ticket, actions)):
            res = kt.ticket_technician_answers(132269)
            res_public_only = kt.ticket_technician_answers(132269, allow_internal=False)
        self.assertEqual(res['status'], 'Fechado')
        self.assertEqual([a['action_id'] for a in res['answers']], ['4', '5'])
        # Prioriza a última resposta pública ('4') como recomendada mesmo havendo nota interna posterior ('5')
        self.assertEqual(res['recommended_action_id'], '4')
        self.assertEqual(res['answers'][0]['author'], 'Marília')
        self.assertEqual(res['answers'][0]['visibility'], 'public')
        self.assertEqual(res['answers'][1]['visibility'], 'internal')
        self.assertEqual([a['action_id'] for a in res_public_only['answers']], ['4'])

    def test_signature_only_action_is_not_a_technician_answer_or_image_source(self):
        ticket = {'subject': 'Relatório de vendas', 'status': 'Cancelado', 'baseStatus': 'Closed'}
        actions = [
            {'id': 1, 'type': 2, 'createdDate': '2026-10-23T03:50:00',
             'createdBy': {'profileType': 2, 'businessName': 'Cliente'},
             'description': 'No Master existe relatório de vendas por produto?'},
            {'id': 4, 'type': 2, 'createdDate': '2026-10-23T03:54:00',
             'createdBy': {'profileType': 1, 'businessName': 'Lucas'},
             'description': '<p>Acesse Relatórios &gt; Vendas e abra Vendas por produto.</p>',
             'htmlDescription': '<p>Acesse Relatórios &gt; Vendas e abra Vendas por produto.</p>'},
            {'id': 8, 'type': 2, 'createdDate': '2026-10-26T14:49:00',
             'createdBy': {'profileType': 1, 'businessName': 'Lucas'},
             'description': 'Obrigado por entrar em contato conosco por este canal de atendimento. '
                            'Você já assistiu nossos vídeos sobre a Base de Conhecimento?'},
        ]
        with patch.object(kt, 'ensure_schema'), patch.object(kt, 'connect'), \
             patch.object(kt, 'load_ticket', return_value=(ticket, actions)):
            result = kt.ticket_technician_answers(132273, refresh_live=False)
        self.assertEqual([answer['action_id'] for answer in result['answers']], ['4'])
        self.assertEqual(result['recommended_action_id'], '4')
        self.assertEqual(kt._recommended_curation_pair(actions), ('1', '4'))

    def test_save_review_draft_and_submit_proposal_with_classification_and_score(self):
        import os
        app = FastAPI()
        app.include_router(router)
        with TestClient(app, base_url='https://cortex.example') as client, \
             patch.dict(os.environ, {'CORTEX_PUBLIC_URL': 'https://cortex.example', 'CORTEX_TECHNICIAN_PINS': '1234:Lucas'}), \
             patch.object(kt, 'save_answer_score', return_value={'id': 10, 'score': 7, 'rating': 1}) as score_fn, \
             patch.object(kt, 'save_review_draft', return_value={'id': 10, 'is_draft': True}) as draft_fn, \
             patch.object(kt, 'review_answer', return_value={'id': 10, 'candidate_id': 77, 'example_ticket_id': 132269}) as review_fn:
            headers = {'Origin': 'https://cortex.example', 'X-Cortex-Technician-Pin': '1234'}
            score_res = client.post('/api/treinamento/tecnico/tickets/132269/nota', headers=headers,
                                    json={'action_id': '2', 'score': 7, 'module_key': 'relatorios'})
            self.assertEqual(score_res.status_code, 200)
            score_fn.assert_called_once_with(132269, '2', 7, 'Lucas', 'relatorios', None)

            draft_res = client.post('/api/treinamento/tecnico/tickets/132269/rascunho', headers=headers,
                                    json={'action_id': '2', 'score': 7, 'module_key': 'relatorios',
                                          'subtopic_key': 'vendas', 'keywords': ['fornecedor', 'historico'],
                                          'title': 'Relatório de produtos por fornecedor',
                                          'answer': 'Acesse o menu Relatórios > Vendas > Histórico.',
                                          'procedure': '1. Acesse Relatórios > Vendas.\n2. Clique em Fornecedor.',
                                          'source_answer_action_id': '4'})
            self.assertEqual(draft_res.status_code, 200)
            draft_fn.assert_called_once()

            prop_res = client.post('/api/treinamento/tecnico/tickets/132269/propostas', headers=headers,
                                   json={'action_id': '2', 'question_action_id': '1',
                                         'module_key': 'relatorios', 'subtopic_key': 'vendas',
                                         'keywords': ['fornecedor', 'historico'], 'score': 7,
                                         'title': 'Relatório de produtos por fornecedor',
                                         'answer': 'Sim, o relatório está disponível no menu Relatórios > Vendas.',
                                         'procedure': '1. Acesse Relatórios > Vendas > Histórico.\n2. Selecione Fornecedor.',
                                         'validated_html': '<p>Sim, o relatório está disponível.</p><ol><li>Acesse Relatórios</li></ol>',
                                         'source_answer_action_id': '4', 'comment': 'Validado no atendimento', 'images': []})
            self.assertEqual(prop_res.status_code, 200)
            self.assertEqual(prop_res.json()['candidate_id'], 77)
            self.assertEqual(review_fn.call_args.args[5], 'Lucas')
            self.assertEqual(review_fn.call_args.args[12], '4')
            self.assertEqual(review_fn.call_args.kwargs['subtopic_key'], 'vendas')
            self.assertEqual(review_fn.call_args.kwargs['keywords'], ['fornecedor', 'historico'])
            self.assertEqual(review_fn.call_args.kwargs['score'], 7)

    def test_approve_candidate_materializes_unprepared_ticket_images_and_accepts_default_reason(self):
        candidate = {
            'id': 5,
            'ticket_external_id': 132273,
            'status': 'pending',
            'suggested_module': 'sales',
            'suggested_subtopic': 'general',
            'extraction': {
                'title': 'Localização de Relatório de Vendas',
                'symptom': 'Usuário quer localizar relatório de vendas por produto.',
                'root_cause': 'Dúvida operacional de navegação no menu de relatórios.',
                'procedure': '1. Acesse o menu Vendas.\n2. Selecione Relatório por Produto.',
                'customer_answer': 'Acesse o menu Vendas > Relatório.',
                'applicability': {},
                'error_messages': [],
            }
        }
        raw_png = io.BytesIO()
        Image.new('RGB', (10, 10), 'blue').save(raw_png, 'PNG')
        prepared_meta = {'file': 'd41d8cd98f00b204e9800998ecf8427e.png', 'url': '/training-media/d41d8cd98f00b204e9800998ecf8427e.png'}

        db = MagicMock()
        db.execute.return_value.fetchone.side_effect = [
            candidate, # SELECT FOR UPDATE candidates
            {'id': 101}, # INSERT items RETURNING id
            {'n': 0}, # SELECT max(version_no) AS n
            {'id': 201, 'version_no': 1}, # INSERT versions RETURNING *
            {'v': None}, # _cached_vector
        ]
        db.execute.return_value.fetchall.return_value = []

        content = {
            'title': 'Localização de Relatório de Vendas',
            'module_key': 'sales',
            'subtopic_key': 'general',
            'symptom': 'Usuário quer localizar relatório de vendas por produto.',
            'root_cause': 'Dúvida operacional de navegação no menu de relatórios.',
            'procedure': '1. Acesse o menu Vendas.\n2. Selecione Relatório por Produto.',
            'customer_answer': 'Acesse o menu Vendas > Relatório.',
            'customer_answer_html': '<p>Acesse o menu</p><img src="/api/treinamento/candidatos/5/imagens/7/0">',
            'images': [{
                'file': 'candidate-5-7-0.png',
                'name': 'Tela de Vendas',
                'caption': 'Menu de vendas',
                'placement': 'answer',
                'after_answer_paragraph': 1,
            }],
            'applicability': {},
            'error_messages': [],
        }

        with patch.object(kt, 'ensure_schema'),              patch.object(kt, 'connect') as conn_mock,              patch.object(kt, 'prepare_candidate_image', return_value=prepared_meta) as prep_mock,              patch.object(kt, 'procedure_images', return_value=[{'file': 'd41d8cd98f00b204e9800998ecf8427e.png'}]),              patch.object(kt, '_embed', return_value=None),              patch.object(kt, '_publish'),              patch.object(kt, 'audit') as audit_mock,              patch.object(kt, 'item_detail', return_value={'id': 101}):
            conn_mock.return_value.__enter__.return_value = db
            result = kt.approve(5, 'new', content, 'Lucas', reason='')
            self.assertEqual(result, {'id': 101})
            prep_mock.assert_called_once_with(5, 7, 0, 'Lucas')
            # Check audit action and reason
            audit_call = [call for call in audit_mock.call_args_list if call.args[2] == 5 and call.args[3] == 'approved:new']
            self.assertTrue(len(audit_call) > 0)
            self.assertIn('132273', audit_call[0].args[5])

    def test_approve_api_route_allows_omitted_reason_and_returns_clean_422_detail(self):
        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client:
            # 1. Missing required field in content gives 422 with structured detail
            bad_res = client.post('/api/treinamento/candidatos/5/aprovar', json={
                'decision': 'new',
                'content': {
                    'title': 'abc', # too short (min_length=5)
                    'module_key': 'sales',
                    'subtopic_key': 'general',
                    'symptom': 'Curto', # min_length=5
                    'root_cause': 'Causa', # min_length=5
                    'procedure': '1. Passo curto',
                }
            })
            self.assertEqual(bad_res.status_code, 422)
            self.assertIsInstance(bad_res.json()['detail'], list)
            locs = [d['loc'][-1] for d in bad_res.json()['detail']]
            self.assertIn('title', locs)

    def test_treinamento_html_removed_reason_and_target_from_candidate_footer(self):
        html = (Path(__file__).parent / 'static' / 'treinamento.html').read_text(encoding='utf-8')
        # Check that candidateForm footer does not contain #reason or #target inputs
        candidate_form_idx = html.find('candidateForm(')
        self.assertNotEqual(candidate_form_idx, -1)
        self.assertNotIn('placeholder="Motivo da decisão (obrigatório, fica na auditoria)"', html)
        self.assertNotIn('— vincular a procedimento existente —', html)
        self.assertIn('candidateReviewError', html)
        self.assertIn('candidate-error-banner', html)

if __name__ == '__main__': unittest.main()
