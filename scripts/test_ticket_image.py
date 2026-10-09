import base64
import io
import unittest
from unittest.mock import patch
from PIL import Image
from ticket_image import transcribe, focused_text, visual_evidence


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.provider = patch('ticket_image.llm_client.status', return_value={'available': False})
        self.provider.start()
        self.addCleanup(self.provider.stop)

    def test_focus_preserves_rejection_without_customer_details(self):
        text = focused_text('Notas Fiscais Eletrônicas (NFe)\nJAPI S/A\nCPF/CNPJ\nRua Huber 370\n'
            'Ocorreram os seguintes erros ao enviar a NFe:\nRejeição: IE do destinatário não informada\n'
            '8681->Rejeição: IE do destinatário não informada\nSalvar\nSair')
        self.assertIn('Rejeição: IE do destinatário não informada', text)
        self.assertEqual(text.count('Rejeição:'), 1)
        for noise in ('JAPI', 'CPF/CNPJ', 'Rua Huber', 'Salvar', 'Sair'):
            self.assertNotIn(noise, text)

    def test_visual_reader_returns_only_visible_evidence(self):
        with patch('ticket_image.llm_client.generate', return_value={'ok': True, 'text':
            '{"screen":"Notas Fiscais Eletrônicas (NFe)","error_text":"Rejeição: IE do destinatário não informada"}'}) as model:
            result = visual_evidence(b'image', 'image/png')
        self.assertEqual(result['error'], 'Rejeição: IE do destinatário não informada')
        self.assertEqual(model.call_args.kwargs['image']['mimeType'], 'image/png')
    def image(self):
        buf=io.BytesIO();Image.new('RGB',(30,30),'white').save(buf,format='PNG')
        return 'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()

    def test_ocr_returns_verbatim_text_for_confirmation(self):
        with patch('winocr.recognize_pil_sync',return_value={'text':'Rejeição 539: Duplicidade de NF-e'}):
            result=transcribe(self.image())
        self.assertEqual(result['text'],'Rejeição 539: Duplicidade de NF-e')
        self.assertTrue(result['requires_confirmation'])
        self.assertNotIn('diagnosis',result)

    def test_unreadable_image_does_not_invent_error(self):
        with patch('winocr.recognize_pil_sync',return_value={'text':''}): result=transcribe(self.image())
        self.assertEqual(result['text'],'')
        self.assertIn('não consegui',result['notice'])

    def test_lines_preserve_log_structure_and_small_image_is_enlarged(self):
        with patch('winocr.recognize_pil_sync', return_value={'lines': [
                {'text': 'HTTP 404'}, {'text': 'Not found shipment invoice'}], 'text': ''}) as ocr:
            result = transcribe(self.image())
        self.assertEqual(result['text'], 'HTTP 404\nNot found shipment invoice')
        self.assertEqual(ocr.call_args.args[0].size, (60, 60))

    def test_invalid_image_and_non_image_rejected(self):
        for value in ('data:text/html;base64,PHNjcmlwdD4=', 'https://example.test/a.png', 'data:image/png;base64,YWJj'):
            with self.assertRaises(ValueError): transcribe(value)

    def test_ocr_unavailable_is_explicit(self):
        with patch('winocr.recognize_pil_sync',side_effect=RuntimeError('unavailable')):
            result = transcribe(self.image())
        self.assertEqual(result['text'], '')
        self.assertIn('copie o texto', result['notice'])


if __name__=='__main__': unittest.main()
