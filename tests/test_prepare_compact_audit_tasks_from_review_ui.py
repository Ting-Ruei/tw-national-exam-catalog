from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "prepare_compact_audit_tasks_from_review_ui.py"
SPEC = importlib.util.spec_from_file_location("prepare_compact_audit_tasks", SCRIPT_PATH)
assert SPEC and SPEC.loader
prepare = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = prepare
SPEC.loader.exec_module(prepare)


class PrepareCompactAuditTasksTests(unittest.TestCase):
    def test_safe_task_strips_answers_reviews_and_prior_ai(self) -> None:
        candidate = {
            "candidate_key": "moex:test:q001",
            "source_registry_key": "moex:test:question",
            "question_number": "1",
            "question_number_occurrence": 1,
            "stem": "修正後題幹",
            "answer": "A",
            "options": [{"key": "A", "text": "選項", "image": None}],
            "image_refs": [],
            "metadata": {
                "normalized_category_name": "醫事檢驗師",
                "normalized_subject_name": "測試",
                "subject_code": "0501",
                "year": "115",
                "exam_ordinal": "2",
            },
            "review": {"action": "accept", "notes": "人工註記"},
            "ai_review": {"model": "old-model"},
            "issues": [
                {
                    "issue_code": "too_few_options",
                    "severity": "error",
                    "message": "選項不足",
                }
            ],
        }
        task = prepare.safe_task(candidate)
        serialized = json.dumps(task, ensure_ascii=False)
        self.assertNotIn('"answer"', serialized)
        self.assertNotIn("人工註記", serialized)
        self.assertNotIn("old-model", serialized)
        self.assertEqual(task["content"]["stem"], "修正後題幹")
        self.assertEqual(
            task["signals"]["parser_issues"][0]["code"],
            "too_few_options",
        )

    def test_neighbors_stop_at_paper_boundary(self) -> None:
        tasks = [
            {
                "candidate_key": "a1",
                "source_registry_key": "a",
                "exam": {"question_number": "1"},
                "content": {"stem": "A1"},
            },
            {
                "candidate_key": "b1",
                "source_registry_key": "b",
                "exam": {"question_number": "1"},
                "content": {"stem": "B1"},
            },
        ]
        prepare.attach_neighbors(tasks)
        self.assertIsNone(tasks[0]["neighbors"]["next"])
        self.assertIsNone(tasks[1]["neighbors"]["previous"])

    def test_asset_ref_preserves_source_existence_and_records_local_state(self) -> None:
        ref = prepare.normalize_asset_ref(
            {
                "raw_ref": "remote.png",
                "path_relative": "missing-fixture/remote.png",
                "exists": True,
                "bytes": 123,
            }
        )
        self.assertIsNotNone(ref)
        assert ref is not None
        self.assertTrue(ref["source_exists"])
        self.assertFalse(ref["local_exists"])
        self.assertTrue(ref["exists"])

    def test_variable_size_paper_manifest_disables_fixed_range_assumptions(self) -> None:
        rows = [
            {
                "candidate_key": f"a{number}",
                "source_registry_key": "paper-a",
                "exam": {
                    "category": "藥師(二)",
                    "year": "115",
                    "ordinal": "1",
                    "subject_code": "0401",
                    "subject": "藥學(四)",
                    "question_number": str(number),
                },
                "content": {"stem": "題目"},
            }
            for number in range(1, 71)
        ]
        manifest = prepare.paper_manifest(rows, 0)
        self.assertEqual(manifest[0]["task_count"], 70)
        self.assertEqual(manifest[0]["missing_question_numbers"], [])
        self.assertEqual(manifest[0]["unexpected_question_numbers"], [])

    def test_candidate_sort_keeps_same_subject_papers_contiguous(self) -> None:
        rows = []
        for category, paper in [("藥師", "paper-a"), ("藥師(一)", "paper-b")]:
            for number in (1, 2):
                rows.append(
                    {
                        "candidate_key": f"{paper}:q{number}",
                        "source_registry_key": paper,
                        "question_number": str(number),
                        "metadata": {
                            "normalized_category_name": category,
                            "normalized_subject_name": "藥理學與藥物化學",
                            "subject_code": "11",
                            "year": "105",
                            "exam_ordinal": "1",
                        },
                    }
                )
        ordered = sorted(reversed(rows), key=prepare.candidate_sort_key)
        paper_order = [row["source_registry_key"] for row in ordered]
        self.assertIn(paper_order, [["paper-a", "paper-a", "paper-b", "paper-b"], ["paper-b", "paper-b", "paper-a", "paper-a"]])


if __name__ == "__main__":
    unittest.main()
