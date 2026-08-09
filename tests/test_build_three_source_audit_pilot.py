from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_three_source_audit_pilot.py"


def import_script():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("build_three_source_audit_pilot", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ThreeSourcePilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = import_script()

    def test_question_segment_stops_at_next_question(self):
        text = "4. 前題\nA. x\n5. 目標題\nA. 甲\nB. 乙\n6. 後題\nA. y"
        self.assertEqual(self.module.split_question_segment(text, 5), "5. 目標題\nA. 甲\nB. 乙")

    def test_question_segment_accepts_official_pdf_marker_without_period(self):
        text = "43 前題\nA 甲\n44 下列何者正確？\nA 乙\n45 後題"
        self.assertEqual(
            self.module.split_question_segment(text, 44),
            "44 下列何者正確？\nA 乙",
        )

    def test_grouped_lines_keeps_words_with_nearby_top(self):
        words = [
            {"text": "5.", "top": 10.0, "bottom": 20.0, "x0": 5.0, "x1": 10.0},
            {"text": "題幹", "top": 11.0, "bottom": 20.0, "x0": 15.0, "x1": 30.0},
            {"text": "A.", "top": 30.0, "bottom": 40.0, "x0": 5.0, "x1": 10.0},
        ]
        rows = self.module.grouped_lines(words)
        self.assertEqual([row["text"] for row in rows], ["5. 題幹", "A."])

    def test_blind_content_excludes_answer(self):
        candidate = {
            "stem": "題幹",
            "answer": "B",
            "options": [{"key": "A", "text": "甲"}],
            "parser_original": {"stem": "原題幹", "answer": "B", "options": []},
        }
        self.assertNotIn("answer", self.module.candidate_content(candidate, original=False))
        self.assertNotIn("answer", self.module.candidate_content(candidate, original=True))

    def test_question_consensus_deduplicates_poppler_variants(self):
        engines = {
            "pdftotext_raw": {"source_family": "poppler", "text": "31. KCl"},
            "pdftotext_layout": {"source_family": "poppler", "text": "31.  KCl"},
            "pypdf": {"source_family": "pypdf", "text": "31.KCl"},
        }
        result = self.module.question_text_consensus(engines)
        self.assertEqual(result["status"], "usable_question_consensus")
        self.assertEqual(result["family_count"], 2)

    def test_page_consensus_does_not_mask_empty_question_text(self):
        engines = {
            "pdfplumber": {"source_family": "pdfplumber", "text": ""},
            "pypdf": {"source_family": "pypdf", "text": ""},
        }
        result = self.module.question_text_consensus(engines)
        self.assertEqual(result["status"], "insufficient_question_text")
        self.assertEqual(result["family_count"], 0)

    def test_fetch_remote_asset_prefers_existing_local_file(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
            source = Path(directory) / "asset.png"
            source.write_bytes(b"png")
            cache: dict[str, Path | None] = {}
            resolved = self.module.fetch_remote_asset(
                str(source),
                base_url="http://127.0.0.1:9",
                cache_dir=Path(directory) / "cache",
                timeout=0.1,
                cache=cache,
            )
            self.assertEqual(resolved, source.resolve())

    def test_fetch_remote_asset_handles_repeated_repository_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = (
                Path(directory)
                / "tw-national-exam-catalog"
                / "tw-national-exam-catalog"
            )
            source = project_root / "tmp" / "asset.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"png")
            with (
                patch.object(self.module, "PROJECT_ROOT", project_root),
                patch.object(self.module, "ASSET_ROOT", project_root / "國考題資料夾"),
            ):
                resolved = self.module.fetch_remote_asset(
                    str(source),
                    base_url="http://127.0.0.1:9",
                    cache_dir=project_root / "tmp" / "cache",
                    timeout=0.1,
                    cache={},
                )
            self.assertEqual(resolved, source.resolve())

    def test_walk_asset_values_deduplicates_option_and_image_refs(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
            source = Path(directory) / "asset.png"
            source.write_bytes(b"png")
            asset = {"path": str(source)}
            candidate = {
                "options": [{"key": "A", "image": asset}],
                "image_refs": [asset],
            }
            found = self.module.walk_asset_values(
                candidate,
                base_url="http://127.0.0.1:9",
                cache_dir=Path(directory) / "cache",
                timeout=0.1,
                cache={},
            )
            self.assertEqual(found, [("CURRENT_OPTION_A", source.resolve())])


if __name__ == "__main__":
    unittest.main()
