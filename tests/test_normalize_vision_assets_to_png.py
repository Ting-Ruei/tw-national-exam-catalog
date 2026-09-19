from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "normalize_vision_assets_to_png",
    SCRIPTS / "normalize_vision_assets_to_png.py",
)
assert SPEC and SPEC.loader
normalizer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = normalizer
SPEC.loader.exec_module(normalizer)


TEST_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class NormalizeVisionAssetsTests(unittest.TestCase):
    def test_identity_png_creates_rewritten_manifests_without_touching_source(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            source_root = root / "mineru"
            source_root.mkdir()
            image = source_root / "figure.png"
            image.write_bytes(TEST_PNG)
            candidates = root / "candidates.jsonl"
            candidate = {
                "candidate_key": "q1",
                "source_registry_key": "source-q1",
                "question_number": "1",
                "stem": "看圖",
                "options": [],
                "metadata": {},
                "image_refs": [{"path": str(image), "exists": True}],
            }
            candidates.write_text(json.dumps(candidate, ensure_ascii=False) + "\n", encoding="utf-8")
            issues = root / "issues.csv"
            issues.write_text("candidate_key,issue_code\n", encoding="utf-8")
            source_artifact = root / "parser-source.json"
            source_artifact.write_text("source", encoding="utf-8")
            source_manifest = root / "source_manifest.json"
            source_manifest.write_text(
                json.dumps(
                    {
                        "manifest_version": "ai395-source-manifest-v1",
                        "fixture_id": "real:test",
                        "source_artifacts": [{
                            "artifact_id": "source-artifact",
                            "path": source_artifact.name,
                            "sha256": sha256(source_artifact.read_bytes()),
                            "immutable": True,
                            "read_only": True,
                        }],
                        "candidate_jsonl": {"path": candidates.name, "sha256": sha256(candidates.read_bytes())},
                        "issue_csv": {"path": issues.name, "sha256": sha256(issues.read_bytes())},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            mineru_manifest = root / "mineru_manifest.json"
            mineru_manifest.write_text(
                json.dumps(
                    {
                        "manifest_version": "ai395-mineru-manifest-v1",
                        "mineru_run_id": "test-mineru",
                        "source_artifact_id": "source-artifact",
                        "status": "existing_artifact",
                        "input_sha256": sha256(candidates.read_bytes()),
                        "output_root": source_root.name,
                        "image_root": source_root.name,
                        "read_only": True,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            output = root / "png-view"
            result = normalizer.normalize_scope(source_manifest, mineru_manifest, output)

            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["candidate_count"], 1)
            self.assertEqual(result["image_reference_count"], 1)
            self.assertEqual(result["identity_png_count"], 1)
            self.assertFalse(result["original_assets_modified"])
            converted = json.loads((output / "candidates.png.jsonl").read_text(encoding="utf-8"))
            ref = converted["image_refs"][0]
            self.assertEqual(ref["mime_type"], "image/png")
            self.assertEqual(ref["source_asset"]["sha256"], sha256(TEST_PNG))
            self.assertTrue((output / "assets" / ref["path"]).is_file())
            rewritten_mineru = json.loads((output / "mineru_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(rewritten_mineru["image_root"], "assets")
            self.assertEqual(image.read_bytes(), TEST_PNG)


if __name__ == "__main__":
    unittest.main()
