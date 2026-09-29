# -*- coding: utf-8 -*-
"""The re-read: a transcription is compared, and never a verdict.

Only the pure parts are tested here - the crop and the model call need a PDF and an endpoint. What
matters for correctness is the comparison: a report that fires on formatting is useless, and a
report that stays silent on a real substitution is worse. Both directions are asserted, and the
substitution case is one this corpus actually contains.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from qbr import reread, reflow  # noqa: E402


def test_the_comparison_folds_the_radicals_the_text_layer_stores():
    """`⻑`(U+2ED1) 與 `長`(U+9577) 在比較式裡是同一個字嗎？

    **不是**，而這正是重點：`disputes.py` 已經把這個取代獨立報出來了，所以重讀要在
    比較式裡做**同樣的區分**，否則兩個機制會對同一件事給出不同答案。
    """
    assert reread.comparison_form("年⻑者") != reread.comparison_form("年長者")


def test_the_comparison_folds_the_kangxi_radicals_the_reader_sees_correctly():
    """負對照：Kangxi radical 的 `⽤` 與 `用` 在畫面上是同一個字，比較式必須把它們折在一起。

    沒有這一條，每一題都會因為顯示字形而被旗標，報告就變成噪音。
    """
    assert reread.comparison_form("使⽤") == reread.comparison_form("使用")
    assert reread.comparison_form("⾎中濃度") == reread.comparison_form("血中濃度")


def test_whitespace_and_a_leading_question_number_are_not_differences():
    """排版細節不是內容差異：換行位置、以及題號前綴。"""
    assert reread.comparison_form("下列何者 最正確 ？") == reread.comparison_form("下列何者最正確？")
    assert reread.comparison_form("34.選項甲", drop_leading_number=True) == \
        reread.comparison_form("選項甲", drop_leading_number=True)


def test_latex_offsets_become_the_characters_the_paper_prints():
    """模型有時會用 LaTeX 寫上下標。內容是對的，所以折成紙本的字，而不是丟掉整個回答。"""
    assert reread.comparison_form("C_p=Be^{-kt}") == reread.comparison_form("Cp=Be⁻ᵏᵗ")
    assert reread.comparison_form("10^{-5}") == reread.comparison_form("10⁻⁵")
    assert reread.comparison_form("K_m") == reread.comparison_form("Kₘ")


def test_a_clean_question_compares_equal():
    """對照組：完全一致的兩個讀法不得產生任何差異。"""
    pipeline = {"stem": "下列何者為人體最大的器官？",
                "options": {"A": "肝臟", "B": "皮膚", "C": "肺臟", "D": "腎臟"}}
    seen = {"stem": "下列何者為人體最大的器官？",
            "options": {"A": "肝臟", "B": "皮膚", "C": "肺臟", "D": "腎臟"}}
    report = reread.compare(pipeline, seen)
    assert report["stem_differs"] is None
    assert report["option_differs"] == {}
    assert report["parse_error"] is None


def test_a_real_substitution_is_reported_with_both_readings():
    """紙本 `長`、管線 `⻑`：差異必須被報出來，而且兩邊都要顯示，否則人無法判斷。"""
    pipeline = {"stem": "", "options": {"A": "延⻑藥物於黏膜之作用時間"}}
    seen = {"stem": "", "options": {"A": "延長藥物於黏膜之作用時間"}}
    report = reread.compare(pipeline, seen)
    assert list(report["option_differs"]) == ["A"]
    assert "⻑" in report["option_differs"]["A"]["pipeline"]
    assert "長" in report["option_differs"]["A"]["page"]


def test_an_unparsed_answer_is_reported_rather_than_counted_as_agreement():
    """模型沒給 JSON 時，**不得**當成「一致」。把失敗講成 OK 是最糟的失敗模式。"""
    report = reread.compare({"stem": "題", "options": {}}, None)
    assert report["parse_error"] == "unparsed"
    assert reread.parse("我看不清楚") is None
    assert reread.parse('前言 ```json\n{"stem":"a","options":{"A":"b"}}\n```')["stem"] == "a"


def test_options_are_read_from_either_shape_the_pipeline_produces():
    """`segment_mixed` 給 dict，`package` 給 list。量測工具曾在這裡出錯（把 key 當成值），
    所以兩種形狀都必須吃，而且要有測試釘住。"""
    assert reread.options_of({"options": {"A": "0.1"}}) == {"A": "0.1"}
    assert reread.options_of({"options": [{"key": "A", "text": "0.1"}]}) == {"A": "0.1"}
    assert reread.options_of({}) == {}


def test_the_band_ends_at_the_next_question_and_crosses_pages():
    """一題的範圍是「自己的題號到下一個題號」，而且跨頁。"""
    rows = [
        {"text": "1.第一題", "page": 1, "y0": 10.0, "x0": 20.0, "x1": 100.0, "y1": 24.0},
        {"text": "A.甲", "page": 1, "y0": 30.0, "x0": 20.0, "x1": 100.0, "y1": 44.0},
        {"text": "續行", "page": 2, "y0": 12.0, "x0": 20.0, "x1": 100.0, "y1": 26.0},
        {"text": "2.第二題", "page": 2, "y0": 30.0, "x0": 20.0, "x1": 100.0, "y1": 44.0},
    ]
    band = reread.band_rows(rows, 1)
    assert [row["text"] for row in band] == ["1.第一題", "A.甲", "續行"]
    assert reread.band_rows(rows, 2) == [rows[3]]
    assert reread.band_rows(rows, 9) == []


# --------------------------------------------------- 裁切區域必須含這一題自己的圖

def _q42_rows_and_pictures():
    """`1152_藥師(一)_藥學(一)` q42 的實測幾何（page 9）。

    這一題的選項是圖：文字列只有三個記號（`B.`/`C.`/`D.`），x 到 50.3；而同一題的圖（step ⑤ 量到
    的 `image_refs` box）在 x 到 158.4、y 34.6→537.1。只按文字列裁切＝給模型一條 11pt 寬的細條，
    它照實回答「讀不出來」（`▢`），而人看到的是「AI 說沒問題」或「AI 問我一個它沒看過的格子」
    （業主 2026-09-25：「有些題目的圖片沒有截圖正確」）。
    """
    rows = [
        {"text": "42. 下列何者為…", "page": 9, "x0": 39.2, "y0": 30.0, "x1": 50.3, "y1": 40.0},
        {"text": "B.", "page": 9, "x0": 39.2, "y0": 34.6, "x1": 50.3, "y1": 537.1},
    ]
    pictures = [{"page": 9, "box": [47.0, 34.6, 158.4, 537.1], "asset_role": "option-image"}]
    return rows, pictures


def test_the_crop_region_is_the_rows_unioned_with_this_questions_own_pictures():
    """負對照：不併圖時（＝修好之前的行為）右界停在文字列的 50.3，選項圖整片不見。"""
    rows, pictures = _q42_rows_and_pictures()
    assert reread.page_extents(rows) == {9: [39.2, 30.0, 50.3, 537.1]}
    assert reread.page_extents(rows, pictures) == {9: [39.2, 30.0, 158.4, 537.1]}


def test_the_renderer_is_asked_for_that_region_and_not_the_rows_alone():
    """走真正的 `crop_rows`（含 stitch），量它**交給 renderer 的框**：這是模型會看到的圖。"""
    import io

    from PIL import Image

    rows, pictures = _q42_rows_and_pictures()
    asked = []

    def fake_crop_region(pdf_path, page, box, *, dpi, margin):
        asked.append((page, list(box)))
        buffer = io.BytesIO()
        Image.new("RGB", (10, 10), (255, 255, 255)).save(buffer, format="PNG")
        return buffer.getvalue()

    original = reread.vision.crop_region
    reread.vision.crop_region = fake_crop_region
    try:
        assert reread.crop_rows("x.pdf", rows, boxes=pictures)
        assert asked == [(9, [39.2, 30.0, 158.4, 537.1])]
        asked.clear()
        assert reread.crop_rows("x.pdf", rows)
        assert asked == [(9, [39.2, 30.0, 50.3, 537.1])], "沒給圖 box 時維持舊行為"
    finally:
        reread.vision.crop_region = original


def test_a_picture_on_a_page_the_band_never_reaches_contributes_the_picture_alone():
    """跨頁的一題：圖落在下一頁、而下一頁沒有這一題的文字列時，該頁只貢獻那張圖的框——
    **不可以**把那一頁的文字一起拉進來（那就是「截別題的來貼上」的機制）。"""
    rows = [{"text": "42. 題幹", "page": 9, "x0": 39.2, "y0": 30.0, "x1": 300.0, "y1": 40.0}]
    pictures = [{"page": 10, "box": [47.0, 34.6, 158.4, 100.0]}]
    assert reread.page_extents(rows, pictures) == {9: [39.2, 30.0, 300.0, 40.0],
                                                   10: [47.0, 34.6, 158.4, 100.0]}


# --------------------------------------------------- 題號印成獨立儲存格的紙本

def _bare_number_paper(number, total=28):
    """A paper that prints each question's number as **its own cell**, with no dot in it.

    The measured shape of `1011_醫事檢驗師_微生物學及臨床微生物學`: `80` number cells, `80` of them
    the leftmost cell on their visual line, `0` carrying a `.`/`、`.
    """
    rows = []
    y = 10.0
    for value in range(1, total + 1):
        rows.append({"text": "%d " % value, "page": 1, "y0": y, "x0": 30.0, "x1": 45.0, "y1": y + 12.0,
                     "size": 10.0})
        rows.append({"text": "第 %d 題的題幹" % value, "page": 1, "y0": y + 1.0, "x0": 50.0,
                     "x1": 400.0, "y1": y + 12.0, "size": 10.0})
        y += 20.0
    return rows


def test_a_paper_that_prints_the_question_number_alone_is_still_numbered():
    """實測 `1011_醫事檢驗師_微生物學及臨床微生物學`：紙本把題號印成**獨立的儲存格**（`'27 '`），
    80 個題號 80 個都是自己那一行的最左格、**0 個**帶點。

    舊規則要求 `27.`／`27、` 落在同一個儲存格裡，所以那一卷 80 題的 `band_rows` 全部回 `[]`
    ——2026-09-25T05:58Z 那一輪 46 題判讀中 **44 題 `no-rows`（95.7%）**，模型一張圖都沒看到、
    人看到的是「紙本讀不到」。負控制：把題號儲存格右移一格（不是最左），這一題就不再被認出來。
    """
    rows = _bare_number_paper(27)
    band = reread.band_rows(rows, 27)
    assert [row["text"] for row in band] == ["27 ", "第 27 題的題幹"], band
    # 舊規則對這個儲存格完全沒有反應——這一行就是那個缺陷的形狀。
    assert re.match(r"^[ \t]*(\d{1,3})[ \t]*[.、．]", "27 ") is None
    assert reread.band_rows(rows, 28)[0]["text"] == "28 "

    indented = [dict(row) for row in rows]
    for row in indented:
        if row["text"] == "27 ":
            row["x0"] = 60.0
    assert reread.band_rows(indented, 27) == [], "不在最左的數字格不可以被當成題號"


def test_a_number_inside_a_table_is_not_a_question_start():
    """`reflow.skeleton` 的兩條幾何規則之一（左邊還有別的東西⇒不是題號）。實測依據在那一支的
    docstring：`1151_藥師(一)_學三` 的表格劑量欄有 `100`，它一旦被當成題號就吃掉整整 15 題。"""
    rows = _bare_number_paper(3)
    rows.append({"text": "100 ", "page": 1, "y0": 10.5, "x0": 200.0, "x1": 215.0, "y1": 22.5,
                 "size": 10.0})  # 與第一題的題號同一行，但在更右邊
    assert reread.band_rows(rows, 100) == []
    assert reread.band_rows(rows, 3)[0]["text"] == "3 "


def test_a_paper_the_skeleton_refuses_still_uses_the_old_rule():
    """**負控制。** 號碼不從 1 開始的卷，骨架回空（`reflow.skeleton` 的規則），此時舊的 `NN.` 規則
    必須接手——否則這一改會讓那種卷比改之前更差。

    （`234.8）` 那種折行**不是**這一條要擋的東西：量到的結果是骨架自己也會收它（插在 q1 之後，
    `skeleton` 給出 `question-234`），所以那不是骨架比舊規則嚴的地方，拿它當閘門的理由不成立。）"""
    rows = [{"text": "2.第二題", "page": 1, "y0": 10.0, "x0": 20.0, "x1": 200.0, "y1": 22.0},
            {"text": "A.甲", "page": 1, "y0": 24.0, "x0": 20.0, "x1": 200.0, "y1": 36.0},
            {"text": "3.第三題", "page": 1, "y0": 40.0, "x0": 20.0, "x1": 200.0, "y1": 52.0}]
    assert reflow.skeleton(reflow.line_table(rows))["questions"] == {}
    assert [row["text"] for row in reread.band_rows(rows, 2)] == ["2.第二題", "A.甲"]
