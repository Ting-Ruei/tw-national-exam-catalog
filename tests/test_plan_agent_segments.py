from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    PROJECT_ROOT / "docs" / "skills" / "national-exam-ai-audit" / "scripts"
)


def import_script():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "plan_agent_segments",
        SCRIPTS / "plan_agent_segments.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


class PlanAgentSegmentsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = import_script()

    def manifest(
        self,
        path: Path,
        lane: str,
        task_counts: list[int],
    ) -> Path:
        batches = [
            {
                "batch_id": f"{lane}__model__{index:05d}",
                "task_count": task_count,
            }
            for index, task_count in enumerate(task_counts, start=1)
        ]
        write_json(
            path,
            {
                "schema_version": "national_exam_sparse_run_manifest_v1",
                "lane": lane,
                "task_count": sum(task_counts),
                "batches": batches,
            },
        )
        return path

    def test_cli_defaults_to_twenty_batches_per_segment(self) -> None:
        with mock.patch.object(
            sys,
            "argv",
            [
                "plan_agent_segments.py",
                "--manifest",
                "run.json",
                "group.json",
                "--output-dir",
                "segments",
            ],
        ):
            args = self.module.parse_args()
        self.assertEqual(
            args.manifest,
            [Path("run.json"), Path("group.json")],
        )
        self.assertEqual(args.batches_per_segment, 20)

    def test_plans_multiple_manifests_in_input_and_batch_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.manifest(root / "ocr.json", "ocr_text", [2, 3, 5])
            second = self.manifest(root / "group.json", "group", [7, 11])
            output = root / "segments"
            plan = self.module.plan_segments(
                [first, second],
                batches_per_segment=2,
                output_dir=output,
            )
            segment_manifests = [
                json.loads(
                    Path(segment["segment_manifest_path"]).read_text(
                        encoding="utf-8"
                    )
                )
                for segment in plan["segments"]
            ]
            plan_exists = (output / "plan.json").exists()

        self.assertEqual(plan["segment_count"], 3)
        self.assertEqual(plan["pending_count"], 3)
        self.assertEqual(plan["completed_count"], 0)
        self.assertEqual(plan["invalid_count"], 0)
        self.assertEqual(
            [segment["lane"] for segment in segment_manifests],
            ["ocr_text", "ocr_text", "group"],
        )
        self.assertEqual(
            segment_manifests[0]["batch_ids"],
            ["ocr_text__model__00001", "ocr_text__model__00002"],
        )
        self.assertEqual(segment_manifests[0]["task_count"], 5)
        self.assertEqual(
            segment_manifests[1]["first_batch_id"],
            "ocr_text__model__00003",
        )
        self.assertEqual(
            segment_manifests[2]["last_batch_id"],
            "group__model__00002",
        )
        self.assertEqual(
            plan["next_pending_by_lane"]["ocr_text"]["first_batch_id"],
            "ocr_text__model__00001",
        )
        self.assertTrue(plan_exists)

    def test_unrelated_results_files_are_not_treated_as_segments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.manifest(root / "run.json", "ocr_text", [4])
            output = root / "segments"
            output.mkdir()
            valid_row = {
                "batch_id": "ocr_text__model__00001",
                "checked_count": 4,
            }
            write_jsonl(output / "results.jsonl", [valid_row])
            write_jsonl(output / "results.partial.jsonl", [valid_row])
            plan = self.module.plan_segments(
                [manifest],
                batches_per_segment=20,
                output_dir=output,
            )

        self.assertEqual(plan["pending_count"], 1)
        self.assertEqual(plan["completed_count"], 0)
        self.assertEqual(plan["invalid_count"], 0)
        self.assertNotIn(
            Path(plan["segments"][0]["expected_result_path"]).name,
            {"results.jsonl", "results.partial.jsonl"},
        )

    def test_existing_result_is_completed_only_when_every_batch_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.manifest(root / "run.json", "ocr_text", [2, 3])
            output = root / "segments"
            initial = self.module.plan_segments(
                [manifest],
                batches_per_segment=20,
                output_dir=output,
            )
            result_path = Path(initial["segments"][0]["expected_result_path"])
            write_jsonl(
                result_path,
                [
                    {
                        "batch_id": "ocr_text__model__00002",
                        "checked_count": 3,
                    },
                    {
                        "batch_id": "ocr_text__model__00001",
                        "checked_count": 2,
                    },
                ],
            )
            completed = self.module.plan_segments(
                [manifest],
                batches_per_segment=20,
                output_dir=output,
            )
            write_jsonl(
                result_path,
                [
                    {
                        "batch_id": "ocr_text__model__00001",
                        "checked_count": 99,
                    }
                ],
            )
            invalid = self.module.plan_segments(
                [manifest],
                batches_per_segment=20,
                output_dir=output,
            )
            segment_manifest = json.loads(
                Path(invalid["segments"][0]["segment_manifest_path"]).read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(completed["completed_count"], 1)
        self.assertEqual(completed["pending_count"], 0)
        self.assertEqual(invalid["invalid_count"], 1)
        self.assertTrue(
            any(
                "checked_count_mismatch" in error
                for error in segment_manifest["validation_errors"]
            )
        )
        self.assertTrue(
            any(
                "missing_batch_ids" in error
                for error in segment_manifest["validation_errors"]
            )
        )

    def test_summary_counts_and_next_pending_skip_invalid_segment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.manifest(
                root / "run.json",
                "semantic_transcription",
                [1, 2, 3, 4, 5],
            )
            output = root / "segments"
            initial = self.module.plan_segments(
                [manifest],
                batches_per_segment=2,
                output_dir=output,
            )
            first_result = Path(initial["segments"][0]["expected_result_path"])
            second_result = Path(initial["segments"][1]["expected_result_path"])
            write_jsonl(
                first_result,
                [
                    {
                        "batch_id": "semantic_transcription__model__00001",
                        "checked_count": 1,
                    },
                    {
                        "batch_id": "semantic_transcription__model__00002",
                        "checked_count": 2,
                    },
                ],
            )
            second_result.write_text("", encoding="utf-8")
            plan = self.module.plan_segments(
                [manifest],
                batches_per_segment=2,
                output_dir=output,
            )

        self.assertEqual(plan["completed_count"], 1)
        self.assertEqual(plan["invalid_count"], 1)
        self.assertEqual(plan["pending_count"], 1)
        self.assertEqual(
            plan["next_pending_by_lane"]["semantic_transcription"][
                "first_batch_id"
            ],
            "semantic_transcription__model__00005",
        )


if __name__ == "__main__":
    unittest.main()
