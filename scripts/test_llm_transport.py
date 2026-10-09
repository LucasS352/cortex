import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import llm_client


class TransportTests(unittest.TestCase):
    def test_truncated_generation_is_not_published(self):
        with self.assertRaisesRegex(ValueError, 'MAX_TOKENS'):
            llm_client._response_text({'candidates': [{'finishReason': 'MAX_TOKENS',
                'content': {'parts': [{'text': 'Produtos 59126, 40193, 5'}]}}]})

    def test_complete_response_keeps_all_text_parts(self):
        result = llm_client._response_text({'candidates': [{'finishReason': 'STOP',
            'content': {'parts': [{'text': 'interno', 'thought': True},
                                 {'text': 'Explicação completa.'}, {'text': 'Passo final.'}]}}]})
        self.assertEqual(result, 'Explicação completa.\nPasso final.')

    def test_curl_uses_utf8_and_stdin_for_long_prompts(self):
        reply='Olá! Você pode gerar o relatório.'
        response=SimpleNamespace(returncode=0,stdout=json.dumps({'candidates':[{'content':{'parts':[{'text':reply}]}}]},ensure_ascii=False))
        prompt='Contexto extenso com acentuação. '*2000
        with patch('shutil.which',return_value='curl.exe'), patch('subprocess.run',return_value=response) as run:
            self.assertEqual(llm_client._call(prompt,.2,2048),reply)
        self.assertEqual(run.call_args.kwargs['encoding'],'utf-8')
        self.assertIn('@-',run.call_args.args[0])
        self.assertNotIn(prompt,run.call_args.args[0])
        self.assertEqual(json.loads(run.call_args.kwargs['input'])['contents'][0]['parts'][0]['text'],prompt)


if __name__=='__main__': unittest.main()
