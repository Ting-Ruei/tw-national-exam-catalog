from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import local_answer_evidence as evidence  # noqa: E402


def manifest_for(directory: Path, *, pypdf: str | None = None, plumber: str | None = None) -> dict[str, object]:
    directory.mkdir(parents=True, exist_ok=True)
    layout = """題數： 4題
題號 01 02 03 04
答案 A B C D
"""
    pypdf = pypdf or layout
    plumber = plumber or layout
    pages = directory / "pages.jsonl"
    engines = {
        "pdftotext_layout": {"source_family": "poppler", "text": layout},
        "pypdf": {"source_family": "pypdf", "text": pypdf},
        "pdfplumber": {"source_family": "pdfplumber", "text": plumber},
    }
    pages.write_text(json.dumps({"page": 1, "engines": engines}, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"source_pdf_sha256": "x" * 64, "output": {"pages_jsonl": str(pages)}}


class LocalAnswerEvidenceTests(unittest.TestCase):
    def test_complete_three_family_table_verifies_without_solving(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "ANS.pdf"
            pdf.write_bytes(b"official answer pdf")
            cache = root / "cache"
            manifest = manifest_for(cache)
            manifest["source_pdf_sha256"] = evidence._sha256(pdf)
            with patch.object(evidence.reference, "build_reference", return_value=manifest):
                result = evidence.build_answer_evidence(
                    [{"candidate_key": "q2", "question_number": 2, "answer": "B", "metadata": {"answer_pdf_primary_relative": "ANS.pdf"}}],
                    root,
                    root / "out",
                )
            self.assertEqual(result["q2"]["status"], "verified")
            self.assertEqual(result["q2"]["source_answer"], "B")
            self.assertEqual(result["q2"]["qnum"], 2)
            self.assertEqual(result["q2"]["source_pdf_sha256"], evidence._sha256(pdf))

    def test_candidate_answer_disagreement_is_not_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "ANS.pdf"
            pdf.write_bytes(b"official answer pdf")
            cache = root / "cache"
            manifest = manifest_for(cache)
            manifest["source_pdf_sha256"] = evidence._sha256(pdf)
            with patch.object(evidence.reference, "build_reference", return_value=manifest):
                result = evidence.build_answer_evidence(
                    [{"candidate_key": "q2", "question_number": 2, "answer": "D", "metadata": {"answer_pdf_primary_relative": "ANS.pdf"}}],
                    root,
                    root / "out",
                )
            self.assertEqual(result["q2"]["status"], "source_unverified")
            self.assertEqual(result["q2"]["reason"], "candidate_answer_disagrees_with_source")

    def test_pypdf_fixed_two_digit_concatenation_requires_valid_sequence(self) -> None:
        text = "題數： 4題\n題號 01 0203 04\n答案 A B C D\n"
        count, answers, error = evidence.parse_family_answers(text)
        self.assertEqual(count, 4)
        self.assertIsNone(error)
        self.assertEqual(answers, {1: "A", 2: "B", 3: "C", 4: "D"})

    def test_incomplete_or_disagreeing_source_is_unverified_with_bounded_excerpts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "ANS.pdf"
            pdf.write_bytes(b"another pdf")
            cache = root / "cache"
            bad = manifest_for(cache, pypdf="題數： 4題\n題號 01 02 03\n答案 A B C\n")
            bad["source_pdf_sha256"] = evidence._sha256(pdf)
            with patch.object(evidence.reference, "build_reference", return_value=bad):
                result = evidence.build_answer_evidence(
                    [{"candidate_key": "q1", "question_number": 1, "metadata": {"answer_pdf_primary_relative": "ANS.pdf"}}],
                    root,
                    root / "out",
                )
            item = result["q1"]
            self.assertEqual(item["status"], "source_unverified")
            self.assertLessEqual(len(item["excerpts"]["pypdf"]), evidence.MAX_EXCERPT_CHARS)
            self.assertIn("invalid_answer_table", item["reason"])

    def test_special_mod_is_never_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "ANS_MOD.pdf"
            pdf.write_bytes(b"mod")
            manifest = manifest_for(root / "cache")
            manifest["source_pdf_sha256"] = evidence._sha256(pdf)
            with patch.object(evidence.reference, "build_reference", return_value=manifest):
                result = evidence.build_answer_evidence(
                    [{"candidate_key": "q1", "question_number": 1, "metadata": {"answer_pdf_primary_relative": "ANS_MOD.pdf"}}],
                    root,
                    root / "out",
                )
            self.assertEqual(result["q1"]["status"], "source_unverified")
            self.assertEqual(result["q1"]["reason"], "special_or_correction_answer_pdf")


if __name__ == "__main__":
    unittest.main()
