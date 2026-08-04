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

AGY_RUNNER_PATH = PROJECT_ROOT / "scripts" / "run_antigravity_question_audit.py"
AGY_RUNNER_SPEC = importlib.util.spec_from_file_location("run_antigravity_question_audit", AGY_RUNNER_PATH)
assert AGY_RUNNER_SPEC and AGY_RUNNER_SPEC.loader
runner = importlib.util.module_from_spec(AGY_RUNNER_SPEC)
sys.modules["run_antigravity_question_audit"] = runner
AGY_RUNNER_SPEC.loader.exec_module(runner)


def packet() -> dict[str, object]:
    return {
        "request": {
            "system_instruction": "只回傳 <FINAL_JSON>。",
            "questions": [{"candidate_key": "question-a", "content": {"stem": "題幹", "options": []}}],
        }
    }


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


class AntigravityRunnerTests(unittest.TestCase):
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

    def test_prompt_retries_with_complete_patch_requirement(self) -> None:
        initial_prompt = runner.prompt_for_packet(packet(), attempt=1)
        prompt = runner.prompt_for_packet(packet(), attempt=2)
        self.assertIn("格式修復重試", prompt)
        self.assertIn("不得新增、刪除、重新命名選項 key", prompt)
        self.assertIn("recommended_action=fix_parser", prompt)
        self.assertIn("不得新增、刪除、重新命名選項 key", initial_prompt)
        self.assertNotIn("格式修復重試", initial_prompt)
        self.assertIn('"candidate_key":"question-a"', prompt)

    @patch.object(runner.subprocess, "run", return_value=Completed())
    def test_extracts_success_response_and_usage(self, _run: object) -> None:
        response, usage, model = runner.antigravity_request(
            packet(),
            model="gemini-3.6-flash-low",
            effort="low",
            timeout_seconds=30,
            attempt=1,
        )
        self.assertEqual(response, "<FINAL_JSON>[]</FINAL_JSON>")
        self.assertEqual(usage, {"input_tokens": 10, "output_tokens": 5, "thinking_tokens": 0})
        self.assertEqual(model, "gemini-3.6-flash-low")


if __name__ == "__main__":
    unittest.main()
