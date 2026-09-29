"""A cached file must revalidate as 304, not as an empty 200.

實測 2026-09-23：`/file` 在 `If-None-Match` 命中時先呼叫了 `send_response(200)`，
所以瀏覽器收到的是 `200 OK` 加 `Content-Length: 0`。Chrome 相信那個 200，把空 body
當成真正的回應，PDF 檢視器就畫出一片空白——審題者看到「某些 PDF 顯示不出來」，
而無痕視窗（沒有快取，因此不會送 `If-None-Match`）卻正常。

這條測試直接驅動 handler，不經由 socket，所以它量的是「回應狀態是什麼」而不是
「伺服器有沒有跑起來」。
"""

from __future__ import annotations

import hashlib
import sys
import unittest
from email.message import Message
from http.client import HTTPMessage
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "qbr" / "src"))

from qbr.review_ui.handlers import Handler  # noqa: E402


class _FakeWfile:
    def __init__(self) -> None:
        self.payload = b""

    def write(self, data: bytes) -> None:
        self.payload += data


class _FileHandler(Handler):
    """A Handler with the socket replaced by recorders.

    `BaseHTTPRequestHandler` normally owns its socket; here `wfile` and `headers` are the
    only two things the `/file` branch reads, so they are the only two we supply.
    """

    def __init__(self, headers: dict[str, str]) -> None:  # noqa: D107 - no super() on purpose
        self.wfile = _FakeWfile()
        message = HTTPMessage()
        for key, value in headers.items():
            message[key] = value
        self.headers = message
        self.status: int | None = None
        self.sent_headers: list[tuple[str, str]] = []
        self._path = ""

    # -- recorders ---------------------------------------------------------
    def send_response(self, code: int, message: str | None = None) -> None:
        self.status = code

    def send_header(self, keyword: str, value: str) -> None:
        self.sent_headers.append((keyword, value))

    def end_headers(self) -> None:
        return

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        self.status = code

    def log_message(self, *args, **kwargs) -> None:  # keep the test quiet
        return

    # -- drive just the /file branch ---------------------------------------
    def run_file(self, path: str) -> None:
        self._path = f"/file?path={path}"
        message = Message()
        message["path"] = path
        with patch("urllib.parse.urlparse") as parsed:
            parsed.return_value = type("P", (), {"path": "/file", "query": f"path={message['path']}"})()
            # The handler calls parse_qs(self.path); give it a real query string.
            Handler.do_GET.__wrapped__ if False else None
            self.path = self._path
            real_parse = __import__("urllib.parse", fromlist=["parse_qs"]).parse_qs

            def fake_urlparse(value: str):
                return type("U", (), {"path": "/file", "query": value.split("?", 1)[1] if "?" in value else ""})()

            with patch("urllib.parse.urlparse", fake_urlparse), \
                 patch("urllib.parse.parse_qs", real_parse):
                super().do_GET()


class CachedFileRevalidationTest(unittest.TestCase):
    def setUp(self) -> None:
        # A file inside the asset root, so safe_file_path accepts it.
        self.asset_root = PROJECT_ROOT / "國考題資料夾"
        if not self.asset_root.is_dir():
            self.skipTest("no local asset root")
        self.target = next(self.asset_root.rglob("*.pdf"), None)
        if self.target is None:
            self.skipTest("no PDF in the asset root")

    def _relative(self) -> str:
        return str(self.target.relative_to(PROJECT_ROOT))

    def test_revalidation_hit_answers_304_not_empty_200(self) -> None:
        data = self.target.read_bytes()
        etag = '"%s"' % hashlib.sha256(data).hexdigest()[:32]

        handler = _FileHandler({"If-None-Match": etag})
        handler.run_file(self._relative())

        self.assertEqual(
            handler.status, 304,
            "a cache revalidation hit must be 304; a 200 with no body makes Chrome "
            "keep an empty response and draw a blank PDF viewer",
        )
        self.assertEqual(handler.wfile.payload, b"", "a 304 carries no body")

    def test_miss_answers_200_with_the_whole_file(self) -> None:
        handler = _FileHandler({})
        handler.run_file(self._relative())

        self.assertEqual(handler.status, 200)
        self.assertEqual(len(handler.wfile.payload), self.target.stat().st_size)
        self.assertTrue(handler.wfile.payload.startswith(b"%PDF"))

    def _cache_control_of(self, path: str, headers: dict | None = None) -> str:
        handler = _FileHandler(headers or {})
        handler.run_file(path)
        values = [value for key, value in handler.sent_headers if key.lower() == "cache-control"]
        self.assertTrue(values, "every /file response must state a cache policy")
        return values[0]

    def test_a_paper_must_be_revalidated_not_declared_immutable(self) -> None:
        """A corrected paper has to become visible without the reviewer clearing site data.

        Measured 2026-09-23: `/file` served the official PDFs with `immutable`, so after the papers
        whose scanned pages are JPEG 2000 were rewritten into a form Chrome can draw, every browser
        that had already opened one kept the blank copy for the full day. The fix was live and
        invisible, and the report was still "white". `immutable` must not be sent for a PDF.
        """
        policy = self._cache_control_of(self._relative())
        self.assertNotIn(
            "immutable", policy,
            "a PDF must not be `immutable`: its bytes can legitimately change under one path",
        )
        self.assertIn("no-cache", policy, "it must still be revalidated on every use")

    def test_a_crop_stays_immutable(self) -> None:
        """The original reason for `immutable` still holds: a crop's path fixes its bytes."""
        crop = None
        for suffix in (".png", ".jpg", ".jpeg", ".webp"):
            found = next(self.asset_root.rglob(f"*{suffix}"), None)
            if found is not None:
                crop = found
                break
        if crop is None:
            self.skipTest("no crop image in the asset root")

        relative = str(crop.relative_to(PROJECT_ROOT))
        policy = self._cache_control_of(relative)
        self.assertIn("immutable", policy)

    def test_revalidation_304_repeats_the_same_policy_as_the_200(self) -> None:
        """A 304 that changes the policy would let a browser upgrade a `no-cache` file to
        `immutable` on the second request, which is how a corrected paper disappears again."""
        data = self.target.read_bytes()
        etag = '"%s"' % hashlib.sha256(data).hexdigest()[:32]
        relative = self._relative()

        on_200 = self._cache_control_of(relative)
        on_304 = self._cache_control_of(relative, {"If-None-Match": etag})
        self.assertEqual(on_200, on_304)


if __name__ == "__main__":
    unittest.main()
