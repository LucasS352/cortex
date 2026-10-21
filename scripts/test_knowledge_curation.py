import unittest
import io
import json
import os
from unittest.mock import patch
from knowledge_curation import apply_overlay, classify, subtopic, connect, ensure_schema, curate
from knowledge_access import search
from admin_console import knowledge_detail, suggest_knowledge
from starlette.requests import Request


class KnowledgeCurationTest(unittest.TestCase):
    def test_module_families_use_imported_tags(self):
        self.assertEqual(classify('Como cadastrar grade', ['Estoque', 'Produtos']), 'products')
        self.assertEqual(classify('Rejeição 783', ['NF-e']), 'fiscal')
        self.assertEqual(classify('Pedido não sincronizou', ['SINC', 'VTEX']), 'integrations')
        self.assertEqual(classify('Sem informação', []), 'unclassified')
        self.assertEqual(classify('Mapa de produção com quantidade incorreta',[]),'production')
        self.assertEqual(classify('Erro na emissão de NFS-e',[]),'fiscal')
        self.assertEqual(subtopic('products','Cadastrar grade e variações',['Produtos']),'grades')
        self.assertEqual(subtopic('fiscal','Rejeição 783: IE inválida',['NF-e']),'rejections')

    def test_draft_retains_imported_text_and_approval_applies_correction(self):
        node = {'id': 1, 'title': 'Título original', 'problem': 'Problema antigo', 'solution': 'Solução antiga', 'modules': ['Estoque'], 'manual_category': ''}
        edit = {'status': 'draft', 'module_key': 'products', 'subtopic_key':'grades', 'revision': 1,
                'title_override': 'Título corrigido', 'problem_override': 'Problema correto',
                'solution_override': 'Procedimento revisado', 'version_note': '3.0'}
        self.assertEqual(apply_overlay(node, edit)['solution'], 'Solução antiga')
        edit['status'] = 'approved'
        corrected = apply_overlay(node, edit)
        self.assertEqual((corrected['title'], corrected['solution'], corrected['version_reference']),
                ('Título corrigido', 'Procedimento revisado', '3.0'))
        self.assertEqual(node['title'], 'Título original')

    def test_marked_screenshot_is_exposed_only_after_approval(self):
        original = {'id': 819, 'title': 'Entrada sem XML', 'problem': '', 'solution': '',
                    'modules': ['Compras'], 'manual_category': ''}
        edit = {'status': 'draft', 'module_key': 'purchases', 'subtopic_key': 'general',
                'revision': 1, 'solution_override': '1. Clique em Gravar\n![Tela](/curation_media/819/frame-090.jpg)',
                'image_annotations': [{'url': '/curation_media/819/frame-090.jpg',
                                       'x': 0.6, 'y': 0.8, 'label': 'Gravar'}]}
        self.assertNotIn('marked.svg', apply_overlay(original, edit)['solution'])
        edit['status'] = 'approved'
        self.assertIn('frame-090.marked.svg', apply_overlay(original, edit)['solution'])

    @unittest.skipUnless(os.getenv('CORTEX_TEST_DATABASE') == '1',
                         'requires the opt-in PostgreSQL integration database')
    def test_exclusion_revision_and_corrected_retrieval_rollback(self):
        ensure_schema()
        with connect() as conn:
            node = conn.execute('''SELECT kn.id,kn.source_external_id FROM ticket_knowledge_nodes kn
                LEFT JOIN knowledge_curation c ON c.node_id=kn.id
                WHERE c.node_id IS NULL AND kn.source_external_id IS NOT NULL
                ORDER BY kn.id LIMIT 1''').fetchone()
            self.assertIsNotNone(node)
            base = dict(module_key='products',subtopic_key='grades',status='excluded',
                        title_override='Curadoria verificacao temporaria', problem_override='Caso de teste',
                        solution_override='Procedimento temporário usado apenas no teste transacional.',
                        prerequisites='Acesso de teste',expected_result='Resultado observado no teste.',
                        version_note='Teste',change_reason='Teste de exclusão transacional')
            curate(conn,node['id'],base,0)
            excluded=search(conn,str(node['source_external_id']),top_k=30)
            self.assertNotIn(node['id'],[r['id'] for r in excluded])
            with self.assertRaises(RuntimeError): curate(conn,node['id'],base,0)
            # A failed statement inside PostgreSQL aborts the transaction;
            # this conflict is raised before SQL mutation, so editing continues.
            base.update(status='approved',change_reason='Teste de texto corrigido na busca')
            curate(conn,node['id'],base,1)
            approved=search(conn,'curadoria verificacao temporaria',top_k=30)
            self.assertIn(node['id'],[r['id'] for r in approved])
            conn.rollback()

    @unittest.skipUnless(os.getenv('CORTEX_TEST_DATABASE') == '1',
                         'requires the opt-in PostgreSQL integration database')
    def test_draft_keywords_retrieve_original_without_approving_steps(self):
        ensure_schema()
        with connect() as conn:
            node = conn.execute('''SELECT kn.id FROM ticket_knowledge_nodes kn
                LEFT JOIN knowledge_curation c ON c.node_id=kn.id
                WHERE c.node_id IS NULL ORDER BY kn.id LIMIT 1''').fetchone()
            update = dict(module_key='products',subtopic_key='grades',status='draft',
                title_override='Rascunho não aprovado',problem_override='Problema de teste',
                solution_override='Passos que não podem ser exibidos',prerequisites='',
                expected_result='',version_note='',keywords=['xylophonetik'],
                change_reason='Teste de sinônimo pesquisável')
            curate(conn,node['id'],update,0)
            hits=search(conn,'xylophonetik',top_k=30)
            match=next(r for r in hits if r['id']==node['id'])
            self.assertNotEqual(match['title'],'Rascunho não aprovado')
            self.assertIn('xylophonetik',match['search_keywords'])
            conn.rollback()

    @unittest.skipUnless(os.getenv('CORTEX_TEST_DATABASE') == '1',
                         'requires the opt-in PostgreSQL integration database')
    def test_article_video_is_linked_without_imagined_procedure(self):
        request=Request({'type':'http','client':('testclient',0)})
        detail=knowledge_detail(request,1325)
        self.assertTrue(any(m['kind']=='video' for m in detail['media']))
        self.assertFalse(detail['original_content_text'].strip())

    @unittest.skipUnless(os.getenv('CORTEX_TEST_DATABASE') == '1',
                         'requires the opt-in PostgreSQL integration database')
    def test_video_frames_generate_review_only_proposal(self):
        request=Request({'type':'http','client':('testclient',0)})
        with connect(True) as conn:
            revision_before=conn.execute('SELECT revision FROM knowledge_curation WHERE node_id=895').fetchone()['revision']
        draft={'problem':'Entrada de compra via XML','prerequisites':'Ter XML',
               'steps':[{'text':'Confira a movimentação.', 'image_indexes':[1]}],
               'expected_result':'Registro gravado','version_note':'Conferir versão','gaps':[]}
        response={'candidates':[{'content':{'parts':[{'text':json.dumps(draft)}]}}]}
        with patch('admin_console.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(response).encode())):
            result=suggest_knowledge(request,895)
        self.assertEqual(result['status'],'draft')
        self.assertIn('/curation_media/895/frame-',result['solution'])
        with connect(True) as conn:
            self.assertEqual(conn.execute('SELECT revision FROM knowledge_curation WHERE node_id=895').fetchone()['revision'],revision_before)


if __name__ == '__main__': unittest.main()
