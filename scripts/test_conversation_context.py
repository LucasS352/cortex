import unittest
from unittest.mock import patch
from conversation_context import active_context, concrete_evidence
import ticket_simulator as sim

LOG='ATENÇÃO! A descrição curta tem mais que 230 caracteres e foi ajustado.\nHTTP/1.1 400 Bad Request'
SUBJECT='Produto não sobe para a Loja Integrada'


def history():
    return [{'role':'client','content':'Produto não sobe para Loja Integrada'},
            {'role':'agent','content':'Envie a ocorrência'},
            {'role':'client','content':LOG},
            {'role':'agent','content':'Confira a descrição'},
            {'role':'client','content':'entendi, perfeito, deu certo! obrigado'},
            {'role':'agent','content':'Que bom que deu certo!','meta':{'stage':'resolved'}},
            {'role':'client','content':'putz cara, agora tô com outro erro, só que de nota fiscal agora'}]


class ContextTests(unittest.TestCase):
    def test_new_fiscal_issue_archives_integration_evidence(self):
        issue,state=active_context(SUBJECT,history())
        self.assertEqual(issue.id,2)
        self.assertEqual(state['issues'][0]['status'],'resolved')
        self.assertIn('fiscal',issue.modules)
        self.assertNotIn(LOG,[m['content'] for m in issue.messages])
        self.assertNotIn('Loja Integrada',issue.subject)

    def test_fiscal_collection_does_not_search_old_log(self):
        with patch.object(sim,'_search') as search, patch.object(sim.llm_client,'generate') as model:
            result=sim.simulate(SUBJECT,'Lucas',history(),{'specialists':[{'id':'fiscal','name':'Fiscal','enabled':True}]})
        self.assertEqual(result['origin'],'coleta_evidencia')
        self.assertEqual(result['stage'],'evidence_collection')
        self.assertEqual(result['specialist']['id'],'fiscal')
        self.assertIn('print',result['reply'])
        self.assertIn('texto completo',result['reply'])
        self.assertNotIn('descrição curta',result['reply'])
        search.assert_not_called();model.assert_not_called()

    def test_fiscal_log_only_searches_current_issue(self):
        messages=history()+[{'role':'agent','content':'Envie o print ou o erro escrito'},
                            {'role':'client','content':'Rejeição 539: Duplicidade de NF-e'}]
        with patch.object(sim,'_search',return_value=([],{'query':'fiscal','ms':0,'error':''})) as search:
            sim.simulate(SUBJECT,'Lucas',messages,{})
        query=search.call_args.args[0]
        self.assertIn('539',query)
        self.assertNotIn('230',query)
        self.assertNotIn('Loja Integrada',query)

    def test_implicit_domain_change_before_resolution(self):
        messages=history()[:4]+[{'role':'client','content':'Estou com erro na nota fiscal'}]
        issue,state=active_context(SUBJECT,messages)
        self.assertEqual(issue.id,2)
        self.assertEqual(state['issues'][0]['status'],'open')

    def test_log_about_product_is_still_integration_evidence(self):
        issue,_=active_context(SUBJECT,history()[:4])
        self.assertEqual(issue.id,1)
        self.assertIn(LOG,[m['content'] for m in issue.messages])

    def test_related_invoice_integration_stays_same_issue(self):
        issue,_=active_context(SUBJECT,history()[:4]+[{'role':'client','content':'A nota fiscal também não sobe para a Loja Integrada'}])
        self.assertEqual(issue.id,1)

    def test_new_error_same_module_does_not_reuse_old_log(self):
        issue,_=active_context(SUBJECT,history()[:4]+[{'role':'client','content':'Agora é outro erro na Loja Integrada'}])
        self.assertEqual(issue.id,2)
        self.assertNotIn(LOG,[m['content'] for m in issue.messages])

    def test_ambiguous_topic_requests_clarification(self):
        with patch.object(sim,'_search') as search:
            result=sim.simulate(SUBJECT,'Lucas',history()[:4]+[{'role':'client','content':'E a nota fiscal?'}],{})
        self.assertEqual(result['origin'],'clarificacao')
        search.assert_not_called()

    def test_explicit_resume_restores_correct_issue_only(self):
        issue,state=active_context(SUBJECT,history()+[{'role':'client','content':'Voltando à Loja Integrada, o mesmo erro voltou'}])
        self.assertEqual(issue.id,1)
        self.assertEqual(state['transition'],'resume')
        self.assertIn(LOG,[m['content'] for m in issue.messages])
        self.assertNotIn(history()[-1],issue.messages)

    def test_new_howto_after_closure_has_own_subject(self):
        issue,_=active_context(SUBJECT,history()[:-1]+[{'role':'client','content':'Como vejo relatório de vendas por produto?'}])
        self.assertEqual(issue.id,2)
        self.assertNotIn('Loja Integrada',issue.subject)

    def test_bare_error_is_not_technical_evidence(self):
        for text in ('erro de nota fiscal','deu erro','produto 536','ocorrências'):
            self.assertFalse(concrete_evidence(text))
        self.assertTrue(concrete_evidence('Rejeição 539: Duplicidade'))
        self.assertTrue(concrete_evidence('Rejeição: IE do destinatário não informada'))

    def test_transcribed_platform_error_is_evidence_even_with_ocr_damaged_id(self):
        self.assertTrue(concrete_evidence(
            'Erro no retorno da plataforma (15122): O preço custo não pode ser maior que o preço\n'
            'Código do erro: [9A3752E2-SSCE-4FA4-A687-702DC1119E28]'))


if __name__=='__main__': unittest.main()
