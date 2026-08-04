from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    PROJECT_ROOT / "docs" / "skills" / "national-exam-ai-audit" / "scripts"
)


def import_script():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "build_prior_pass_recheck_scope",
        SCRIPTS / "build_prior_pass_recheck_scope.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class BuildPriorPassRecheckScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = import_script()

    def task(self, key: str, fingerprint: str | None = None) -> dict:
        return {
            "schema_version": "compact_audit_task_snapshot_v1",
            "candidate_key": key,
            "source_registry_key": "moex:fixture:paper",
            "stage": "question",
            "source_fingerprint": fingerprint or f"fingerprint:{key}",
            "content": {
                "stem": f"{key} 題幹",
                "options": [
                    {"key": label, "text": text}
                    for label, text in zip("ABCD", "甲乙丙丁")
                ],
            },
            "exam": {"subject": "fixture", "question_number": key},
        }

    def prior(self, key: str, fingerprint: str | None = None) -> dict:
        return {
            "schema_version": "national_exam_prior_ai_pass_terminal_v1",
            "candidate_key": key,
            "source_fingerprint": fingerprint or f"fingerprint:{key}",
            "primary_route": "terminal_prior_codex54_pass",
            "route_reasons": ["carry_forward_exact_latest_codex54_pass"],
            "frozen_human_review": {
                "status": "unreviewed",
                "action": None,
                "event_count": 0,
                "updated_at": None,
            },
            "carry_forward_evidence": {
                "provider": "codex",
                "model": "codex-5.4",
                "audit_status": "pass",
            },
            "not_independent_luna_review": True,
            "deterministic_proof": False,
        }

    def test_exact_join_writes_only_prior_pass_rows_in_prior_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tasks = root / "tasks.jsonl"
            prior = root / "prior.jsonl"
            output = root / "scope"
            write_jsonl(
                tasks,
                [self.task("a"), self.task("b"), self.task("c")],
            )
            write_jsonl(prior, [self.prior("c"), self.prior("a")])

            manifest = self.module.build_scope(
                tasks_path=tasks,
                prior_pass_path=prior,
                output_dir=output,
            )

            self.assertEqual(manifest["source_task_count"], 3)
            self.assertEqual(manifest["recheck_task_count"], 2)
            self.assertTrue(all(manifest["invariants"].values()))
            lane = read_jsonl(output / "lanes/ocr_text.jsonl")
            self.assertEqual(
                [row["candidate_key"] for row in lane],
                ["c", "a"],
            )
            self.assertTrue(all("carry_forward_evidence" not in row for row in lane))
            ledger = read_jsonl(output / "coverage_ledger.jsonl")
            self.assertTrue(
                all(
                    row["independent_luna_review_completed"] is False
                    for row in ledger
                )
            )

    def test_duplicate_prior_key_is_rejected_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tasks = root / "tasks.jsonl"
            prior = root / "prior.jsonl"
            output = root / "scope"
            write_jsonl(tasks, [self.task("a")])
            write_jsonl(prior, [self.prior("a"), self.prior("a")])
            with self.assertRaisesRegex(ValueError, "duplicate prior-pass"):
                self.module.build_scope(
                    tasks_path=tasks,
                    prior_pass_path=prior,
                    output_dir=output,
                )
            self.assertFalse(output.exists())

    def test_missing_task_and_fingerprint_drift_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tasks = root / "tasks.jsonl"
            prior = root / "prior.jsonl"
            output = root / "missing"
            write_jsonl(tasks, [self.task("a")])
            write_jsonl(prior, [self.prior("missing")])
            with self.assertRaisesRegex(ValueError, "missing from tasks"):
                self.module.build_scope(
                    tasks_path=tasks,
                    prior_pass_path=prior,
                    output_dir=output,
                )

            write_jsonl(prior, [self.prior("a", "different")])
            with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
                self.module.build_scope(
                    tasks_path=tasks,
                    prior_pass_path=prior,
                    output_dir=root / "drift",
                )

    def test_rejects_rows_already_claiming_independent_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tasks = root / "tasks.jsonl"
            prior = root / "prior.jsonl"
            output = root / "scope"
            write_jsonl(tasks, [self.task("a")])
            row = self.prior("a")
            row["not_independent_luna_review"] = False
            write_jsonl(prior, [row])
            with self.assertRaisesRegex(
                ValueError,
                "not marked as lacking independent LUNA review",
            ):
                self.module.build_scope(
                    tasks_path=tasks,
                    prior_pass_path=prior,
                    output_dir=output,
                )


if __name__ == "__main__":
    unittest.main()
