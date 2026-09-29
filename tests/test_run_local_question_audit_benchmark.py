from __future__ import annotations

import subprocess
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RetiredBenchmarkTests(unittest.TestCase):
    def test_entrypoint_fails_closed(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "run_local_question_audit_benchmark.py")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("retired", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
