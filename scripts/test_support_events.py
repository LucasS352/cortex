from pathlib import Path
import sqlite3
from contextlib import closing
import tempfile
import unittest
from unittest.mock import patch

import support_events as events


class SupportEventTests(unittest.TestCase):
    def test_missing_store_read_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(events,'STORE',Path(folder)/'absent.sqlite3'):
            self.assertEqual(events.snapshot()['total'], 0)
            self.assertFalse(events.STORE.exists())

    def test_event_deduplication_and_no_raw_customer_text(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(events,'STORE',Path(folder)/'events.sqlite3'):
            decision = {'state':'INVESTIGAR','evidence':{'integrations':['tray'],'codes':['401']},
                        'raw_question':'Pessoa Exemplo senha=confidencial'}
            self.assertTrue(events.record('event:1','workbench',decision,'42'))
            self.assertFalse(events.record('event:1','workbench',decision,'42'))
            self.assertEqual(events.snapshot()['total'], 1)
            with closing(sqlite3.connect(events.STORE)) as db:
                rows = str(db.execute('SELECT * FROM support_events').fetchall())
            self.assertNotIn('Pessoa Exemplo',rows)
            self.assertNotIn('confidencial',rows)
            self.assertIn('tray',rows)

    def test_result_without_decision_creates_no_event(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(events,'STORE',Path(folder)/'events.sqlite3'):
            self.assertFalse(events.record('event:1','workbench',None))
            self.assertFalse(events.STORE.exists())


if __name__ == '__main__':
    unittest.main()
