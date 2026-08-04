#!/usr/bin/env python3
"""Regression coverage for Review UI table / image routing."""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "scripts" / "serve_question_review_ui.py"
SPEC = importlib.util.spec_from_file_location("review_ui_table_detection", MODULE_PATH)
assert SPEC and SPEC.loader
review_ui = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review_ui)

EXPORTER_PATH = PROJECT_ROOT / "scripts" / "export_visual_ai_audit_batch.py"
EXPORTER_SPEC = importlib.util.spec_from_file_location("visual_audit_exporter", EXPORTER_PATH)
assert EXPORTER_SPEC and EXPORTER_SPEC.loader
visual_exporter = importlib.util.module_from_spec(EXPORTER_SPEC)
EXPORTER_SPEC.loader.exec_module(visual_exporter)


class TableDetectionTests(unittest.TestCase):
    def profile(self, stem: str) -> dict:
        return review_ui.candidate_visual_profile({"stem": stem})

    def test_pharmacy_terms_are_not_table_cues(self) -> None:
        for text in (
            "調劑 Glyceryl trinitrate tablets 給病患時，使用期限為何？",
            "病人有 chronic stable angina，何者最適當？",
            "使用 rotary tabletting machine 時，何者正確？",
            "每次服用一 tablespoonful。",
        ):
            with self.subTest(text=text):
                profile = self.profile(text)
                self.assertFalse(profile["has_structured_table"])
                self.assertFalse(profile["has_visual_dependency"])

    def test_real_table_cues_remain_visible(self) -> None:
        for text in (
            "如下表所示，何者正確？",
            "Refer to Table 1 for the pharmacokinetic data.",
            "藥物動力學數據：<table><tr><td>藥品</td></tr></table>",
        ):
            with self.subTest(text=text):
                self.assertTrue(self.profile(text)["has_structured_table"])

    def test_original_table_survives_display_suppression(self) -> None:
        profile = review_ui.candidate_visual_profile(
            {
                "stem": "藥物動力學數據：",
                "stem_with_tables": "藥物動力學數據：<table><tr><td>藥品</td></tr></table>",
            }
        )
        self.assertTrue(profile["has_structured_table"])

    def test_table_screenshot_removes_markup_only_from_corrected_stem(self) -> None:
        candidate = {
            "candidate_key": "visual-table-test",
            "question_number": "1",
            "stem": "藥物數據如下：<table><tr><td>藥品</td></tr></table>何者正確？",
            "options": [],
            "metadata": {
                "normalized_category_name": "藥師",
                "normalized_subject_name": "藥劑學",
                "year": "115",
                "exam_ordinal": "1",
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            candidate_path = root / "candidates.jsonl"
            candidate_path.write_text(json.dumps(candidate, ensure_ascii=False) + "\n", encoding="utf-8")
            original_asset_root = review_ui.MANUAL_ASSET_ROOT
            review_ui.MANUAL_ASSET_ROOT = root / "manual_assets"
            try:
                state = review_ui.ReviewState(
                    candidate_path,
                    None,
                    root / "question_review_events.jsonl",
                    review_backend="jsonl",
                )
                data_url = "data:image/png;base64," + base64.b64encode(b"table screenshot").decode("ascii")
                result = state.save_manual_image_asset(
                    candidate["candidate_key"],
                    data_url,
                    placement="table",
                    reviewer="test",
                )
            finally:
                review_ui.MANUAL_ASSET_ROOT = original_asset_root

        correction = result["event"]["correction"]
        self.assertNotIn("<table", correction["stem"].lower())
        self.assertEqual(correction["stem"], "藥物數據如下：何者正確？")
        self.assertEqual(correction["image_refs"][0]["asset_role"], "table_manual_screenshot")
        self.assertIn("結構化表格文字", result["event"]["notes"])
        self.assertIn("<table", candidate["stem"].lower())

    def test_sql_pattern_has_the_same_false_positive_guard(self) -> None:
        pattern = review_ui.TABLE_DEPENDENCY_SQL_RE
        self.assertIn("[^[:alnum:]_]", pattern)
        self.assertNotEqual(pattern, r"(表中|下表|附表|如下表|table)")

    def test_full_unreviewed_visual_audit_uses_the_same_table_guard(self) -> None:
        args = argparse.Namespace(
            category="",
            subject="",
            year="",
            ordinal="",
            source="all",
            limit=0,
            chunk_size=0,
            include_reviewed=False,
            all_unreviewed=True,
            include_manual_assets=False,
            force=False,
            ai_visual_status="",
        )
        sql = visual_exporter.build_sql(args)
        self.assertIn("COALESCE(human_review_action, '') IN ('', 'unreviewed', 'reset_review')", sql)
        self.assertIn("[^[:alnum:]_]", sql)
        self.assertIn("AND true", sql)

    def test_category_split_keeps_independent_resumable_task_roots(self) -> None:
        selected = [
            {"candidate_key": "a", "category": "藥師"},
            {"candidate_key": "b", "category": "物理治療師"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            tasks, results, counts = visual_exporter.write_category_chunks(
                selected,
                Path(directory),
                "20260730-150500",
                24,
                split_by_category=True,
            )
            self.assertEqual(counts, {"藥師": 1, "物理治療師": 1})
            self.assertEqual(len(tasks), 2)
            self.assertEqual(len(results), 2)
            self.assertTrue(all("category__" in str(path) for path in tasks))


if __name__ == "__main__":
    unittest.main()
