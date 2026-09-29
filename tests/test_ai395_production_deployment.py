from __future__ import annotations

import subprocess
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RetiredDeploymentTests(unittest.TestCase):
    def test_retired_production_helper_fails_closed(self) -> None:
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "ai395_catalog_production.sh")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("retired", result.stderr.lower())

    def test_retired_runtime_helper_fails_closed(self) -> None:
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "ai395_catalog_runtime.sh")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("retired", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
