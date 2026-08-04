from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    PROJECT_ROOT / "docs" / "skills" / "national-exam-ai-audit" / "scripts"
)


def import_script():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "build_rule_observations",
        SCRIPTS / "build_rule_observations.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BuildRuleObservationsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = import_script()

    def test_scope_is_bounded(self) -> None:
        self.assertEqual(
            self.module.bounded_scope({"藥師", "醫師"}),
            sorted(["醫師", "藥師"]),
        )
        self.assertEqual(
            self.module.bounded_scope({str(value) for value in range(9)}),
            [],
        )

    def test_hash_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "results.jsonl"
            path.write_bytes(b"one\\ntwo\\n")
            self.assertEqual(
                self.module.sha256_file(path),
                hashlib.sha256(b"one\\ntwo\\n").hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
