import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest_manuals import extract_screen_path, extract_shortcuts, manual_source_id
from master_knowledge_staging import ROOT, build_pilot_cards, load_catalog, validate


class ManualIngestionIdentityTests(unittest.TestCase):
    def test_each_chunk_and_image_has_a_distinct_stable_id(self):
        chunk_1 = manual_source_id("Fiscal/Manual.docx", "chunk", 1)
        chunk_2 = manual_source_id("Fiscal/Manual.docx", "chunk", 2)
        image_1 = manual_source_id("Fiscal/Manual.docx", "image", 1)
        self.assertNotEqual(chunk_1, chunk_2)
        self.assertNotEqual(chunk_1, image_1)
        self.assertEqual(chunk_1, manual_source_id("Fiscal\\Manual.docx", "chunk", 1))
        self.assertLessEqual(len(chunk_1), 50)

    def test_contextual_shortcut_with_space_keeps_modifier(self):
        self.assertEqual(extract_shortcuts("usar CTRL F9 para abrir"), ["CTRL+F9"])
        self.assertEqual(extract_shortcuts("pressione Ctrl+R"), ["CTRL+R"])

    def test_double_chevron_path_is_normalized(self):
        text = "CADASTROS >> FISCAL >> TRIBUTAÇÃO >> CADASTRO DE GRUPO"
        self.assertEqual(
            extract_screen_path(text),
            "CADASTROS > FISCAL > TRIBUTAÇÃO > CADASTRO DE GRUPO",
        )


@unittest.skipUnless(
    (ROOT / "output/master_screens/catalogo_oficial_telas_master.json").is_file()
    and (ROOT / "assets/screens").is_dir(),
    "Artefatos locais de staging não fazem parte da imagem de produção",
)
class StagingCardTests(unittest.TestCase):
    def setUp(self):
        self.cards = build_pilot_cards()

    def test_three_initial_pilots_exist(self):
        self.assertEqual(len(self.cards), 3)
        self.assertEqual(len({card["card_id"] for card in self.cards}), 3)

    def test_all_mapped_images_exist_and_point_to_a_step(self):
        for card in self.cards:
            step_numbers = {step["number"] for step in card["steps"]}
            for image in card["images"]:
                self.assertTrue((ROOT / image["path"]).is_file(), image["path"])
                self.assertIn(image["after_step"], step_numbers)
                self.assertTrue(image["name"])
                self.assertTrue(image["caption"])
                self.assertTrue(image["sha256"])

    def test_pilot_pack_has_no_structural_errors(self):
        report = validate(self.cards, load_catalog())
        self.assertEqual(report["summary"]["errors"], 0, report["issues"])
        self.assertEqual(report["summary"]["review_items"], 5)

    def test_negative_intents_exist_for_ambiguous_pilots(self):
        for card in self.cards:
            self.assertTrue(card["must_not_match"], card["card_id"])


if __name__ == "__main__":
    unittest.main()
