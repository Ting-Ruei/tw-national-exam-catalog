from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ai395_review_staging as staging  # noqa: E402
import ai395_llm_adapter as llm_adapter  # noqa: E402
from ai395_context_guard import ContextBudgetExceeded, enforce_payload  # noqa: E402
import probe_qwen_mlx_tailscale as qwen_probe  # noqa: E402


class AI395ReviewStagingTests(unittest.TestCase):
    def test_exam_question_count_is_not_compared_to_single_candidate_count(self) -> None:
        ordinary = {"candidate_key": "q1", "metadata": {"expected_question_count": 20}}
        broken = {
            "candidate_key": "q12",
            "metadata": {"expected_question_count": 20, "actual_question_count": 19},
        }
        self.assertEqual(staging.parser_status(ordinary)[:3], ("needs_review", 20, 20))
        self.assertEqual(staging.parser_status(broken)[:3], ("blocked", 20, 19))

    def test_fixture_e2e_has_disjoint_coverage_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "staging.sqlite3"
            args = argparse.Namespace(
                config_dir=staging.DEFAULT_CONFIG_DIR,
                fixture="mini20",
                source_manifest=None,
                mineru_manifest=None,
                artifact_dir=root / "artifacts",
                database_url=f"sqlite://{database}",
                run_id="unittest-mini20",
                source_mode="mock_fixture",
                mineru_mode="mock_fixture",
            )
            report = staging.run_e2e(args)
            self.assertTrue(report["disjoint_coverage"])
            self.assertEqual(report["scope_count"], 20)
            self.assertEqual(report["production_write_count"], 0)
            replay = staging.run_e2e(args)
            self.assertTrue(replay["idempotent_replay"])
            connection = sqlite3.connect(database)
            try:
                self.assertEqual(connection.execute("SELECT count(*) FROM pipeline_candidates").fetchone()[0], 20)
                self.assertEqual(connection.execute("SELECT count(*) FROM formal_dry_runs").fetchone()[0], 1)
            finally:
                connection.close()

    def test_qwen_probe_accepts_only_local_or_tailnet_https(self) -> None:
        self.assertEqual(
            qwen_probe.normalized_base_url("https://macbook.tailnet.ts.net/v1")[0],
            "https://macbook.tailnet.ts.net/v1",
        )
        with self.assertRaises(qwen_probe.ProbeError):
            qwen_probe.normalized_base_url("http://macbook.tailnet.ts.net/v1")
        with self.assertRaises(qwen_probe.ProbeError):
            qwen_probe.normalized_base_url("http://0.0.0.0:11434/v1")
        with self.assertRaisesRegex(qwen_probe.ProbeError, "placeholder"):
            qwen_probe.normalized_base_url("https://你的MacBook.你的tailnet.ts.net/v1")

    def test_qwen_native_adapter_keeps_json_and_pixel_contracts(self) -> None:
        request = llm_adapter.build_request(
            "text_evidence",
            {"candidate_key": "q1", "stem": "題目", "options": [], "metadata": {}},
            [],
            "qwen3.8:27b-mlx",
            256,
        )
        native = llm_adapter.build_native_request(request, [])
        self.assertFalse(native["think"])
        self.assertEqual(native["format"], "json")
        self.assertEqual(native["options"]["num_predict"], 256)
        self.assertEqual(
            llm_adapter._extract_content(
                {"message": {"role": "assistant", "content": '{"status":"pass"}'}}
            ),
            '{"status":"pass"}',
        )
        with self.assertRaises(llm_adapter.PixelsUnavailable):
            llm_adapter.build_packet(
                "vision",
                {
                    "candidate_key": "q1",
                    "stem": "看圖",
                    "options": [],
                    "metadata": {},
                    "image_refs": [{"relative_path": "missing.svg"}],
                },
                {
                    "candidate_key": "q1",
                    "stem": "看圖",
                    "options": [],
                    "metadata": {},
                    "image_refs": [],
                },
                ROOT / "fixtures" / "ai395_review" / "mini20",
            )

    def test_json_parser_recovers_only_unambiguous_root_object(self) -> None:
        self.assertEqual(
            llm_adapter._parse_json_object("說明文字\n{\"status\":\"pass\"}\n結束"),
            {"status": "pass"},
        )
        self.assertEqual(
            llm_adapter._parse_json_object(
                '{"status":"pass"}\n```json\n{"status":"pass"}\n```'
            ),
            {"status": "pass"},
        )
        with self.assertRaisesRegex(llm_adapter.LLMAdapterError, "invalid JSON"):
            llm_adapter._parse_json_object('{"status":"pass"}\n{"status":"finding"}')

    def test_context_guard_fails_closed_before_transport(self) -> None:
        with self.assertRaises(ContextBudgetExceeded) as raised:
            enforce_payload({"prompt": "x" * 2_000}, output_tokens=32, context_limit_tokens=1_024, safety_margin_tokens=64)
        self.assertFalse(raised.exception.details["within_budget"])
        self.assertEqual(raised.exception.details["context_limit_tokens"], 1_024)

    def test_lane_packet_allow_lists_metadata_and_declares_evidence_requests(self) -> None:
        item = {
            "candidate_key": "q1",
            "question_number": "1",
            "stem": "題目",
            "options": [{"key": "A", "text": "選項", "image": {"bytes": 12, "path": "/secret/path.png"}}],
            "metadata": {
                "group_name": "藥師",
                "year": "115",
                "raw_block": "不應該送出的完整來源區塊",
                "question_pdf": "/secret/question.pdf",
            },
        }
        packet, _refs = llm_adapter.build_packet("text_evidence", item, item, ROOT)
        self.assertNotIn("raw_block", packet["metadata"])
        self.assertNotIn("question_pdf", packet["metadata"])
        self.assertNotIn("path", packet["options"][0])
        request = llm_adapter.build_request("text_evidence", packet, [], "qwen", 64)
        self.assertIn("requested_evidence", request["messages"][1]["content"])

    def test_model_agent_performs_one_allowlisted_evidence_followup(self) -> None:
        item = {
            "candidate_key": "q1",
            "question_number": "1",
            "stem": "題目",
            "options": [],
            "metadata": {},
        }
        runtime = {
            "base_url": "https://macbook.tailnet.ts.net/v1",
            "model": "qwen3.8:27b-mlx",
            "timeout": 5,
            "max_tokens": 64,
            "transport": "ollama_native",
            "context_limit_tokens": 131072,
            "context_safety_margin_tokens": 8192,
            "slow_threshold_seconds": 30,
            "max_tool_turns": 1,
        }
        budget = {
            "attempts": 0,
            "calls": 0,
            "errors": 0,
            "max_calls": 3,
            "slow_calls": 0,
            "tool_turns": 0,
            "context_rejections": 0,
            "compact_level": 0,
        }
        first = {"status": "unclear", "requested_evidence": [{"kind": "question_markdown", "reason": "need source"}], "model_called": True}
        second = {"status": "pass", "requested_evidence": [], "model_called": True}
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(staging, "call_lane", side_effect=[first, second]) as call_mock, mock.patch.object(
                staging,
                "collect_evidence",
                return_value=[{"kind": "question_markdown", "status": "available", "text": "來源"}],
            ) as tool_mock:
                result = staging.run_model_agent(
                    db=None,
                    run_id="test",
                    lane="text_evidence",
                    item=item,
                    revised=item,
                    fixture_root=ROOT,
                    model_runtime=runtime,
                    model_budget=budget,
                    evidence_cache_dir=Path(directory),
                )
        self.assertEqual(call_mock.call_count, 2)
        self.assertEqual(tool_mock.call_count, 1)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["tool_turn_count"], 1)
        self.assertEqual(budget["tool_turns"], 1)


if __name__ == "__main__":
    unittest.main()
