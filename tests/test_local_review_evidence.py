from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from local_review_evidence import assess_packet, content_fingerprint, load_packets  # noqa: E402


def candidate(stem: str = "完整題幹") -> dict[str, object]:
    return {
        "candidate_key": "exam:Q1",
        "question_number": 1,
        "stem": stem,
        "answer": "A",
        "options": [
            {"key": "A", "text": "甲"},
            {"key": "B", "text": "乙"},
        ],
    }


def packet(item: dict[str, object], *, texts: dict[str, str] | None = None) -> dict[str, object]:
    texts = texts or {"layout": "完整題幹 A甲 B乙", "raw": "完整題幹\nA.甲 B.乙", "pypdf": "完整題幹 A甲 B乙"}
    return {
        "schema_version": "national_exam_three_source_packet_v1",
        "candidate_key": item["candidate_key"],
        "content_fingerprint": content_fingerprint(item),
        "official_pdf_second_source": {
            "pdf_sha256": "f" * 64,
            "page": 1,
            "question_text_by_engine": {
                "pdftotext_layout": {"source_family": "poppler", "text": texts["layout"]},
                "pdftotext_raw": {"source_family": "poppler", "text": texts["raw"]},
                "pypdf": {"source_family": "pypdf", "text": texts["pypdf"]},
                "pdfplumber": {"source_family": "pdfplumber", "text": texts["pypdf"]},
            }
        },
    }


class LocalReviewEvidenceTests(unittest.TestCase):
    def test_requires_three_independent_families_and_collapses_poppler_modes(self) -> None:
        item = candidate()
        result = assess_packet(packet(item), item)
        self.assertTrue(result["verified"])
        self.assertEqual(result["family_count"], 3)
        self.assertEqual(result["families"], ["pdfplumber", "poppler", "pypdf"])

    def test_full_question_match_rejects_stem_only_or_option_difference(self) -> None:
        item = candidate()
        bad = packet(item, texts={"layout": "完整題幹 A甲 B錯", "raw": "完整題幹 A甲 B乙", "pypdf": "完整題幹 A甲 B乙"})
        result = assess_packet(bad, item)
        self.assertFalse(result["verified"])
        self.assertTrue(result["source_available"])
        self.assertIn("three_source_disagreement", result["reasons"])
        self.assertFalse(result["answer_verified"])

    def test_label_punctuation_and_circled_labels_are_not_question_content(self) -> None:
        item = candidate()
        result = assess_packet(packet(item, texts={
            "layout": "完整題幹\nⒶ.甲\nⒷ)乙",
            "raw": "完整題幹 A：甲 B、乙",
            "pypdf": "完整題幹（A）甲 （B）乙",
        }), item)
        self.assertTrue(result["verified"])
        self.assertTrue(result["source_available"])

    def test_compatibility_normalization_cannot_erase_subscript_difference(self) -> None:
        item = candidate("請判斷 H₂O")
        source = "請判斷 H2O A甲 B乙"
        result = assess_packet(packet(item, texts={"layout": source, "raw": source, "pypdf": source}), item)
        self.assertFalse(result["verified"])

    def test_answer_source_is_independent_and_requires_identity_qnum_and_hash(self) -> None:
        item = candidate()
        value = packet(item)
        value["answer_source_evidence"] = {
            "status": "verified",
            "candidate_key": "exam:Q1",
            "question_number": 1,
            "source_sha256": "a" * 64,
            "candidate_answer": "A",
            "source_answer": "A",
        }
        self.assertTrue(assess_packet(value, item)["answer_verified"])
        value["answer_source_evidence"] = {
            "status": "verified",
            "candidate_key": "exam:Q1",
            "question_number": 2,
            "source_sha256": "a" * 64,
        }
        self.assertFalse(assess_packet(value, item)["answer_verified"])

    def test_missing_fingerprint_and_partial_packet_are_unverified(self) -> None:
        item = candidate()
        value = packet(item)
        value.pop("content_fingerprint")
        del value["official_pdf_second_source"]["question_text_by_engine"]["pypdf"]
        result = assess_packet(value, item)
        self.assertFalse(result["verified"])
        self.assertIn("missing_fingerprint", result["reasons"])
        self.assertIn("partial_or_missing_families", result["reasons"])

    def test_jsonl_loader_is_keyed_and_rejects_duplicate_keys(self) -> None:
        item = candidate()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "packets.jsonl"
            path.write_text(json.dumps(packet(item), ensure_ascii=False) + "\n", encoding="utf-8")
            self.assertIn("exam:Q1", load_packets(path))
            path.write_text("\n".join([json.dumps(packet(item)), json.dumps(packet(item))]), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_packets(path)


if __name__ == "__main__":
    unittest.main()
