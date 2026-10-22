import unittest
from unittest.mock import Mock, patch

import evaluate_real_support as replay


class HistoricalReplayTests(unittest.TestCase):
    def test_generation_phase_never_invokes_the_reviewer(self):
        entry={'ticket':1}
        with patch.object(replay,'run_case',return_value={'ticket':1}) as generate, \
             patch.object(replay,'review_result') as review:
            _,before,after=replay.evaluate_planned((entry,'HEAD',None,True))
        self.assertEqual(generate.call_count,2)
        review.assert_not_called()
        self.assertEqual(before['ticket'],after['ticket'])

    def original(self, **updates):
        return {'payload': {'id': 1, 'subject': 'Solução final confidencial',
            'createdDate':'2026-01-01T12:00:00Z',
            'baseStatus': 'Closed', 'customFields': ['resposta esperada'],
            'clients': [], 'actions': [
                {'id': 1, 'type': 2, 'createdDate': '2026-01-01T12:00:00Z',
                 'description': 'Pedido não foi integrado com retorno HTTP 401.',
                 'createdBy': {'profileType': 2, 'businessName': 'Cliente de teste'}},
                {'id': 2, 'type': 2, 'createdDate': '2026-01-02T12:00:00Z',
                 'description': 'Solução: trocada credencial e confirmado sucesso.'}]}, **updates}

    def test_resolution_and_final_ticket_metadata_never_enter_opening(self):
        db = Mock(); db.execute.return_value.fetchone.return_value = self.original()
        _, opening, _, _, author = replay.original_case(db, 1)
        self.assertEqual(author, 'verified_requester')
        self.assertNotIn('customFields', opening)
        self.assertNotIn('baseStatus', opening)
        self.assertEqual(len(opening['actions']), 1)
        self.assertNotIn('Solução', str(opening))

    def test_missing_authorship_is_not_silently_certified(self):
        row = self.original(); row['payload']['actions'][0].pop('createdBy')
        db = Mock(); db.execute.return_value.fetchone.return_value = row
        self.assertEqual(replay.original_case(db, 1)[-1], 'unknown_in_import')

    def test_late_first_public_response_cannot_be_mistaken_for_opening(self):
        row=self.original()
        row['payload']['actions'][0]['createdDate']='2026-01-01T13:00:00Z'
        db=Mock();db.execute.return_value.fetchone.return_value=row
        with self.assertRaisesRegex(ValueError,'possible resolution leakage'):
            replay.original_case(db,1)

    def test_no_human_labels_means_no_human_accuracy(self):
        result = replay.summary([])
        self.assertIsNone(result['human_quality']['correct']['rate'])
        self.assertEqual(result['human_quality']['correct']['denominator'], 0)

    def test_redaction_removes_personal_fields_and_url_query(self):
        text = replay.redact('Pessoa Teste pessoa@example.com https://host.test/a?token=abc', ['Pessoa Teste'])
        self.assertNotIn('Pessoa Teste', text)
        self.assertNotIn('pessoa@example.com', text)
        self.assertNotIn('token=abc', text)


if __name__ == '__main__':
    unittest.main()
