from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "skills"
    / "national-exam-ai-audit"
    / "scripts"
    / "validate_review_results.py"
)
SPEC = importlib.util.spec_from_file_location("national_exam_validation", SCRIPT_PATH)
assert SPEC and SPEC.loader
validation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validation)


def task() -> dict:
    return {
        "candidate_key": "moex:test:q001",
        "stage": "question",
        "content": {
            "options": [
                {"key": "A", "text": "甲"},
                {"key": "B", "text": "温度"},
                {"key": "C", "text": "丙"},
                {"key": "D", "text": "丁"},
            ]
        },
    }


def result(correction: dict) -> dict:
    return {
        "candidate_key": "moex:test:q001",
        "stage": "question",
        "status": "needs_review",
        "issue_families": ["ocr_character"],
        "confidence": 0.98,
        "reason": "選項 B 使用異體字「温」。",
        "evidence": [{"field": "content.options[B].text", "before": "温", "after": "溫"}],
        "recommended_action": "human_review_text",
        "findings": [
            {
                "issue_family": "ocr_character",
                "location": "option_B",
                "observed": "温度",
                "suggested": "溫度",
                "confidence": 0.98,
                "correction_applicable": True,
                "correction_omission_reason": None,
            }
        ],
        "correction_coverage": "complete",
        "uncorrected_findings": [],
        "suggested_correction": correction,
        "suggested_changes": ["選項 B：温度 → 溫度"],
        "model": "gpt-5.6-luna",
        "prompt_version": "codex_gpt56_luna_question_audit_v3",
    }


class LunaV3CorrectionValidationTests(unittest.TestCase):
    def test_partial_option_array_is_rejected(self) -> None:
        row = result({"options": [{"key": "B", "text": "溫度"}]})
        errors = validation.validate_luna_v3_correction_contract(
            row,
            row["suggested_correction"],
            task(),
        )
        self.assertIn(
            "v3_options_correction_must_include_all_original_options_in_order",
            errors,
        )

    def test_complete_option_array_is_allowed(self) -> None:
        row = result(
            {
                "options": [
                    {"key": "A", "text": "甲"},
                    {"key": "B", "text": "溫度"},
                    {"key": "C", "text": "丙"},
                    {"key": "D", "text": "丁"},
                ]
            }
        )
        self.assertEqual(
            validation.validate_luna_v3_correction_contract(
                row,
                row["suggested_correction"],
                task(),
            ),
            [],
        )
        self.assertEqual(
            validation.validate_v2_correction_contract(
                row,
                row["status"],
                row["suggested_correction"],
            ),
            [],
        )

    def test_correction_requires_human_readable_change_labels(self) -> None:
        row = result({"stem": "修正後完整題幹"})
        row["suggested_changes"] = []
        errors = validation.validate_luna_v3_correction_contract(
            row,
            row["suggested_correction"],
            task(),
        )
        self.assertIn("v3_correction_requires_suggested_changes", errors)

    def test_complete_ocr_patch_rejects_residual_original_character(self) -> None:
        row = result(
            {
                "options": [
                    {"key": "A", "text": "甲"},
                    {"key": "B", "text": "溫度"},
                    {"key": "C", "text": "仍有温度"},
                    {"key": "D", "text": "丁"},
                ]
            }
        )
        errors = validation.validate_v2_correction_contract(
            row,
            row["status"],
            row["suggested_correction"],
        )
        self.assertIn(
            "finding_1_original_ocr_text_remains_in_correction",
            errors,
        )


if __name__ == "__main__":
    unittest.main()
