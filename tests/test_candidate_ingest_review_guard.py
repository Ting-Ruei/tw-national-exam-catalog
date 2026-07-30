from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import ingest_question_candidates_to_postgres as ingest  # noqa: E402


class CandidateIngestReviewGuardTests(unittest.TestCase):
    def test_question_answer_alignment_failures_are_scoped_to_selected_candidates(self) -> None:
        failures = ingest.question_answer_alignment_failures(
            [
                {
                    "candidate_key": "keep",
                    "issue_code": "question_answer_number_set_mismatch",
                },
                {
                    "candidate_key": "skip",
                    "issue_code": "answer_number_set_unusable",
                },
                {
                    "candidate_key": "keep",
                    "issue_code": "option_count_not_four",
                },
            ],
            {"keep"},
        )
        self.assertEqual(
            failures,
            {"question_answer_number_set_mismatch": 1},
        )

    def test_question_answer_alignment_failure_blocks_ingest(self) -> None:
        with self.assertRaisesRegex(
            SystemExit,
            "question/answer number-set alignment failures",
        ):
            ingest.validate_question_answer_alignment(
                {"question_answer_number_set_mismatch": 1}
            )

    def test_question_answer_alignment_passes_without_failures(self) -> None:
        ingest.validate_question_answer_alignment({})

    def test_empty_reviewed_change_report_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reviewed_changes.json"
            report = ingest.write_reviewed_change_report(
                path,
                [],
                candidate_path=Path("candidates.jsonl"),
            )
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["reviewed_candidate_change_count"], 0)
            self.assertTrue(path.exists())

    def test_reviewed_change_report_preserves_field_and_layer_scope(self) -> None:
        changes = [
            {
                "candidate_key": "moex:test:question:q008",
                "question_changed_fields": ["stem", "group_ref"],
                "answer_changed_fields": [],
                "group_changed_fields": ["stem", "group_ref"],
                "blocked_review_layers": ["question", "group"],
            }
        ]
        report = ingest.write_reviewed_change_report(
            None,
            changes,
            candidate_path=Path("candidates.jsonl"),
        )
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["reviewed_candidate_change_count"], 1)
        self.assertEqual(
            report["changes"][0]["blocked_review_layers"],
            ["question", "group"],
        )


if __name__ == "__main__":
    unittest.main()
