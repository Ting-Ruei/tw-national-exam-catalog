from __future__ import annotations

import argparse
import base64
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TEST_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
sys.path.insert(0, str(ROOT / "scripts"))

import ai395_review_staging as staging  # noqa: E402
import ai395_llm_adapter as llm_adapter  # noqa: E402
import ai395_feedback as feedback  # noqa: E402
from ai395_context_guard import ContextBudgetExceeded, enforce_payload  # noqa: E402
import probe_qwen_mlx_tailscale as qwen_probe  # noqa: E402
import probe_litellm_glm as glm_probe  # noqa: E402


class AI395ReviewStagingTests(unittest.TestCase):
    def test_local_qwen_context_policy_accepts_192k_but_caps_above_it(self) -> None:
        config = {
            "pipeline": {
                "model_stage": {
                    "advisory_only": True,
                    "context_policy": {},
                }
            },
            "provider_registry": {
                "providers": {
                    "local_qwen_mlx": {
                        "enabled": False,
                        "endpoint_env": "QWEN_MLX_BASE_URL",
                        "model_env": "QWEN_MLX_MODEL",
                        "network_allowed": True,
                        "tailnet_only": True,
                        "transport": "ollama_native",
                    }
                }
            },
        }
        args = argparse.Namespace(
            model_mode="local_qwen_mlx",
            allow_live_provider=True,
            context_limit_tokens=196608,
            context_safety_margin_tokens=8192,
            max_tool_turns=1,
            model_timeout=5,
            model_max_tokens=256,
            model_slow_threshold=30,
        )
        with mock.patch.dict(
            os.environ,
            {
                "QWEN_MLX_ENABLED": "1",
                "QWEN_MLX_BASE_URL": "https://macbook.tailnet.ts.net/v1",
                "QWEN_MLX_MODEL": "qwen3.8:27b-mlx",
            },
            clear=False,
        ):
            runtime = staging.build_model_runtime(config, args)
            self.assertEqual(runtime["context_limit_tokens"], 196608)
            args.context_limit_tokens = 196609
            with self.assertRaisesRegex(staging.StagingContractError, "196608"):
                staging.build_model_runtime(config, args)

    def test_explicit_artifact_manifests_override_default_fixture(self) -> None:
        args = argparse.Namespace(
            fixture="mini20",
            source_manifest=Path("/tmp/real-source-manifest.json"),
            mineru_manifest=Path("/tmp/real-mineru-manifest.json"),
        )
        self.assertEqual(
            staging.locate_manifests(args),
            (args.source_manifest, args.mineru_manifest, "external-existing-artifact"),
        )

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
            self.assertEqual(report["feedback_events"], 1)
            self.assertEqual(report["guardrail_candidates"], 1)
            self.assertEqual(report["guardrail_active"], 0)
            self.assertTrue((root / "artifacts" / "unittest-mini20" / "feedback_events.jsonl").is_file())
            self.assertTrue((root / "artifacts" / "unittest-mini20" / "guardrail_candidates.jsonl").is_file())
            replay = staging.run_e2e(args)
            self.assertTrue(replay["idempotent_replay"])
            connection = sqlite3.connect(database)
            try:
                self.assertEqual(connection.execute("SELECT count(*) FROM pipeline_candidates").fetchone()[0], 20)
                self.assertEqual(connection.execute("SELECT count(*) FROM formal_dry_runs").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT count(*) FROM feedback_events").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT count(*) FROM guardrail_candidates").fetchone()[0], 1)
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

    def test_litellm_glm_runtime_uses_env_secret_without_returning_it(self) -> None:
        config = {
            "pipeline": {
                "model_stage": {
                    "advisory_only": True,
                    "context_policy": {
                        "hard_limit_tokens": 262144,
                        "safety_margin_tokens": 8192,
                        "one_time_extension_limit_tokens": 393216,
                    },
                }
            },
            "provider_registry": {
                "providers": {
                    "litellm_glm": {
                        "enabled": True,
                        "credential_env": "LITELLM_API_KEY",
                        "endpoint_env": "LITELLM_BASE_URL",
                        "model_env": "LITELLM_MODEL",
                        "model_default": "glm-5.3-flash",
                        "transport": "openai_chat_completions",
                        "network_allowed": True,
                        "profile_id": "glm-5.3-flash-litellm",
                    }
                }
            },
        }
        args = argparse.Namespace(
            model_mode="litellm_glm",
            allow_live_provider=True,
            context_limit_tokens=262144,
            context_safety_margin_tokens=8192,
            max_tool_turns=1,
            model_timeout=5,
            model_max_tokens=256,
            model_slow_threshold=30,
        )
        with mock.patch.dict(
            os.environ,
            {
                "LITELLM_BASE_URL": "https://litellm.test.example/v1",
                "LITELLM_MODEL": "glm-5.3-flash",
                "LITELLM_API_KEY": "test-secret-must-not-leak",
                "LITELLM_REASONING_EFFORT": "low",
            },
            clear=False,
        ):
            runtime = staging.build_model_runtime(config, args)
        self.assertEqual(runtime["provider"], "litellm_glm")
        self.assertEqual(runtime["transport"], "openai_chat_completions")
        self.assertEqual(runtime["model"], "glm-5.3-flash")
        self.assertEqual(runtime["reasoning_effort"], "low")
        self.assertFalse(runtime["clear_thinking"])
        self.assertTrue(runtime["send_thinking"])
        self.assertEqual(runtime["context_limit_tokens"], 262144)
        self.assertEqual(runtime["context_extension_limit_tokens"], 393216)
        self.assertNotIn("test-secret-must-not-leak", runtime.values())

    def test_openai_compatible_endpoint_rejects_remote_http_and_credentials(self) -> None:
        self.assertEqual(
            llm_adapter.normalized_openai_base_url("https://litellm.example/v1")[0],
            "https://litellm.example/v1",
        )
        self.assertEqual(
            llm_adapter.normalized_openai_base_url("http://127.0.0.1:4000/v1")[0],
            "http://127.0.0.1:4000/v1",
        )
        with self.assertRaisesRegex(llm_adapter.LLMAdapterError, "HTTPS"):
            llm_adapter.normalized_openai_base_url("http://192.168.10.20:4000/v1")
        with self.assertRaisesRegex(llm_adapter.LLMAdapterError, "credentials"):
            llm_adapter.normalized_openai_base_url("https://user:pass@litellm.example/v1")

    def test_glm_request_is_multimodal_ready_and_reasoning_is_configurable(self) -> None:
        request = llm_adapter.build_request(
            "vision",
            {"candidate_key": "q1", "stem": "看圖", "options": [], "metadata": {}},
            [{
                "data_url": f"{llm_adapter.PNG_DATA_URL_PREFIX}{base64.b64encode(TEST_PNG).decode('ascii')}",
                "bytes": len(TEST_PNG),
                "mime_type": "image/png",
            }],
            "glm-5.3-flash",
            256,
            reasoning_effort="low",
            clear_thinking=True,
        )
        self.assertEqual(request["reasoning_effort"], "low")
        self.assertTrue(request["thinking"]["clear_thinking"])
        self.assertEqual(request["thinking"]["type"], "enabled")
        self.assertNotIn("think", request)
        user_content = request["messages"][1]["content"]
        self.assertIsInstance(user_content, list)
        self.assertEqual(user_content[-1]["type"], "image_url")
        self.assertTrue(user_content[-1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_glm_vision_rejects_non_png_bytes_instead_of_mislabelling_them(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            image = Path(directory) / "not-really-a-png.png"
            image.write_bytes(b"jpeg-or-corrupt-pixels")
            item = {
                "candidate_key": "q1",
                "stem": "看圖",
                "options": [],
                "metadata": {},
                "image_refs": [{"path": str(image)}],
            }
            with self.assertRaisesRegex(llm_adapter.PixelsUnavailable, "real PNG"):
                llm_adapter.build_packet("vision", item, item, ROOT)

    def test_feedback_event_is_bounded_and_never_authorizes_direct_activation(self) -> None:
        before = {
            "candidate_key": "feedback-q1",
            "question_number": "1",
            "stem": "下列何者具有荧膜？",
            "options": [{"key": "A", "text": "選項"}],
        }
        after = {**before, "stem": "下列何者具有莢膜？"}
        event = feedback.build_feedback_event(
            candidate_key="feedback-q1",
            before=before,
            after=after,
            source_kind="human_correction",
            scope="question",
            lane="notation",
            reviewer="owner",
            evidence=[{"kind": "human_review", "reference": "review:1"}],
        )
        self.assertEqual(event["change_class"], "exact_ocr_rule")
        self.assertTrue(event["promotion_policy"]["requires_owner_approval"])
        self.assertFalse(event["promotion_policy"]["may_activate_rule_directly"])
        candidate = feedback.build_guardrail_candidate(
            event,
            ai_result={
                "status": "candidate",
                "guardrail_type": "exact_ocr_rule",
                "rule_proposal": {"status": "proposed", "source": "荧膜", "target": "莢膜"},
                "negative_controls": ["不要改寫 Histoplasma capsulatum"],
            },
        )
        self.assertEqual(candidate["status"], "proposed")
        self.assertFalse(candidate["promotion_policy"]["active"])
        self.assertFalse(candidate["promotion_policy"]["direct_file_write"])

    def test_three_evidence_feedback_requires_independent_families(self) -> None:
        with self.assertRaisesRegex(feedback.FeedbackContractError, "three independent"):
            feedback.build_feedback_event(
                candidate_key="feedback-q2",
                before={"stem": "甲"},
                after={"stem": "乙"},
                source_kind="three_evidence_correction",
                evidence=[{"kind": "pdf", "family": "same"}, {"kind": "pdf", "family": "same"}],
            )

    def test_glm_probe_builds_small_text_and_vision_contract(self) -> None:
        text_payload = glm_probe.build_live_payload("glm-5.3-flash", None)
        self.assertEqual(text_payload["reasoning_effort"], "low")
        self.assertEqual(text_payload["response_format"], {"type": "json_object"})
        self.assertNotIn("think", text_payload)
        with tempfile.NamedTemporaryFile(suffix=".png") as image:
            image.write(TEST_PNG)
            image.flush()
            vision_payload = glm_probe.build_live_payload("glm-5.3-flash", Path(image.name))
        self.assertEqual(vision_payload["messages"][0]["content"][-1]["type"], "image_url")
        self.assertTrue(
            vision_payload["messages"][0]["content"][-1]["image_url"]["url"].startswith(
                "data:image/png;base64,"
            )
        )

    def test_glm_context_can_use_only_one_bounded_extension(self) -> None:
        runtime = {
            "mode": "litellm_glm",
            "provider": "litellm_glm",
            "base_url": "https://litellm.example/v1",
            "model": "glm-5.3-flash",
            "timeout": 5,
            "max_tokens": 64,
            "transport": "openai_chat_completions",
            "context_limit_tokens": 100,
            "context_extension_limit_tokens": 200,
            "context_safety_margin_tokens": 10,
            "max_context_extensions_per_lane": 1,
            "slow_threshold_seconds": 30,
            "max_tool_turns": 0,
        }
        budget = {
            "attempts": 0,
            "calls": 0,
            "errors": 0,
            "max_calls": 3,
            "slow_calls": 0,
            "tool_turns": 0,
            "context_rejections": 0,
            "context_extensions": 0,
            "compact_level": 0,
        }
        context_error = ContextBudgetExceeded(
            {
                "within_budget": False,
                "total_upper_bound_tokens": 180,
                "admitted_input_budget_tokens": 90,
                "context_limit_tokens": 100,
            }
        )
        item = {"candidate_key": "q1", "question_number": "1", "stem": "題目", "options": [], "metadata": {}}
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                staging,
                "call_lane",
                side_effect=[context_error, {"status": "pass", "requested_evidence": [], "model_called": True}],
            ) as call_mock:
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
        self.assertEqual(budget["context_extensions"], 1)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["context_policy"]["context_extension_used"])
        self.assertEqual(result["context_policy"]["effective_limit_tokens"], 200)
        self.assertEqual(result["agentic_trace"][0]["adaptive_action"], "one_time_context_extension")

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
