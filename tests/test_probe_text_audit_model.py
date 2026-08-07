from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "probe_text_audit_model.py"


def import_script():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("probe_text_audit_model", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TextAuditProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = import_script()

    def test_read_packets_holds_back_unrelated_fields(self):
        row = {
            "case_id": "x",
            "lane": "text",
            "answer": "D",
            "human_review": {"action": "accept"},
            "effective_candidate": {"stem": "題幹"},
            "official_pdf_second_source": {"question_consensus": {"status": "usable"}},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "packets.jsonl"
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            packets = self.module.read_packets(path, {"text"})
        rendered = json.dumps(packets, ensure_ascii=False)
        self.assertNotIn("answer", rendered)
        self.assertNotIn("human_review", rendered)

    def test_read_packets_keeps_mineru_only_when_it_differs(self):
        rows = [
            {
                "case_id": "same",
                "lane": "text",
                "effective_candidate": {"stem": "甲"},
                "mineru_or_parser": {"stem": "甲"},
            },
            {
                "case_id": "different",
                "lane": "text",
                "effective_candidate": {"stem": "氫鍵"},
                "mineru_or_parser": {"stem": "氬鍵"},
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "packets.jsonl"
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
            packets = self.module.read_packets(path, {"text"})
        self.assertTrue(packets[0]["current_and_mineru_same"])
        self.assertNotIn("mineru", packets[0])
        self.assertFalse(packets[1]["current_and_mineru_same"])
        self.assertEqual(packets[1]["mineru"]["stem"], "氬鍵")

    def test_pdf_evidence_deduplicates_within_source_family(self):
        pdf = {
            "question_text_by_engine": {
                "pdftotext_raw": {"source_family": "poppler", "text": "題目"},
                "pdftotext_layout": {"source_family": "poppler", "text": "題目"},
                "pypdf": {"source_family": "pypdf", "text": ""},
            }
        }
        compact = self.module.compact_pdf_evidence(pdf)
        self.assertEqual(
            compact["family_variants"],
            [{"source_family": "poppler", "texts": ["題目"]}],
        )
        self.assertEqual(compact["empty_engines"], ["pypdf"])

    def test_llmshare_response_falls_back_to_reasoning_content(self):
        content, source = self.module.response_content(
            "llmshare",
            {"choices": [{"message": {"content": "", "reasoning_content": "trace"}}]},
        )
        self.assertEqual(content, "trace")
        self.assertEqual(source, "reasoning_content")

    def test_ollama_request_disables_thinking(self):
        payload = self.module.build_request("ollama", "m", [{"case_id": "x"}], 200)
        self.assertFalse(payload["think"])
        self.assertEqual(payload["format"], "json")


if __name__ == "__main__":
    unittest.main()
