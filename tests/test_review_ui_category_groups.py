from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import serve_question_review_ui as review_ui  # noqa: E402


class ReviewUiCategoryGroupTests(unittest.TestCase):
    def test_historical_professional_categories_share_ui_filters(self) -> None:
        cases = {
            "__chinese_medicine_track__": ("中醫師", "中醫師（一）", "中醫師(二)"),
            "__physician_track__": ("醫師", "醫師（一）", "醫師(二)", "醫師(ㄧ)"),
            "__dentist_track__": ("牙醫師", "牙醫師（一）", "牙醫師(二)"),
            "__pharmacist_track__": ("藥師", "藥師（一）", "藥師(二)"),
        }
        for filter_key, categories in cases.items():
            with self.subTest(filter_key=filter_key):
                for category in categories:
                    self.assertTrue(review_ui.category_matches_filter(category, filter_key))
                self.assertFalse(review_ui.category_matches_filter("醫事檢驗師", filter_key))

        aliases = review_ui.category_filter_values("__physician_track__")
        self.assertIn("醫師(一)", aliases)
        self.assertIn("醫師（一）", aliases)

    def test_jsonl_backend_filters_all_rows_and_subject_facets_by_group(self) -> None:
        candidates = [
            {
                "candidate_key": "physician-one",
                "question_number": "1",
                "stem": "題目一",
                "options": [],
                "metadata": {
                    "normalized_category_name": "醫師(一)",
                    "group_name": "醫師",
                    "normalized_subject_name": "醫學(一)",
                    "year": "115",
                    "exam_ordinal": "1",
                },
            },
            {
                "candidate_key": "physician-two",
                "question_number": "2",
                "stem": "題目二",
                "options": [],
                "metadata": {
                    "normalized_category_name": "醫師(二)",
                    "group_name": "醫師",
                    "normalized_subject_name": "醫學(二)",
                    "year": "115",
                    "exam_ordinal": "1",
                },
            },
            {
                "candidate_key": "dentist-one",
                "question_number": "1",
                "stem": "牙醫題目",
                "options": [],
                "metadata": {
                    "normalized_category_name": "牙醫師(一)",
                    "group_name": "牙醫師",
                    "normalized_subject_name": "牙醫學(一)",
                    "year": "115",
                    "exam_ordinal": "1",
                },
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_path = root / "candidates.jsonl"
            candidate_path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in candidates),
                encoding="utf-8",
            )
            review_log = root / "question_review_events.jsonl"
            review_log.write_text("", encoding="utf-8")
            state = review_ui.ReviewState(candidate_path, None, review_log, review_backend="jsonl")

            self.assertEqual(len(state.candidates), 3)
            payload = state.filtered_candidate_payloads({"category": "__physician_track__", "limit": "20"})

        self.assertEqual(payload["filtered_count"], 2)
        self.assertEqual(
            {item["candidate_key"] for item in payload["candidates"]},
            {"physician-one", "physician-two"},
        )
        self.assertEqual(set(payload["facets"]["subjects"]), {"醫學(一)", "醫學(二)"})

    def test_sql_group_filter_uses_alias_array_and_group_name_fallback(self) -> None:
        state = review_ui.ReviewState.__new__(review_ui.ReviewState)
        clauses, values = state._sql_candidate_where({"category": "__pharmacist_track__"})

        self.assertIn("= ANY(%s)", clauses[-1])
        self.assertIn("藥師(一)", values[-1])
        self.assertIn("藥師（一）", values[-1])
        self.assertIn("group_name", review_ui.SQL_CANDIDATE_CATEGORY_EXPR)

    def test_desktop_and_mobile_pages_expose_all制度群組_options(self) -> None:
        for key, label in review_ui.CATEGORY_GROUP_LABELS.items():
            self.assertIn(key, review_ui.PAGE_HTML)
            self.assertIn(label, review_ui.PAGE_HTML)

        mobile = review_ui.mobile_asset_response("/mobile/")
        self.assertIsNotNone(mobile)
        assert mobile is not None
        mobile_html = mobile[0].decode("utf-8")
        for key, label in review_ui.CATEGORY_GROUP_LABELS.items():
            self.assertIn(key, mobile_html)
            self.assertIn(label, mobile_html)


if __name__ == "__main__":
    unittest.main()
