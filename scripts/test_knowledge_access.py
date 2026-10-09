import unittest
from unittest.mock import MagicMock
from knowledge_access import query_plan,rank_rows,quality,search,assessment,applicability
from tech_evidence import detail


class AccessTests(unittest.TestCase):
    def test_conversational_noise_and_common_typo_do_not_hide_fiscal_subject(self):
        plan = query_plan('erro de inscrição estadual do destinatio invalida ta dando isso ao tentar emitir a nota')
        self.assertIn('destinatario', plan['terms'])
        self.assertNotIn('destinatio', plan['terms'])
        self.assertNotIn('dando', plan['terms'])
        self.assertNotIn('tentar', plan['terms'])

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
