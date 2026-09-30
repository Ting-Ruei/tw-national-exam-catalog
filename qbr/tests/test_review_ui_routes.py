# -*- coding: utf-8 -*-
"""審題介面的路由契約（5.1，設計者 2026-09-30 裁決：root 即 v2、v1 遷 `/v1/*`）。

負控制的地圖：
  `/` 不再回 v1 工作台      → root 變成「看起來對但其實是舊頁」（拒：必須是 v2 的字）
  `/mobile/sw.js` 被搬走    → 已安裝 PWA 的契約被破壞（拒：仍是 no-cache + 200）
  `/v1/workflow` 404        → 舊書籤的遷徙沒有著地（拒：同一頁的內容）
  `/v2/*.js` 的檔案供應被表取代 → 2026-09-24 量過的「兩份清單不同步」重演（拒：任何存在的區檔都答）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
ROOT = os.path.dirname(PKG)
for path in (os.path.join(ROOT, "scripts"), os.path.join(ROOT, "qbr", "scripts"),
             os.path.join(ROOT, "qbr", "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

from qbr.review_ui import legacy_assets, handlers  # noqa: E402


def test_the_root_route_serves_v2_and_v1_pages_move_under_v1():
    # root 的新內容就是 v2（同一份 bytes，兩個路由都解析到 /v2/<name>.js）
    assert legacy_assets.v2_page() == (legacy_assets.MOBILE_UI_ROOT / "v2.html").read_bytes()
    # 5.1：v1 的兩頁落在 /v1/*（內容與舊路由同源，不改一字）
    assert legacy_assets.mobile_asset_response("/v1/workflow")[0] == legacy_assets.workflow_page()
    assert legacy_assets.mobile_asset_response("/v1/legacy")[0] == legacy_assets.html_page()


def test_v2_bookmark_and_old_pages_redirect_transitional_302():
    """書籤過渡是 302 不是 301：保留 client 繼續問舊路徑的能力，撤銷時不必清瀏覽器快取。"""
    for old, new in (("/v2", "/"), ("/v2/", "/"),
                     ("/workflow", "/v1/workflow"), ("/workflow/", "/v1/workflow"),
                     ("/legacy", "/v1/legacy"), ("/legacy/", "/v1/legacy")):
        assert handlers.PAGE_REDIRECTS[old] == new, old
    # 移除後的舊 route_map 項目不得死灰復燃：/v2 與 /workflow 不再有第二份供應來源
    # （2026-09-24 的「兩份清單不同步」就是第二份來源造成的）。
    assert legacy_assets.mobile_asset_response("/v2") is None
    assert legacy_assets.mobile_asset_response("/workflow") is None


def test_the_pwa_contract_stays_at_mobile_sw_js():
    kind, cache = (legacy_assets.mobile_asset_response("/mobile/sw.js")[1],
                   legacy_assets.mobile_asset_response("/mobile/sw.js")[2])
    assert kind == "text/javascript; charset=utf-8" and cache == "no-cache", "PWA 契約要保留"


def test_every_v2_area_script_keeps_being_served_from_the_file():
    # 負控制：區檔由檔案供應（2026-09-24 的教訓：清單會忘記，檔案不會）。
    import tempfile, pathlib
    scratch = pathlib.Path(tempfile.mkdtemp())
    v2dir = scratch / "v2"
    v2dir.mkdir()
    (v2dir / "09-area-future.js").write_text("// 區块", encoding="utf-8")
    original = legacy_assets.MOBILE_UI_ROOT
    try:
        legacy_assets.MOBILE_UI_ROOT = scratch
        asset = legacy_assets.mobile_asset_response("/v2/09-area-future.js")
        assert asset is not None and asset[0] == "// 區块".encode("utf-8"), "檔案存在就該被服務"
    finally:
        legacy_assets.MOBILE_UI_ROOT = original


def test_old_mobile_routes_still_answer():
    # 手機快審的頁與 workflow 開頁（PWA start_url 附近）都還在原位。
    assert legacy_assets.mobile_asset_response("/mobile") is not None
    assert legacy_assets.mobile_asset_response("/mobile/workflow") is not None
    assert legacy_assets.mobile_asset_response("/mobile/manifest.webmanifest") is not None