"""Regressions exposed by publishing ticket 132176 in an isolated real corpus."""
import copy
import base64
import io
import random
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock

import knowledge_access as ka
from support_triage import intake
from technician_workbench import build_reply
from movidesk_note_worker import render_note
from technician_workbench import evidence_for_company, cases_for_selected_evidence
import mapped_replies
from PIL import Image, ImageDraw


ANSWER = ('Bom dia,\n\nO produto não estava relacionado.\n\n'
          'Entramos no cadastro do produto e informamos o código de fora.\n\n'
          'Após o ajuste, o pedido foi integrado com sucesso.')
PROCEDURE = '1. Abra Relação - Site com Ctrl+R.\n2. Salve e peça ao técnico para validar a integração.'
SOURCE = {'id': 123, 'source_id': 'canon:123', 'title': 'Venda Magazord: produto sem código externo',
          'problem': 'Produto Magazord não relacionado; venda não integra.',
          'diagnosis': 'Falta a relação do produto no cadastro.',
          'solution': ANSWER + '\n\n' + PROCEDURE,
          'metadata': {'applicability': {'integrations': ['magazord']}},
          'evidence_status': 'human_approved_procedure', 'applicable': True,
          'answer_use': 'procedure_reference', 'quality_flags': []}


class CurationPublicationTests(unittest.TestCase):
    def test_large_reviewed_print_is_delivered_without_changing_redacted_original(self):
        image = Image.frombytes('RGB', (600, 400), random.Random(42).randbytes(600 * 400 * 3))
        ImageDraw.Draw(image).rectangle((0, 0, 80, 40), fill='black')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'reviewed.png'
            image.save(path, 'PNG')
            original = path.read_bytes()
            self.assertGreater(len(original), 350_000)
            data = mapped_replies.reviewed_image_data(path)
            self.assertEqual(mapped_replies.safe_image_data(data), data)
            self.assertTrue(data)
            delivered = Image.open(io.BytesIO(base64.b64decode(data.partition(',')[2])))
            self.assertEqual(delivered.size, (600, 400))
            delivered_rgba = delivered.convert('RGBA')
            covered = delivered_rgba.getpixel((5, 5))
            self.assertEqual(covered, delivered_rgba.getpixel((20, 20)))
            self.assertLessEqual(max(covered[:3]), 2)
            self.assertEqual(covered[3], 255)
            self.assertEqual(path.read_bytes(), original)
            html = render_note({'reply': 'Resposta aprovada.', 'images': [
                {'data_url': data, 'caption': 'Print grande tratado', 'placement': 'answer',
                 'after_answer_paragraph': 1}]}, 'qa')
            self.assertEqual(html.count('<img '), 1)

    def test_approved_procedure_reads_only_its_linked_tickets(self):
        source = copy.deepcopy(SOURCE)
        source['metadata']['source_references'] = [
            {'source_type': 'ticket', 'external_ref': '132176'},
            {'source_type': 'ticket', 'external_ref': '132177'}]
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            {'external_id': 132176, 'subject': 'Produto Magazord sem relação', 'base_status': 'Resolved',
             'solution_statement': 'Foi relacionado o código de fora do produto e validada a integração.'},
            {'external_id': 132177, 'subject': 'Produto Magazord sem relação', 'base_status': 'InAttendance',
             'solution_statement': 'Registro em andamento, sem comprovação de encerramento.'}]
        with patch('knowledge_access.approved_procedures', return_value=[source]), \
             patch('technician_workbench.company_similar_cases') as broad_history:
            sources, cases, scope = evidence_for_company(conn, 'tenant', 'company',
                                                        'Magazord produto sem código externo')
        broad_history.assert_not_called()
        self.assertEqual(scope, 'human_approved_procedure')
        self.assertEqual(conn.execute.call_args.args[1], ('tenant', [132176, 132177]))
        self.assertEqual(len(cases_for_selected_evidence(cases, sources[0])), 2)
        self.assertFalse(cases[0]['is_active'])
        self.assertTrue(cases[1]['is_active'])
        self.assertIsNone(cases[1]['solution_statement'])

    def test_successful_product_relation_does_not_hide_payment_failure(self):
        for question in ('Magazord: forma de pagamento não integrada.',
                         'No Magazord o produto está relacionado, mas a venda não integra por forma de pagamento não integrada.',
                         'Produto relacionado e forma de pagamento não integrada.'):
            with self.subTest(question=question):
                self.assertEqual(ka.mapping_failure_entities(question), {'pagamento'})
                result = ka.applicability(SOURCE, question)
                self.assertFalse(result['applicable'])
                self.assertIn('mapping_entity_not_matched', result['applicability_reasons'])

    def test_explicit_product_code_failure_bypasses_generic_occurrences_intake(self):
        for question in ('Venda Magazord não integra: produto sem código externo ou tipo S.',
                         'Meu pedido não entra no Master: produto sem código de fora na Magazord.',
                         'Produto 40345 sem relação do tipo sku (ctrl+r do tipo S) na Magazord.'):
            with self.subTest(question=question):
                self.assertEqual(ka.mapping_failure_entities(question), {'produto'})
                self.assertIsNone(intake(question, question, []))
                self.assertTrue(ka.applicability(SOURCE, question)['applicable'])

    def test_unrelated_integrator_or_carrier_cannot_reuse_product_answer(self):
        for question in ('AnyMarket: produto sem código externo.',
                         'Magazord: transportadora não relacionada.',
                         'Pedido Magazord não integra: HTTP 503 na API.'):
            with self.subTest(question=question):
                self.assertFalse(ka.applicability(SOURCE, question)['applicable'])

    def test_generic_unknown_order_still_collects_the_actual_error(self):
        reply = intake('Meu pedido da Magazord não entrou no Master',
                       'Meu pedido da Magazord não entrou no Master', [])
        self.assertEqual(reply['mode'], 'triage')

    def test_human_approved_reply_keeps_its_words_and_inline_images(self):
        self._assert_reply_images(None)

    def test_prerequisites_do_not_shift_images_into_the_wrong_paragraph(self):
        self._assert_reply_images('Confirme o cadastro.\nAbra o ambiente correto.')

    def _assert_reply_images(self, prerequisites):
        source = copy.deepcopy(SOURCE)
        source['metadata']['prerequisites'] = prerequisites
        with patch('llm_client.generate') as generate:
            reply, meta = build_reply({}, '', [], [], [source], 'Magazord produto sem código externo')
        generate.assert_not_called()
        self.assertIn(SOURCE['solution'], reply)
        if not prerequisites:
            self.assertEqual(reply, SOURCE['solution'])
        image_data = ('data:image/png;base64,'
                      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==')
        images = [{'data_url': image_data, 'name': 'Print QA ' + str(index),
                   'caption': 'Print QA ' + str(index), 'placement': 'answer',
                   'after_answer_paragraph': paragraph}
                  for index, paragraph in ((1, 3), (2, 4))]
        html = render_note({'reply': reply, 'images': images,
                            'answer_paragraph_offset': meta['answer_paragraph_offset']}, 'qa')
        self.assertLess(html.index('informamos o código de fora'), html.index('Print QA 1'))
        self.assertLess(html.index('Print QA 1'), html.index('Após o ajuste'))
        self.assertLess(html.index('Após o ajuste'), html.index('Print QA 2'))
        self.assertLess(html.index('Print QA 2'), html.index('1. Abra Relação'))


if __name__ == '__main__':
    unittest.main()
