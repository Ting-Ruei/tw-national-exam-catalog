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
        "build_validated_partial_run",
        SCRIPTS / "build_validated_partial_run.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


class BuildValidatedPartialRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = import_script()

    def test_accepts_only_complete_validated_nonexcluded_segments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            segments = root / "segments"
            segments.mkdir()
            full_manifest = root / "manifest.json"
            batches = [
                {
                    "batch_id": f"ocr_text__gpt-5.6-luna__{number:05d}",
                    "task_count": 2,
                    "candidate_keys": [f"k{number}a", f"k{number}b"],
                    "packet_path": f"packets/{number}.json",
                    "request_path": f"requests/{number}.json",
                    "packet_sha256": f"sha:{number}",
                }
                for number in range(1, 5)
            ]
            write_json(
                full_manifest,
                {
                    "schema_version": "national_exam_sparse_run_manifest_v1",
                    "lane": "ocr_text",
                    "task_count": 8,
                    "batches": batches,
                },
            )
            for segment_index, batch_numbers in ((1, (1, 2)), (2, (3, 4))):
                batch_ids = [batches[number - 1]["batch_id"] for number in batch_numbers]
                write_json(
                    segments
                    / f"segment_{segment_index:05d}_ocr_text.manifest.json",
                    {"batch_ids": batch_ids},
                )
                rows = [
                    {
                        "batch_id": batch_id,
                        "model": "gpt-5.6-luna",
                        "prompt_version": "national_exam_sparse_audit_v4",
                        "checked_count": 2,
                        "issues": [],
                    }
                    for batch_id in batch_ids
                ]
                write_jsonl(
                    segments
                    / f"segment_{segment_index:05d}_ocr_text.result.jsonl",
                    rows,
                )
                write_json(
                    segments
                    / f"segment_{segment_index:05d}_ocr_text.validator_report.json",
                    {
                        "ok": True,
                        "expected_batch_count": 2,
                        "result_batch_count": 2,
                        "issue_count": 0,
                        "missing_batch_count": 0,
                        "errors": [],
                    },
                )

            output = root / "partial"
            report = self.module.build_partial_run(
                manifest_path=full_manifest,
                segment_dir=segments,
                output_dir=output,
                excluded_segments={2},
            )

            self.assertEqual(report["accepted_segment_count"], 1)
            self.assertEqual(report["completed_batch_count"], 2)
            self.assertEqual(report["completed_task_count"], 4)
            self.assertEqual(report["missing_batch_count"], 2)
            partial = json.loads((output / "manifest.json").read_text())
            self.assertTrue(partial["partial"])
            self.assertEqual(partial["task_count"], 4)
            self.assertEqual(len(partial["batches"]), 2)
            self.assertEqual(
                partial["batches"][0]["packet_path"],
                str((root / "packets" / "1.json").resolve()),
            )
            self.assertEqual(
                partial["batches"][0]["request_path"],
                str((root / "requests" / "1.json").resolve()),
            )
            results = [
                json.loads(line)
                for line in (output / "results.jsonl").read_text().splitlines()
            ]
            self.assertEqual(len(results), 2)
            self.assertEqual(
                report["rejected_segments"][0]["reason"],
                "explicitly_excluded",
            )

    def test_rejects_result_without_matching_validation_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            segments = root / "segments"
            segments.mkdir()
            manifest = root / "manifest.json"
            batch = {
                "batch_id": "ocr_text__gpt-5.6-luna__00001",
                "task_count": 1,
                "candidate_keys": ["k"],
                "packet_path": "packets/1.json",
                "request_path": "requests/1.json",
                "packet_sha256": "sha",
            }
            write_json(
                manifest,
                {"task_count": 1, "batches": [batch]},
            )
            write_json(
                segments / "segment_00001_ocr_text.manifest.json",
                {"batch_ids": [batch["batch_id"]]},
            )
            write_jsonl(
                segments / "segment_00001_ocr_text.result.jsonl",
                [
                    {
                        "batch_id": batch["batch_id"],
                        "checked_count": 1,
                        "issues": [],
                    }
                ],
            )
            with self.assertRaisesRegex(ValueError, "no fully validated"):
                self.module.build_partial_run(
                    manifest_path=manifest,
                    segment_dir=segments,
                    output_dir=root / "partial",
                    excluded_segments=set(),
                )

    def test_accepts_isolated_result_suffix_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            segments = root / "segments"
            segments.mkdir()
            manifest = root / "manifest.json"
            batch = {
                "batch_id": "ocr_text__gpt-5.6-luna__00001",
                "task_count": 1,
                "candidate_keys": ["k"],
                "packet_path": "packets/1.json",
                "request_path": "requests/1.json",
                "packet_sha256": "sha",
            }
            write_json(manifest, {"task_count": 1, "batches": [batch]})
            write_json(
                segments / "segment_00001_ocr_text.manifest.json",
                {"batch_ids": [batch["batch_id"]]},
            )
            write_jsonl(
                segments / "segment_00001_ocr_text.clean_d.result.jsonl",
                [{"batch_id": batch["batch_id"], "checked_count": 1, "issues": []}],
            )
            write_json(
                segments / "segment_00001_ocr_text.clean_d.validation.json",
                {
                    "ok": True,
                    "expected_batch_count": 1,
                    "result_batch_count": 1,
                    "issue_count": 0,
                    "missing_batch_count": 0,
                    "errors": [],
                },
            )
            report = self.module.build_partial_run(
                manifest_path=manifest,
                segment_dir=segments,
                output_dir=root / "partial",
                excluded_segments=set(),
            )
            self.assertEqual(report["accepted_segment_count"], 1)
            self.assertEqual(report["completed_batch_count"], 1)


if __name__ == "__main__":
    unittest.main()
