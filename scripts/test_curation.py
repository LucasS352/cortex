import copy
import unittest
from curation import clean, signatures, risk_flags, make_source, locate, validate_card, search


class CurationTests(unittest.TestCase):
    def test_sql_and_technical_objects_preserved(self):
        self.assertIn("x < 5",clean("<p>SELECT x FROM T WHERE x < 5</p>"))
        sig=signatures("ORA-00942 select * from SPT_VW_FATURA_PEDIDO_ECOMMERCE")
        self.assertEqual(sig["codes"],["ORA-00942"])
        self.assertEqual(sig["objects"],["SPT_VW_FATURA_PEDIDO_ECOMMERCE"])

    def test_rejection_not_arbitrary_number(self):
        self.assertEqual(signatures("pedido 694")['codes'],[])
        self.assertIn('SEFAZ:694',signatures('694-Rejeição: Não informado')['codes'])

    def test_evidence_cannot_be_fabricated_or_changed(self):
        s=make_source('tenant','ticket',1,'Case',[{'id':'action:1','text':'Tabela ausente neste cliente.'}],None)
        evidence=locate(s,'Tabela ausente')
        c={'key':'test','claims':[{'evidence':evidence}]}
        validate_card(c,{'ticket:1':s})
        with self.assertRaises(ValueError): locate(s,'Problema corrigido')
        altered=copy.deepcopy(c);altered['claims'][0]['evidence']['quote']='Corrigido'
        with self.assertRaises(ValueError): validate_card(altered,{'ticket:1':s})

    def test_risky_legacy_guidance_flagged(self):
        self.assertIn('unsafe_generalization_review',risk_flags('IE 00000000000'))
        self.assertIn('contains_database_mutation',risk_flags('DELETE FROM NOTAFISCAL'))

    def test_draft_excluded_from_approved_search(self):
        c={'key':'x','title':'Erro XML','aliases':['qryXMLSTF'],'summary':'XML',
           'claims':[],'kind':'curated','status':'draft'}
        self.assertTrue(search([c],'qryXMLSTF'))
        self.assertEqual(search([c],'qryXMLSTF',True),[])


if __name__=='__main__':unittest.main()
