from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
import tempfile
import unittest
from types import SimpleNamespace
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

    def test_content_fingerprint_matches_local_evidence_canonical_contract(self):
        candidate = {
            "stem": "題幹",
            "options": [{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}],
            "answer": "B",
        }
        canonical = json.dumps(
            {"stem": "題幹", "options": [{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}]},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.assertEqual(self.module.content_fingerprint(candidate), expected)

    def test_official_pdf_reference_accepts_exported_staging_metadata(self):
        candidate = {
            "source_files": None,
            "metadata": {"question_pdf_relative": "10_official_pdf/example.pdf"},
        }
        self.assertEqual(
            self.module.official_pdf_reference(candidate),
            "10_official_pdf/example.pdf",
        )

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

    def test_offline_candidate_lookup_is_exact_and_rejects_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidates.jsonl"
            path.write_text(
                json.dumps({"candidate_key": "wanted", "stem": "ok"}) + "\n",
                encoding="utf-8",
            )
            selected = self.module.load_offline_candidates(
                path, [{"id": "case", "candidate_key": "wanted"}, {"id": "missing", "candidate_key": "other"}]
            )
            self.assertEqual(list(selected), ["wanted"])
            path.write_text(
                "\n".join(
                    json.dumps({"candidate_key": "wanted"}) for _ in range(2)
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "duplicate candidate_key"):
                self.module.load_offline_candidates(path, [{"candidate_key": "wanted"}])

    def test_resume_rejects_pre_fingerprint_case_manifest_without_overwrite_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / "assets"
            assets.mkdir()
            pdf = assets / "source.pdf"
            pdf.write_bytes(b"pdf")
            case_dir = root / "out" / "cases" / "case-1"
            case_dir.mkdir(parents=True)
            packet_path = case_dir / "blind-packet.json"
            packet_path.write_text(json.dumps({"case_id": "case-1", "candidate_key": "key"}), encoding="utf-8")
            (case_dir / "case-manifest.json").write_text(
                json.dumps(
                    {
                        "case_id": "case-1",
                        "candidate_key": "key",
                        "candidate_hash": self.module.stable_hash({"candidate_key": "key", "stem": "x", "options": []}),
                        "pdf_sha256": self.module.file_hash(pdf),
                        "packet_sha256": self.module.file_hash(packet_path),
                    }
                ),
                encoding="utf-8",
            )
            candidate = {
                "candidate_key": "key",
                "stem": "x",
                "options": [],
                "source_files": {"official_pdf": "source.pdf"},
            }
            with self.assertRaisesRegex(RuntimeError, "use a new --output-dir"):
                self.module.resume_packet(
                    {"id": "case-1"}, candidate, root / "out", assets,
                    {"spec_sha256": "spec", "candidate_jsonl_sha256": "input", "asset_root": str(assets), "render_dpi": 180},
                    "current-builder",
                )

    def test_offline_asset_resolution_stays_inside_trusted_root_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "assets"
            root.mkdir()
            image = root / "image.png"
            image.write_bytes(b"png")
            with patch.object(self.module.urllib.request, "urlopen", side_effect=AssertionError("network")):
                self.assertEqual(
                    self.module.fetch_remote_asset(
                        "image.png",
                        base_url="http://127.0.0.1:9",
                        cache_dir=Path(directory) / "cache",
                        timeout=0.1,
                        cache={},
                        asset_root=root,
                    ),
                    image.resolve(),
                )
                self.assertIsNone(
                    self.module.fetch_remote_asset(
                        "not-present.png",
                        base_url="http://127.0.0.1:9",
                        cache_dir=Path(directory) / "cache",
                        timeout=0.1,
                        cache={},
                        asset_root=root,
                    )
                )

    def test_main_offline_keeps_failed_pdf_in_error_ledger_and_manifest_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset_root = root / "assets"
            asset_root.mkdir()
            (asset_root / "good.pdf").write_bytes(b"pdf")
            spec_path = root / "spec.json"
            spec_path.write_text(
                json.dumps(
                    {"cases": [
                        {"id": "good", "candidate_key": "good-key", "lane": "a"},
                        {"id": "bad", "candidate_key": "bad-key", "lane": "b"},
                    ]}
                ),
                encoding="utf-8",
            )
            candidates_path = root / "candidates.jsonl"
            candidates_path.write_text(
                "\n".join(
                    json.dumps(
                        {
                            "candidate_key": key,
                            "question_number": 1,
                            "stem": key,
                            "source_files": {"official_pdf": pdf},
                        }
                    )
                    for key, pdf in (("good-key", "good.pdf"), ("bad-key", "missing.pdf"))
                )
                + "\n",
                encoding="utf-8",
            )
            output = root / "out"
            args = SimpleNamespace(
                spec=spec_path,
                output_dir=output,
                base_url="http://127.0.0.1:9",
                candidate_jsonl=candidates_path,
                asset_root=asset_root,
                timeout=1.0,
                render_dpi=180,
                resume=False,
            )

            def fake_build_case(case, candidate, output_dir, *_args):
                if case["id"] == "bad":
                    raise FileNotFoundError("missing.pdf")
                case_dir = output_dir / "cases" / case["id"]
                case_dir.mkdir(parents=True)
                packet = {"case_id": case["id"], "candidate_key": candidate["candidate_key"], "lane": case["lane"]}
                (case_dir / "blind-packet.json").write_text(json.dumps(packet), encoding="utf-8")
                return packet

            with patch.object(self.module, "parse_args", return_value=args), patch.object(
                self.module, "build_case", side_effect=fake_build_case
            ), patch.object(self.module.urllib.request, "urlopen", side_effect=AssertionError("network")):
                self.assertEqual(self.module.main(), 0)

            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            ledger = [json.loads(line) for line in (output / "error-ledger.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(manifest["case_count"], 2)
            self.assertEqual(manifest["packet_count"], 1)
            self.assertEqual(manifest["error_count"], 1)
            self.assertEqual([row["status"] for row in manifest["coverage"]], ["packet", "error"])
            self.assertEqual(ledger[0]["case_id"], "bad")
            self.assertEqual(len((output / "blind-packets.jsonl").read_text(encoding="utf-8").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
