
"""Which pictures belong to which option, and whether a crop holds the whole picture.

Two separate defects live here and both were reported from the served review UI:

* **a picture split into strips.** The paper prints one structure; the PDF stores it as several
  objects of the same width, touching end to end. Binding the raw object list and keeping only the
  first served the top strip of each structure, and every automated check passed because that strip
  really is a placed object.
* **a verifier that cannot be a model.** Asking the local model whether a crop is complete was
  measured and it does not work: 80 crops, half known-broken, came back `same=true` 80 times, and a
  control that kept only the top 8 pt of a structure still came back `same=true` 7 times out of 8.
  The check is therefore made from the page itself - the file says which objects are one picture.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qbr import verify_crops  # noqa: E402
from qbr import vision  # noqa: E402

def _placed(page, x0, y0, x1, y1, xref):
    return {"page": page, "x0": x0, "y0": y0, "x1": x1, "y1": y1, "xref": xref}


def _item_with_option_marks(marks):
    """`marks` maps option key -> marker y, on page 1 at x=39-50."""
    cells = {"options": {}}
    rows = []
    for index, (key, y) in enumerate(marks.items(), start=1):
        rows.append({"page": 1, "x0": 39.0, "y0": float(y), "x1": 50.0, "y1": float(y) + 11.0})
        cells["options"][key] = [index]
    return {"cells": cells}, rows


def test_two_stacked_strips_of_one_structure_are_not_split():
    """兩條同寬相接的物件是一張圖，不是兩張。

    量測 `1151_藥師(一)_藥學(一)` Q41 選項 C：`xref` 16 與 17 都是 x=50.4-225.4、相接於
    y=392.4。只取第一條會切掉 `Cytotoxic drug` 橢圓，而所有自動檢查都會通過，
    因為那條切片本身真的是一個被放在頁面上的物件。
    """
    from qbr import vision
    images = [_placed(1, 50.4, 100.0, 225.4, 150.0, 16),
              _placed(1, 50.4, 150.0, 225.4, 200.0, 17),
              _placed(1, 50.4, 310.0, 225.4, 380.0, 20)]
    item, rows = _item_with_option_marks({"A": 95.0, "B": 300.0})
    owned = vision.option_figures(item, rows, images, limit=(1, 500.0))
    assert owned, "應綁定到選項 A"
    first = [entry for entry in owned if entry["key"] == "A"][0]
    assert first["strips"] == 2
    assert first["box"][3] == 200.0, "框的下緣必須包含第二條切片"
    assert first["xref"] is None, "多物件組成的圖沒有單一 xref，必須改用 render 該區域"


def test_two_different_structures_same_width_are_not_merged_across_options():
    """四個選項的圖直接印在彼此下面且同寬時，不可以被融合成一張。

    整頁先合併會把 `1051_醫事檢驗師_臨床血清免疫學與臨床病毒學` Q20 的四張圖併成
    一張，每個選項都失去自己的圖。
    """
    from qbr import vision
    images = [_placed(1, 50.0, 100.0, 200.0, 190.0, 30),
              _placed(1, 50.0, 200.0, 200.0, 290.0, 31),
              _placed(1, 50.0, 300.0, 200.0, 390.0, 32),
              _placed(1, 50.0, 400.0, 200.0, 490.0, 33)]
    item, rows = _item_with_option_marks({"A": 95.0, "B": 195.0, "C": 295.0, "D": 395.0})
    owned = vision.option_figures(item, rows, images, limit=(1, 600.0))
    assert sorted(entry["key"] for entry in owned) == ["A", "B", "C", "D"]
    for entry in owned:
        assert entry["strips"] == 1, f"{entry['key']} 不該與鄰選項合併"
    assert [entry["xref"] for entry in owned] == [30, 31, 32, 33]


def test_a_short_strip_below_a_picture_belongs_to_the_same_picture():
    """高度門檻只挑選，不修剪：分子底部較短的一條仍是同一個圖。

    量測 `1081_藥師(一)_藥理學與藥物化學` Q63 B：分子最後兩條是 13.7 與 13.8 pt，
    用門檻修剪會正好切掉分子的下半部。
    """
    from qbr import vision
    images = [_placed(1, 50.0, 100.0, 200.0, 160.0, 110),   # 60 pt
              _placed(1, 50.0, 160.0, 200.0, 174.0, 113),   # 14 pt，低於門檻
              _placed(1, 50.0, 174.0, 200.0, 190.0, 114),   # 16 pt，低於門檻
              _placed(1, 50.0, 310.0, 200.0, 380.0, 120)]
    item, rows = _item_with_option_marks({"A": 95.0, "B": 300.0})
    owned = vision.option_figures(item, rows, images, limit=(1, 500.0))
    first = [entry for entry in owned if entry["key"] == "A"][0]
    assert first["strips"] == 3, "矮切片仍屬於同一個圖"
    assert first["box"][3] == 190.0


def test_a_crop_is_cut_from_the_picture_page_not_the_marker_page():
    """裁切要從圖所在的頁切，不是 marker 所在的頁。

    量測 `1081_藥師(一)_藥理學與藥物化學` Q63：A、B 的 marker 在第 11 頁，
    結構在第 12 頁；渲染 marker 那一頁等於畫一個在該頁沒有意義的矩形。
    """
    from qbr import vision
    # 真實形狀（`1081` Q63）：A、B 的 marker 印在第 11 頁下半，B 與 C 跨頁，
    # 而 B 的結構在第 12 頁頁首 —— 所以 B 的裁切頁（12）與它的 marker 頁（11）不同。
    cells = {"options": {"A": [1], "B": [2], "C": [3]}}
    rows = [{"page": 1, "x0": 39.0, "y0": 600.0, "x1": 50.0, "y1": 611.0},
            {"page": 1, "x0": 39.0, "y0": 700.0, "x1": 50.0, "y1": 711.0},
            {"page": 2, "x0": 39.0, "y0": 300.0, "x1": 50.0, "y1": 311.0}]
    item = {"cells": cells}
    images = [_placed(1, 50.0, 600.0, 200.0, 690.0, 108),
              _placed(2, 50.0, 30.0, 200.0, 130.0, 110),
              _placed(2, 50.0, 320.0, 200.0, 420.0, 115)]
    owned = vision.option_figures(item, rows, images, limit=(2, 500.0))
    bound = {entry["key"]: entry for entry in owned}
    assert "B" in bound, f"跨頁的選項 B 應該綁到第 2 頁的圖，得到 {sorted(bound)}"
    assert bound["B"]["page"] == 2, "裁切頁必須是圖所在的頁，不是 marker 所在的頁"
    assert bound["B"]["xref"] == 110


def test_sibling_strips_ignore_a_strip_on_another_option():
    """同寬相接的物件只有在同一選項的 band 內才算同一張圖。

    量測 `1091_藥師(一)_藥劑學與生物藥劑學` Q53 C：下方同寬的物件是 D 選項的公式，
    沒有 band 條件就會誤報；加 band 後 400 張樣本 0 誤報。
    """
    from qbr import verify_crops
    box = (50.0, 100.0, 200.0, 160.0)
    images = [_placed(1, 50.0, 160.0, 200.0, 260.0, 76)]
    # 沒有 band：會被當成同一張圖的下半。
    loose = verify_crops.verify_one("/nonexistent.pdf", 1, box, images=images)
    assert loose["complete"] is False
    # 有 band 且下方的物件超出 band：正確放行。
    tight = verify_crops.verify_one("/nonexistent.pdf", 1, box, images=images,
                                    band=(1, 60.0, 200.0, 1, 200.0))
    assert tight["complete"] is True
    # 下方的物件落在 band 內：真的被切掉。
    inside = verify_crops.verify_one("/nonexistent.pdf", 1, box, images=images,
                                     band=(1, 60.0, 400.0, 1, 400.0))
    assert inside["complete"] is False


def test_a_hairline_object_is_not_a_sibling_strip():
    """0.8 pt 的物件是產生器吐出的線，不是圖的下一條切片。

    量測 `1091_藥師(一)_藥劑學與生物藥劑學` Q53 C 與 `1082_藥師(一)_藥理學與藥物化學`
    Q73 C：下方是 0.8 與 0.9 pt 的細線。
    """
    from qbr import verify_crops
    box = (50.0, 100.0, 200.0, 160.0)
    images = [_placed(1, 50.0, 160.0, 200.0, 160.8, 76)]
    outcome = verify_crops.verify_one("/nonexistent.pdf", 1, box, images=images)
    assert outcome["complete"] is True, "細線不該被當成切片"


def test_a_wider_object_below_is_not_a_sibling_strip():
    """寬度不同就是另一張圖，即使相接。

    量測 `1102_醫事檢驗師_醫學分子檢驗學與臨床鏡檢學` Q77：四條電泳道寬 78-84 pt、
    間隔 20.7 pt，用相接合併會讓兩個選項共用一張圖。
    """
    from qbr import verify_crops
    box = (50.0, 100.0, 130.0, 200.0)
    images = [_placed(1, 50.0, 200.0, 212.0, 300.0, 77)]
    outcome = verify_crops.verify_one("/nonexistent.pdf", 1, box, images=images)
    assert outcome["complete"] is True


def test_figure_crop_leaves_out_the_option_pictures():
    """圖裁切不可以把選項圖再放一次。

    量測 `1141_藥師(一)_藥學(一)` Q41：這一題有五張圖——題幹的環狀圖，加每個選項一張結構。
    裁切全部五張會讓**每個選項在畫面上出現兩次**（包含答案那一個），
    審題者得自己判斷要看哪一份。圖裁切存在的理由是「文字帶不了的東西」，
    所以選項圖要移除，不是重複。
    """
    from qbr import vision
    entry = {"figure_boxes": [(45.0, 409.0, 416.0, 472.0),     # 題幹
                              (50.0, 478.0, 440.0, 532.0),     # A
                              (50.0, 538.0, 345.0, 610.0),     # B
                              (50.0, 616.0, 354.0, 683.0),     # C
                              (50.0, 690.0, 351.0, 759.0)]}    # D
    owned = [{"key": "A", "box": (39.2, 478.4, 440.2, 531.8)},
             {"key": "B", "box": (39.2, 538.4, 345.4, 609.8)},
             {"key": "C", "box": (39.2, 616.4, 353.8, 683.0)},
             {"key": "D", "box": (39.2, 689.9, 350.8, 758.9)}]
    region = vision.figure_region(entry, exclude=[o["box"] for o in owned])
    assert region == (45.0, 409.0, 416.0, 472.0), "只該留下題幹的圖"
    assert vision.options_cover_the_figure(entry, owned) is False, "題幹有圖，所以仍需要圖裁切"


def test_figure_region_is_none_when_every_picture_is_an_option():
    """若所有圖都是選項圖，就沒有圖裁切可做。"""
    from qbr import vision
    owned = [{"key": "A", "box": (39.2, 478.4, 440.2, 531.8)},
             {"key": "B", "box": (39.2, 538.4, 345.4, 609.8)}]
    entry = {"figure_boxes": [(50.0, 478.0, 440.0, 532.0),
                              (50.0, 538.0, 345.0, 610.0)]}
    assert vision.figure_region(entry, exclude=[o["box"] for o in owned]) is None
    assert vision.options_cover_the_figure(entry, owned) is True


def test_describing_no_crop_is_a_named_outcome_not_a_crash():
    """沒有裁切可看時，問模型是錯誤的呼叫，不是模型的回答。

    量測 `1141_藥師(一)_藥學(一)` Q41：這一題的每張圖都是選項圖，所以沒有圖裁切，
    `png` 是 `None`——第一個版本把它交給模型，`base64` 直接拋
    `TypeError: a bytes-like object is required, not 'NoneType'`，整個切圖批次當在那裡。

    這裡守的是呼叫邊界：`None` 必須回一個**有名字的結果**（`error=no-crop-to-describe`），
    而不是讓下游的 encoder 決定訊息長什麼樣子。**沒有讀數**和**讀失敗**是兩件事。
    """
    from qbr import vision
    outcome = vision.describe_crop(None, subject="藥學(一)", question="下列結構何者正確？")
    assert outcome["error"] == "no-crop-to-describe"
    assert outcome["verdict"] is None
    assert outcome["bytes"] == 0
    # 而且它必須是「不會去問模型」：真的送出請求的話，離線環境下會拖到 timeout 或被拒。
    assert outcome["raw"] == "" and outcome["usage"] is None
