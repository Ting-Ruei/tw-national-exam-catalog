from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "analyze_ai395_three_source_pilot.py"
spec = importlib.util.spec_from_file_location("analyze_ai395_three_source_pilot", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class AI395ThreeSourceAnalysisTests(unittest.TestCase):
    def test_compatibility_glyph_difference_requires_two_consistent_families(self) -> None:
        packet = {
            "effective_candidate": {"stem": "下列何者正確"},
            "official_pdf_second_source": {
                "question_consensus": {"status": "usable_question_consensus"},
                "question_text_by_engine": {
                    "pdftotext_layout": {"source_family": "poppler", "text": "1 下列何者正確"},
                    "pypdf": {"source_family": "pypdf", "text": "1 下列何者正確"},
                },
            },
        }
        result = module.classify_candidate_source(packet)
        self.assertEqual(result["status"], "compatibility_glyph_difference")
        self.assertTrue(result["auto_rule_candidate"])

    def test_single_family_never_becomes_auto_rule_candidate(self) -> None:
        packet = {
            "effective_candidate": {"stem": "下列何者正確"},
            "official_pdf_second_source": {
                "question_consensus": {"status": "insufficient_question_text"},
                "question_text_by_engine": {
                    "pdftotext_layout": {"source_family": "poppler", "text": "1 下列何者正確"},
                },
            },
        }
        result = module.classify_candidate_source(packet)
        self.assertEqual(result["status"], "insufficient_independent_families")
        self.assertFalse(result["auto_rule_candidate"])


if __name__ == "__main__":
    unittest.main()
