from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_visual_balance_probe.py"


def import_script():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("run_visual_balance_probe", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class VisualBalanceProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = import_script()

    def test_reads_only_visual_case_and_contact_sheet(self):
        rows = [
            {"case_id": "t", "lane": "text", "human_review": "secret"},
            {
                "case_id": "v",
                "lane": "visual",
                "answer": "A",
                "visual_evidence": {"contact_sheet": "/tmp/v.png"},
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "packets.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            packets = self.module.read_visual_packets(path, set())
        self.assertEqual(packets, [{"case_id": "v", "contact_sheet": "/tmp/v.png"}])

    def test_parses_fenced_json(self):
        self.assertEqual(self.module.parse_json_content('```json\n{"a":1}\n```'), {"a": 1})


if __name__ == "__main__":
    unittest.main()
