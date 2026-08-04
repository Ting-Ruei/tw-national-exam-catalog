from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = PROJECT_ROOT / "scripts" / "llmshare_question_audit.py"
AUDIT_SPEC = importlib.util.spec_from_file_location("llmshare_question_audit", AUDIT_PATH)
assert AUDIT_SPEC and AUDIT_SPEC.loader
audit = importlib.util.module_from_spec(AUDIT_SPEC)
sys.modules["llmshare_question_audit"] = audit
AUDIT_SPEC.loader.exec_module(audit)

RUNNER_PATH = PROJECT_ROOT / "scripts" / "run_llmshare_question_audit.py"
RUNNER_SPEC = importlib.util.spec_from_file_location("run_llmshare_question_audit", RUNNER_PATH)
assert RUNNER_SPEC and RUNNER_SPEC.loader
runner = importlib.util.module_from_spec(RUNNER_SPEC)
RUNNER_SPEC.loader.exec_module(runner)


def packet() -> dict[str, object]:
    return {
        "candidate_keys": ["a"],
        "request": {
            "questions": [
                {
                    "candidate_key": "a",
                    "content": {
                        "stem": "何者含辅酶？",
                        "options": [
                            {"key": "A", "text": "辅酶"},
                            {"key": "B", "text": "無"},
                        ],
                    },
                }
            ]
        },
    }


def response(*, complete_options: bool = True) -> str:
    options = {"A": "輔酶", "B": "無"} if complete_options else {"A": "輔酶"}
    rows = [
        {
            "candidate_key": "a",
            "stage": "question",
            "status": "needs_review",
            "issue_families": ["ocr_character"],
            "confidence": 0.95,
            "reason": "疑似簡體字。",
            "evidence": [{"field": "option_A", "before": "辅酶", "after": "輔酶"}],
            "recommended_action": "human_review_text",
            "findings": [
                {
                    "issue_family": "ocr_character",
                    "location": "option_A",
                    "observed": "辅酶",
                    "suggested": "輔酶",
                    "confidence": 0.95,
                    "correction_applicable": True,
                    "correction_omission_reason": None,
                }
            ],
            "correction_coverage": "complete",
            "uncorrected_findings": [],
            "suggested_correction": {"options": options},
        }
    ]
    return "<FINAL_JSON>" + json.dumps(rows, ensure_ascii=False) + "</FINAL_JSON>"


class RunLlmShareQuestionAuditTests(unittest.TestCase):
    def test_validator_accepts_mechanically_repaired_complete_options_object(self) -> None:
        result = runner.validate_answer(response(), packet(), model="gemma4:31b")
        self.assertEqual(result["returned_count"], 1)
        self.assertEqual(result["suggested_correction_count"], 1)
        self.assertEqual(
            result["warning_counts"],
            {"suggested_correction_options_object_repaired": 1},
        )

    def test_validator_rejects_incomplete_options_patch(self) -> None:
        with self.assertRaises(runner.DispatchError) as caught:
            runner.validate_answer(
                response(complete_options=False),
                packet(),
                model="gemma4:31b",
            )
        self.assertEqual(caught.exception.kind, "audit_schema_gate")

    def test_latency_guard_uses_floor_until_baseline_is_available(self) -> None:
        breach, threshold, baseline = runner.latency_decision(
            [12_000, 13_000, 12_500],
            current_ms=46_000,
            soft_latency_ms=45_000,
            hard_latency_ms=120_000,
            multiplier=2.5,
        )
        self.assertTrue(breach)
        self.assertEqual(threshold, 45_000)
        self.assertIsNone(baseline)

    def test_latency_guard_scales_from_first_five_successes(self) -> None:
        breach, threshold, baseline = runner.latency_decision(
            [10_000, 11_000, 12_000, 13_000, 14_000],
            current_ms=46_000,
            soft_latency_ms=45_000,
            hard_latency_ms=120_000,
            multiplier=2.5,
        )
        self.assertTrue(breach)
        self.assertEqual(baseline, 12_000)
        self.assertEqual(threshold, 45_000)


if __name__ == "__main__":
    unittest.main()
