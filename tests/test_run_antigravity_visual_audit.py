from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = PROJECT_ROOT / "scripts" / "llmshare_question_audit.py"
AUDIT_SPEC = importlib.util.spec_from_file_location("llmshare_question_audit", AUDIT_PATH)
assert AUDIT_SPEC and AUDIT_SPEC.loader
audit = importlib.util.module_from_spec(AUDIT_SPEC)
sys.modules["llmshare_question_audit"] = audit
AUDIT_SPEC.loader.exec_module(audit)

RUNNER_PATH = PROJECT_ROOT / "scripts" / "run_llmshare_question_audit.py"
RUNNER_SPEC = importlib.util.spec_from_file_location("run_llmshare_question_audit", RUNNER_PATH)
assert RUNNER_SPEC and RUNNER_SPEC.loader
llmshare_runner = importlib.util.module_from_spec(RUNNER_SPEC)
sys.modules["run_llmshare_question_audit"] = llmshare_runner
RUNNER_SPEC.loader.exec_module(llmshare_runner)

VISUAL_RUNNER_PATH = PROJECT_ROOT / "scripts" / "run_antigravity_visual_audit.py"
VISUAL_RUNNER_SPEC = importlib.util.spec_from_file_location("run_antigravity_visual_audit", VISUAL_RUNNER_PATH)
assert VISUAL_RUNNER_SPEC and VISUAL_RUNNER_SPEC.loader
runner = importlib.util.module_from_spec(VISUAL_RUNNER_SPEC)
sys.modules["run_antigravity_visual_audit"] = runner
VISUAL_RUNNER_SPEC.loader.exec_module(runner)


def task_rows() -> list[dict[str, object]]:
    return [
        {"candidate_key": "visual-a", "source_fingerprint": "a" * 32, "stem": "Glyceryl trinitrate tablets"},
        {"candidate_key": "visual-b", "source_fingerprint": "b" * 32, "stem": "如下表所示"},
    ]


def final_response(entries: list[dict[str, object]]) -> str:
    return "<FINAL_JSON>" + json.dumps(entries, ensure_ascii=False) + "</FINAL_JSON>"


class Completed:
    returncode = 0
    stderr = ""
    stdout = json.dumps(
        {
            "status": "SUCCESS",
            "response": "<FINAL_JSON>[]</FINAL_JSON>",
            "usage": {"input_tokens": 10, "output_tokens": 5, "thinking_tokens": 0},
        }
    )


class AntigravityVisualAuditTests(unittest.TestCase):
    def test_prompt_makes_tablet_false_positive_rule_explicit(self) -> None:
        prompt = runner.visual_prompt(task_rows(), attempt=2)
        self.assertIn("tablet、tablets、stable", prompt)
        self.assertIn("格式重試", prompt)
        self.assertIn('"candidate_key":"visual-a"', prompt)

    def test_validate_response_normalizes_expected_order(self) -> None:
        answer = final_response(
            [
                {
                    "candidate_key": "visual-b",
                    "visual_status": "visual_required_likely",
                    "confidence": 0.91,
                    "reason": "明確指向下表",
                    "evidence": "如下表所示",
                    "recommended_human_action": "review_pdf_visual",
                },
                {
                    "candidate_key": "visual-a",
                    "visual_status": "visual_not_required_likely",
                    "confidence": 0.97,
                    "reason": "藥錠名稱，不是表格",
                    "evidence": "tablets",
                    "recommended_human_action": "confirm_no_visual_required",
                },
            ]
        )
        normalized, metrics = runner.validate_response(answer, task_rows())
        self.assertEqual([row["candidate_key"] for row in normalized], ["visual-a", "visual-b"])
        self.assertEqual(metrics, {"returned_count": 2, "required_count": 1, "not_required_count": 1, "uncertain_count": 0})

    def test_validate_response_rejects_omitted_candidate(self) -> None:
        answer = final_response(
            [
                {
                    "candidate_key": "visual-a",
                    "visual_status": "visual_not_required_likely",
                    "confidence": 0.97,
                    "reason": "藥錠名稱",
                    "evidence": "tablets",
                    "recommended_human_action": "confirm_no_visual_required",
                }
            ]
        )
        with self.assertRaises(runner.VisualDispatchError) as raised:
            runner.validate_response(answer, task_rows())
        self.assertEqual(raised.exception.kind, "visual_schema_gate")

    def test_completed_result_requires_a_matching_complete_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "visual_ai_audit_results__test.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "candidate_key": "visual-a",
                        "visual_status": "visual_not_required_likely",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertFalse(runner.completed_result(path, task_rows()))

    def test_exclusive_run_lock_rejects_a_second_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with runner.exclusive_run_lock(root):
                with self.assertRaises(SystemExit):
                    with runner.exclusive_run_lock(root):
                        pass

    def test_shared_traffic_lock_rejects_a_second_stream(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "traffic.lock"
            with patch.dict("os.environ", {"AGY_TRAFFIC_LOCK": str(lock_path)}):
                with runner.shared_traffic_lock():
                    with self.assertRaises(SystemExit):
                        with runner.shared_traffic_lock():
                            pass

    @patch.object(runner.subprocess, "run", return_value=Completed())
    def test_request_extracts_success_response_and_usage(self, _run: object) -> None:
        response, usage, model = runner.request(
            task_rows(), model="gemini-3.6-flash-low", effort="low", timeout_seconds=30, attempt=1
        )
        self.assertEqual(response, "<FINAL_JSON>[]</FINAL_JSON>")
        self.assertEqual(usage, {"input_tokens": 10, "output_tokens": 5, "thinking_tokens": 0})
        self.assertEqual(model, "gemini-3.6-flash-low")


if __name__ == "__main__":
    unittest.main()
