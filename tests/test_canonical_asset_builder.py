from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_canonical_asset_root.py"


def import_script():
    spec = importlib.util.spec_from_file_location("canonical_asset_builder_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = import_script()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_rows(path: Path, fields, rows, delimiter=","):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=delimiter, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class Fixture:
    def __init__(self, base: Path):
        self.base = base
        self.left_root = base / "left"
        self.right_root = base / "right"
        self.left_root.mkdir()
        self.right_root.mkdir()
        self.left_manifest = base / "left.csv"
        self.right_manifest = base / "right.csv"
        self.comparison = base / "comparison.csv"
        self.resolution = base / "resolution.csv"
        self.left_map = base / "left-map.tsv"
        self.right_map = base / "right-map.tsv"
        self.left_symlinks = base / "left-symlinks.tsv"
        self.right_symlinks = base / "right-symlinks.tsv"
        self.output = base / "candidates" / "candidate-1"
        self.output.parent.mkdir()
        self.report = base / "reports" / "build-1"
        self.left_rows = []
        self.right_rows = []
        self.resolution_rows = []
        write_rows(self.left_map, MODULE.NAME_MAP_FIELDS, [], "\t")
        write_rows(self.right_map, MODULE.NAME_MAP_FIELDS, [], "\t")
        write_rows(self.left_symlinks, MODULE.SYMLINK_PLAN_FIELDS, [], "\t")
        write_rows(self.right_symlinks, MODULE.SYMLINK_PLAN_FIELDS, [], "\t")

    def add_file(self, side: str, relative: str, content: bytes, storage: str | None = None):
        root = self.left_root if side == "left" else self.right_root
        label = side
        physical = root / (storage or relative)
        physical.parent.mkdir(parents=True, exist_ok=True)
        physical.write_bytes(content)
        row = {
            "root_label": label,
            "relative_path": relative,
            "entry_type": "file",
            "bytes": str(len(content)),
            "mtime_ns": "0",
            "sha256": hashlib.sha256(content).hexdigest(),
            "link_target": "",
        }
        (self.left_rows if side == "left" else self.right_rows).append(row)
        return row

    def add_symlink(self, side: str, relative: str, target_logical: str, target_storage: str):
        root = self.left_root if side == "left" else self.right_root
        label = side
        source_parent = str(Path(relative).parent)
        manifest_link_target = os.path.relpath(target_logical, source_parent)
        staged_link_target = os.path.relpath(target_storage, source_parent)
        physical = root / relative
        physical.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(staged_link_target, physical)
        target_row = next(
            row for row in (self.left_rows if side == "left" else self.right_rows)
            if row["relative_path"] == target_logical
        )
        row = {
            "root_label": label,
            "relative_path": relative,
            "entry_type": "symlink",
            "bytes": str(len(manifest_link_target.encode())),
            "mtime_ns": "0",
            "sha256": "",
            "link_target": manifest_link_target,
        }
        (self.left_rows if side == "left" else self.right_rows).append(row)
        plan = {
            "root_label": label,
            "source_relative_path": relative,
            "target_logical_relative_path": target_logical,
            "target_storage_relative_path": target_storage,
            "normalized_link_target": staged_link_target,
            "bytes": target_row["bytes"],
            "sha256": target_row["sha256"],
        }
        path = self.left_symlinks if side == "left" else self.right_symlinks
        write_rows(path, MODULE.SYMLINK_PLAN_FIELDS, [plan], "\t")
        return row

    def add_mapping(self, side: str, logical: str, storage: str):
        rows = self.left_rows if side == "left" else self.right_rows
        source = next(row for row in rows if row["relative_path"] == logical)
        mapping = {
            "root_label": side,
            "source_relative_path": logical,
            "storage_relative_path": storage,
            "bytes": source["bytes"],
            "sha256": source["sha256"],
        }
        path = self.left_map if side == "left" else self.right_map
        write_rows(path, MODULE.NAME_MAP_FIELDS, [mapping], "\t")

    def finalize(self):
        write_rows(self.left_manifest, MODULE.MANIFEST_FIELDS, self.left_rows)
        write_rows(self.right_manifest, MODULE.MANIFEST_FIELDS, self.right_rows)
        left = {row["relative_path"]: row for row in self.left_rows}
        right = {row["relative_path"]: row for row in self.right_rows}
        comparison_rows = [
            MODULE.expected_comparison_row(path, left.get(path), right.get(path))
            for path in sorted(set(left) | set(right))
        ]
        write_rows(self.comparison, MODULE.COMPARISON_FIELDS, comparison_rows)
        comparison_hash = digest(self.comparison)
        for index, row in enumerate(comparison_rows, 1):
            if row["status"] != "content_conflict":
                continue
            self.resolution_rows.append(
                {
                    "schema_version": "test_v1",
                    "decision_id": "decision-set-v1",
                    "approved_at": "2026-08-09T00:00:00+08:00",
                    "comparison_sha256": comparison_hash,
                    "relative_path": row["relative_path"],
                    "left_sha256": row["left_sha256"],
                    "right_sha256": row["right_sha256"],
                    "resolution": "exclude_both",
                    "canonical_action": "omit",
                    "authority": "none",
                    "rebuild_source": "",
                    "notes": "test",
                }
            )
        write_rows(self.resolution, MODULE.RESOLUTION_FIELDS, self.resolution_rows)

    def command(self, *extra):
        return [
            sys.executable,
            str(SCRIPT),
            "--left-manifest", str(self.left_manifest),
            "--left-label", "left",
            "--left-root", str(self.left_root),
            "--expect-left-manifest-sha256", digest(self.left_manifest),
            "--right-manifest", str(self.right_manifest),
            "--right-label", "right",
            "--right-root", str(self.right_root),
            "--expect-right-manifest-sha256", digest(self.right_manifest),
            "--comparison", str(self.comparison),
            "--expect-comparison-sha256", digest(self.comparison),
            "--resolution", str(self.resolution),
            "--expect-resolution-sha256", digest(self.resolution),
            "--name-map", f"left={self.left_map}",
            "--expect-name-map-sha256", f"left={digest(self.left_map)}",
            "--name-map", f"right={self.right_map}",
            "--expect-name-map-sha256", f"right={digest(self.right_map)}",
            "--safe-symlink-plan", f"left={self.left_symlinks}",
            "--expect-safe-symlink-plan-sha256", f"left={digest(self.left_symlinks)}",
            "--safe-symlink-plan", f"right={self.right_symlinks}",
            "--expect-safe-symlink-plan-sha256", f"right={digest(self.right_symlinks)}",
            "--output-root", str(self.output),
            "--report-dir", str(self.report),
            "--progress-every", "0",
            *extra,
        ]

    def run(self, *extra):
        return subprocess.run(
            self.command(*extra), text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, check=False,
        )


class CanonicalAssetBuilderTests(unittest.TestCase):
    def test_dry_run_builds_hash_bound_plan_without_creating_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            fixture.add_file("left", "shared.txt", b"same")
            fixture.add_file("right", "shared.txt", b"same")
            fixture.add_file("left", "left.txt", b"left")
            fixture.add_file("right", "right.txt", b"right")
            fixture.add_file("left", ".DS_Store", b"left-metadata")
            fixture.add_file("right", ".DS_Store", b"right-metadata")
            fixture.add_file("left", "generated-report.txt", b"left-report")
            fixture.add_file("right", "generated-report.txt", b"right-report")
            fixture.finalize()
            result = fixture.run("--verify-source-files")
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertFalse(fixture.output.exists())
            report = json.loads((fixture.report / "canonical-build-report.json").read_text())
            self.assertEqual(report["state"], "dry_run_complete")
            self.assertEqual(report["counts"]["approved_conflicts"], 2)
            self.assertEqual(report["counts"]["actions"]["omit"], 2)
            self.assertTrue(report["gates"]["source_files_rehashed"])

    def test_missing_resolution_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            fixture.add_file("left", "conflict.txt", b"left")
            fixture.add_file("right", "conflict.txt", b"right")
            fixture.finalize()
            write_rows(fixture.resolution, MODULE.RESOLUTION_FIELDS, [])
            result = fixture.run()
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("exactly one resolution", result.stdout)

    def test_stale_resolution_hash_fails_closed_even_with_rehashed_resolution_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            fixture.add_file("left", "conflict.txt", b"left")
            fixture.add_file("right", "conflict.txt", b"right")
            fixture.finalize()
            fixture.resolution_rows[0]["left_sha256"] = "0" * 64
            write_rows(fixture.resolution, MODULE.RESOLUTION_FIELDS, fixture.resolution_rows)
            result = fixture.run()
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("stale_resolution", result.stdout)

    def test_unhashed_same_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            left = fixture.add_file("left", "shared.txt", b"same")
            right = fixture.add_file("right", "shared.txt", b"same")
            left["sha256"] = ""
            right["sha256"] = ""
            fixture.finalize()
            result = fixture.run()
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("regular file is not SHA-256 bound", result.stdout)

    def test_approved_volatile_drift_requires_flag_and_writes_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            fixture.add_file("left", "latest.json", b"left-old")
            fixture.add_file("right", "latest.json", b"right-old")
            fixture.finalize()
            fixture.resolution_rows[0]["resolution"] = "regenerate_on_ai395"
            fixture.resolution_rows[0]["canonical_action"] = "omit_then_regenerate"
            fixture.resolution_rows[0]["rebuild_source"] = "runtime reporter"
            write_rows(fixture.resolution, MODULE.RESOLUTION_FIELDS, fixture.resolution_rows)
            (fixture.right_root / "latest.json").write_bytes(b"right-new")

            strict = fixture.run("--verify-source-files")
            self.assertEqual(strict.returncode, 2, strict.stdout)
            self.assertIn("source file SHA-256 mismatch", strict.stdout)

            fixture.report = fixture.base / "reports" / "build-2"
            allowed = fixture.run("--verify-source-files", "--allow-approved-volatile-drift")
            self.assertEqual(allowed.returncode, 0, allowed.stdout)
            with (fixture.report / "approved-volatile-drift.csv").open(encoding="utf-8", newline="") as handle:
                drift = list(csv.DictReader(handle))
            self.assertEqual(len(drift), 1)
            self.assertEqual(drift[0]["source_label"], "right")
            report = json.loads((fixture.report / "canonical-build-report.json").read_text())
            self.assertEqual(report["counts"]["approved_volatile_drift"], 1)

    def test_excluded_metadata_drift_is_fatal_even_with_volatile_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            fixture.add_file("left", ".DS_Store", b"left-old")
            fixture.add_file("right", ".DS_Store", b"right-old")
            fixture.finalize()
            (fixture.right_root / ".DS_Store").write_bytes(b"right-new")
            result = fixture.run("--verify-source-files", "--allow-approved-volatile-drift")
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("source file SHA-256 mismatch", result.stdout)

    def test_linux_filename_mapping_is_used_and_written_to_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            logical = "very-long-logical-name.txt"
            storage = ".linux-name-map/objects/aa/" + "a" * 64 + ".txt"
            fixture.add_file("left", logical, b"mapped", storage)
            fixture.add_file("right", "right.txt", b"right")
            fixture.add_mapping("left", logical, storage)
            fixture.finalize()
            result = fixture.run("--apply")
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual((fixture.output / storage).read_bytes(), b"mapped")
            mapping = (fixture.output / ".linux-name-map/canonical-filename-map.tsv").read_text()
            self.assertIn(logical, mapping)
            self.assertFalse((fixture.output / ".canonical-build-incomplete.json").exists())

    def test_apply_supports_legal_255_byte_basename_with_short_atomic_temp_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            basename = "a" * 251 + ".txt"
            self.assertEqual(len(basename.encode("utf-8")), 255)
            fixture.add_file("left", basename, b"max-name")
            fixture.add_file("right", "right.txt", b"right")
            fixture.finalize()
            result = fixture.run("--apply")
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual((fixture.output / basename).read_bytes(), b"max-name")

    def test_safe_symlink_is_rebuilt_to_selected_mapped_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            logical = "long-target.txt"
            storage = ".linux-name-map/objects/bb/" + "b" * 64 + ".txt"
            fixture.add_file("left", logical, b"target", storage)
            fixture.add_mapping("left", logical, storage)
            fixture.add_symlink("left", "links/target.txt", logical, storage)
            next(row for row in fixture.left_rows if row["entry_type"] == "symlink")["link_target"] = (
                "/Users/tim/legacy-assets/" + logical
            )
            fixture.add_file("right", "right.txt", b"right")
            fixture.finalize()
            result = fixture.run("--apply")
            self.assertEqual(result.returncode, 0, result.stdout)
            link = fixture.output / "links/target.txt"
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve().read_bytes(), b"target")

    def test_legacy_absolute_symlink_must_end_with_logical_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            fixture.add_file("left", "target.txt", b"target")
            fixture.add_symlink("left", "links/target.txt", "target.txt", "target.txt")
            next(row for row in fixture.left_rows if row["entry_type"] == "symlink")["link_target"] = (
                "/Users/tim/legacy-assets/different.txt"
            )
            fixture.add_file("right", "right.txt", b"right")
            fixture.finalize()
            result = fixture.run()
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("legacy absolute symlink semantics", result.stdout)

    def test_escaping_symlink_plan_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            fixture.add_file("left", "target.txt", b"target")
            fixture.add_symlink("left", "links/target.txt", "target.txt", "target.txt")
            fixture.add_file("right", "right.txt", b"right")
            with fixture.left_symlinks.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            rows[0]["normalized_link_target"] = "../../../outside"
            write_rows(fixture.left_symlinks, MODULE.SYMLINK_PLAN_FIELDS, rows, "\t")
            fixture.finalize()
            result = fixture.run()
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("escapes root", result.stdout)

    def test_apply_refuses_existing_output_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            fixture.add_file("left", "left.txt", b"left")
            fixture.add_file("right", "right.txt", b"right")
            fixture.finalize()
            fixture.output.mkdir()
            original = (fixture.left_root / "left.txt").read_bytes()
            result = fixture.run("--apply")
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("already exists", result.stdout)
            self.assertEqual((fixture.left_root / "left.txt").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
