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
        "build_residual_audit_scope",
        SCRIPTS / "build_residual_audit_scope.py",
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


class BuildResidualAuditScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = import_script()
        cls.active_rules = cls.module.load_active_exact_rules()
        cls.semantic_terms = cls.module.load_semantic_terms()
        cls.negative_controls = cls.module.load_negative_controls()

    def task(
        self,
        key: str,
        *,
        stem: str = "一般完整題幹",
        stage: str = "question",
        parser_codes: tuple[str, ...] = (),
        group_ref: str | None = None,
        image: bool = False,
    ) -> dict:
        return {
            "candidate_key": key,
            "source_registry_key": "moex:fixture:paper",
            "stage": stage,
            "content": {
                "stem": stem,
                "options": [
                    {
                        "key": label,
                        "text": value,
                        **(
                            {
                                "image": {
                                    "path_relative": f"assets/{label}.png",
                                    "exists": True,
                                }
                            }
                            if image and label == "A"
                            else {}
                        ),
                    }
                    for label, value in zip("ABCD", ("甲", "乙", "丙", "丁"))
                ],
                "image_refs": [],
                "stem_image": None,
                "group_ref": group_ref,
            },
            "exam": {
                "category": "fixture",
                "subject": "fixture",
                "year": "115",
                "question_number": "1",
            },
            "signals": {
                "parser_issues": [
                    {"code": code, "severity": "warning", "message": code}
                    for code in parser_codes
                ]
            },
            "effective_content_hash": f"hash:{key}",
        }

    def evidence(self, **overrides: object) -> dict:
        value = {
            "frozen_human_review": {
                "status": "unreviewed",
                "action": None,
                "event_count": 0,
                "updated_at": None,
            },
            "provider": "codex",
            "model": "codex-5.4",
            "audit_status": "pass",
            "active": True,
            "superseded_by_human": False,
            "has_suggestion": False,
            "recommended_action": "no_action",
            "visual_status": "",
            "labels": [],
            "finding_codes": [],
            "event_id": 42,
            "event_count": 1,
            "input_hash": "fixture-input",
            "updated_at": "2026-07-30T00:00:00",
        }
        value.update(overrides)
        return value

    def classify(self, task: dict, evidence: dict | None = None) -> dict:
        return self.module.classify_task(
            task,
            evidence or self.evidence(),
            active_rules=self.active_rules,
            semantic_terms=self.semantic_terms,
            negative_controls=self.negative_controls,
        )

    def raw_candidate(self, key: str, **ai_overrides: object) -> dict:
        ai_review = {
            "provider": "codex",
            "model": "codex-5.4",
            "audit_status": "pass",
            "active": True,
            "superseded_by_human": False,
            "suggested_correction": None,
            "suggested_changes": [],
            "labels": [],
            "findings": [],
            "event_id": 42,
            "input_hash": f"input:{key}",
            "updated_at": "2026-07-30T00:00:00",
        }
        ai_review.update(ai_overrides)
        return {
            "candidate_key": key,
            "review": {
                "status": "unreviewed",
                "action": None,
                "event_count": 0,
                "updated_at": None,
            },
            "ai_review": ai_review,
        }

    def write_shard(
        self,
        directory: Path,
        rows: list[dict],
        *,
        number: int = 1,
    ) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"shard_{number:04d}.json"
        path.write_text(
            json.dumps(
                {
                    "candidates": rows,
                    "filtered_count": len(rows),
                    "returned_count": len(rows),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def test_route_priority_parser_then_active_then_group(self) -> None:
        parser_task = self.task(
            "parser",
            stem="承上題，如下圖的 valine 社區藁局",
            parser_codes=("option_order_unusual",),
        )
        self.assertEqual(
            self.classify(parser_task)["primary_route"],
            "parser_queue",
        )

        active_task = self.task(
            "active",
            stem="承上題，如下圖的 valine 社區藁局",
        )
        active = self.classify(active_task)
        self.assertEqual(active["primary_route"], "active_rule_exact")
        self.assertEqual(active["active_rule_matches"][0]["field"], "stem")

        group_task = self.task(
            "group",
            stem="承上題，如下圖所示的 valine 結果",
        )
        self.assertEqual(self.classify(group_task)["primary_route"], "group")

    def test_visual_precedes_semantic_and_answer_stage_never_reaches_model(self) -> None:
        visual = self.task(
            "visual",
            stem="如下圖所示的 valine 結果",
        )
        self.assertEqual(self.classify(visual)["primary_route"], "visual")

        answer = self.task("answer", stage="answer")
        decision = self.classify(answer)
        self.assertEqual(decision["primary_route"], "parser_queue")
        self.assertIn("answer_or_non_question_stage", decision["route_reasons"])

    def test_non_unique_active_rule_match_is_residual_not_terminal(self) -> None:
        task = self.task("ambiguous", stem="藁局與藁局皆在題幹")
        decision = self.classify(task)
        self.assertEqual(decision["primary_route"], "ocr_text")
        self.assertIn(
            "active_rule_match_not_safely_terminal",
            decision["route_reasons"],
        )
        self.assertEqual(
            decision["unresolved_active_rules"][0]["reason"],
            "non_unique_materializable_match",
        )

    def test_terminal_requires_exact_active_codex54_pass_without_suggestion(self) -> None:
        task = self.task("terminal")
        terminal = self.classify(task)
        self.assertEqual(
            terminal["primary_route"],
            "terminal_prior_codex54_pass",
        )
        self.assertTrue(terminal["not_independent_luna_review"])
        self.assertFalse(terminal["deterministic_proof"])

        unsafe_variants = (
            {"provider": "llmshare"},
            {"model": "gpt-5.4"},
            {"audit_status": "needs_review"},
            {"active": False},
            {"superseded_by_human": True},
            {"has_suggestion": True},
            {
                "frozen_human_review": {
                    "status": "reviewed",
                    "action": "accept",
                    "event_count": 1,
                    "updated_at": "2026-07-30T01:00:00",
                }
            },
        )
        for overrides in unsafe_variants:
            with self.subTest(overrides=overrides):
                decision = self.classify(
                    task,
                    self.evidence(**overrides),
                )
                self.assertEqual(decision["primary_route"], "ocr_text")

    def test_raw_join_requires_frozen_human_unreviewed_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            shards = Path(temporary) / "shards"
            raw = self.raw_candidate("reviewed")
            raw["review"] = {
                "status": "reviewed",
                "action": "accept",
                "event_count": 1,
                "updated_at": "2026-07-30T01:00:00",
            }
            self.write_shard(shards, [raw])
            with self.assertRaisesRegex(
                ValueError,
                "not frozen human-unreviewed",
            ):
                self.module.load_raw_ai_evidence(shards)

    def test_source_task_hash_change_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tasks = Path(temporary) / "tasks.jsonl"
            write_jsonl(tasks, [self.task("stable")])
            initial = self.module.sha256_file(tasks)
            with tasks.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(self.task("changed"), ensure_ascii=False) + "\n"
                )
            with self.assertRaisesRegex(
                ValueError,
                "source task file changed during build",
            ):
                self.module.verify_source_hash(tasks, initial)

    def test_duplicate_raw_key_and_missing_raw_key_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shards = root / "shards"
            duplicate = self.raw_candidate("duplicate")
            self.write_shard(shards, [duplicate], number=1)
            self.write_shard(shards, [duplicate], number=2)
            with self.assertRaisesRegex(
                ValueError,
                "duplicate raw candidate key",
            ):
                self.module.load_raw_ai_evidence(shards)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tasks = root / "tasks.jsonl"
            shards = root / "shards"
            output = root / "scope"
            write_jsonl(tasks, [self.task("missing")])
            self.write_shard(shards, [self.raw_candidate("other")])
            with self.assertRaisesRegex(
                ValueError,
                "task candidate key missing from raw shards",
            ):
                self.module.build_scope(
                    tasks_path=tasks,
                    api_response_dir=shards,
                    output_dir=output,
                )
            self.assertFalse(output.exists())

    def test_duplicate_task_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tasks = root / "tasks.jsonl"
            shards = root / "shards"
            output = root / "scope"
            row = self.task("duplicate-task")
            write_jsonl(tasks, [row, row])
            self.write_shard(
                shards,
                [self.raw_candidate("duplicate-task")],
            )
            with self.assertRaisesRegex(
                ValueError,
                "duplicate task candidate key",
            ):
                self.module.build_scope(
                    tasks_path=tasks,
                    api_response_dir=shards,
                    output_dir=output,
                )
            self.assertFalse(output.exists())

    def test_build_scope_writes_disjoint_complete_union_and_minimal_terminal(self) -> None:
        rows = [
            self.task(
                "parser",
                parser_codes=("option_order_unusual",),
            ),
            self.task("active", stem="社區藁局管理"),
            self.task("group", stem="承上題，選出正確敘述"),
            self.task("visual", stem="如下圖所示，選出正確敘述"),
            self.task("semantic", stem="valine 的中文轉錄"),
            self.task("ocr"),
            self.task("terminal"),
        ]
        raw_rows = [
            self.raw_candidate(row["candidate_key"])
            for row in rows
        ]
        for raw in raw_rows:
            if raw["candidate_key"] == "ocr":
                raw["ai_review"]["audit_status"] = "needs_review"

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tasks = root / "tasks.jsonl"
            shards = root / "shards"
            output = root / "scope"
            write_jsonl(tasks, rows)
            self.write_shard(shards, raw_rows)
            manifest = self.module.build_scope(
                tasks_path=tasks,
                api_response_dir=shards,
                output_dir=output,
            )

            self.assertEqual(manifest["task_count"], 7)
            self.assertEqual(manifest["raw_candidate_count"], 7)
            self.assertEqual(
                manifest["route_counts"],
                {
                    "parser_queue": 1,
                    "active_rule_exact": 1,
                    "group": 1,
                    "visual": 1,
                    "semantic_transcription": 1,
                    "ocr_text": 1,
                    "terminal_prior_codex54_pass": 1,
                },
            )
            self.assertTrue(all(manifest["invariants"].values()))
            ledger = read_jsonl(output / "coverage_ledger.jsonl")
            self.assertEqual(len(ledger), 7)
            self.assertEqual(
                {row["candidate_key"] for row in ledger},
                {row["candidate_key"] for row in rows},
            )

            terminal = read_jsonl(output / "terminal/prior_pass.jsonl")
            self.assertEqual(len(terminal), 1)
            self.assertEqual(terminal[0]["candidate_key"], "terminal")
            self.assertNotIn("content", terminal[0])
            self.assertNotIn("exam", terminal[0])
            self.assertTrue(terminal[0]["not_independent_luna_review"])
            self.assertFalse(terminal[0]["deterministic_proof"])
            self.assertEqual(
                terminal[0]["frozen_human_review"],
                {
                    "status": "unreviewed",
                    "action": None,
                    "event_count": 0,
                    "updated_at": None,
                },
            )
            terminal_ledger = next(
                row for row in ledger if row["candidate_key"] == "terminal"
            )
            self.assertEqual(
                terminal_ledger["frozen_human_review"]["status"],
                "unreviewed",
            )

            for lane in self.module.MODEL_LANES:
                lane_rows = read_jsonl(output / f"lanes/{lane}.jsonl")
                self.assertEqual(len(lane_rows), 1)
                self.assertIn("content", lane_rows[0])
                self.assertNotIn("residual_routing", lane_rows[0])


if __name__ == "__main__":
    unittest.main()
