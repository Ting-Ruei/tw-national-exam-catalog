"""每一個區的 `.js` 都要送得出去——而且**沒有第二份清單可以忘記**。

實測 2026-09-24：`review_ui/v2/03-area-answer.js` 與 `03-area-principles.js` 加進了 `v2.html` 的
`<script src>`，但沒有加進 `legacy_assets.py::mobile_asset_response` 的 `route_map`。瀏覽器拿到
**404**，`renderPrinciples` 因此是 `undefined`，原則是「畫不出東西」——而頁面是 `200`、console 乾淨、
伺服器沒有抱怨。缺一條路由的症狀是**安靜的**，所以這種缺陷只能靠一條會紅的檢查，或靠結構上沒有東西
可以忘。這裡兩者都做：

1. 頁面載入的每一個 script 都必須送得出來，而且內容等於磁碟上的檔案（不是 404、不是空 body）。
2. 路由**由檔名推導**，不是清單比對——負對照就是「清單版」：把一個沒被列過、但真的存在的檔案放進
   `v2/`，它必須送得出來。舊行為（只認 `route_map`）在這裡一定紅。
3. 走不出去：路徑穿越、子目錄、不存在、名字帶點，全部 `None`。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "qbr" / "src"))

from qbr.review_ui import legacy_assets  # noqa: E402

V2_HTML = PROJECT_ROOT / "review_ui" / "v2.html"


def script_sources() -> list[str]:
    """The `<script src>` list as the browser sees it - read from the page, not retyped here."""
    page = V2_HTML.read_text(encoding="utf-8")
    out = []
    for chunk in page.split("<script src=")[1:]:
        raw = chunk.split(">", 1)[0].strip().strip('"').strip("'")
        out.append(raw)
    return out


class ReviewUiV2AssetTests(unittest.TestCase):
    def test_every_script_the_page_loads_is_served_and_is_the_file_on_disk(self) -> None:
        sources = script_sources()
        # Guard against the extractor silently finding nothing (a renamed tag would otherwise make
        # this whole test vacuous and green).
        self.assertGreaterEqual(len(sources), 5, f"只從 v2.html 讀到 {sources}")
        for source in sources:
            with self.subTest(src=source):
                served = legacy_assets.mobile_asset_response(f"/{source}")
                self.assertIsNotNone(served, f"{source} 沒有路由（頁面會拿到 404，而且不會有人說）")
                body, content_type, cache_control = served
                self.assertEqual(body, (PROJECT_ROOT / "review_ui" / source).read_bytes())
                self.assertTrue(content_type.startswith("text/javascript"), content_type)
                self.assertEqual(cache_control, "no-cache")

    def test_route_comes_from_the_file_not_from_a_list(self) -> None:
        # 負對照：這條在「清單比對」的實作上一定失敗——那個檔案沒有被列在任何地方。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "v2").mkdir()
            (root / "v2" / "99-brand-new-area.js").write_text("// new\n", encoding="utf-8")
            with patch.object(legacy_assets, "MOBILE_UI_ROOT", root):
                served = legacy_assets.mobile_asset_response("/v2/99-brand-new-area.js")
        self.assertIsNotNone(served, "新增一個區檔卻要改第二份清單，就是 2026-09-24 的缺陷")
        self.assertEqual(served[0], b"// new\n")

    def test_unknown_and_traversing_routes_stay_unserved(self) -> None:
        for route in (
            "/v2/not-a-real-file.js",
            "/v2/../01-core.js",
            "/v2/..%2f..%2fetc%2fpasswd.js",
            "/v2/sub/01-core.js",
            "/v2/01-core.js.bak",
            "/v2/.js",
            "/v2/01-core.js%00",
        ):
            with self.subTest(route=route):
                self.assertIsNone(legacy_assets.mobile_asset_response(route))

    def test_a_trailing_slash_is_normalised_like_the_page_route(self) -> None:
        # `/v2/` is the page route and relies on this; the script route keeps the same rule so the
        # resolver has one behaviour, not two. Pinned so it is a decision, not an accident.
        self.assertIsNotNone(legacy_assets.mobile_asset_response("/v2/01-core.js/"))


if __name__ == "__main__":
    unittest.main()
