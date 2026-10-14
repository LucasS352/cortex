import unittest
from unittest.mock import patch
import knowledge_access as knowledge
from technician_workbench import build_reply
from movidesk_note_worker import render_note


class TutorialEvidenceTests(unittest.TestCase):
    def test_import_and_export_are_different_procedures(self):
        source = {'title': 'Como exportar cadastros de produtos no ERP Master?'}
        self.assertFalse(knowledge.applicability(source, 'como importar cadastros de produtos?')['applicable'])

    def test_specific_grade_request_rejects_basic_registration(self):
        source = {'title': 'Como fazer cadastro de produto no ERP Master?'}
        self.assertFalse(knowledge.applicability(source, 'como cadastrar produtos e grades?')['applicable'])

    def test_creation_ranks_basic_tutorial_before_related_operations(self):
        titles = ['Como importar cadastros de produtos no ERP Master?',
                  'Como fazer cadastro de kits de produtos no ERP Master?',
                  'Cálculo de margem dos campos do cadastro de produtos & custo',
                  'Como fazer cadastro de produto no ERP Master?']
        rows = [{'id': i, 'title': title, 'source_type': 'OFFICIAL_KB', 'solution': ''}
                for i, title in enumerate(titles)]
        query = 'como faço pra criar um produto?'
        result = knowledge.rank_rows(rows, knowledge.query_plan(query))
        self.assertEqual(result[0]['title'], titles[-1])
        self.assertTrue(knowledge.applicability(result[0], query)['applicable'])

    def test_untranscribed_video_is_shown_without_invented_steps(self):
        source = {'title': 'Como fazer cadastro de produto no ERP Master?', 'applicable': True,
                  'answer_use': 'investigation_only', 'evidence_status': 'video_reference_without_transcript',
                  'videos': [{'title': 'Cadastro de produto', 'url': 'https://youtu.be/A6nUw8j7TE4'}]}
        with patch('technician_workbench.llm_client.generate') as model:
            reply, meta = build_reply({}, '', [], [], [source], 'como criar produto?', audience='customer_draft')
        model.assert_not_called()
        self.assertEqual(meta['mode'], 'video_reference')
        note = render_note({'reply': reply, 'videos': source['videos']}, 'test')
        self.assertIn('href="https://youtu.be/A6nUw8j7TE4"', note)
        self.assertNotIn('Cadastros →', note)

    def test_video_renderer_rejects_untrusted_hosts(self):
        note = render_note({'reply': 'Teste', 'videos': [{'url': 'https://evil.example/video'}]}, 'test')
        self.assertNotIn('evil.example', note)


if __name__ == '__main__':
    unittest.main()
