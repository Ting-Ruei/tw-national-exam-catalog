from __future__ import annotations

import subprocess
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RetiredStagingEntrypointTests(unittest.TestCase):
    def test_retired_staging_entrypoint_fails_closed(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "ai395_review_staging.py")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("retired", result.stderr.lower())

    def test_retired_http_entrypoint_fails_closed(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "ai395_review_staging_http.py")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("retired", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
