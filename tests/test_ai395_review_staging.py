from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ai395_review_staging as staging  # noqa: E402
import probe_qwen_mlx_tailscale as qwen_probe  # noqa: E402


class AI395ReviewStagingTests(unittest.TestCase):
    def test_exam_question_count_is_not_compared_to_single_candidate_count(self) -> None:
        ordinary = {"candidate_key": "q1", "metadata": {"expected_question_count": 20}}
        broken = {
            "candidate_key": "q12",
            "metadata": {"expected_question_count": 20, "actual_question_count": 19},
        }
        self.assertEqual(staging.parser_status(ordinary)[:3], ("needs_review", 20, 20))
        self.assertEqual(staging.parser_status(broken)[:3], ("blocked", 20, 19))

    def test_fixture_e2e_has_disjoint_coverage_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "staging.sqlite3"
            args = argparse.Namespace(
                config_dir=staging.DEFAULT_CONFIG_DIR,
                fixture="mini20",
                source_manifest=None,
                mineru_manifest=None,
                artifact_dir=root / "artifacts",
                database_url=f"sqlite://{database}",
                run_id="unittest-mini20",
                source_mode="mock_fixture",
                mineru_mode="mock_fixture",
            )
            report = staging.run_e2e(args)
            self.assertTrue(report["disjoint_coverage"])
            self.assertEqual(report["scope_count"], 20)
            self.assertEqual(report["production_write_count"], 0)
            replay = staging.run_e2e(args)
            self.assertTrue(replay["idempotent_replay"])
            connection = sqlite3.connect(database)
            try:
                self.assertEqual(connection.execute("SELECT count(*) FROM pipeline_candidates").fetchone()[0], 20)
                self.assertEqual(connection.execute("SELECT count(*) FROM formal_dry_runs").fetchone()[0], 1)
            finally:
                connection.close()

    def test_qwen_probe_accepts_only_local_or_tailnet_https(self) -> None:
        self.assertEqual(
            qwen_probe.normalized_base_url("https://macbook.tailnet.ts.net/v1")[0],
            "https://macbook.tailnet.ts.net/v1",
        )
        with self.assertRaises(qwen_probe.ProbeError):
            qwen_probe.normalized_base_url("http://macbook.tailnet.ts.net/v1")
        with self.assertRaises(qwen_probe.ProbeError):
            qwen_probe.normalized_base_url("http://0.0.0.0:11434/v1")
        with self.assertRaisesRegex(qwen_probe.ProbeError, "placeholder"):
            qwen_probe.normalized_base_url("https://你的MacBook.你的tailnet.ts.net/v1")


if __name__ == "__main__":
    unittest.main()
