from __future__ import annotations

import subprocess
import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RetiredModelValidationTests(unittest.TestCase):
    def test_model_result_validator_fails_closed(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(
                    PROJECT_ROOT
                    / "docs"
                    / "skills"
                    / "national-exam-ai-audit"
                    / "scripts"
                    / "validate_review_results.py"
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("retired", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
