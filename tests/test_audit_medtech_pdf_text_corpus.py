from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_medtech_pdf_text_corpus.py"


def import_script():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("audit_medtech_pdf_text_corpus", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class MedtechPdfCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = import_script()

    def test_question_selector_excludes_answer_and_modification_notices(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "x.pdf").touch()
            (root / "x_ANS.pdf").touch()
            (root / "x_MOD.pdf").touch()
            self.assertEqual([path.name for path in self.module.question_pdfs(root)], ["x.pdf"])

    def test_split_questions_keeps_cross_page_continuation(self):
        text = "1. 題幹\nA.甲\n續行\n2. 次題\nA.乙"
        rows = self.module.split_questions(text)
        self.assertIn("續行", rows[1])
        self.assertNotIn("次題", rows[1])

    def test_sequence_flags_detects_missing_and_duplicate_markers(self):
        flags = self.module.sequence_flags([1, 2, 2, 4], 4)
        self.assertIn("missing_question_markers", flags)
        self.assertIn("duplicate_question_markers", flags)

    def test_legacy_question_marker_without_period(self):
        legacy_options = "\ue18c\ue18d\ue18e\ue18f" * 10
        self.assertEqual(self.module.marker_sequence(f"1 舊式題幹\n{legacy_options}\n2 次題"), [1, 2])

    def test_modern_numeric_line_is_not_a_question_marker(self):
        self.assertEqual(self.module.marker_sequence("1. 題幹\n50 mL/min\n2. 次題"), [1, 2])

    def test_structural_private_use_glyphs_are_resolved_but_unknown_glyphs_are_not(self):
        value = "\ue18c甲 \ue18d乙 \ue000一 \ue2c6"
        self.assertEqual(self.module.unresolved_private_use_count(value), 1)
        normalized = self.module.normalized_exact(value)
        self.assertIn("A.甲", normalized)
        self.assertIn("①一", normalized)

    def test_stacked_text_layer_signal_detects_four_copies(self):
        value = "代 代 代 代 號 號 號 號\n臨床血液學臨床血液學臨床血液學臨床血液學"
        result = self.module.stacked_text_signals(value)
        self.assertTrue(result["detected"])
        self.assertGreaterEqual(result["token_runs"], 2)
        self.assertGreaterEqual(result["tandem_lines"], 1)

    def test_numeric_table_repetition_is_not_high_confidence_stacked_text(self):
        result = self.module.stacked_text_signals("2 2 2\n+ + +")
        self.assertTrue(result["detected"])
        self.assertFalse(result["high_confidence"])


if __name__ == "__main__":
    unittest.main()
