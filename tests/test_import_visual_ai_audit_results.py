from __future__ import annotations

import argparse
import importlib.util
import unittest
from unittest.mock import patch
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMPORTER_PATH = PROJECT_ROOT / "scripts" / "import_visual_ai_audit_results.py"
SPEC = importlib.util.spec_from_file_location("visual_audit_importer", IMPORTER_PATH)
assert SPEC and SPEC.loader
importer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(importer)


def args() -> argparse.Namespace:
    return argparse.Namespace(postgres_db="tw_national_exam_dev", postgres_user="national_exam")


def row(key: str = "candidate-a", fingerprint: str = "a" * 32) -> dict[str, object]:
    return {
        "candidate_key": key,
        "audit_json": {"source_fingerprint": fingerprint},
    }


class VisualImportFreshnessTests(unittest.TestCase):
    @patch.object(importer, "current_candidate_state")
    def test_filters_changed_and_human_reviewed_candidates(self, current_state: object) -> None:
        current_state.return_value = {
            "candidate-a": {"source_fingerprint": "a" * 32, "question_action": "", "visual_review_status": ""},
            "candidate-b": {"source_fingerprint": "b" * 32, "question_action": "accept", "visual_review_status": ""},
            "candidate-c": {"source_fingerprint": "d" * 32, "question_action": "", "visual_review_status": ""},
            "candidate-d": {"source_fingerprint": "e" * 32, "question_action": "", "visual_review_status": "no_visual_required"},
        }
        rows = [row("candidate-a", "a" * 32), row("candidate-b", "b" * 32), row("candidate-c", "c" * 32), row("candidate-d", "e" * 32)]
        fresh, skipped = importer.filter_current_rows(rows, args())
        self.assertEqual([item["candidate_key"] for item in fresh], ["candidate-a"])
        self.assertEqual(
            {item["reason"] for item in skipped},
            {"question_already_reviewed", "source_changed", "visual_already_reviewed"},
        )

    def test_audit_keeps_source_fingerprint_for_display_and_provenance(self) -> None:
        audit = importer.audit_from_record({"source_fingerprint": "a" * 32}, "antigravity", "gemini")
        self.assertEqual(audit["source_fingerprint"], "a" * 32)


if __name__ == "__main__":
    unittest.main()
