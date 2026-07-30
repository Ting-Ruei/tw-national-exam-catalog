from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import build_question_candidates_from_mineru as builder  # noqa: E402


def candidate(number: int, occurrence: int = 1) -> dict[str, object]:
    suffix = "" if occurrence == 1 else f":dup{occurrence:02d}"
    return {
        "candidate_key": f"source:q{number:03d}{suffix}",
        "source_registry_key": "source",
        "question_number": str(number),
        "metadata": {"normalized_category_name": "測試類科"},
    }


class QuestionAnswerAlignmentTests(unittest.TestCase):
    def test_answer_number_rows_survive_blank_answer_cells(self) -> None:
        markdown = """
        <table>
          <tr><td>題號</td><td>1</td><td>2</td><td>3</td></tr>
          <tr><td>答案</td><td>A</td><td></td><td>C</td></tr>
        </table>
        """

        self.assertEqual(builder.parse_answer_question_numbers(markdown), {1, 2, 3})

    def test_dense_answer_set_is_authoritative_with_small_header_extra(self) -> None:
        answer_numbers = set(range(1, 81))
        candidate_numbers = answer_numbers | {112}

        authoritative, reason = builder.answer_number_set_is_authoritative(
            answer_numbers,
            candidate_numbers,
        )

        self.assertTrue(authoritative)
        self.assertEqual(reason, "authoritative")

    def test_partial_answer_extract_is_not_authoritative(self) -> None:
        authoritative, reason = builder.answer_number_set_is_authoritative(
            set(range(1, 41)),
            set(range(1, 81)),
        )

        self.assertFalse(authoritative)
        self.assertEqual(reason, "answer_extract_looks_partial")

    def test_document_issue_compares_sets_and_duplicates_not_only_counts(self) -> None:
        candidates = [candidate(number) for number in range(1, 81) if number != 30]
        candidates.append(candidate(29, occurrence=2))

        issues = builder.document_issues(
            candidates,
            "source",
            set(range(1, 81)),
        )

        alignment = [
            issue
            for issue in issues
            if issue.issue_code == "question_answer_number_set_mismatch"
        ]
        self.assertEqual(len(alignment), 1)
        self.assertEqual(alignment[0].issue_json["missing_candidate_numbers"], [30])
        self.assertEqual(alignment[0].issue_json["duplicate_candidate_numbers"], [29])
        self.assertEqual(
            alignment[0].issue_json["candidate_numbers_absent_from_answer_key"],
            [],
        )

    def test_numeric_stem_question_is_recovered_only_inside_number_sequence(self) -> None:
        markdown = """
1.250 mL 輸注液中含多少 mEq？

A.1
B.2
C.3
D.4

2. 第二題？

A.1
B.2
C.3
D.4
"""
        with tempfile.TemporaryDirectory() as directory:
            parsed = builder.parse_questions(
                markdown,
                Path(directory) / "questions.md",
                "113",
            )

        self.assertEqual([item["question_number"] for item in parsed], ["1", "2"])
        self.assertTrue(str(parsed[0]["stem"]).startswith("250 mL"))

    def test_unnumbered_image_stem_between_two_option_sets_is_split(self) -> None:
        body = """
第一題？
A.一
B.二
C.三
D.四

![](images/next-question.jpg)

A.甲
B.乙
C.丙
D.丁
"""

        parts = builder.split_merged_questions("70", body)

        self.assertEqual([number for number, _ in parts], ["70", "71"])
        self.assertIn("next-question.jpg", parts[1][1])

    def test_unnumbered_image_only_question_after_four_options_is_split(self) -> None:
        body = """
第一題？
A.一
B.二
C.三
D.四

下列何者不是活性代謝物？
![](images/image-options.jpg)
"""

        parts = builder.split_merged_questions("64", body)

        self.assertEqual([number for number, _ in parts], ["64", "65"])
        self.assertIn("活性代謝物", parts[1][1])

    def test_group_subquestion_one_is_folded_into_monotonic_exam_number(self) -> None:
        markdown = """
78.依序回答下列3題。

1. 第一個子題？
A.1
B.2
C.3
D.4

79.2.第二個子題？
A.1
B.2
C.3
D.4

80.3.第三個子題？
A.1
B.2
C.3
D.4
"""
        with tempfile.TemporaryDirectory() as directory:
            parsed = builder.parse_questions(
                markdown,
                Path(directory) / "questions.md",
                "109",
            )

        self.assertEqual(
            [item["question_number"] for item in parsed],
            ["78", "79", "80"],
        )
        self.assertIn("第一個子題", parsed[0]["stem"])

    def test_out_of_range_numeric_header_is_not_a_candidate(self) -> None:
        markdown = """
1. 第一題？
A.1
B.2
C.3
D.4

2. 第二題？
A.1
B.2
C.3
D.4

500. page marker
"""
        with tempfile.TemporaryDirectory() as directory:
            parsed = builder.parse_questions(
                markdown,
                Path(directory) / "questions.md",
                "102",
            )

        self.assertEqual([item["question_number"] for item in parsed], ["1", "2"])


if __name__ == "__main__":
    unittest.main()
