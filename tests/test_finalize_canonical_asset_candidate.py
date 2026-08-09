from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "finalize_canonical_asset_candidate.py"
MANIFEST_FIELDS = ("root_label", "relative_path", "entry_type", "bytes", "mtime_ns", "sha256", "link_target")
QUEUE_FIELDS = (
    "logical_relative_path",
    "resolution",
    "canonical_action",
    "authority",
    "rebuild_source",
    "decision_id",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path: Path, fields, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class CanonicalFinalizerTests(unittest.TestCase):
    def fixture(self, base: Path, *, unexpected: bool = False) -> list[str]:
        candidate = base / "candidate"
        candidate.mkdir()
        seed = candidate / "seed.txt"
        seed.write_text("seed\n", encoding="utf-8")
        manifest = base / "initial.csv"
        write_csv(
            manifest,
            MANIFEST_FIELDS,
            [
                {
                    "root_label": "canonical-main",
                    "relative_path": "seed.txt",
                    "entry_type": "file",
                    "bytes": seed.stat().st_size,
                    "mtime_ns": seed.stat().st_mtime_ns,
                    "sha256": digest(seed),
                    "link_target": "",
                }
            ],
        )
        queue = base / "queue.csv"
        paths = [
            "Registry/mineru_runs/status_snapshots/mineru_status__latest.json",
            "Registry/mineru_runs/status_snapshots/mineru_status__latest.txt",
        ]
        if unexpected:
            paths.append("Registry/unapproved.txt")
        write_csv(
            queue,
            QUEUE_FIELDS,
            [
                {
                    "logical_relative_path": path,
                    "resolution": "regenerate_on_ai395",
                    "canonical_action": "omit_then_regenerate",
                    "authority": "runtime_generated",
                    "rebuild_source": "scripts/report_mineru_status.py",
                    "decision_id": "test-v1",
                }
                for path in paths
            ],
        )
        catalog = base / "catalog.csv"
        catalog.write_text(
            "year,exam_code,exam_label,exam_level,category_code,category_label,category_name,subject_code,subject_name,question_set,question_url,answer_url,correction_url,registry_key\n",
            encoding="utf-8",
        )
        return [
            sys.executable,
            str(SCRIPT),
            "--candidate-root",
            str(candidate),
            "--regeneration-queue",
            str(queue),
            "--expect-regeneration-queue-sha256",
            digest(queue),
            "--initial-physical-manifest",
            str(manifest),
            "--expect-initial-physical-manifest-sha256",
            digest(manifest),
            "--catalog",
            str(catalog),
            "--report-dir",
            str(base / "report"),
        ]

    def test_finalizes_exact_supported_queue_and_emits_promotion_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            result = subprocess.run(
                self.fixture(base),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            report = json.loads((base / "report" / "canonical-finalization-report.json").read_text())
            self.assertTrue(report["ready_for_promotion"])
            self.assertEqual(report["counts"]["remaining_regeneration_items"], 0)
            self.assertTrue(
                (base / "candidate" / "Registry/mineru_runs/status_snapshots/mineru_status__latest.json").is_file()
            )
            self.assertTrue((base / "report" / "canonical-final-physical-manifest.csv").is_file())

    def test_rejects_unapproved_regeneration_path_without_mutating_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            result = subprocess.run(
                self.fixture(base, unexpected=True),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("unsupported regeneration path", result.stdout)
            self.assertFalse((base / "candidate" / "Registry").exists())


if __name__ == "__main__":
    unittest.main()
