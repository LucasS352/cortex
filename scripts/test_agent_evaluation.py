import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_evaluation
import agent_operations


class EvaluationTest(unittest.TestCase):
    def test_only_human_labeled_cases_enter_accuracy_denominator(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
                agent_operations, 'OPERATIONS_DB', Path(directory) / 'evaluation.sqlite'):
            seeded = agent_evaluation.seed(limit=12)
            self.assertGreater(seeded['added'], 0)
            cases = agent_evaluation.list_cases()
            self.assertEqual(cases['labeled'], 0)
            self.assertEqual(cases['primary_correct'], 0)
            first = cases['items'][0]
            agent_evaluation.label(first['ticket_id'], first['current_route']['modules'][:1],
                                   'Conferir o cenário, consultar a fonte e validar o resultado com o usuário.')
            cases = agent_evaluation.list_cases()
            self.assertEqual(cases['labeled'], 1)
            self.assertEqual(cases['primary_correct'], 1)
            self.assertEqual(agent_evaluation.seed(limit=12)['added'], 0)


if __name__ == '__main__':
    unittest.main()
