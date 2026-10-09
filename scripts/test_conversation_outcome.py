import unittest
from types import SimpleNamespace
from unittest.mock import patch

from conversation_outcome import classify_outcome, RESOLVED_REPLY
from romario_dialogue import RomarioDialogueEngine
import ticket_simulator as sim


class OutcomeTests(unittest.TestCase):
    def test_explicit_success_and_typo(self):
        for text in ('perfeito, arrumei a descrição aqui e deu ceerto, obrigado',
                     'Deu certo, obrigado!', 'Funcionou, muito obrigado pela ajuda!',
                     'O erro foi resolvido, obrigado', 'Consegui resolver',
                     'Voltou a funcionar', 'Agora está funcionando sem erros',
                     'Deu certo, não aparece mais erro.'):
            with self.subTest(text=text):
                self.assertEqual(classify_outcome(text), 'resolved')

    def test_thanks_does_not_prove_success(self):
        for text in ('Obrigado', 'Muito obrigado, vou testar', 'obrigado, ainda vou testar',
                     'Obrigado, ainda não testei', 'Perfeito, obrigado pela ajuda'):
            with self.subTest(text=text):
                self.assertEqual(classify_outcome(text), 'acknowledged')

    def test_pending_failed_partial_or_question_is_not_resolved(self):
        for text in ('Não deu certo, obrigado', 'Obrigado, mas continua o erro',
                     'Deu certo, mas apareceu outro erro', 'Funcionou só para um produto',
                     'Acho que deu certo', 'Se deu certo, posso reenviar?',
                     'Deu certo?', 'Deu certo, agora preciso configurar outra loja',
                     'O problema ainda não foi resolvido', 'Ontem funcionou, hoje voltou o erro',
                     'O log diz "resolvido", mas não funcionou'):
            with self.subTest(text=text):
                self.assertIsNone(classify_outcome(text))

    def history(self, text):
        return [{'role':'client','content':'HTTP/1.1 400 Bad Request'},
                {'role':'agent','content':'Confirme o integrador e a descrição curta.'},
                {'role':'client','content':text}]

    def test_success_bypasses_search_and_model_and_clears_pending_review(self):
        with patch.object(sim, '_search') as search, patch.object(sim.llm_client, 'generate') as model:
            result=sim.simulate('Produto não sobe para Loja Integrada', 'Lucas',
                self.history('perfeito, arrumei a descrição aqui e deu ceerto, obrigado'), {})
        self.assertEqual(result['reply'],RESOLVED_REPLY)
        self.assertEqual(result['stage'],'resolved')
        self.assertEqual(result['meta']['stage'],'resolved')
        self.assertFalse(result['needs_human'])
        self.assertEqual(result['handoff_reason'],'')
        self.assertEqual(result['sources'],[])
        self.assertEqual(result['suggested_replies'],[])
        search.assert_not_called(); model.assert_not_called()

    def test_acknowledgement_keeps_validation_pending_without_repeating_diagnosis(self):
        with patch.object(sim, '_search') as search:
            result=sim.simulate('Produto não sobe', 'Lucas', self.history('Obrigado, vou testar'), {})
        self.assertEqual(result['stage'],'awaiting_validation')
        self.assertNotIn('deu certo',result['reply'])
        search.assert_not_called()

    def test_new_failure_after_resolution_resumes_investigation(self):
        messages=self.history('Funcionou, obrigado')
        messages += [{'role':'agent','content':RESOLVED_REPLY,'meta':{'stage':'resolved'}},
                     {'role':'client','content':'O erro voltou, não funcionou'}]
        with patch.object(sim,'_search',return_value=([],{'query':'erro','ms':0,'error':''})) as search:
            result=sim.simulate('Produto não sobe','Lucas',messages,{})
        self.assertNotEqual(result['stage'],'resolved')
        search.assert_called_once()

    def test_thanks_after_closing_does_not_reopen(self):
        messages=self.history('Funcionou, obrigado')
        messages += [{'role':'agent','content':RESOLVED_REPLY,'meta':{'stage':'resolved'}},
                     {'role':'client','content':'Obrigado'}]
        with patch.object(sim,'_search') as search:
            result=sim.simulate('Produto não sobe','Lucas',messages,{})
        self.assertEqual(result['stage'],'resolved')
        search.assert_not_called()

    def test_legacy_dialogue_also_prioritizes_latest_success(self):
        session=SimpleNamespace(turns=[SimpleNamespace(role='user',content='Erro HTTP 400'),
                                     SimpleNamespace(role='user',content='Deu certo, obrigado')])
        state=SimpleNamespace(intent='erro_integracao',evidence=[])
        self.assertEqual(RomarioDialogueEngine.generate_response(session,state),RESOLVED_REPLY)


if __name__=='__main__': unittest.main()
