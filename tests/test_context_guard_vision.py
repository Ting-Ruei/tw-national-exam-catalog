from __future__ import annotations

import base64
import struct
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from review_context_guard import (  # noqa: E402
    MAX_VISION_IMAGE_DIMENSION,
    MAX_VISION_IMAGE_PIXELS,
    VISION_PIXELS_PER_ESTIMATED_TOKEN,
    VisionPayloadError,
    inspect_payload,
    payload_size_breakdown,
)


def png_with_dimensions(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sIIBBBBB", 13, b"IHDR", width, height, 8, 2, 0, 0, 0)


class ContextGuardVisionTests(unittest.TestCase):
    def test_native_raw_base64_is_dimension_accounted_not_text_accounted(self) -> None:
        raw = png_with_dimensions(2000, 2000)
        encoded = base64.b64encode(raw).decode("ascii")
        payload = {
            "model": "qwen",
            "messages": [{"role": "user", "content": "inspect", "images": [encoded]}],
        }

        breakdown = payload_size_breakdown(payload)
        expected_pixels = 2000 * 2000
        self.assertEqual(breakdown["vision_count"], 1)
        self.assertEqual(breakdown["vision_pixels"], expected_pixels)
        self.assertEqual(
            breakdown["vision_estimated_tokens"],
            (expected_pixels + VISION_PIXELS_PER_ESTIMATED_TOKEN - 1) // VISION_PIXELS_PER_ESTIMATED_TOKEN,
        )
        self.assertLess(breakdown["vision_bytes"], len(encoded))
        self.assertGreater(breakdown["vision_estimated_tokens"], len(encoded))

        inspected = inspect_payload(payload, output_tokens=0, context_limit_tokens=1_000, safety_margin_tokens=0)
        self.assertEqual(inspected["method"], "utf8_text_plus_dimension_vision_estimate")
        self.assertTrue(inspected["estimate_is_not_tokenizer_bound"])
        self.assertFalse(inspected["within_budget"])

    def test_data_url_uses_same_dimension_estimate(self) -> None:
        raw = png_with_dimensions(32, 64)
        payload = {"image_url": "data:image/png;base64," + base64.b64encode(raw).decode("ascii")}
        breakdown = payload_size_breakdown(payload)
        self.assertEqual(breakdown["vision_pixels"], 2048)
        self.assertEqual(breakdown["vision_estimated_tokens"], 8)

    def test_dimension_and_pixel_limits_are_enforced_before_accounting(self) -> None:
        with self.assertRaisesRegex(VisionPayloadError, "dimensions"):
            payload_size_breakdown({"images": [base64.b64encode(png_with_dimensions(MAX_VISION_IMAGE_DIMENSION + 1, 1)).decode("ascii")]})
        with self.assertRaisesRegex(VisionPayloadError, "maximum"):
            payload_size_breakdown({"images": [base64.b64encode(png_with_dimensions(2000, 2001)).decode("ascii")]})
        self.assertLess(MAX_VISION_IMAGE_PIXELS, MAX_VISION_IMAGE_DIMENSION * MAX_VISION_IMAGE_DIMENSION)


if __name__ == "__main__":
    unittest.main()
