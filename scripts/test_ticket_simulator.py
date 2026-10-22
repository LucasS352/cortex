"""Regression: exact synchronizer evidence, context and unavailable model."""
import unittest
from unittest.mock import patch, MagicMock

import knowledge_access as access
import ticket_simulator as sim
from curate_sync_130962 import PROBLEM, SOLUTION, DIAGNOSIS, ERROR_ID

LOG = PROBLEM.split('\n', 1)[1]


def historical_row(**overrides):
    return dict(dict(id=30231, source_id='ticket-130962', source_external_id=130962,
                     title='SUP - SNC - Produtos sem integrar | UAPPI',
                     problem=PROBLEM, diagnosis=DIAGNOSIS, solution=SOLUTION,
                     content_for_embedding=PROBLEM, source_type='PROBABLE_TICKET',
                     tier='SILVER', metadata={'reviewed_summary': True}, lexical_rank=0), **overrides)


class SignatureTests(unittest.TestCase):
    def test_fiscal_screenshot_without_numeric_rejection_searches_new_issue(self):
        messages = [{'role': 'client', 'content': LOG},
                    {'role': 'client', 'content': 'Perfeito deu certo obrigado'},
                    {'role': 'client', 'content': 'Agora fui transmitir a nota fiscal e deu erro'},
                    {'role': 'client', 'content': 'Notas Fiscais Eletrônicas (NFe)\n'
                     'Ocorreram os seguintes erros ao enviar a NFe:\n'
                     'Rejeição: IE do destinatário não informada'}]
        with patch.object(sim, '_search', return_value=([], {'query': 'rejeição', 'ms': 1, 'error': ''})) as search:
            result = sim.simulate('Produto não sobe para a Loja Integrada', 'Lucas', messages, {})
        search.assert_called_once()
        self.assertNotEqual(result['origin'], 'coleta_evidencia')
        self.assertNotIn('230 caracteres', search.call_args.args[0])
        self.assertEqual(result['conversation']['active_issue_id'], 2)

    def test_transcribed_platform_error_searches_without_requesting_same_print(self):
        log = ('Sincronizando 4 registro(s) de Produto(s)\n'
               'Erro no retorno da plataforma (15122): O preço custo não pode ser maior que o preço\n'
               'Código do erro: [9A3752E2-SSCE-4FA4-A687-702DC1119E28]')
        with patch.object(sim, '_search', return_value=([], {'query': log, 'ms': 1, 'error': ''})) as search:
            result = sim.simulate('Produto não sobe para a Loja Integrada', 'Lucas',
                [{'role': 'client', 'content': 'Produtos não sobem para a Loja Integrada'},
                 {'role': 'client', 'content': log}], {})
        search.assert_called_once()
        self.assertNotEqual(result['origin'], 'coleta_evidencia')
        self.assertNotIn('Pode enviar um **print', result['reply'])

    def test_unreadable_new_screenshot_does_not_reuse_previous_log(self):
        messages = [{'role': 'client', 'content': LOG},
                    {'role': 'client', 'content': 'Segue o print do outro erro',
                     'meta': {'screenshot_unreadable': True}}]
        with patch.object(sim, '_search') as search:
            result = sim.simulate('Produto não sobe', 'Lucas', messages, {})
        self.assertEqual(result['origin'], 'imagem_ilegivel')
        self.assertEqual(result['sources'], [])
        self.assertIn('mensagem completa', result['reply'])
        search.assert_not_called()

    def test_specific_log_outranks_generic_product_titles(self):
        generic = historical_row(id=1, source_id='ticket-other', title='Não foi possível enviar nenhum Produto - sincronizador',
                                 problem='Produtos não sobem', solution='Ajustar o fabricante no cadastro do produto.',
                                 content_for_embedding='Produtos não sobem', lexical_rank=1)
        ranked = access.rank_rows([generic, historical_row()], access.query_plan(LOG))
        self.assertEqual(ranked[0]['source_external_id'], 130962)
        self.assertEqual(ranked[0]['matched_identifiers'], [ERROR_ID.lower()])
        self.assertTrue(ranked[0]['matched_signatures'])

    def test_changed_request_id_still_matches_warning(self):
        query = LOG.replace(ERROR_ID, 'AAAAAAAA-1111-2222-3333-BBBBBBBBBBBB')
        ranked = access.rank_rows([historical_row()], access.query_plan(query))
        self.assertFalse(ranked[0]['matched_identifiers'])
        self.assertTrue(ranked[0]['matched_signatures'])

    def test_http_400_and_product_numbers_are_not_diagnostic_anchors(self):
        row = historical_row(problem='HTTP 400 Produto 536', content_for_embedding='HTTP 400 Produto 536')
        plan = access.query_plan('HTTP/1.1 400 Bad Request Produto (536)')
        ranked = access.rank_rows([row], plan)
        self.assertFalse(ranked[0]['matched_codes'])
        self.assertFalse(ranked[0]['matched_identifiers'])
        self.assertFalse(ranked[0]['matched_signatures'])

    def test_same_guid_with_different_cause_loses_to_matching_warning(self):
        other = historical_row(id=2, source_id='ticket-other', problem=f'Erro {ERROR_ID}',
                               content_for_embedding=f'Erro {ERROR_ID}', solution='Ajustar fabricante no cadastro')
        matching = historical_row(problem=PROBLEM.replace(ERROR_ID, ''), content_for_embedding='')
        rows = access.rank_rows([other, matching], access.query_plan(LOG))
        self.assertEqual(rows[0]['id'], 30231)


class SimulationTests(unittest.TestCase):
    def approved_source(self):
        source = self.source()
        source.update(context_conflict=False, applicable=True, evidence_status='human_approved_procedure',
                      source_type='OFFICIAL_KB',source_id='canon:qa',metadata={'version_no':1})
        return source

    def source(self, **overrides):
        row = access.rank_rows([historical_row()], access.query_plan(LOG))[0]
        row.update(overrides)
        conn = MagicMock()
        with patch('admin_console._conn', return_value=conn), patch.object(access, 'search', return_value=[row]):
            return sim._search(LOG, set(), 'Loja Integrada')[0][0]

    def run_sim(self, sources, log=LOG, settings=None):
        with patch.object(sim, '_search', return_value=(sources, {'query':log,'ms':1,'error':''})) as search, \
             patch.object(sim.llm_client, 'generate', return_value={'ok':False,'error':'timeout','latency_ms':12}) as generate:
            result = sim.simulate('Produto não sobe para a Loja Integrada', 'Lucas',
                                  [{'role':'client','content':'Produtos não sobem para a Loja Integrada'},
                                   {'role':'client','content':log}], settings or {})
            return result, search, generate

    def test_context_is_preserved_and_conflict_is_explicit(self):
        result, search, generate = self.run_sim([self.source()])
        self.assertIn('Loja Integrada', search.call_args.args[0])
        self.assertEqual(result['origin'], 'validacao_contexto')
        self.assertTrue(result['needs_human'])
        self.assertNotIn(SOLUTION, result['reply'])
        self.assertIn('UAPPI/WELL', result['reply'])
        self.assertIn('LOJA INTEGRADA', result['reply'])
        self.assertNotIn('fabricante', result['reply'])
        self.assertNotIn('base oficial', result['reply'])
        generate.assert_not_called()

    def test_weak_match_never_becomes_solution_even_when_model_available(self):
        source = self.source()
        source.update(exact_match=False, context_conflict=False, excerpt='Ajustar fabricante')
        result, _, generate = self.run_sim([source])
        self.assertEqual(result['origin'], 'triagem_tecnica')
        self.assertTrue(result['needs_human'])
        self.assertTrue(result['handoff_reason'])
        self.assertNotIn('fabricante', result['reply'])
        self.assertFalse(any('Fiz a alteração' in s for s in result['suggested_replies']))
        generate.assert_not_called()

    def test_opaque_identifier_without_matching_warning_is_not_applicable(self):
        source = self.source(matched_signatures=[], matched_identifiers=[ERROR_ID.lower()])
        self.assertFalse(source['exact_match'])

    def test_missing_original_quality_is_not_recomputed(self):
        source = self.source(answer_use='investigation_only', quality_flags=['original_not_linked'])
        self.assertEqual(source['answer_use'], 'investigation_only')
        result, _, generate = self.run_sim([source])
        self.assertEqual(result['origin'], 'triagem_tecnica')
        generate.assert_not_called()

    def test_timeout_does_not_promote_historical_review_to_canonical_procedure(self):
        source = self.source()
        source['context_conflict'] = False
        result, _, generate = self.run_sim([source])
        self.assertEqual(result['origin'],'triagem_tecnica')
        self.assertNotIn(SOLUTION,result['reply'])
        generate.assert_not_called()

    def test_timeout_can_preserve_an_approved_procedure(self):
        source = self.approved_source()
        result, _, generate = self.run_sim([source])
        self.assertEqual(result['origin'], 'base_conhecimento')
        self.assertIn('histórico', result['reply'])
        self.assertNotIn('base oficial', result['reply'])
        self.assertIn(SOLUTION, result['reply'])
        generate.assert_called_once()

    def test_supported_response_includes_selected_source_image_when_model_omits_it(self):
        source = self.approved_source()
        source.update(context_conflict=False,
                      images=['/manual_media/apoio/cadastro-produtos-precos.png'],
                      image_labels={'/manual_media/apoio/cadastro-produtos-precos.png': 'Exemplo ilustrativo; valores de outro cadastro'})
        result, _, _ = self.run_sim([source])
        self.assertIn('![Exemplo ilustrativo; valores de outro cadastro]', result['reply'])

    def test_unsupported_response_does_not_show_procedure_image(self):
        source = self.source()
        source.update(exact_match=False, context_conflict=False,
                      images=['/manual_media/apoio/cadastro-produtos-precos.png'])
        result, _, _ = self.run_sim([source])
        self.assertNotIn('![' , result['reply'])

    def test_timeout_never_pastes_unreviewed_ticket(self):
        source = self.source()
        source.update(context_conflict=False, reviewed_summary=False, excerpt='Boa tarde Luiz, ajustar fabricante')
        result, _, _ = self.run_sim([source])
        self.assertEqual(result['origin'], 'triagem_tecnica')
        self.assertNotIn('Luiz', result['reply'])

    def test_source_excerpt_does_not_cut_procedure_at_420_characters(self):
        solution = ('Contexto técnico. ' * 40) + 'Retirar da fila, alterar e reenviar.'
        self.assertEqual(self.source(solution=solution)['excerpt'], solution)

    def test_empty_search_requires_human(self):
        result, _, generate = self.run_sim([])
        self.assertTrue(result['needs_human'])
        generate.assert_not_called()

    def test_incomplete_primary_cannot_be_replaced_by_secondary(self):
        first = self.source(answer_use='investigation_only')
        second = self.source()
        second.update(id=9, context_conflict=False)
        result, _, generate = self.run_sim([first, second])
        self.assertEqual(result['origin'], 'triagem_tecnica')
        generate.assert_not_called()

    def test_secondary_text_does_not_enter_model_prompt(self):
        first = self.approved_source()
        first.update(context_conflict=False)
        second = self.source()
        second.update(id=9, context_conflict=False, answer_use='investigation_only',
                      excerpt='OTHER_PROCEDURE ' * 20, solution='OTHER_PROCEDURE ' * 20)
        _, _, generate = self.run_sim([first, second])
        self.assertNotIn('OTHER_PROCEDURE', generate.call_args.args[0])

    def test_followup_confirmation_keeps_log_and_updates_integrator(self):
        source = self.approved_source()
        source['context_conflict'] = False
        with patch.object(sim, '_search', return_value=([source], {'query':LOG,'ms':1,'error':''})) as search, \
             patch.object(sim.llm_client, 'generate', return_value={'ok':False,'error':'timeout','latency_ms':12}):
            result = sim.simulate('Loja Integrada: produto não sobe', 'Lucas', [
                {'role':'client','content':LOG},
                {'role':'client','content':'Conferi a coluna, é UAPPI.'}], {})
        self.assertIn(ERROR_ID, search.call_args.args[0])
        self.assertEqual(search.call_args.args[2], 'Conferi a coluna, é UAPPI.')
        self.assertEqual(result['origin'], 'base_conhecimento')

    def test_initial_playbook_still_collects_log(self):
        settings = {'specialists':[{'id':'integrations','name':'Integrações','enabled':True}]}
        with patch.object(sim, 'load_playbooks', return_value=sim.DEFAULT_PLAYBOOKS), \
             patch.object(sim, '_search') as search:
            result = sim.simulate('Produto não sobe para a Loja Integrada', 'Lucas',
                                  [{'role':'client','content':'Produtos não sobem para a Loja Integrada'}], settings)
        self.assertEqual(result['origin'], 'playbook')
        self.assertIn('ocorrencias-1.png', result['reply'])
        search.assert_not_called()


if __name__ == '__main__':
    unittest.main()
