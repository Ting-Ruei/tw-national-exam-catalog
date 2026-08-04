from __future__ import annotations

import importlib.util
import base64
import json
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "llmshare_question_audit.py"
SPEC = importlib.util.spec_from_file_location("llmshare_question_audit", SCRIPT_PATH)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)

UI_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "serve_question_review_ui.py"
UI_SPEC = importlib.util.spec_from_file_location("review_ui", UI_SCRIPT_PATH)
assert UI_SPEC and UI_SPEC.loader
review_ui = importlib.util.module_from_spec(UI_SPEC)
UI_SPEC.loader.exec_module(review_ui)


def task(number: int, paper: str = "paper-a") -> dict[str, object]:
    return {
        "candidate_key": f"moex:{paper}:q{number:03d}",
        "source_registry_key": paper,
        "effective_content_hash": f"hash-{paper}-{number}",
        "stage": "question",
        "exam": {"category": "醫事檢驗師", "subject": "測試", "year": "115", "ordinal": "2", "question_number": str(number), "occurrence": 1},
        "content": {"stem": f"題目 {number}", "options": []},
        "human_state": {"action": "accept" if number == 1 else "", "notes": "已人工通過" if number == 1 else ""},
    }


def model_result(candidate_key: str, model: str, *, status: str = "pass", correction: dict[str, object] | None = None) -> dict[str, object]:
    finding = {
        "issue_family": "ocr_character",
        "location": "stem",
        "observed": "辅酶",
        "suggested": "輔酶",
        "confidence": 0.9,
        "correction_applicable": True,
        "correction_omission_reason": None,
    }
    return {
        "candidate_key": candidate_key,
        "stage": "question",
        "status": status,
        "issue_families": [] if status == "pass" else ["ocr_character"],
        "confidence": 0.9,
        "reason": "未見轉寫問題。" if status == "pass" else "疑似簡體字。",
        "evidence": [] if status == "pass" else [{"field": "content.stem", "before": "辅酶", "after": "輔酶"}],
        "recommended_action": "none" if status == "pass" else "human_review_text",
        "findings": [] if status == "pass" else [finding],
        "correction_coverage": "none" if status == "pass" or correction is None else "complete",
        "uncorrected_findings": (
            [{"finding_index": 1, "reason": "需核對原卷。"}]
            if status != "pass" and correction is None else []
        ),
        "suggested_correction": correction,
        "model": model,
        "prompt_version": audit.PROMPT_VERSION,
    }


class LlmShareQuestionAuditTests(unittest.TestCase):
    def test_paper_preserving_batch_strategies_cover_every_question_once(self) -> None:
        tasks = [task(number) for number in range(1, 81)]
        expected_counts = {"8": 10, "16": 5, "24": 4, "half-paper": 2, "full-paper": 1}
        for strategy, expected in expected_counts.items():
            batches = audit.make_batches(tasks, strategy)
            self.assertEqual(len(batches), expected)
            actual = [row["candidate_key"] for batch in batches for row in batch]
            self.assertEqual(actual, [row["candidate_key"] for row in tasks])
            self.assertTrue(all({row["source_registry_key"] for row in batch} == {"paper-a"} for batch in batches))
        odd = audit.make_batches([task(number) for number in range(1, 6)], "half-paper")
        self.assertEqual([len(batch) for batch in odd], [3, 2])

    def test_plan_creates_each_model_and_strategy_without_cross_paper_batches(self) -> None:
        tasks = [task(number, "paper-a") for number in range(1, 5)] + [task(number, "paper-b") for number in range(1, 4)]
        with tempfile.TemporaryDirectory() as directory:
            manifest = audit.build_plan(tasks, Path(directory), list(audit.DEFAULT_MODELS), list(audit.DEFAULT_STRATEGIES))
            self.assertEqual(manifest["dispatch_count"], len(audit.DEFAULT_MODELS) * (2 + 2 + 2 + 4 + 2))
            self.assertTrue((Path(directory) / "manifest.json").exists())
            for item in manifest["dispatches"]:
                self.assertEqual(len(item["paper_keys"]), 1)

    def test_tagged_response_is_parsed_and_normalized(self) -> None:
        raw = '<FINAL_JSON>\n[{"candidate_key":"a","status":"pass"}]\n</FINAL_JSON>'
        self.assertEqual(audit.parse_raw_response(raw), [{"candidate_key": "a", "status": "pass"}])

    def test_normalizer_repairs_shape_not_semantics(self) -> None:
        row, warnings = audit.normalize_model_row(
            {
                "candidate_key": "a",
                "status": "needs_review",
                "issue_families": ["ocr_character"],
                "confidence": 1,
                "reason": "疑似簡體字。",
                "evidence": "題幹有辅酶",
                "recommended_action": "human_review_text",
                "findings": [{
                    "issue_family": "ocr_character",
                    "location": "stem",
                    "observed": "辅酶",
                    "suggested": "輔酶",
                    "confidence": 1,
                    "correction_applicable": True,
                    "correction_omission_reason": None,
                }],
                "correction_coverage": "complete",
                "uncorrected_findings": [],
                "suggested_correction": {"stem": "輔酶"},
            },
            "gemma4:31b",
            audit.PROMPT_VERSION,
        )
        self.assertIn("evidence_not_list", warnings)
        self.assertEqual(row["evidence"], [{"field": "model_output", "text": "題幹有辅酶"}])
        self.assertEqual(row["suggested_correction"], {"stem": "輔酶"})

    def test_ensemble_keeps_shared_patch_only_when_two_models_agree(self) -> None:
        tasks = [task(1), task(2)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tasks_path = root / "tasks.jsonl"
            audit.write_jsonl(tasks_path, tasks)
            results = []
            for model in audit.DEFAULT_MODELS:
                path = root / f"{audit.model_slug(model)}.jsonl"
                rows = [model_result(str(tasks[0]["candidate_key"]), model)]
                correction = {
                    "gemma4:31b": {"stem": "輔酶 Q10"},
                    "minimax-m2.7": {"stem": "輔酶 Q10"},
                    "deepseek-v4-flash": {"stem": "輔酶Q10"},
                    "qwen3.5:397b": {"stem": "輔酶-Q10"},
                    "gpt-oss:120b": None,
                }[model]
                rows.append(model_result(str(tasks[1]["candidate_key"]), model, status="needs_review", correction=correction))
                audit.write_jsonl(path, rows)
                results.append(f"{model}={path}")
            output = root / "ensemble.jsonl"
            report = audit.build_ensemble(tasks_path, results, output)
            self.assertEqual(report["pass_count"], 1)
            rows = audit.read_jsonl(output)
            self.assertEqual(rows[0]["status"], "pass")
            self.assertEqual(rows[1]["suggested_correction"], {"stem": "輔酶 Q10"})
            self.assertEqual(rows[1]["ensemble"]["shared_correction_models"], ["gemma4:31b", "minimax-m2.7"])

    def test_two_explicit_findings_require_one_complete_correction(self) -> None:
        row, warnings = audit.normalize_model_row(
            {
                "candidate_key": "a",
                "status": "needs_review",
                "issue_families": ["ocr_character"],
                "confidence": 0.9,
                "reason": "有兩處錯字。",
                "evidence": [{"field": "content.stem", "before": "辅酶及鎂胺酸", "after": "輔酶及纈胺酸"}],
                "recommended_action": "human_review_text",
                "findings": [
                    {
                        "issue_family": "ocr_character",
                        "location": "stem",
                        "observed": "辅酶",
                        "suggested": "輔酶",
                        "confidence": 0.9,
                        "correction_applicable": True,
                        "correction_omission_reason": None,
                    },
                    {
                        "issue_family": "ocr_character",
                        "location": "stem",
                        "observed": "鎂胺酸",
                        "suggested": "纈胺酸",
                        "confidence": 0.9,
                        "correction_applicable": True,
                        "correction_omission_reason": None,
                    },
                ],
                "correction_coverage": "complete",
                "uncorrected_findings": [],
                "suggested_correction": {"stem": "輔酶及鎂胺酸"},
            },
            "gemma4:31b",
            audit.PROMPT_VERSION,
        )
        self.assertIn("explicit_finding_without_complete_correction", warnings)
        self.assertIn("complete_coverage_without_complete_patch", warnings)
        self.assertEqual(row["suggested_correction"], {"stem": "輔酶及鎂胺酸"})

    def test_options_correction_object_is_mechanically_repaired_to_ordered_rows(self) -> None:
        row, warnings = audit.normalize_model_row(
            {
                "candidate_key": "a",
                "status": "needs_review",
                "issue_families": ["ocr_character"],
                "confidence": 0.9,
                "reason": "選項有錯字。",
                "evidence": [{"field": "content.options.A", "before": "辅酶", "after": "輔酶"}],
                "recommended_action": "human_review_text",
                "findings": [{
                    "issue_family": "ocr_character",
                    "location": "option_A",
                    "observed": "辅酶",
                    "suggested": "輔酶",
                    "confidence": 0.9,
                    "correction_applicable": True,
                    "correction_omission_reason": None,
                }],
                "correction_coverage": "complete",
                "uncorrected_findings": [],
                "suggested_correction": {"options": {"A": "輔酶"}},
            },
            "gemma4:31b",
            audit.PROMPT_VERSION,
        )
        self.assertEqual(row["suggested_correction"], {"options": [{"key": "A", "text": "輔酶"}]})
        self.assertIn("suggested_correction_options_object_repaired", warnings)
        self.assertNotIn("unsafe_or_empty_suggested_correction_removed", warnings)

    def test_next_and_save_raw_are_idempotent_for_representative_packet(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = audit.build_plan(
                [task(1), task(2)],
                root,
                ["gemma4:31b"],
                ["full-paper"],
            )
            next_row = audit.next_benchmark_packet(root / "manifest.json")
            self.assertFalse(next_row["done"])
            dispatch_id = next_row["dispatch"]["dispatch_id"]
            response = "<FINAL_JSON>" + json.dumps([
                {"candidate_key": "moex:paper-a:q001"},
                {"candidate_key": "moex:paper-a:q002"},
            ]) + "</FINAL_JSON>"
            encoded = base64.b64encode(response.encode()).decode()
            first = audit.save_raw_response(root / "manifest.json", dispatch_id, encoded, 1234)
            second = audit.save_raw_response(root / "manifest.json", dispatch_id, encoded, 1234)
            self.assertEqual(first["state"], "saved")
            self.assertEqual(second["state"], "already_saved")
            self.assertTrue(audit.next_benchmark_packet(root / "manifest.json")["done"])
            timings = json.loads((root / "benchmark_timings.json").read_text(encoding="utf-8"))
            self.assertEqual(len(timings["runs"]), 1)

    def test_catch_report_never_contains_an_approval_or_reset_event(self) -> None:
        tasks = [task(1), task(2)]
        ensemble = [
            {"candidate_key": tasks[0]["candidate_key"], "status": "needs_review", "issue_families": ["ocr_character"], "reason": "需核對", "ensemble": {}},
            {"candidate_key": tasks[1]["candidate_key"], "status": "pass"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tasks_path = root / "tasks.jsonl"
            ensemble_path = root / "ensemble.jsonl"
            output = root / "catch.json"
            audit.write_jsonl(tasks_path, tasks)
            audit.write_jsonl(ensemble_path, ensemble)
            report = audit.build_catch_report(tasks_path, ensemble_path, output)
            self.assertTrue(report["requires_explicit_human_approval_before_reset"])
            self.assertEqual(report["scope"]["already_reviewed_reset_candidates"], 1)
            self.assertNotIn("approval_ref", json.loads(output.read_text(encoding="utf-8")))

    def test_ai_suggestion_server_gate_requires_unreviewed_or_reset_state(self) -> None:
        state = object.__new__(review_ui.ReviewState)
        state.current_question_review = lambda _key: {"action": "accept"}
        self.assertFalse(state.ai_suggestion_apply_allowed("candidate"))
        state.current_question_review = lambda _key: {"action": "reset_review"}
        self.assertTrue(state.ai_suggestion_apply_allowed("candidate"))
        state.current_question_review = lambda _key: {}
        self.assertTrue(state.ai_suggestion_apply_allowed("candidate"))


if __name__ == "__main__":
    unittest.main()
