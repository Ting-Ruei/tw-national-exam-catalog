from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "probe_multimodal_model.py"


def import_script():
    spec = importlib.util.spec_from_file_location("probe_multimodal_model", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class MultimodalProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = import_script()

    def test_llmshare_uses_openai_image_url_content(self):
        payload = self.module.build_request(
            provider="llmshare",
            model="gemma4:31b",
            prompt="inspect",
            image_base64="YWJj",
            mime_type="image/png",
            max_tokens=100,
        )
        content = payload["messages"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "inspect"})
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(payload["response_format"], {"type": "json_object"})

    def test_ollama_uses_images_array(self):
        payload = self.module.build_request(
            provider="ollama",
            model="gemma4:e4b-mlx",
            prompt="inspect",
            image_base64="YWJj",
            mime_type="image/png",
            max_tokens=100,
        )
        self.assertEqual(payload["messages"][0]["images"], ["YWJj"])
        self.assertEqual(payload["format"], "json")


if __name__ == "__main__":
    unittest.main()
