from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "docs" / "skills" / "national-exam-ai-audit"
SCRIPTS = SKILL_ROOT / "scripts"
PROFILE = SKILL_ROOT / "profiles" / "gpt-5.6-luna.yaml"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def run_script(name: str, *args: object, expected: int = 0) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / name), *(str(arg) for arg in args)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != expected:
        raise AssertionError(
            f"{name} returned {result.returncode}, expected {expected}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def import_script(name: str):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SparseAuditV4WorkflowTests(unittest.TestCase):
    def task(self) -> dict:
        return {
            "candidate_key": "moex:test:q001",
            "stage": "question",
            "content": {
                "stem": "下列何者屬於社區藁局管理事項？",
                "options": [
                    {"key": "A", "text": "甲"},
                    {"key": "B", "text": "乙"},
                    {"key": "C", "text": "丙"},
                    {"key": "D", "text": "丁"},
                ],
            },
            "exam": {
                "category": "藥師",
                "subject": "藥事行政",
                "year": "115",
                "question_number": "1",
            },
            "source_fingerprint": "fixture-source-v1",
        }

    def compile_run(self, root: Path) -> tuple[Path, dict, dict]:
        tasks = root / "tasks.jsonl"
        output = root / "run"
        write_jsonl(tasks, [self.task()])
        run_script(
            "compile_agent_packets.py",
            "--tasks",
            tasks,
            "--lane",
            "ocr_text",
            "--profile",
            PROFILE,
            "--output-dir",
            output,
        )
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        packet = json.loads(
            (output / manifest["batches"][0]["packet_path"]).read_text(encoding="utf-8")
        )
        return manifest_path, manifest, packet

    def test_compile_validate_and_materialize_active_exact_rule(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path, manifest, packet = self.compile_run(root)
            compact = packet["tasks"][0]
            self.assertEqual(
                [rule["id"] for rule in compact["active_rules"]],
                ["global-ocr-yaoju"],
            )
            batch_id = manifest["batches"][0]["batch_id"]
            results = root / "results.jsonl"
            write_jsonl(
                results,
                [
                    {
                        "batch_id": batch_id,
                        "checked_count": 1,
                        "issues": [
                            {
                                "candidate_key": "moex:test:q001",
                                "field": "stem",
                                "before": "藁局",
                                "after": "藥局",
                                "issue_family": "ocr_character",
                                "source_class": "candidate_mismatch",
                                "route": "deterministic",
                                "confidence": 1.0,
                                "rule_id": "global-ocr-yaoju",
                                "note": "active exact rule global-ocr-yaoju matches the supplied text",
                            }
                        ],
                        "model": "gpt-5.6-luna",
                        "prompt_version": "national_exam_sparse_audit_v4",
                    }
                ],
            )
            validation = root / "validation.json"
            run_script(
                "validate_sparse_results.py",
                "--manifest",
                manifest_path,
                "--results",
                results,
                "--report",
                validation,
            )
            self.assertTrue(json.loads(validation.read_text(encoding="utf-8"))["ok"])

            previews = root / "previews.jsonl"
            report = root / "materialize-report.json"
            run_script(
                "materialize_sparse_corrections.py",
                "--manifest",
                manifest_path,
                "--results",
                results,
                "--validation-report",
                validation,
                "--output",
                previews,
                "--report",
                report,
            )
            preview = json.loads(previews.read_text(encoding="utf-8"))
            self.assertEqual(
                preview["suggested_correction"]["stem"],
                "下列何者屬於社區藥局管理事項？",
            )
            materialize_report = json.loads(report.read_text(encoding="utf-8"))
            self.assertFalse(materialize_report["database_written"])
            self.assertFalse(materialize_report["review_events_written"])

    def test_missing_batch_result_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path, _, _ = self.compile_run(root)
            results = root / "empty-results.jsonl"
            results.write_text("", encoding="utf-8")
            report = root / "validation.json"
            run_script(
                "validate_sparse_results.py",
                "--manifest",
                manifest_path,
                "--results",
                results,
                "--report",
                report,
                expected=1,
            )
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["missing_batch_count"], 1)

    def test_negative_control_blocks_deterministic_rewrite(self) -> None:
        validation = import_script("validate_sparse_results")
        issue = {
            "candidate_key": "moex:109100:301:22:1:question:q026",
            "field": "stem",
            "before": "呈獻",
            "after": "呈現",
            "issue_family": "ocr_character",
            "source_class": "candidate_mismatch",
            "route": "deterministic",
            "confidence": 1.0,
            "rule_id": "unsafe-fixture-rule",
        }
        task = {
            "candidate_key": issue["candidate_key"],
            "content": {"stem": "抗原呈獻作用", "options": []},
            "active_rules": [
                {"id": "unsafe-fixture-rule", "source": "呈獻", "target": "呈現"}
            ],
            "negative_controls": [
                {
                    "id": "preserve-official-chengxian",
                    "observed": "呈獻",
                    "rejected_target": "呈現",
                }
            ],
        }
        errors = validation.issue_errors(issue, task)
        self.assertIn(
            "negative_control_violation:preserve-official-chengxian",
            errors,
        )

    def test_advisory_before_must_match_declared_text_field(self) -> None:
        validation = import_script("validate_sparse_results")
        issue = {
            "candidate_key": "moex:fixture:q001",
            "field": "option_A",
            "before": "只存在題幹",
            "after": None,
            "issue_family": "semantic_ocr",
            "source_class": "source_unverified",
            "route": "human_pdf",
            "confidence": 0.9,
        }
        task = {
            "candidate_key": issue["candidate_key"],
            "content": {
                "stem": "只存在題幹",
                "options": [{"key": "A", "text": "選項文字"}],
            },
        }
        self.assertIn(
            "before_not_found_in_declared_field",
            validation.issue_errors(issue, task),
        )

    def test_visual_compaction_preserves_option_image_metadata(self) -> None:
        common = import_script("v4_common")
        task = {
            "candidate_key": "moex:fixture:visual:q016",
            "stage": "question",
            "content": {
                "stem": "圖像選項題",
                "options": [
                    {
                        "key": "A",
                        "text": "",
                        "image": {
                            "raw_ref": "a.png",
                            "path_relative": "assets/a.png",
                            "exists": True,
                            "source_exists": True,
                            "local_exists": False,
                            "bytes": 123,
                        },
                    }
                ],
                "image_refs": [],
            },
            "exam": {
                "category": "fixture",
                "subject": "fixture",
                "year": "115",
                "question_number": "16",
            },
        }
        compact = common.compact_task(task, lane="visual")
        image = compact["content"]["options"][0]["image"]
        self.assertEqual(image["path_relative"], "assets/a.png")
        self.assertTrue(image["source_exists"])
        self.assertFalse(image["local_exists"])

    def test_compaction_attaches_only_relevant_semantic_and_name_guards(self) -> None:
        common = import_script("v4_common")
        hydrogen = common.compact_task(
            {
                "candidate_key": "moex:fixture:hydrogen",
                "stage": "question",
                "content": {
                    "stem": "蛋白質的氩鍵（hydrogen bonds）",
                    "options": [],
                },
            },
            lane="semantic_transcription",
        )
        self.assertEqual(
            [anchor["id"] for anchor in hydrogen["semantic_anchors"]],
            ["hydrogen-bond-argon-glyph-conflict"],
        )
        self.assertNotIn("scientific_name_policy", hydrogen)

        bacteria = common.compact_task(
            {
                "candidate_key": "moex:fixture:bacteria",
                "stage": "question",
                "content": {
                    "stem": "Staphylococcus aureus",
                    "options": [],
                },
            },
            lane="semantic_transcription",
        )
        self.assertIn("scientific_name_policy", bacteria)
        self.assertNotIn("semantic_anchors", bacteria)

        abbreviated = common.compact_task(
            {
                "candidate_key": "moex:fixture:abbreviated",
                "stage": "question",
                "content": {"stem": "B. cereus", "options": []},
            },
            lane="semantic_transcription",
        )
        self.assertIn("scientific_name_policy", abbreviated)
        self.assertNotIn("semantic_anchors", abbreviated)

    def test_group_continuation_is_not_a_text_lane_finding(self) -> None:
        exporter = import_script("build_review_ui_advisory_results")
        task = {
            "content": {"stem": "承上題，下列何者正確？", "options": []}
        }
        routed = exporter.route_group_scope_issues(
            task,
            [
                {
                    "candidate_key": "group-q1",
                    "field": "stem",
                    "before": "承上題",
                    "after": None,
                    "issue_family": "group_dependency",
                    "source_class": "source_unverified",
                    "route": "group",
                    "confidence": 0.9,
                    "note": "題組線索",
                }
            ],
        )
        self.assertEqual(routed[0]["issue_family"], "group_dependency")
        self.assertEqual(routed[0]["field"], "group_ref")
        self.assertEqual(routed[0]["route"], "group")

    def test_human_queue_prioritizes_flagged_rows_and_builds_focus_links(self) -> None:
        evaluator = import_script("evaluate_shadow_pilot")
        clean_key = "moex:fixture:q001"
        flagged_key = "moex:fixture:q002"
        tasks = {
            clean_key: {
                "candidate_key": clean_key,
                "exam": {"year": "115", "question_number": "1"},
                "content": {"stem": "clean"},
                "sources": {"question_pdf_relative": "paper.pdf"},
            },
            flagged_key: {
                "candidate_key": flagged_key,
                "exam": {"year": "115", "question_number": "2"},
                "content": {"stem": "flagged"},
                "sources": {"question_pdf_relative": "paper.pdf"},
            },
        }
        issues = {
            flagged_key: [
                {
                    "field": "stem",
                    "before": "强化",
                    "after": "強化",
                    "route": "propose_rule",
                }
            ]
        }
        queue = evaluator.build_human_queue(
            tasks,
            {clean_key: "ocr_text", flagged_key: "ocr_text"},
            issues,
            "http://review.example/",
        )
        self.assertEqual(
            [row["candidate_key"] for row in queue],
            [flagged_key, clean_key],
        )
        self.assertIn(
            "focusKey=moex%3Afixture%3Aq002",
            queue[0]["review_url"],
        )
        self.assertEqual(queue[1]["review_checks"][0], "confirm_no_material_issue")

    def test_review_ui_pass_advisory_maps_to_no_action(self) -> None:
        exporter = import_script("build_review_ui_advisory_results")
        self.assertEqual(exporter.recommended_action([]), "no_action")
        self.assertEqual(
            exporter.recommended_action([{"route": "group"}]),
            "review_group",
        )

    def test_shadow_scope_selects_three_papers_and_one_lane_per_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_files: list[Path] = []
            required: list[str] = []
            for paper_no in range(1, 4):
                rows = []
                for question_no in range(1, 5):
                    key = f"moex:fixture:{paper_no}:question:q{question_no:03d}"
                    rows.append(
                        {
                            "candidate_key": key,
                            "source_registry_key": f"moex:fixture:{paper_no}:question",
                            "stage": "question",
                            "content": {
                                "stem": (
                                    "如圖所示，判斷結果"
                                    if question_no == 2
                                    else "一般完整題幹"
                                ),
                                "options": [
                                    {"key": "A", "text": "甲"},
                                    {"key": "B", "text": "乙"},
                                ],
                                "image_refs": [],
                                "group_ref": None,
                            },
                            "exam": {
                                "category": "fixture",
                                "subject": f"paper-{paper_no}",
                                "year": "115",
                                "ordinal": "1",
                                "question_number": str(question_no),
                            },
                            "signals": {"parser_issues": []},
                            "effective_content_hash": f"fixture-hash-{paper_no}-{question_no}",
                        }
                    )
                task_path = root / f"paper-{paper_no}.jsonl"
                write_jsonl(task_path, rows)
                task_files.append(task_path)
                required.append(rows[-1]["candidate_key"])

            output = root / "scope"
            args: list[object] = []
            for task_path in task_files:
                args.extend(["--tasks", task_path])
            for key in required:
                args.extend(["--required-key", key])
            run_script(
                "build_shadow_pilot_scope.py",
                *args,
                "--per-paper",
                2,
                "--output-dir",
                output,
            )
            manifest = json.loads(
                (output / "selection_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["paper_count"], 3)
            self.assertEqual(manifest["task_count"], 6)
            self.assertEqual(sum(manifest["lane_counts"].values()), 6)
            self.assertTrue(set(required).issubset(manifest["selection"]))

    def test_visual_option_assets_take_priority_over_stale_parser_issue(self) -> None:
        selector = import_script("build_shadow_pilot_scope")
        task = {
            "candidate_key": "moex:fixture:visual:q016",
            "content": {
                "stem": "選出正確血液抹片",
                "options": [
                    {"key": "A", "text": "", "image": {"raw_ref": "a.png"}},
                    {"key": "B", "text": "", "image": {"raw_ref": "b.png"}},
                ],
            },
            "signals": {
                "parser_issues": [
                    {"code": "too_few_options", "severity": "error"}
                ]
            },
        }
        lane, reasons = selector.classify_task(
            task,
            semantic_terms=set(),
            negative_keys=set(),
        )
        self.assertEqual(lane, "visual")
        self.assertEqual(reasons, ["visual_signal"])

    def test_shadow_scope_can_route_every_backlog_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tasks = root / "tasks.jsonl"
            rows = [
                {
                    "candidate_key": f"moex:fixture:question:q{number:03d}",
                    "source_registry_key": "moex:fixture:question",
                    "stage": "question",
                    "content": {
                        "stem": "如圖所示" if number == 2 else "一般題目",
                        "options": [{"key": "A", "text": "甲"}],
                        "image_refs": [],
                    },
                    "exam": {
                        "year": "115",
                        "question_number": str(number),
                    },
                }
                for number in (1, 2)
            ]
            write_jsonl(tasks, rows)
            output = root / "scope"
            run_script(
                "build_shadow_pilot_scope.py",
                "--tasks",
                tasks,
                "--all-tasks",
                "--output-dir",
                output,
            )
            manifest = json.loads(
                (output / "selection_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["selection_mode"], "all_tasks")
            self.assertEqual(manifest["task_count"], 2)
            self.assertEqual(manifest["per_paper"], None)
            self.assertEqual(sum(manifest["lane_counts"].values()), 2)

    def test_shadow_scope_can_exclude_already_processed_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tasks = root / "tasks.jsonl"
            rows = [
                {
                    "candidate_key": f"moex:fixture:question:q{number:03d}",
                    "source_registry_key": "moex:fixture:question",
                    "stage": "question",
                    "content": {
                        "stem": "一般題目",
                        "options": [{"key": "A", "text": "甲"}],
                    },
                    "exam": {"year": "115", "question_number": str(number)},
                }
                for number in (1, 2)
            ]
            write_jsonl(tasks, rows)
            excluded = root / "excluded.jsonl"
            write_jsonl(excluded, [rows[0]])
            output = root / "scope"
            run_script(
                "build_shadow_pilot_scope.py",
                "--tasks",
                tasks,
                "--exclude-keys-from",
                excluded,
                "--all-tasks",
                "--output-dir",
                output,
            )
            manifest = json.loads(
                (output / "selection_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["task_count"], 1)
            self.assertEqual(manifest["excluded_key_count"], 1)
            self.assertNotIn(rows[0]["candidate_key"], manifest["selection"])

    def test_group_shadow_control_joins_inferred_range_without_review_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            question_tasks = root / "questions.jsonl"
            rows = []
            for question_no in (19, 20, 21):
                rows.append(
                    {
                        "candidate_key": f"moex:fixture:question:q{question_no:03d}",
                        "source_registry_key": "moex:fixture:question",
                        "stage": "question",
                        "content": {
                            "stem": f"題目 {question_no}",
                            "options": [{"key": "A", "text": "甲"}],
                            "group_ref": None,
                        },
                        "exam": {
                            "category": "fixture",
                            "subject": "fixture",
                            "year": "115",
                            "ordinal": "2",
                            "question_number": str(question_no),
                        },
                        "effective_content_hash": f"hash-{question_no}",
                    }
                )
            write_jsonl(question_tasks, rows)
            group_response = root / "groups.json"
            group_response.write_text(
                json.dumps(
                    {
                        "returned_count": 1,
                        "candidates": [
                            {
                                "group_sheet_key": "fixture-group",
                                "inferred_group_ref": "q019-q021",
                                "inferred_group_kind": "explicit_count",
                                "gate_status": "inferred_continuation",
                                "rows": [
                                    {"candidate_key": row["candidate_key"]} for row in rows
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "group"
            run_script(
                "build_group_shadow_controls.py",
                "--group-api-response",
                group_response,
                "--question-tasks",
                question_tasks,
                "--output-dir",
                output,
            )
            tasks = [
                json.loads(line)
                for line in (output / "group_tasks.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(len(tasks), 3)
            self.assertTrue(
                all(task["content"]["group_ref"] == "q019-q021" for task in tasks)
            )
            self.assertNotIn("review", tasks[0])

    def test_compile_records_source_hash_timestamp_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path, manifest, _packet = self.compile_run(root)
            source_path = Path(manifest["source_tasks"])
            self.assertEqual(
                manifest["source_tasks_sha256"],
                hashlib.sha256(source_path.read_bytes()).hexdigest(),
            )
            self.assertIsNotNone(datetime.fromisoformat(manifest["compiled_at"]).tzinfo)

            result = run_script(
                "compile_agent_packets.py",
                "--tasks",
                source_path,
                "--lane",
                "ocr_text",
                "--profile",
                PROFILE,
                "--output-dir",
                manifest_path.parent,
                expected=1,
            )
            self.assertIn("refusing to overwrite", result.stderr)
            run_script(
                "compile_agent_packets.py",
                "--tasks",
                source_path,
                "--lane",
                "ocr_text",
                "--profile",
                PROFILE,
                "--output-dir",
                manifest_path.parent,
                "--force",
            )

    def test_compile_shrinks_batches_to_compact_packet_byte_limit(self) -> None:
        compiler = import_script("compile_agent_packets")
        common = import_script("v4_common")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = []
            for number in range(1, 4):
                row = self.task()
                row["candidate_key"] = f"moex:fixture:q{number:03d}"
                row["content"]["stem"] = f"題目 {number} " + ("長文字" * 30)
                row["exam"]["question_number"] = str(number)
                rows.append(row)
            compact_tasks = [
                common.compact_task(row, lane="ocr_text") for row in rows
            ]
            two_task_limit = compiler.compact_packet_bytes(
                compiler.build_packet(
                    lane="ocr_text",
                    model="gpt-5.6-luna",
                    batch_no=1,
                    tasks=compact_tasks[:2],
                )
            )
            self.assertGreater(
                compiler.compact_packet_bytes(
                    compiler.build_packet(
                        lane="ocr_text",
                        model="gpt-5.6-luna",
                        batch_no=1,
                        tasks=compact_tasks,
                    )
                ),
                two_task_limit,
            )

            tasks = root / "tasks.jsonl"
            output = root / "run"
            write_jsonl(tasks, rows)
            run_script(
                "compile_agent_packets.py",
                "--tasks",
                tasks,
                "--lane",
                "ocr_text",
                "--profile",
                PROFILE,
                "--output-dir",
                output,
                "--batch-size",
                3,
                "--max-packet-bytes",
                two_task_limit,
            )
            manifest = json.loads(
                (output / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                [batch["task_count"] for batch in manifest["batches"]],
                [2, 1],
            )
            self.assertTrue(
                all(
                    batch["packet_bytes"] <= two_task_limit
                    for batch in manifest["batches"]
                )
            )

    def test_compile_allows_and_marks_oversized_singleton(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tasks = root / "tasks.jsonl"
            output = root / "run"
            write_jsonl(tasks, [self.task()])
            run_script(
                "compile_agent_packets.py",
                "--tasks",
                tasks,
                "--lane",
                "ocr_text",
                "--profile",
                PROFILE,
                "--output-dir",
                output,
                "--max-packet-bytes",
                1,
            )
            manifest = json.loads(
                (output / "manifest.json").read_text(encoding="utf-8")
            )
            batch = manifest["batches"][0]
            packet = json.loads(
                (output / batch["packet_path"]).read_text(encoding="utf-8")
            )
            self.assertTrue(batch["oversized_singleton"])
            self.assertTrue(packet["oversized_singleton"])
            self.assertGreater(batch["packet_bytes"], 1)

    def test_group_compile_preserves_adjacent_paper_affinity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = []
            for number, source_key in enumerate(
                ("paper-a", "paper-a", "paper-b", "paper-b"),
                start=1,
            ):
                row = self.task()
                row["candidate_key"] = f"moex:fixture:q{number:03d}"
                row["source_registry_key"] = source_key
                row["content"]["group_ref"] = None
                row["exam"]["question_number"] = str(number)
                rows.append(row)
            tasks = root / "tasks.jsonl"
            output = root / "run"
            write_jsonl(tasks, rows)
            run_script(
                "compile_agent_packets.py",
                "--tasks",
                tasks,
                "--lane",
                "group",
                "--profile",
                PROFILE,
                "--output-dir",
                output,
                "--batch-size",
                3,
            )
            manifest = json.loads(
                (output / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                [batch["candidate_keys"] for batch in manifest["batches"]],
                [
                    ["moex:fixture:q001", "moex:fixture:q002"],
                    ["moex:fixture:q003", "moex:fixture:q004"],
                ],
            )

    def test_validator_recomputes_packet_hash_and_candidate_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path, manifest, packet = self.compile_run(root)
            batch = manifest["batches"][0]
            packet["tasks"][0]["content"]["stem"] += "遭竄改"
            (manifest_path.parent / batch["packet_path"]).write_text(
                json.dumps(packet, ensure_ascii=False),
                encoding="utf-8",
            )
            results = root / "results.jsonl"
            write_jsonl(
                results,
                [
                    {
                        "batch_id": batch["batch_id"],
                        "checked_count": 1,
                        "issues": [],
                        "model": manifest["model"],
                        "prompt_version": "national_exam_sparse_audit_v4",
                    }
                ],
            )
            report = root / "validation.json"
            run_script(
                "validate_sparse_results.py",
                "--manifest",
                manifest_path,
                "--results",
                results,
                "--report",
                report,
                expected=1,
            )
            errors = json.loads(report.read_text(encoding="utf-8"))["errors"]
            self.assertTrue(
                any(
                    "packet_sha256_mismatch" in row.get("errors", [])
                    for row in errors
                )
            )

            packet["tasks"][0]["content"]["stem"] = self.task()["content"]["stem"]
            (manifest_path.parent / batch["packet_path"]).write_text(
                json.dumps(packet, ensure_ascii=False),
                encoding="utf-8",
            )
            common = import_script("v4_common")
            batch["packet_sha256"] = common.sha256_json(packet)
            batch["candidate_keys"] = ["moex:fixture:wrong"]
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )
            run_script(
                "validate_sparse_results.py",
                "--manifest",
                manifest_path,
                "--results",
                results,
                "--report",
                report,
                expected=1,
            )
            errors = json.loads(report.read_text(encoding="utf-8"))["errors"]
            self.assertTrue(
                any(
                    "manifest_candidate_keys_mismatch_packet_tasks"
                    in row.get("errors", [])
                    for row in errors
                )
            )

    def test_validator_rejects_candidate_overlap_across_batches(self) -> None:
        common = import_script("v4_common")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = []
            for number in (1, 2):
                row = self.task()
                row["candidate_key"] = f"moex:fixture:q{number:03d}"
                row["exam"]["question_number"] = str(number)
                rows.append(row)
            tasks = root / "tasks.jsonl"
            output = root / "run"
            write_jsonl(tasks, rows)
            run_script(
                "compile_agent_packets.py",
                "--tasks",
                tasks,
                "--lane",
                "ocr_text",
                "--profile",
                PROFILE,
                "--output-dir",
                output,
                "--batch-size",
                1,
            )
            manifest_path = output / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            first_key = manifest["batches"][0]["candidate_keys"][0]
            second_batch = manifest["batches"][1]
            second_packet_path = output / second_batch["packet_path"]
            second_packet = json.loads(second_packet_path.read_text(encoding="utf-8"))
            second_packet["tasks"][0]["candidate_key"] = first_key
            second_packet_path.write_text(
                json.dumps(second_packet, ensure_ascii=False),
                encoding="utf-8",
            )
            second_batch["candidate_keys"] = [first_key]
            second_batch["packet_sha256"] = common.sha256_json(second_packet)
            second_batch["packet_bytes"] = len(
                common.compact_json(second_packet).encode("utf-8")
            )
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )
            results = root / "results.jsonl"
            write_jsonl(
                results,
                [
                    {
                        "batch_id": batch["batch_id"],
                        "checked_count": 1,
                        "issues": [],
                        "model": manifest["model"],
                        "prompt_version": "national_exam_sparse_audit_v4",
                    }
                    for batch in manifest["batches"]
                ],
            )
            report = root / "validation.json"
            run_script(
                "validate_sparse_results.py",
                "--manifest",
                manifest_path,
                "--results",
                results,
                "--report",
                report,
                expected=1,
            )
            errors = json.loads(report.read_text(encoding="utf-8"))["errors"]
            self.assertTrue(
                any(
                    "candidate_key_overlaps_batches" in row.get("errors", [])
                    for row in errors
                )
            )

    def test_validator_rejects_duplicate_exact_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path, manifest, _packet = self.compile_run(root)
            batch = manifest["batches"][0]
            issue = {
                "candidate_key": "moex:test:q001",
                "field": "stem",
                "before": "藁局",
                "after": "藥局",
                "issue_family": "ocr_character",
                "source_class": "source_unverified",
                "route": "propose_rule",
                "confidence": 0.99,
            }
            results = root / "results.jsonl"
            write_jsonl(
                results,
                [
                    {
                        "batch_id": batch["batch_id"],
                        "checked_count": 1,
                        "issues": [issue, dict(issue)],
                        "model": manifest["model"],
                        "prompt_version": "national_exam_sparse_audit_v4",
                    }
                ],
            )
            report = root / "validation.json"
            run_script(
                "validate_sparse_results.py",
                "--manifest",
                manifest_path,
                "--results",
                results,
                "--report",
                report,
                expected=1,
            )
            errors = json.loads(report.read_text(encoding="utf-8"))["errors"]
            self.assertTrue(
                any(
                    "duplicate_exact_before_after_for_candidate"
                    in row.get("errors", [])
                    for row in errors
                )
            )


class RuleProposalGateTests(unittest.TestCase):
    def proposal(self) -> dict:
        return {
            "rule_id": "candidate-ocr-example",
            "rule_type": "exact_replacement",
            "lane": "ocr_text",
            "status": "proposed",
            "source": "錯字詞",
            "target": "正字詞",
            "scope": {"categories": ["藥師"], "subjects": []},
            "evidence": [
                {"kind": "official_pdf", "reference": "fixture.pdf#page=1"}
            ],
            "positive_examples": ["題幹含錯字詞"],
            "negative_examples": ["合法同形詞不可更動"],
            "proposed_by": "fixture-model",
            "version": 1,
        }

    def test_valid_proposal_passes_without_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            proposals = root / "proposals.jsonl"
            report = root / "report.json"
            write_jsonl(proposals, [self.proposal()])
            run_script(
                "validate_rule_proposals.py",
                "--proposals",
                proposals,
                "--report",
                report,
            )
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["rules_promoted"], 0)
            self.assertEqual(payload["rules_applied"], 0)

    def test_agent_cannot_self_activate_rule(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            proposal = self.proposal()
            proposal["status"] = "active"
            proposals = root / "proposals.jsonl"
            report = root / "report.json"
            write_jsonl(proposals, [proposal])
            run_script(
                "validate_rule_proposals.py",
                "--proposals",
                proposals,
                "--report",
                report,
                expected=1,
            )
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertIn(
                "agent_cannot_submit_promoted_status",
                payload["errors"][0]["errors"],
            )

    def test_negative_control_pair_cannot_be_proposed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            proposal = self.proposal()
            proposal["source"] = "呈獻"
            proposal["target"] = "呈現"
            proposals = root / "proposals.jsonl"
            report = root / "report.json"
            write_jsonl(proposals, [proposal])
            run_script(
                "validate_rule_proposals.py",
                "--proposals",
                proposals,
                "--report",
                report,
                expected=1,
            )
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertIn(
                "exact_replacement_matches_negative_control",
                payload["errors"][0]["errors"],
            )

    def test_model_only_evidence_is_observed_not_proposed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            proposal = self.proposal()
            proposal["evidence"] = [
                {
                    "kind": "model_observation",
                    "reference": "checkpoint/results.jsonl#pair=錯字詞->正字詞",
                }
            ]
            proposals = root / "proposals.jsonl"
            report = root / "report.json"
            write_jsonl(proposals, [proposal])
            run_script(
                "validate_rule_proposals.py",
                "--proposals",
                proposals,
                "--report",
                report,
                expected=1,
            )
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertIn(
                "proposed_status_requires_source_or_human_evidence",
                payload["errors"][0]["errors"],
            )

            proposal["status"] = "observed"
            write_jsonl(proposals, [proposal])
            run_script(
                "validate_rule_proposals.py",
                "--proposals",
                proposals,
                "--report",
                report,
            )
            self.assertTrue(json.loads(report.read_text(encoding="utf-8"))["ok"])


if __name__ == "__main__":
    unittest.main()
