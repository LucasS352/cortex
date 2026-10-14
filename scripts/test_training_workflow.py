"""Human approval boundary and exact-answer regression checks."""
import unittest
from unittest.mock import patch, MagicMock
import knowledge_training as kt
import knowledge_access as ka
from fastapi import FastAPI
from fastapi.testclient import TestClient
from knowledge_training_api import router
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

    def test_unclassified_cannot_be_published(self):
        with self.assertRaisesRegex(ValueError, 'módulo'):
            kt._validate_content(dict(title='Titulo completo',symptom='Sintoma',root_cause='Causa confirmada',
                procedure='1. Procedimento',module_key='unclassified',subtopic_key='general'))

    def test_negative_feedback_never_creates_candidate(self):
        db = MagicMock(); db.execute.return_value.fetchone.return_value = {'id': 4}
        with patch.object(kt,'taxonomy_options',return_value=[{'key':'fiscal'}]), \
             patch.object(kt,'connect') as connect, patch.object(kt,'load_ticket',return_value=({},[{'id':2,'description':'CORTEX-AI-NOTE:abc'}])), \
             patch.object(kt,'create_candidate') as create:
            connect.return_value.__enter__.return_value = db
            result = kt.review_answer(131726,'2',-1,'fiscal','Passo incorreto','tecnico')
            create.assert_not_called()
            self.assertIsNone(result['candidate_id'])

    def test_positive_feedback_is_candidate_not_publication(self):
        db = MagicMock(); db.execute.return_value.fetchone.return_value = {'id': 4}
        with patch.object(kt,'taxonomy_options',return_value=[{'key':'fiscal'}]), \
             patch.object(kt,'connect') as connect, patch.object(kt,'load_ticket',return_value=({},[{'id':2,'description':'CORTEX-AI-NOTE:abc'}])), \
             patch.object(kt,'create_candidate',return_value={'id':8}) as create, patch.object(kt,'_publish') as publish:
            connect.return_value.__enter__.return_value = db
            result = kt.review_answer(131726,'2',1,'fiscal','Validado no atendimento','tecnico')
            create.assert_called_once_with(131726,'tecnico',True,'2','fiscal')
            publish.assert_not_called(); self.assertEqual(result['candidate_id'],8)

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

    def test_feedback_link_targets_ticket_and_exact_marker(self):
        from movidesk_note_worker import render_note
        import os
        with patch.dict(os.environ, {'CORTEX_PUBLIC_URL':'https://cortex.example'}):
            note=render_note({'reply':'Resposta','feedback_ticket':131726},'CORTEX-AI-NOTE:abc')
        self.assertIn('/treinamento?ticket=131726&amp;marker=CORTEX-AI-NOTE%3Aabc',note)
        self.assertIn('Avaliar esta resposta',note)

    def test_review_api_rejects_invalid_rating(self):
        app=FastAPI(); app.include_router(router)
        with TestClient(app) as client, patch.object(kt,'review_answer') as review:
            result=client.post('/api/treinamento/tickets/131726/avaliar',json={'action_id':'1','module_key':'fiscal','rating':0})
            self.assertEqual(result.status_code,422); review.assert_not_called()

if __name__ == '__main__': unittest.main()
