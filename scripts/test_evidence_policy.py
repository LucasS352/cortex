import unittest
from unittest.mock import patch
from types import SimpleNamespace

from knowledge_access import applicability, quality, assessment, query_plan
from triage_synthesizer import TriageSynthesizer
from romario_dialogue import RomarioDialogueEngine
import knowledge_index
import simulator_app


def source(**kwargs):
    row=dict(id=1,source_type='PROBABLE_TICKET', title='Erro no envio do produto',
             problem='Código 400 - O atributo marca é obrigatório',
             solution='No cadastro do produto, preencher o atributo marca e conferir o resultado do reenvio.',
             tier='GOLD',confidence_score=.99)
    row.update(kwargs)
    return row


class EvidencePolicyTests(unittest.TestCase):
    def test_diagnostic_message_is_generic_not_ticket_specific(self):
        query='Código 400 - O atributo marca é obrigatório'
        self.assertTrue(query_plan(query)['signatures'])
        self.assertTrue(applicability(source(),query)['applicable'])
        self.assertFalse(applicability(source(problem='Produto sem foto'),query)['applicable'])

    def test_generic_http_status_does_not_authorize_correction(self):
        self.assertFalse(applicability(source(), 'HTTP/1.1 400 Bad Request')['applicable'])

    def test_rejection_numbers_are_compared_not_product_ids(self):
        row=source(problem='Rejeição 539: duplicidade')
        self.assertTrue(applicability(row,'Rejeição 539')['applicable'])
        self.assertFalse(applicability(row,'Rejeição 204')['applicable'])

    def test_integrator_conflict_blocks_even_with_matching_message(self):
        row=source(title='VTEX: produto sem marca')
        decision=applicability(row,'Loja Integrada\nCódigo 400 - O atributo marca é obrigatório')
        self.assertFalse(decision['applicable'])
        self.assertIn('integration_context_mismatch',decision['applicability_reasons'])

    def test_gold_and_database_change_do_not_authorize_procedure(self):
        self.assertEqual(quality(source(solution='Execute UPDATE produtos SET marca=1 e então reenvie os produtos.'))['answer_use'],'investigation_only')
        self.assertEqual(quality(source(hard_block_reason='ISSUE_PERSISTED_OR_REOPENED'))['answer_use'],'investigation_only')

    def test_inapplicable_primary_is_not_replaced_by_weaker_source(self):
        rows=[dict(answer_use='supporting_reference',applicable=False),
              dict(answer_use='procedure_reference',applicable=True)]
        self.assertFalse(assessment(rows)['can_support_procedure'])

    def test_unrelated_natural_language_query_does_not_pass(self):
        self.assertFalse(applicability(source(),'Como configurar boleto bancário')['applicable'])

    def test_legacy_synthesis_has_no_source_free_fiscal_template(self):
        result=TriageSynthesizer.synthesize('Erro NF-e','Rejeição 539',error_code='539')
        self.assertEqual(result['procedure_steps'],[])
        self.assertEqual(result['shortcuts'],[])
        self.assertNotIn('%',result['confidence'])
        self.assertTrue(result['needs_human'])

    def test_unreviewed_historical_conversation_is_not_pasted(self):
        result=TriageSynthesizer.synthesize('Erro','Código 400 - O atributo marca é obrigatório',
            search_results=[source(solution='Boa tarde Luiz! Preencher o atributo marca no cadastro e retransmitir.')])
        self.assertNotIn('Luiz',result['client_ready_response'])
        self.assertEqual(result['procedure_steps'],[])

    def test_official_matching_procedure_stays_available(self):
        result=TriageSynthesizer.synthesize('Erro','Código 400 - O atributo marca é obrigatório',
            search_results=[source(source_type='OFFICIAL_KB',evidence_status='human_approved_procedure',
                                   answer_use='procedure_reference',metadata={'version_no':1})])
        self.assertEqual(len(result['procedure_steps']),1)
        self.assertNotIn('%',result['confidence'])

    def test_official_type_alone_does_not_certify_a_procedure(self):
        result=TriageSynthesizer.synthesize('Erro','Código 400 - O atributo marca é obrigatório',
            search_results=[source(source_type='OFFICIAL_KB')])
        self.assertEqual(result['procedure_steps'],[])

    def test_browser_supplied_approval_is_not_trusted_for_draft(self):
        forged = source(source_type='OFFICIAL_KB',evidence_status='human_approved_procedure',
                        answer_use='procedure_reference',metadata={'version_no':999})
        req=simulator_app.DraftRequest(subject='Erro',problem='Código 400 - O atributo marca é obrigatório',top_results=[forged])
        with patch.object(simulator_app,'get_agent_admin_state',return_value={'source_types':['OFFICIAL_KB']}), \
             patch('technician_workbench.connection'), patch('knowledge_access.approved_procedures',return_value=[]), \
             patch('mapped_replies.match',return_value=None), patch('llm_client.generate') as model:
            result=simulator_app._generate_draft(req)
        self.assertFalse(result['decision']['can_propose_procedure'])
        model.assert_not_called()

    def test_dialogue_does_not_invent_duplicate_invoice_steps(self):
        session=SimpleNamespace(turns=[SimpleNamespace(role='user',content='Erro Rejeição 539')])
        state=SimpleNamespace(intent='erro_nota_fiscal',evidence=[])
        result=RomarioDialogueEngine.generate_response(session,state,top_candidates=[])
        self.assertNotIn('F9',result)
        self.assertNotIn('Vendas > Faturamento',result)

    def test_draft_does_not_call_model_for_wrong_source(self):
        req=simulator_app.DraftRequest(subject='Erro financeiro',problem='Erro: boleto não registrado no banco',top_results=[source()])
        with patch.object(simulator_app,'get_agent_admin_state',return_value={'source_types':['PROBABLE_TICKET']}), \
             patch('technician_workbench.connection') as database, \
             patch('llm_client.generate') as generate:
            result=simulator_app._generate_draft(req)
        self.assertTrue(result['needs_human'])
        generate.assert_not_called()
        database.assert_not_called()

    def test_coverage_check_is_throttled_and_retries_after_failure(self):
        with patch.object(knowledge_index,'_checked_at',0),patch.object(knowledge_index.time,'monotonic',return_value=100), \
             patch.object(knowledge_index,'sync_missing',side_effect=[RuntimeError('DB unavailable'),{'inserted':2}]) as sync:
            with self.assertRaises(RuntimeError): knowledge_index.ensure_coverage()
            self.assertEqual(knowledge_index.ensure_coverage()['inserted'],2)
            knowledge_index.ensure_coverage()
            self.assertEqual(sync.call_count,2)


if __name__=='__main__': unittest.main()
