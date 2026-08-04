from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "build_luna_open_model_comparison.py"
SPEC = importlib.util.spec_from_file_location("luna_open_comparison", SCRIPT_PATH)
assert SPEC and SPEC.loader
comparison = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = comparison
SPEC.loader.exec_module(comparison)


class LunaOpenModelComparisonTests(unittest.TestCase):
    def test_same_site_match_allows_short_observed_fragment(self) -> None:
        pair = {
            "before": "胸膜腔渗液",
            "after": "胸膜腔滲液",
        }
        issue = {
            "observed": "渗液",
            "replacement": "滲液",
            "note": "",
        }
        self.assertTrue(comparison.same_site_match(issue, pair))

    def test_unrelated_issue_is_not_same_site_match(self) -> None:
        pair = {
            "before": "横川吸蟲",
            "after": "橫川吸蟲",
        }
        issue = {
            "observed": "生魚片",
            "replacement": None,
            "note": "應為繁體魚片",
        }
        self.assertFalse(comparison.same_site_match(issue, pair))

    def test_invalid_batch_cannot_count_as_miss_or_match(self) -> None:
        row = {
            "luna_reference": {
                "evidence_transition": {
                    "pairs": [
                        {
                            "before": "KC1",
                            "after": "KCl",
                            "before_present_now": True,
                        }
                    ]
                }
            },
            "model_results": {"test:model": {"batch_valid": False}},
        }
        result = comparison.compare_model(row, "test:model")
        self.assertEqual(result["state"], "invalid_batch")
        self.assertFalse(result["same_site_match"])


if __name__ == "__main__":
    unittest.main()
