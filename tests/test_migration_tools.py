from __future__ import annotations

import csv
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def import_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class MigrationAssetManifestTests(unittest.TestCase):
    def test_sha256_manifest_detects_same_size_content_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            destination = base / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "題目.txt").write_text("alpha", encoding="utf-8")
            (destination / "題目.txt").write_text("alpha", encoding="utf-8")
            manifest = base / "manifest.csv"

            build = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_migration_asset_manifest.py"),
                    "build",
                    "--root",
                    f"main={source}",
                    "--output",
                    str(manifest),
                    "--sha256",
                    "--progress-every",
                    "0",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(build.returncode, 0, build.stdout)
            with manifest.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["relative_path"], "題目.txt")
            self.assertEqual(len(rows[0]["sha256"]), 64)

            verify = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_migration_asset_manifest.py"),
                    "verify",
                    "--manifest",
                    str(manifest),
                    "--root",
                    f"main={destination}",
                    "--require-sha256",
                    "--progress-every",
                    "0",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(verify.returncode, 0, verify.stdout)

            (destination / "題目.txt").write_text("bravo", encoding="utf-8")
            changed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_migration_asset_manifest.py"),
                    "verify",
                    "--manifest",
                    str(manifest),
                    "--root",
                    f"main={destination}",
                    "--require-sha256",
                    "--progress-every",
                    "0",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(changed.returncode, 2, changed.stdout)
            self.assertIn("sha256 mismatch", changed.stdout)

    def test_manifest_refuses_output_inside_source_root(self):
        module = import_script("build_migration_asset_manifest_test", "build_migration_asset_manifest.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(module.output_is_inside_root(root / "manifest.csv", {"main": root}))

    def test_manifest_comparison_requires_hash_equality(self):
        module = import_script("build_migration_asset_manifest_compare_test", "build_migration_asset_manifest.py")
        left = {
            "entry_type": "file",
            "bytes": "5",
            "sha256": "a" * 64,
            "link_target": "",
        }
        right = {**left, "sha256": "b" * 64}
        self.assertEqual(module.comparison_status(left, right), "content_conflict")
        self.assertEqual(module.comparison_status(left, {**left}), "same_sha256")
        self.assertEqual(module.comparison_status(left, None), "left_only")

    def test_verify_rejects_manifest_path_escape(self):
        module = import_script("build_migration_asset_manifest_escape_test", "build_migration_asset_manifest.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mismatch = module.verify_row(
                {
                    "root_label": "main",
                    "relative_path": "../outside",
                    "entry_type": "file",
                    "bytes": "0",
                    "mtime_ns": "0",
                    "sha256": "",
                    "link_target": "",
                },
                {"main": root},
                check_mtime=False,
            )
            self.assertIn("unsafe relative path", mismatch)


class MigrationPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = import_script("migration_preflight_test", "migration_preflight.py")

    def test_root_spec_requires_label(self):
        with self.assertRaises(SystemExit):
            self.module.parse_root_specs("target", ["/data/assets"])

    def test_recursive_usage_counts_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sub").mkdir()
            (root / "a").write_bytes(b"abc")
            (root / "sub" / "b").write_bytes(b"12345")
            files, total_bytes, errors = self.module.recursive_usage(root)
            self.assertEqual(files, 2)
            self.assertEqual(total_bytes, 8)
            self.assertEqual(errors, [])


class ReviewUiMigrationPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = import_script("serve_question_review_ui_migration_test", "serve_question_review_ui.py")

    def test_old_mac_asset_path_rebinds_to_configured_root(self):
        original = self.module.ASSET_ROOT
        try:
            self.module.ASSET_ROOT = Path("/data/tw-national-exam-catalog/國考題資料夾")
            migrated = self.module.project_path(
                "/Users/tim/tw-national-exam-catalog/國考題資料夾/10_official_pdf/sample.pdf"
            )
            self.assertEqual(
                migrated,
                Path("/data/tw-national-exam-catalog/國考題資料夾/10_official_pdf/sample.pdf"),
            )
        finally:
            self.module.ASSET_ROOT = original


if __name__ == "__main__":
    unittest.main()
