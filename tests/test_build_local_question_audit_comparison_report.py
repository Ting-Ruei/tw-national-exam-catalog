from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PROJECT_ROOT / "scripts" / "build_local_question_audit_comparison_report.py"
)
SPEC = importlib.util.spec_from_file_location("audit_comparison_report", SCRIPT_PATH)
assert SPEC and SPEC.loader
report = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = report
SPEC.loader.exec_module(report)


class LocalQuestionAuditComparisonReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task = {
            "candidate_key": "candidate:q001",
            "content": {
                "stem": "題幹",
                "options": [{"key": "A", "text": "選項"}],
                "image_refs": [],
                "stem_image": None,
                "group_ref": None,
                "group_sequence_no": None,
            },
        }

    def test_failed_chunk_is_never_rendered_as_pass(self) -> None:
        summary = {
            "source_offset": 0,
            "task_count": 20,
            "lane_validation": {
                "ocr_text": {
                    "complete": False,
                    "errors": ["invalid_json"],
                },
                "meaning": {"complete": True},
                "visual": {"complete": True, "skipped": True},
                "group": {"complete": True, "skipped": True},
            },
        }
        result = report.failed_chunk_result(
            self.task,
            "test:model",
            summary,
            Path("/path/that/does/not/exist"),
        )
        self.assertFalse(result["batch_valid"])
        self.assertEqual(result["status"], "validation_failed")
        self.assertEqual(
            result["lane_results"]["ocr_text"]["status"],
            "validation_failed",
        )
        self.assertIn(
            "不能當成",
            "\n".join(report.markdown_model_result("test:model", result)),
        )

    def test_summary_counts_only_valid_text_findings(self) -> None:
        valid_flag = {
            "batch_valid": True,
            "suggested_correction": {"stem": "修正"},
            "lane_results": {
                "ocr_text": {"issues": [{"code": "ocr_character"}]},
                "meaning": {"issues": []},
            },
        }
        invalid_partial = {
            "batch_valid": False,
            "suggested_correction": None,
            "lane_results": {
                "ocr_text": {"issues": [{"code": "ocr_character"}]},
                "meaning": {"issues": []},
            },
        }
        rows = [
            {"model_results": {"test:model": valid_flag}},
            {"model_results": {"test:model": invalid_partial}},
        ]
        metric = report.model_metrics(rows, "test:model")
        self.assertEqual(metric["valid_question_count"], 1)
        self.assertEqual(metric["questions_with_valid_text_findings"], 1)
        self.assertEqual(metric["valid_text_finding_count"], 1)
        self.assertEqual(metric["questions_with_suggested_correction"], 1)

    def test_visual_and_group_applicability(self) -> None:
        self.assertFalse(report.task_has_visual(self.task))
        self.assertFalse(report.task_has_group(self.task))
        visual = {
            "content": {
                **self.task["content"],
                "image_refs": [{"path": "image.png"}],
            }
        }
        group = {
            "content": {
                **self.task["content"],
                "group_ref": "group-1",
            }
        }
        self.assertTrue(report.task_has_visual(visual))
        self.assertTrue(report.task_has_group(group))


if __name__ == "__main__":
    unittest.main()
