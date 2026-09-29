# -*- coding: utf-8 -*-
"""The re-read: a transcription is compared, and never a verdict.

Only the pure parts are tested here - the crop and the model call need a PDF and an endpoint. What
matters for correctness is the comparison: a report that fires on formatting is useless, and a
report that stays silent on a real substitution is worse. Both directions are asserted, and the
substitution case is one this corpus actually contains.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from qbr import reread  # noqa: E402


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
