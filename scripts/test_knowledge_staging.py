import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import knowledge_staging as staging


class StagingTests(unittest.TestCase):
    def test_preview_is_read_only_even_if_card_claims_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'staging.json'
            path.write_text(json.dumps([{'card_id':'x','title':'Produto de teste', 'status':'APPROVED',
                'steps':[{'number':1,'instruction':'Conferir cadastro de teste.'}]}]), encoding='utf-8')
            with patch.object(staging, 'STAGING', path), patch.object(staging, 'PROJECT_ROOT', Path(directory)), \
                 patch.object(staging, 'REVIEWED', Path(directory)/'missing'), patch.object(staging.kt, 'connect') as db:
                card = staging.previews()[0]
        db.assert_not_called()
        self.assertFalse(card['auto_publish'])
        self.assertEqual(card['status'], 'preview')

    def test_changed_card_cannot_be_approved_with_stale_hash(self):
        with patch.object(staging, 'detail', return_value={'sha256':'current'}), patch.object(staging.kt,'connect') as db:
            with self.assertRaises(RuntimeError):
                staging.approve('1','stale',{},'curador','Revisão conferida')
        db.assert_not_called()

    def test_invalid_image_path_cannot_read_files_outside_assets(self):
        with patch.object(staging, 'detail', return_value={'sha256':'ok', 'source_images':[{'path':'.env'}]}):
            with self.assertRaises(ValueError):
                staging.approve('1','ok',{'procedure':'1. Conferir.'},'curador','Revisão conferida')


if __name__ == '__main__':
    unittest.main()
