from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_pdf_reference_source.py"


def import_script():
    spec = importlib.util.spec_from_file_location("build_pdf_reference_source", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PdfReferenceSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = import_script()

    def test_normalization_is_only_for_comparison(self):
        self.assertEqual(
            self.module.normalize_text("  氫鍵\r\n(hydrogen)  "),
            "氫鍵 (hydrogen)",
        )

    def test_two_independent_extractor_families_produce_consensus(self):
        result = self.module.choose_consensus(
            {
                "pdftotext_layout": "第 1 題\nB. cereus",
                "pdftotext_raw": "第 1 題 B. cereus",
                "pypdf": "第 1 題\nB. cereus",
                "pdfplumber": "",
            }
        )
        self.assertEqual(result["status"], "usable_consensus")
        self.assertIn(result["engine"], {"pdftotext_layout", "pdftotext_raw", "pypdf"})
        self.assertIn("poppler", result["families"])
        self.assertIn("pypdf", result["families"])

    def test_disagreement_requires_manual_source_review(self):
        result = self.module.choose_consensus(
            {
                "pdftotext_layout": "承上題，下列何者正確？",
                "pdftotext_raw": "承上題，下列何者正確？",
                "pypdf": "下列何者正確？",
                "pdfplumber": "",
            }
        )
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("single_extractor_family", result["flags"])

    def test_repeated_lines_are_a_layer_warning(self):
        quality = self.module.text_quality("題幹\n題幹\n題幹\n選項")
        self.assertEqual(quality["repeated_line_count"], 2)
        self.assertGreaterEqual(quality["repeated_line_ratio"], 0.5)


if __name__ == "__main__":
    unittest.main()
