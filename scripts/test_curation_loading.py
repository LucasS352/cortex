import copy
import unittest
from unittest.mock import patch
from html.parser import HTMLParser

from fastapi import FastAPI
from fastapi.testclient import TestClient
import knowledge_training_api as training
from movidesk_note_worker import render_note


class CurationLoadingTests(unittest.TestCase):
    def test_snapshot_review_does_not_refresh_movidesk_unless_requested(self):
        app = FastAPI()
        app.include_router(training.router)
        with TestClient(app) as client, patch.object(training.kt, 'ticket_technician_answers', return_value={}) as load:
            self.assertEqual(client.get('/api/treinamento/tickets/42/respostas-tecnicas').status_code, 200)
            load.assert_called_once_with(42, refresh_live=False)
            load.reset_mock()
            self.assertEqual(client.get('/api/treinamento/tickets/42/respostas-tecnicas?refresh=true').status_code, 200)
            load.assert_called_once_with(42, refresh_live=True)

    def test_queue_passes_bounded_pagination_and_filters(self):
        app = FastAPI()
        app.include_router(training.router)
        with TestClient(app) as client, patch.object(training.kt, 'list_candidates', return_value=[]) as load:
            self.assertEqual(client.get('/api/treinamento/candidatos?limit=51&offset=50&module=logistics&subtopic=carriers').status_code, 200)
            load.assert_called_once_with(('pending','conflict'), 'logistics', 'carriers', limit=51, offset=50)
            self.assertEqual(client.get('/api/treinamento/candidatos?limit=1000').status_code, 422)
            self.assertEqual(client.get('/api/treinamento/candidatos?offset=-1').status_code, 422)

    def test_compact_note_preserves_customer_prefix_and_all_internal_text(self):
        data = {'reply':'Olá!\n1. Confira o cadastro.', 'sources':[{'title':'Fonte oficial'}],
                'similar_tickets':[{'ticket':12345,'status':'Concluído','title':'Caso confirmado','resolution':'Ajuste validado.'}],
                'history_notice':'Confira o histórico.', 'specialist':{'name':'Integrações'},
                'decision':{'state':'PROCEDIMENTO_CONFIRMADO','confidence':'ALTA'}, 'feedback_ticket':42}
        original = copy.deepcopy(data)
        with patch.dict('os.environ', {'CORTEX_PUBLIC_URL':'https://testserver'}):
            rendered = render_note(data, 'CORTEX-AI-NOTE:teste')
        prefix, footer = rendered.split('<div style="border-top:', 1)
        self.assertEqual(prefix, '<p><strong>Cortex — resposta sugerida ao cliente (nota interna)</strong></p>'
                         '<p>Olá!</p><p>1. Confira o cadastro.</p><p>Fonte: Fonte oficial</p>')
        class Tags(HTMLParser):
            def handle_starttag(self, tag, attrs):
                if tag in {'p','li','a','strong','small','ul'}:
                    self_styles.append(dict(attrs).get('style',''))
        self_styles = []
        Tags().feed(footer)
        self.assertTrue(self_styles)
        self.assertTrue(all('font-size:9pt' in value for value in self_styles))
        for text in ['#12345', 'Ajuste validado.', 'Confira o histórico.', 'Integrações',
                     'PROCEDIMENTO_CONFIRMADO', 'ALTA', '/treinamento/tecnico?', 'CORTEX-AI-NOTE:teste']:
            self.assertIn(text, footer)
        self.assertEqual(original, data)
