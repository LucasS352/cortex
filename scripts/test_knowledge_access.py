import unittest
from unittest.mock import MagicMock, patch
from knowledge_access import query_plan,rank_rows,quality,search,assessment,applicability,report_topic_match
from tech_evidence import detail


class AccessTests(unittest.TestCase):
    def setUp(self):
        # Retrieval unit tests use a fake connection; schema/index preparation
        # must not open a second, real database connection.
        for target in ('knowledge_access.knowledge_index.ensure_coverage', 'knowledge_access.curation.ensure_schema'):
            helper = patch(target)
            helper.start()
            self.addCleanup(helper.stop)
        approved = patch('knowledge_access.approved_procedures', return_value=[])
        approved.start()
        self.addCleanup(approved.stop)

    def test_current_approved_lane_precedes_historical_search(self):
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = [{'id':'tenant'}]
        approved = {'id':2,'source_id':'canon:2','metadata':{'version_no':3}}
        with patch('knowledge_access.approved_procedures',return_value=[approved]):
            self.assertEqual(search(connection,'Forma de pagamento não integrada'),[approved])
        self.assertEqual(connection.execute.call_count,1)

    def test_conversational_noise_and_common_typo_do_not_hide_fiscal_subject(self):
        plan = query_plan('erro de inscrição estadual do destinatio invalida ta dando isso ao tentar emitir a nota')
        self.assertIn('destinatario', plan['terms'])
        self.assertNotIn('destinatio', plan['terms'])
        self.assertNotIn('dando', plan['terms'])
        self.assertNotIn('tentar', plan['terms'])

    def test_monthly_sales_margin_maps_to_markup_without_conversational_noise(self):
        plan = query_plan('agora preciso de um relatório que mostra a margem das minhas vendas do mês')
        self.assertEqual(plan['terms'], ['relatorio', 'margem', 'vendas'])
        self.assertIn('markup', plan['expanded_terms'])
        self.assertNotIn('agora', plan['terms'])
        self.assertNotIn('mes', plan['terms'])
        self.assertTrue(plan['report'])

    def test_generic_greeting_is_not_a_report_intent(self):
        self.assertFalse(query_plan('Olá, preciso de ajuda')['report'])

    def test_report_ranking_prefers_official_usage_article_over_error_article(self):
        rows = [
            {'id': 1, 'title': 'DEV - ERR - Relatório de vendas',
             'solution': 'Descrição suficientemente longa sobre divergência no relatório de vendas.',
             'source_type': 'OFFICIAL_KB', 'lexical_rank': 1},
            {'id': 2, 'title': 'Relatório de vendas por produto',
             'solution': 'Descrição suficientemente longa com filtros e colunas do relatório de vendas.',
             'source_type': 'OFFICIAL_KB', 'lexical_rank': 1},
        ]
        ranked = rank_rows(rows, query_plan('relatório de vendas por produto'))
        self.assertEqual(ranked[0]['id'], 2)

    def test_ticket_service_is_a_ranking_clue(self):
        rows = [
            {'id': 1, 'title': 'Relatório de vendas por produto', 'source_type': 'PROBABLE_TICKET',
             'solution': 'Abra a tela e filtre as vendas pelos produtos desejados para conferir o relatório.',
             'metadata': {'service_full': 'Dúvidas de Relatórios'}},
            {'id': 2, 'title': 'Relatório de vendas por produto', 'source_type': 'PROBABLE_TICKET',
             'solution': 'Abra a tela e filtre as vendas pelos produtos desejados para conferir o relatório.',
             'metadata': {'service_full': 'Dúvidas de Estoque'}},
        ]
        ranked = rank_rows(rows, query_plan('relatório de vendas por produto'),
                           service_hint='Dúvidas de Relatórios')
        self.assertEqual(ranked[0]['id'], 1)

    def test_report_catalog_matches_business_name_not_printing_margin(self):
        self.assertTrue(report_topic_match(
            query_plan('relatório da margem das vendas'), 'Gerenciamento de Markup de Vendas'))
        self.assertTrue(report_topic_match(
            query_plan('vendas por produto'), 'Como utilizar o relatório de estatística de vendas'))
        self.assertFalse(report_topic_match(
            query_plan('margem da etiqueta FR3'), 'Gerenciamento de Markup de Vendas'))

    def test_distinct_report_topics_do_not_share_a_generic_noun(self):
        self.assertFalse(report_topic_match(
            query_plan('como consultar estatística de vendas'), 'Estatística de Frete'))
        self.assertFalse(report_topic_match(
            query_plan('como consultar estatística de vendas'), 'Vendas por Produto'))
        self.assertFalse(report_topic_match(
            query_plan('onde vejo curva ABC dos produtos'), 'Curva ABC de Clientes'))
        self.assertFalse(report_topic_match(
            query_plan('posição de estoque por empresa'), 'Controle de Estoque'))
        self.assertTrue(report_topic_match(
            query_plan('posição de estoque por empresa'), 'Posição de Estoque Multiempresa'))
        self.assertTrue(query_plan('como vejo comissão de vendas por vendedor')['report'])

    def test_markup_article_is_applicable_to_sales_margin_request(self):
        source = {
            'title': 'Relatório Gerenciamento de Markup de Vendas',
            'solution': 'O relatório mostra o lucro líquido de cada venda no período selecionado.',
        }
        result = applicability(source, 'relatório que mostra a margem das vendas do mês')
        self.assertTrue(result['applicable'])

    def test_similar_fiscal_topic_with_different_rejection_is_not_a_procedure(self):
        source = {'title': 'SEFAZ não permite Contribuinte Isento de Inscrição Estadual',
                  'solution': 'Preencher a IE do destinatário e validar a nota.'}
        result = applicability(source, 'inscrição estadual do destinatário inválida ao emitir a nota')
        self.assertFalse(result['applicable'])
        self.assertIn('reported_symptom_not_matched', result['applicability_reasons'])

    def test_numbers_are_not_automatically_rejections(self):
        self.assertEqual(query_plan('pedido 694 cliente 539')['codes'],[])
        self.assertEqual(query_plan('694-Rejeição: teste')['codes'],['rejection:694'])

    def test_unknown_identifier_never_falls_back_to_generic_manual(self):
        rows=[dict(id=1,title='Manual Oracle',solution='Uma solução suficientemente longa para este teste de busca',source_type='MANUAL',lexical_rank=1)]
        self.assertEqual(rank_rows(rows,query_plan('ORA-00942 SFT_TABELA_INEXISTENTE')),[])

    def test_underscore_is_literal_not_wildcard(self):
        c=MagicMock();c.execute.return_value.fetchall.side_effect=[[{'id':'tenant'}],[],[]]
        search(c,'SFT_XML_NOTA')
        sql,args=next(call.args for call in c.execute.call_args_list if ' ILIKE ' in call.args[0])
        self.assertNotIn('SFT_XML_NOTA',sql)
        self.assertEqual(args[0],'%sft\\_xml\\_nota%')

    def test_empty_query_never_returns_all_rows(self):
        c=MagicMock();c.execute.return_value.fetchall.return_value=[{'id':'tenant'}]
        self.assertEqual(search(c,'como eu faço'),[])
        self.assertEqual(c.execute.call_count,1)

    def test_semantic_search_uses_halfvec_index_expression(self):
        class Result:
            def __init__(self, rows): self.rows = rows
            def fetchall(self): return self.rows

        class Connection:
            def __init__(self): self.calls = []
            def execute(self, sql, params=None):
                self.calls.append((sql, params))
                return Result([{'id': 'tenant'}] if 'SELECT id FROM tenants' in sql else [])

        connection = Connection()
        search(connection, 'transportadora AnyMarket', embedding=[0.0] * 3072)
        semantic_calls = [call for call in connection.calls if 'halfvec(3072)' in call[0]]
        self.assertEqual(len(semantic_calls), 1)
        sql, params = semantic_calls[0]
        self.assertIn('embedding_generated IS TRUE', sql)
        self.assertEqual(len(params), 2)

    def test_invalid_embedding_dimension_falls_back_to_lexical_search(self):
        c=MagicMock();c.execute.return_value.fetchall.side_effect=[[{'id':'tenant'}], [], [], [], []]
        search(c, 'transportadora AnyMarket', embedding=[0.0] * 10)
        self.assertFalse(any('halfvec(3072)' in call.args[0] for call in c.execute.call_args_list))

    def test_closure_and_gold_do_not_imply_usable_procedure(self):
        q=quality(dict(solution='Sistema atualizado. Favor testar novamente.',source_type='VALIDATED_TICKET',tier='GOLD'))
        self.assertEqual(q['answer_use'],'investigation_only')
        self.assertIn('generic_resolution',q['quality_flags'])

    def test_missing_primary_document_cannot_be_replaced_by_weaker_match(self):
        rows=[{'answer_use':'investigation_only','quality_flags':['video_without_transcript']},
              {'answer_use':'procedure_reference','quality_flags':[]}]
        self.assertFalse(assessment(rows)['can_support_procedure'])

    def test_unsafe_fiscal_shortcuts_not_generalized(self):
        self.assertEqual(quality({'solution':'Preencha a inscrição estadual com 00000000000 e retransmita a nota fiscal','source_type':'MANUAL'})['answer_use'],'investigation_only')

    def test_kb_detail_reads_kb_not_ticket_with_same_number(self):
        c=MagicMock();c.execute.return_value.fetchall.return_value=[{'id':'tenant'}]
        c.execute.return_value.fetchone.side_effect=[{'id':1,'source_type':'OFFICIAL_KB','metadata':{'external_id':207376}},
          {'external_id':207376,'content_text':'Procedimento original','content_html':None,'updated_at_source':'2026-09-24'}]
        result=detail(c,1)
        self.assertEqual(result['actions'][0]['text'],'Procedimento original')
        self.assertIn('raw_kb_articles',c.execute.call_args.args[0])


if __name__=='__main__':unittest.main()
