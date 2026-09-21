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


# --------------------------------------------------------------- substituted ideograph

def test_a_radicals_supplement_character_is_reported_with_the_character_it_means():
    """紙本印 `長`，文字層存 `⻑`（U+2ED1）—— NFKC 折不回來，所以讀者看到錯的字。

    這一條是實際量到的：`1081_藥師(一)_藥劑學` Q34 的選項 `延⻑藥物…`，以 300 dpi 算圖回對
    紙本，紙本印的是 U+9577。使用者將這題判為 block。
    """
    question = _q(34, stem="下列何者屬於⻑效型的胰島素注射劑？")
    question["options"][0]["text"] = "延⻑藥物於黏膜之作⽤時間"
    found = disputes.of_question(question)
    kinds = [d["kind"] for d in found]
    assert kinds == ["substituted-ideograph"], kinds
    dispute = found[0]
    assert dispute["severity"] == "review"
    # 位址：哪一個欄位、第幾個字元、前後文。沒有位址就沒得查。
    assert [item["char"] for item in dispute["substitutions"]] == ["\u2ed1", "\u2ed1"]
    assert {item["field"] for item in dispute["substitutions"]} == {"stem", "option A"}
    assert all(item["means"] == "\u9577" for item in dispute["substitutions"])
    assert dispute["substitutions"][0]["context"].startswith("下列何者屬於")


def test_kangxi_radicals_are_not_reported_because_nfkc_folds_them():
    """負對照：Kangxi radicals（U+2F00..U+2FDF）NFKC 會折疊，讀者看到的是對的字。

    全庫量測：3,420 次 Kangxi radical 有 3,341 次（97.7%）折得回普通字，而 Radicals
    Supplement 那 79 次一次也折不回。所以判準是**區塊**，不是字表；這條測試就是釘住那個區塊。
    """
    question = _q(9, stem="下列何者可使⽤乾熱滅菌法？")
    question["options"] = [{"key": k, "text": t}
                           for k, t in zip("ABCD", ["⽢油", "⽯蠟", "⽔", "⼈"])]
    assert "substituted-ideograph" not in _kinds(question)
    # 而且那幾個字確實是會被 NFKC 折疊的，所以這個負對照不是空的。
    import unicodedata
    for char in "⽤⽢⽯⽔⼈":
        assert unicodedata.normalize("NFKC", char) != char, char


# --------------------------------------------------------------------- substituted letter

def test_a_cyrillic_character_inside_a_chinese_word_is_reported_with_its_script_and_address():
    """紙本印 `①`，文字層存西里爾 `ћ`（U+045B）—— 讀者看到的是亂碼。

    實際量到的：`1131_物理治療師_神經疾病物理治療學` Q2 的題幹
    `…不可變參數？①總時間 ②相對時間…`，文字層存的是 `ћќѝўџ`。以 300 dpi 回對紙本確認過。
    使用者將同一卷的四題（q030/q036/q044/q078）判為 block。
    """
    question = _q(2, stem="下列何者為通用動作程式中的不可變參數？\u045b\u7576\u6642\u9593")
    found = disputes.of_question(question)
    kinds = [d["kind"] for d in found]
    assert kinds == ["substituted-script"], kinds
    dispute = found[0]
    assert dispute["severity"] == "review"
    substitution = dispute["substitutions"][0]
    assert substitution["char"] == "\u045b"
    assert substitution["script"] == "CYRILLIC"
    assert substitution["field"] == "stem"
    assert substitution["context"] == "不可變參數？\u045b\u7576\u6642\u9593"
    # 這個 kind 刻意不猜紙本印的是什麼，所以它不能帶著一個錯的「應為」。
    assert "means" not in substitution


def test_a_foreign_character_between_latin_letters_is_not_reported():
    """負對照：字母要緊貼漢字才報。

    紙本合法地印英文與希臘文（實測：希臘字母 6,653 次、修飾字母 1,323 次）。
    若把「出現外國字母」當成訊號，整個題庫都是訊號——所以判準是「它插在一個中文詞裡」，
    也就是**錯字的形狀**：一個走錯位置的字母。這一條會讓那條規則若被放寬就失敗。
    """
    # 1ˢᵗ 是實際語料：`insufficient 1ˢᵗ metatarsophalangeal`，ˢ 是合法的修飾字母。
    assert "substituted-script" not in _kinds(_q(62, stem="第一蹠趾關節背屈不足（insufficient 1\u02e2ᵗ）"))
    # 純英文片語裡的西里爾字母：兩側都不是漢字。
    assert "substituted-script" not in _kinds(_q(3, stem="drug name \u045babc"))


def test_greek_is_not_a_foreign_script_because_the_paper_prints_it():
    """負對照：希臘字母是紙本真的會印的字，不是亂碼。

    β/α/μ 在語料裡上萬次，都是真的。把它們列進外國字母會讓這條規則一上線就淹掉。
    """
    assert "substituted-script" not in _kinds(_q(4, stem="血紅素（Hb）與\u03b2球蛋白的結合力"))


def test_a_foreign_punctuation_mark_inside_a_chinese_word_is_also_reported():
    """不限於字母：實測命中的 475 個字元裡有 64 個標點、26 個數字，而它們壞得一樣徹底。

    實際語料：`下࠻何種診斷` 應讀 `下列何種診斷`（U+083B 撒馬利亞標點），`光߉` 應讀 `光量`
    （U+07C9 西非 N'Ko 數字）。所以判準是字元名稱裡的**字母系統**，不是 `category`。
    """
    question = _q(22, stem="下\u083b何種診斷最有可能？")
    dispute = disputes.of_question(question)[0]
    assert dispute["kind"] == "substituted-script"
    assert dispute["substitutions"][0]["script"] == "SAMARITAN"


def test_every_foreign_letter_reports_the_script_so_the_reviewer_knows_what_to_look_for():
    """多個字母來自不同字母系統時，每一個都要說出自己是哪個系統。"""
    question = _q(5, stem="\u0f4a\u5b57\u8207\u045b\u5b57")
    dispute = disputes.of_question(question)[0]
    assert dispute["kind"] == "substituted-script"
    assert {item["script"] for item in dispute["substitutions"]} == {"TIBETAN", "CYRILLIC"}
    assert len(dispute["substitutions"]) == 2


# ------------------------------------------------------------------ severity

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
