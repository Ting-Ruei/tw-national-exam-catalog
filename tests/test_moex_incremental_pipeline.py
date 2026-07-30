from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "run_moex_incremental_review_pipeline.py"
SPEC = importlib.util.spec_from_file_location("moex_incremental", SCRIPT_PATH)
assert SPEC and SPEC.loader
pipeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pipeline)


def row(
    key: str,
    *,
    exam_code: str = "115090",
    category: str = "醫事檢驗師",
    question: str = "",
    answer: str = "",
    correction: str = "",
) -> dict[str, str]:
    return {
        "year": "115",
        "exam_code": exam_code,
        "exam_label": "115年測試考試",
        "category_code": "308",
        "category_name": category,
        "subject_code": "1101",
        "subject_name": "測試科目",
        "question_set": "1",
        "question_url": question,
        "answer_url": answer,
        "correction_url": correction,
        "registry_key": key,
    }


class IncrementalCatalogTests(unittest.TestCase):
    def test_pipeline_lock_rejects_overlapping_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = pipeline.acquire_pipeline_lock(Path(directory))
            try:
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    pipeline.acquire_pipeline_lock(Path(directory))
            finally:
                first.close()

    def test_document_changes_detects_added_roles_and_url_replacement(self) -> None:
        baseline = [row("moex:115090:308:1101:1", question="old-q")]
        live = [row("moex:115090:308:1101:1", question="new-q", answer="new-a")]

        changes = pipeline.document_changes(baseline, live)

        self.assertEqual(
            {(item["document_role"], item["change_type"]) for item in changes},
            {("question", "document_url_changed"), ("answer", "document_added")},
        )

    def test_locked_profile_and_exam_code_filters_are_exact(self) -> None:
        changes = pipeline.document_changes(
            [],
            [
                row("moex:115090:308:1101:1", question="q", answer="a"),
                row(
                    "moex:115080:308:1101:1",
                    exam_code="115080",
                    category="公職醫事檢驗師",
                    question="q2",
                    answer="a2",
                ),
            ],
        )

        selected = pipeline.choose_changes(
            changes,
            profile="locked27",
            locked_categories={"醫事檢驗師"},
            exam_codes={"115090"},
        )

        self.assertTrue(selected)
        self.assertEqual({item["exam_code"] for item in selected}, {"115090"})
        self.assertEqual({item["category_name"] for item in selected}, {"醫事檢驗師"})

    def test_incomplete_answer_pair_waits_instead_of_entering_mineru(self) -> None:
        live = [row("moex:115090:308:1101:1", question="q")]
        changes = pipeline.document_changes([], live)

        targets, waiting = pipeline.target_rows_for_changes(live, changes)

        self.assertEqual(targets, [])
        self.assertEqual(waiting, live)

    def test_merge_preserves_missing_history_and_limits_checkpoint_scope(self) -> None:
        old_070 = row("moex:115070:301:1101:1", exam_code="115070", question="q70")
        live_080 = row("moex:115080:302:1101:1", exam_code="115080", category="一般行政", question="q80")
        live_090 = row("moex:115090:308:1101:1", question="q90", answer="a90")

        merged = pipeline.merge_catalog_rows(
            [old_070],
            [live_080, live_090],
            commit_exam_codes={"115090"},
        )

        self.assertEqual({item["exam_code"] for item in merged}, {"115070", "115090"})
        self.assertNotIn("115080", {item["exam_code"] for item in merged})
        self.assertEqual([item["exam_code"] for item in merged], ["115070", "115090"])

    def test_group_integrity_allows_zero_groups_when_no_cue_was_found(self) -> None:
        pipeline.validate_group_candidate_summary(
            {
                "pending_group_count": 0,
                "unbound_explicit_group_cue_count": 0,
                "incomplete_explicit_group_count": 0,
                "continuation_without_anchor_count": 0,
            }
        )

    def test_group_integrity_blocks_explicit_cue_without_candidate_binding(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError, "unbound_explicit_group_cue_count"
        ):
            pipeline.validate_group_candidate_summary(
                {
                    "pending_group_count": 0,
                    "unbound_explicit_group_cue_count": 1,
                    "incomplete_explicit_group_count": 0,
                    "continuation_without_anchor_count": 0,
                }
            )

    def test_candidate_integrity_allows_nonstructural_review_issues(self) -> None:
        pipeline.validate_candidate_integrity_summary({})

    def test_candidate_integrity_blocks_question_number_gap(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "question_number_gap"):
            pipeline.validate_candidate_integrity_summary(
                {"question_number_gap": 1}
            )


if __name__ == "__main__":
    unittest.main()
