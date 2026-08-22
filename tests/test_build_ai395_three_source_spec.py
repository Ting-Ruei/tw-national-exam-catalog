from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_ai395_three_source_spec.py"
spec = importlib.util.spec_from_file_location("build_ai395_three_source_spec", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class AI395ThreeSourceSpecTests(unittest.TestCase):
    def test_build_spec_selects_only_requested_anomaly_tags(self) -> None:
        result = module.build_spec(
            [
                {"candidate_key": "q1", "metadata": {"ai395_scope_tags": ["notation"]}},
                {"candidate_key": "q2", "metadata": {"ai395_scope_tags": ["clean"]}},
                {"candidate_key": "q3", "metadata": {"ai395_scope_tags": ["visual_missing", "answer_special"]}},
            ],
            scope_id="pilot",
            tags={"notation", "visual_missing"},
        )
        self.assertEqual(result["case_count"], 2)
        self.assertEqual([case["candidate_key"] for case in result["cases"]], ["q1", "q3"])
        self.assertEqual(result["cases"][1]["scope_tags"], ["visual_missing"])
        self.assertEqual(result["cases"][0]["category_scope"], "醫事檢驗師")


if __name__ == "__main__":
    unittest.main()
