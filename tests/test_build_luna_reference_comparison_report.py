from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PROJECT_ROOT / "scripts" / "build_luna_reference_comparison_report.py"
)
SPEC = importlib.util.spec_from_file_location("luna_reference_report", SCRIPT_PATH)
assert SPEC and SPEC.loader
report = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = report
SPEC.loader.exec_module(report)


class LunaReferenceComparisonReportTests(unittest.TestCase):
    def test_evidence_transition_detects_historical_replacement(self) -> None:
        content = {
            "stem": "目前題幹",
            "options": [{"key": "A", "text": "糞便"}],
        }
        audit = {
            "findings": [
                {
                    "evidence": [
                        {
                            "field": "content.options[A].text",
                            "before": "粪便",
                            "after": "糞便",
                        }
                    ]
                }
            ]
        }
        result = report.evidence_transition(content, audit)
        self.assertEqual(
            result["status"],
            "historical_text_replaced_as_suggested",
        )

    def test_correction_reflection_compares_partial_options(self) -> None:
        content = {
            "stem": "題幹",
            "options": [
                {"key": "A", "text": "糞便"},
                {"key": "B", "text": "其他"},
            ],
        }
        correction = {"options": [{"key": "A", "text": "糞便"}]}
        result = report.correction_reflection(content, correction)
        self.assertEqual(result["status"], "matches_current_exactly")

    def test_missing_luna_event_stays_explicitly_unaudited(self) -> None:
        local_row = {
            "candidate_key": "candidate:q026",
            "exam": {"ordinal": "2"},
            "content": {"stem": "題幹", "options": []},
            "model_results": {
                model: {
                    "batch_valid": True,
                    "lane_results": {
                        "ocr_text": {"issues": []},
                        "meaning": {"issues": []},
                    },
                }
                for model in report.LOCAL_MODELS
            },
        }
        rows = report.join_rows([local_row], [], [])
        self.assertFalse(rows[0]["luna_reference"]["audited"])
        self.assertEqual(rows[0]["luna_reference"]["model"], "gpt-5.6-luna")
        summary = report.build_summary(rows)
        self.assertEqual(
            summary["luna_unaudited_questions_within_historical_scope"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
