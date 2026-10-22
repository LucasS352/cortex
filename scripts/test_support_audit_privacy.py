import unittest
from sanitize_support_audit import identity_matcher,transform


class AuditPrivacyTests(unittest.TestCase):
    def test_masks_client_names_without_erasing_technical_brands_or_ids(self):
        matcher=identity_matcher(['Empresa Exemplo','Pessoa Teste','Master','AnyMarket'])
        result=transform({'ticket':42,'source_id':'canon:42',
                          'original_question':'Pessoa Teste da Empresa Exemplo usa Master com AnyMarket.',
                          'justifications':{'understanding':'Pessoa Teste descreveu o erro.'}},matcher)
        self.assertEqual(result['ticket'],42)
        self.assertEqual(result['source_id'],'canon:42')
        self.assertNotIn('Pessoa Teste',result['original_question'])
        self.assertNotIn('Empresa Exemplo',result['original_question'])
        self.assertIn('Master com AnyMarket',result['original_question'])
        self.assertNotIn('Pessoa Teste',result['justifications']['understanding'])

    def test_private_recording_link_and_access_login_are_masked(self):
        result=transform({'original_question':'Login: acesso_privado\nhttps://fathom.video/share/chave_privada'}, {})
        self.assertNotIn('acesso_privado',result['original_question'])
        self.assertNotIn('chave_privada',result['original_question'])
        nested=transform({'metadata':{'source_url':'https://fathom.video/share/chave_privada'}},{})
        self.assertNotIn('chave_privada',nested['metadata']['source_url'])

    def test_generic_support_roles_are_not_treated_as_customer_names(self):
        matcher=identity_matcher(['Suporte','Atendimento','Vendas','Contato'])
        text='Suporte e atendimento: vendas entrou em contato.'
        self.assertEqual(transform({'reply':text},matcher)['reply'],text)


if __name__=='__main__':
    unittest.main()
