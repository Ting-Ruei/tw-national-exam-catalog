from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import build_question_candidates_from_mineru as parser  # noqa: E402
import question_group_detection as groups  # noqa: E402
import serve_question_review_ui as review_ui  # noqa: E402


def candidate(
    number: int,
    stem: str,
    *,
    group_ref: str | None = None,
    source: str = "moex:test:question",
) -> dict[str, object]:
    return {
        "candidate_key": f"{source}:q{number:03d}",
        "source_registry_key": source,
        "question_number": str(number),
        "stem": stem,
        "group_ref": group_ref,
        "metadata": {
            "normalized_category_name": "醫事檢驗師",
            "normalized_subject_name": "醫學分子檢驗學",
            "year": "115",
            "exam_ordinal": "2",
        },
    }


class QuestionGroupDetectionTests(unittest.TestCase):
    def test_small_group_count_accepts_arabic_and_chinese_counts(self) -> None:
        self.assertEqual(groups.small_group_count("3"), 3)
        self.assertEqual(groups.small_group_count("三"), 3)
        self.assertEqual(groups.small_group_count("十二"), 12)
        self.assertEqual(groups.small_group_count("兩"), 2)
        self.assertIsNone(groups.small_group_count("一"))
        self.assertIsNone(groups.small_group_count("二十一"))

    def test_explicit_group_ref_accepts_real_1152_chinese_count_phrase(self) -> None:
        stem = "有關基因 central dogma，依序回答下列三題。"
        self.assertEqual(groups.explicit_group_ref(stem, "42"), "q042-q044")
        self.assertEqual(parser.infer_group_ref(stem, "42"), "q042-q044")

    def test_parser_propagates_chinese_count_group_to_all_members(self) -> None:
        markdown = """
42. 有關基因 central dogma，依序回答下列三題。第一題？
A. 甲
B. 乙
C. 丙
D. 丁
43. 第二題？
A. 甲
B. 乙
C. 丙
D. 丁
44. 第三題？
A. 甲
B. 乙
C. 丙
D. 丁
"""
        parsed = parser.parse_questions(markdown, Path("sample.md"), "115")
        parser.propagate_group_refs(parsed)
        self.assertEqual(
            [item["group_ref"] for item in parsed],
            ["q042-q044", "q042-q044", "q042-q044"],
        )

    def test_question_number_followed_by_age_preserves_group_preamble(self) -> None:
        markdown = """
8.76 歲女性接受化學治療，依序回答下列三題。此時的治療目的為何？
A. 甲
B. 乙
C. 丙
D. 丁
9. 承上題，第二題？
A. 甲
B. 乙
C. 丙
D. 丁
10. 承上題，第三題？
A. 甲
B. 乙
C. 丙
D. 丁
"""
        parsed = parser.parse_questions(markdown, Path("sample.md"), "115")
        parser.propagate_group_refs(parsed)
        self.assertEqual(
            [item["question_number"] for item in parsed],
            ["8", "9", "10"],
        )
        self.assertTrue(parsed[0]["stem"].startswith("76 歲女性"))
        self.assertEqual(
            [item["group_ref"] for item in parsed],
            ["q008-q010", "q008-q010", "q008-q010"],
        )

    def test_age_collision_recovers_continuation_group_anchor(self) -> None:
        markdown = """
44.45 歲患者接受治療，何者正確？
A. 甲
B. 乙
C. 丙
D. 丁
45. 下一題何者正確？
A. 甲
B. 乙
C. 丙
D. 丁
46.72 歲中風患者出現左側忽略，診斷為何？
A. 甲
B. 乙
C. 丙
D. 丁
47. 承上題，治療原則為何？
A. 甲
B. 乙
C. 丙
D. 丁
"""
        parsed = parser.parse_questions(markdown, Path("sample.md"), "115")
        self.assertEqual(
            [item["question_number"] for item in parsed],
            ["44", "45", "46", "47"],
        )
        for item in parsed:
            number = int(item["question_number"])
            item["source_registry_key"] = "moex:test:question"
            item["candidate_key"] = f"moex:test:question:q{number:03d}"
        summary = groups.group_candidate_metrics(parsed)
        self.assertEqual(summary["continuation_without_anchor_count"], 0)
        self.assertIn(
            {
                "source_registry_key": "moex:test:question",
                "group_ref": "q046-q047",
                "detection_sources": ["continuation_marker"],
                "question_numbers": [46, 47],
            },
            summary["groups"],
        )

    def test_ordinary_decimal_line_is_not_a_question_start(self) -> None:
        self.assertIsNone(parser.QUESTION_START_RE_MODERN.search("1.7 3.4\n"))
        self.assertIsNone(parser.QUESTION_START_RE_LEGACY.search("1.7 3.4\n"))

    def test_question_number_followed_by_animal_age_unit_is_preserved(self) -> None:
        markdown = """
20.4 週齡白肉雞出現下痢，最可能感染何種疾病？
A. 甲
B. 乙
C. 丙
D. 丁
21.3 週齡白肉雞出現貧血，最可能感染何種疾病？
A. 甲
B. 乙
C. 丙
D. 丁
22. 下一題何者正確？
A. 甲
B. 乙
C. 丙
D. 丁
"""
        parsed = parser.parse_questions(markdown, Path("sample.md"), "115")
        self.assertEqual(
            [item["question_number"] for item in parsed],
            ["20", "21", "22"],
        )
        self.assertTrue(parsed[1]["stem"].startswith("3 週齡白肉雞"))

    def test_metrics_merge_explicit_and_continuation_evidence(self) -> None:
        rows = [
            candidate(67, "請回答下列 3 題：病例資料", group_ref="q067-q069"),
            candidate(68, "承上題，何者正確？", group_ref="q067-q069"),
            candidate(69, "承上題，何者錯誤？", group_ref="q067-q069"),
        ]
        summary = groups.group_candidate_metrics(rows)
        self.assertEqual(summary["pending_group_count"], 1)
        self.assertEqual(summary["pending_group_question_count"], 3)
        self.assertEqual(summary["unbound_explicit_group_cue_count"], 0)
        self.assertEqual(
            summary["groups"][0]["detection_sources"],
            ["continuation_marker", "explicit_marker", "parser_group_ref"],
        )

    def test_metrics_flag_high_confidence_group_without_parser_binding(self) -> None:
        rows = [
            candidate(42, "依序回答下列三題。"),
            candidate(43, "第二題？"),
            candidate(44, "第三題？"),
        ]
        summary = groups.group_candidate_metrics(rows)
        self.assertEqual(summary["unbound_explicit_group_cue_count"], 1)

    def test_review_ui_infers_existing_unmodified_1152_group(self) -> None:
        rows = [
            candidate(42, "有關基因 central dogma，依序回答下列三題。"),
            candidate(43, "此作用需要何種酵素？"),
            candidate(44, "此作用之產物由哪些鹼基組合？"),
        ]
        state = review_ui.ReviewState.__new__(review_ui.ReviewState)
        inferred = state.inferred_continuation_groups(rows)
        self.assertEqual(len(inferred), 1)
        self.assertEqual(
            [item["question_number"] for item in inferred[0]],
            ["42", "43", "44"],
        )
        self.assertEqual(
            {item["inferred_group_ref"] for item in inferred[0]},
            {"q042-q044"},
        )


if __name__ == "__main__":
    unittest.main()
