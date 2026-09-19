# -*- coding: utf-8 -*-
"""The disputes layer: what is uncertain, and the proof that a clean question stays clean.

Every test here has a **negative control**. A dispute detector that reports nothing and one that
reports everything are equally useless, and the second is the failure mode that actually happened
in this project - 274 `empty-option` disputes raised to find the 23 that were real - so each rule
is asserted both to fire on its case and to stay silent on the case next to it.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from qbr import disputes  # noqa: E402


def _q(number=1, *, options=None, accepted=None, **extra):
    question = {
        "question_number": number,
        "stem": "題幹？",
        "options": options if options is not None else [
            {"key": k, "text": "選項" + k} for k in "ABCD"],
        "answer_payload": {"accepted_values": accepted if accepted is not None else ["A"]},
    }
    question.update(extra)
    return question


def _kinds(question, **kwargs):
    return [d["kind"] for d in disputes.of_question(question, **kwargs)]


# --------------------------------------------------------------- negative control

def test_a_clean_question_raises_nothing():
    """對照組：完全正常的題目必須一個爭議都沒有。

    沒有這一條，一個「永遠說有問題」的偵測器會通過所有正向測試。
    """
    assert disputes.of_question(_q()) == []


def test_an_option_that_is_a_picture_is_not_an_empty_option():
    """空選項但綁了圖 -> 正常，不是爭議。

    量測：274 題有空選項，其中 251 題是正確的（選項就是那張圖），只有 23 題是真缺陷。
    分不出這兩者就會產生一個 92% 是噪音的清單。
    """
    question = _q(options=[{"key": "A", "text": ""}, {"key": "B", "text": "有字"}])
    assert "empty-option" in _kinds(question), "沒有圖時應該報"
    assert "empty-option" not in _kinds(question, option_images={"A"}), "有圖時不該報"


# --------------------------------------------------------------------- dangling

def test_an_answer_that_names_no_option_is_a_blocker():
    """答案卷指到不存在的選項：兩個官方產物互相矛盾，而且永遠不自動裁決。"""
    found = disputes.of_question(_q(accepted=["E"]))
    assert [d["kind"] for d in found] == ["dangling-answer"]
    assert found[0]["severity"] == "blocker"


def test_a_question_with_no_options_at_all_still_reports_the_dangling_answer():
    """十題完全沒有讀到選項，而那十題正好就是答案指不到選項的那十題。

    第一版用 `if keys and dangling` 守衛，於是**最嚴重的那一種情況被自己的守衛藏起來**：
    `keys` 是空的，條件不成立，最強的訊號從來沒被報出來。
    """
    question = _q(options=[], accepted=["B"])
    kinds = _kinds(question)
    assert "dangling-answer" in kinds, "沒有選項時更該報，不是不報"


# ------------------------------------------------------------------ option shape

def test_option_count_is_compared_against_the_papers_own_alphabet():
    """選項數要和紙本自己的字母表比，不是和一個固定數字比。"""
    question = _q(options=[{"key": k, "text": "x"} for k in "ABCD"])
    assert "option-shape" not in _kinds(question, alphabet_size=4)
    found = disputes.of_question(question, alphabet_size=5)
    assert [d["kind"] for d in found] == ["option-shape"]
    assert found[0]["expected"] == 5 and found[0]["found"] == 4


def test_all_four_options_empty_is_a_shape_dispute_not_four_empty_options():
    """四個選項全空＝題身不見了，報一次形狀，不是報四次空選項。"""
    question = _q(options=[{"key": k, "text": ""} for k in "ABCD"])
    kinds = _kinds(question)
    assert kinds == ["option-shape"]
    assert "empty-option" not in kinds


# ------------------------------------------------------------------- lost glyphs

def test_a_lost_glyph_is_a_dispute_with_its_address():
    """字形遺失是已知位置的真缺陷，必須帶著位址，而且不得猜字。"""
    question = _q(lost_glyphs=[{"in": "轉氨", "position": 3}],
                  lost_glyph_note="第 3 個字元無法辨識")
    found = disputes.of_question(question)
    assert [d["kind"] for d in found] == ["lost-glyph"]
    assert found[0]["severity"] == "review"
    assert found[0]["glyphs"], "位址要帶著"


# --------------------------------------------------------------- unresolved mark

def test_an_unresolved_private_use_mark_is_a_dispute_but_a_resolved_one_is_not():
    """紙本定義的記號：對照得到就顯示對照，對照不到才是爭議。"""
    question = _q(unresolved_marks=["\ue000", "\ue001"],
                  subitem_legend={"\ue000": "砂粒病毒"})
    found = disputes.of_question(question)
    assert [d["kind"] for d in found] == ["unresolved-mark"]
    assert found[0]["marks"] == ["\ue001"], "只有沒對照到的那個要報"


# --------------------------------------------------------------------- severity

def test_worst_severity_ranks_blocker_above_review():
    """排序要讓 blocker 浮上來，否則快速審核時會被漏掉。"""
    rows = [
        {"disputes": [{"kind": "lost-glyph", "severity": "review"}]},
        {"disputes": [{"kind": "empty-option", "severity": "review"},
                      {"kind": "dangling-answer", "severity": "blocker"}]},
        {"disputes": None},
    ]
    assert disputes.worst_severity(rows[0]["disputes"]) == "review"
    assert disputes.worst_severity(rows[1]["disputes"]) == "blocker"
    assert disputes.worst_severity(rows[2]["disputes"]) == ""


def test_summarise_counts_kinds_and_severities():
    """彙總要能回答「這一卷有幾種問題、各幾個」。"""
    rows = [
        {"disputes": [{"kind": "lost-glyph", "severity": "review"}]},
        {"disputes": [{"kind": "lost-glyph", "severity": "review"},
                      {"kind": "dangling-answer", "severity": "blocker"}]},
        {"disputes": None},
    ]
    summary = disputes.summarise(rows)
    assert summary["questions_with_disputes"] == 2
    assert summary["disputes"] == 3
    assert summary["by_kind"] == {"lost-glyph": 2, "dangling-answer": 1}
    assert summary["by_severity"] == {"blocker": 1, "review": 2, "info": 0}


def test_every_kind_declares_a_severity_and_a_meaning():
    """每一種爭議都要有嚴重度與一句人話，否則 UI 無從顯示。"""
    for kind, (severity, note) in disputes.KINDS.items():
        assert severity in disputes.SEVERITY_ORDER, kind
        assert note.strip(), kind
