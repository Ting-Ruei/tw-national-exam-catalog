from __future__ import annotations

import argparse
import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "ingest_indexes_to_postgres.py"
SPEC = importlib.util.spec_from_file_location("ingest_indexes", SCRIPT_PATH)
assert SPEC and SPEC.loader
ingest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ingest)


class MinerUIndexTests(unittest.TestCase):
    def test_skipped_existing_markdown_is_kept_for_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote_root = Path("/remote/review-repo")
            markdown = root / "output" / "question" / "vlm" / "question.md"
            markdown.parent.mkdir(parents=True)
            markdown.write_text("parsed", encoding="utf-8")
            result_csv = root / "results.csv"
            row = {field: "" for field in ingest.MINERU_FIELDS}
            row.update(
                {
                    "task_id": "question",
                    "status": "skipped_existing",
                    "pdf_path": str(remote_root / "question.pdf"),
                    "output_parent": str(remote_root / "output"),
                    "expected_md": str(remote_root / markdown.relative_to(root)),
                }
            )
            with result_csv.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=ingest.MINERU_FIELDS)
                writer.writeheader()
                writer.writerow(row)

            args = argparse.Namespace(
                artifact_source_project_root=root,
                artifact_destination_project_root=remote_root,
            )
            rows, assets = ingest.mineru_sample_rows(args, result_csv, 10)

            self.assertEqual(len(rows), 1)
            self.assertEqual(len(assets), 1)
            self.assertEqual(assets[0]["asset_type"], "markdown")
            self.assertEqual(assets[0]["asset_path"], str(remote_root / markdown.relative_to(root)))


if __name__ == "__main__":
    unittest.main()
