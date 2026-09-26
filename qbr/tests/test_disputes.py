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

def test_a_flattened_offset_is_a_dispute_with_the_formula_it_hit():
    # The class the reviewer kept blocking and the model kept naming `SUPERSCRIPT_FLATTENED`. The
    # paper prints `C=80e-0.35t` with `-0.35t` raised; Unicode has no superscript period, so the
    # offset table refuses the whole run and the reader sees it flat. Nothing else can find this -
    # after the text is read, a flattened formula and a flat one are the same string - so the fact
    # is carried out of the page and the dispute reports it with the run's own text.
    raised = _q(flattened_offsets=[{"text": "-0.35t", "kind": "sup", "blockers": ["."],
                                    "why": "offset-table-cannot-express"}])
    kinds = {d["kind"] for d in disputes.of_question(raised)}
    assert "flattened-offset" in kinds
    found = [d for d in disputes.of_question(raised) if d["kind"] == "flattened-offset"][0]
    assert found["runs"] == ["-0.35t"] and "-0.35t" in found["detail"]

    # Negative control: no evidence means no dispute. A rule that fires on every question would
    # look exactly like a rule that finds every flattened formula.
    clean = {d["kind"] for d in disputes.of_question(_q())}
    assert "flattened-offset" not in clean


def test_a_flattened_offset_dispute_is_a_review_not_a_blocker():
    # It is a defect, but not one that makes the question unreadable: the formula is on the paper
    # and a person can fix the reading against it. A blocker would quarantine the paper.
    severity, _note = disputes.KINDS["flattened-offset"]
    assert severity == "review"

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


def test_a_foreign_character_next_to_latin_letters_is_still_reported():
    """曾經漏掉的那一類：錯字的位置在拉丁字母裡面、或旁邊只有空白。

    前一版要求外國字母「緊貼漢字」，理由是「紙本合法地印英文與希臘文」。那個理由**是錯的**
    ——英文與希臘文是 `LATIN` 與 `GREEK`，已經被 `NATIVE_SCRIPT_PREFIXES` 擋掉了，所以鄰居
    條件從來沒有擋掉它們。它實際擋掉的是 1,042 處裡面的 567 處真缺陷，例如：

      * `Waldenstrӧm's`、`Henoch-Schӧnlein`：錯字在一個英文字裡面；
      * `（²²⁶Ra Г=8.25 R-cm²/mg-h）`：紙本印 Γ，錯成 Г，旁邊是空白；
      * `ћќѝ` 這種整串都是西里爾的選項：鄰居是另一個西里爾字母。

    這一條釘住那個結論：字母本身是外國字母系統就夠了，不必旁邊有漢字。
    """
    # 實際語料：Waldenström's macroglobulinemia。
    assert "substituted-script" in _kinds(_q(3, stem="華氏巨球蛋白血症（Waldenstr\u04e7m's）"))
    # 紙本印 Γ，文字層存 Г；前後是空白。
    assert "substituted-script" in _kinds(_q(46, stem="（²²⁶Ra \u0413=8.25 R-cm²/mg-h）"))


def test_a_subscript_or_modifier_letter_is_not_a_foreign_script():
    """負對照：修飾字母（1ˢᵗ 的 ˢ）是紙本真的印得出的排版，不是亂碼。

    實測語料裡有 13,535 個 SUPERSCRIPT、6,469 個 SUBSCRIPT、1,323 個修飾字母。它們的字元名稱
    開頭是 `MODIFIER`／`SUPERSCRIPT`／`SUBSCRIPT`，不在外國字母系統清單裡，所以不會觸發。
    這條是拿掉漢字鄰居條件之後仍然需要存在的界線：**放寬的是位置，不是字母系統。**
    """
    assert "substituted-script" not in _kinds(_q(62, stem="第一蹠趾關節背屈不足（insufficient 1\u02e2ᵗ）"))
    assert "substituted-script" not in _kinds(_q(63, stem="血中濃度C\u209a與時間t的關係"))


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


# ---------------------------------------------------- flat unit exponents (2026-09-22 batch)

def test_a_flat_unit_exponent_is_reported_with_the_printing_the_paper_used():
    """`539 cm-1` is `cm⁻¹` gone flat; the dispute names both spellings.

    The fix form is `<sup>-1</sup>`, not `cm⁻₁`. A negative exponent is a **superscript**, and the
    queue's own correct spelling measures 204 occurrences of `<sup>-1</sup>` and zero of any other
    form (`cm\u207b\u00b9` is the paper's compressed spelling of the same thing). This test first
    asserted `cm\u207b\u2081` - a subscript - which is the wrong character, and the apply step would
    have written it into the question. Kept as a check on the *fix* and not only on the detection.

    Measured on the 2026-09-22 block pass: 34 questions carry the shape, 6 of them were judged by
    a person, all 6 were blocked -> block rate 100% against the 5.4% corpus baseline.
    """
    question = _q(options=[{"key": "A", "text": "254 Å"}, {"key": "B", "text": "254 nm"},
                           {"key": "C", "text": "539 nm"}, {"key": "D", "text": "539 cm-1"}],
                  accepted=["D"])
    found = [d for d in disputes.of_question(question) if d["kind"] == "flat-offset"]
    assert len(found) == 1, found
    assert found[0]["detail"] == "cm-1→cm<sup>-1</sup>", found[0]["detail"]
    assert found[0]["runs"][0]["field"] == "option D"
    # `position` is an index into the **stored** option text, which is what a repair slices.
    assert found[0]["runs"][0]["position"] == len("539 ")

    # Negative control, and the two shapes it must not answer to are the two that sit next to it
    # on the same page: a range has digits on both sides of its hyphen, a nomenclature has letters
    # on the left. Both are printed that way by the paper itself.
    quiet = _q(stem="範圍 2000-4000 與代號 IL-2")
    assert "flat-offset" not in {d["kind"] for d in disputes.of_question(quiet)}


def test_a_flat_unit_exponent_dispute_is_a_review_not_a_blocker():
    severity, _note = disputes.KINDS["flat-offset"]
    assert severity == "review"


# ------------------------------------------------------------ punctuation-only options

def test_an_option_of_bare_marks_is_a_hole_and_the_number_forms_are_not():
    """`;` alone in a slot is a missed read; `Ⅰ＞Ⅱ` / `①②③` / `0.5 L/h` are content.

    The test is `isalnum()`, which is why the corpus's own non-ASCII content (Roman numerals,
    circled digits, subscripts) counts as written. Measured 2026-09-22: 17 questions, 1 judged,
    blocked -> 100%.
    """
    hole = _q(options=[{"key": "A", "text": ";"}, {"key": "B", "text": "0.5 L/h"},
                       {"key": "C", "text": "Ⅰ＞Ⅱ"}, {"key": "D", "text": "①②③"}])
    found = [d for d in disputes.of_question(hole) if d["kind"] == "punctuation-only-option"]
    assert len(found) == 1 and found[0]["options"] == ["A"], found
    assert found[0]["severity"] == "blocker"

    # Negative control: the three non-ASCII forms above are the ones a plain `[0-9A-Za-z]` class
    # would have called empty, so a run that reports them is a run that used the old test.
    full = _q(options=[{"key": k, "text": t} for k, t in
                       (("A", "0.5 L/h"), ("B", "Ⅰ＞Ⅱ"), ("C", "①②③"), ("D", "₂H"))])
    assert "punctuation-only-option" not in {d["kind"] for d in disputes.of_question(full)}


# ------------------------------------------------------------------ flattened tables

_TABLE_JOINED = ("已知下列藥品對肝細胞之穿透力（permeability）都很強，其他相關資訊如下表： "
                 "A藥B藥C藥D藥肝臟固有清除率（L/min）20 0.5 1.5 0.1 "
                 "血中蛋白質結合率（%） 50 10 30 95 "
                 "在正常肝血流（1.5 L/min）的情況下，下列敘述何者正確？")


def test_a_table_that_arrived_as_one_line_is_reported_with_its_headers():
    """The paper's column headers jammed against the numbers, with no newline left, is the join.

    Measured 2026-09-22: 22 questions, 1 judged, blocked -> 100%. The header row is what makes it
    decidable: in prose each of those letters would be an option label followed by a period, so a
    run of three or more with no separator is the table and nothing else.
    """
    question = _q(stem=_TABLE_JOINED)
    found = [d for d in disputes.of_question(question) if d["kind"] == "table-flattened"]
    assert len(found) == 1, found
    assert found[0]["headers"] == ["A", "B", "C", "D"], found[0]
    assert len(found[0]["numbers"]) >= 4, found[0]

    # Negative control: the same content with its rows intact is a normal question. The line break
    # is the whole difference, which is why the rule tests for it rather than counting tokens.
    rows = _q(stem=_TABLE_JOINED.replace("： A藥", "：\nA藥", 1))
    assert "table-flattened" not in {d["kind"] for d in disputes.of_question(rows)}


def test_a_short_stem_without_a_header_row_is_not_called_a_flattened_table():
    """Two headers are not enough to be a table: 3+ is the declared shape."""
    quiet = _q(stem="Aα、Bβ 之比較為何？")
    assert "table-flattened" not in {d["kind"] for d in disputes.of_question(quiet)}


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


# --------------------------------------------------------------------- 上下標的兩種拼法（實作）
# 紙面（PDF 直排，U+2070.. 已含大小與位移）與閱讀面（瀏覽器 UA 樣式，≈Word）
# 是同一事實的兩種拼法；量測只認第三種（ASCII 壓平形）。對照（charter §4 第 5 項）。


def test_the_two_spellings_collapse_to_one_measurement_form():
    """`html_sup_sub` 與 `plain_sup_sub` 的合成律：兩種拼法都收斂到同一個 ASCII 壓平形。"""
    from qbr import extract
    compressed = "cm\u207b\u00b9"                       # 紙面：U+2070 系列的偏移字元
    marked = "cm<sup>-1</sup>"                              # 阅讀面：UA 的 sup/sub（≈Word）
    flat = "cm-1"                                            # 量测面：regex 认的那個形
    assert extract.html_sup_sub(compressed) == marked
    assert extract.plain_sup_sub(marked) == flat
    assert extract.plain_sup_sub(compressed) == flat        # 無標籤時也不動
    assert extract.html_sup_sub(extract.html_sup_sub(compressed)) == marked   # 幂等
    assert extract.plain_sup_sub(extract.plain_sup_sub(marked)) == flat       # 幂等
    # 同字形的雨個拼法（`-` 舆 `≈` 同為 U+207B）在反表裡先者勝，與正表一致
    assert extract.html_sup_sub("\u2080\u2081\u2082") == "<sub>012</sub>"


def test_lone_offset_letter_and_digit_are_the_same_run():
    """單檔上下標（U+2070..）舆 ¹²³ 入同一對摺；一個內文一串標記。"""
    from qbr import extract
    assert extract.html_sup_sub("0.23\u00b9") == "0.23<sup>1</sup>"
    assert extract.html_sup_sub("\u00b9\u00b2\u00b3") == "<sup>123</sup>"
    assert extract.html_sup_sub("H\u2082O") == "H<sub>2</sub>O"
    assert extract.plain_sup_sub("H<sub>2</sub>O") == "H2O"


def test_flat_offset_rule_reads_the_markup_form_and_its_negative_control():
    """正對照：`cm-1` 命中、`position` 在**儲存字串**坐標上。
    負對照：`cm<sup>-1</sup>`（已標好）與 `cm\u207b\u00b9`（紙本自己的形）**不得**命中——
    它們已經帶著上下標，不是缺點。舊版先把輸入 `plain_sup_sub` 折過再量，
    所以這兩者都變成了「有缺點」：量到的 299 run 裡 222 個（74%）是這種誤報。
    界內：`0-4.5`（範圍）、`IL-2`（字母前綴）→ 0 命中。"""
    hits = disputes.flat_offset_pairs({"stem": "cm-1", "options": []})
    assert len(hits) == 1 and hits[0]["flat"] == "cm-1"
    assert hits[0]["position"] == 0, "position 是儲存字串坐標（cm 從 0 開始）"

    assert disputes.flat_offset_pairs({"stem": "cm<sup>-1</sup>", "options": []}) == \
        [], "已經標好的不是缺點（這條是舊版錯掉的負對照）"
    assert disputes.flat_offset_pairs({"stem": "cm\u207b\u00b9", "options": []}) == \
        [], "紙本自己的壓縮形也不是缺點"

    assert disputes.flat_offset_pairs({"stem": "\u918d\u54c1 0-4.5 \u5373\u53ef", "options": []}) == []
    assert disputes.flat_offset_pairs({"stem": "IL-2 \u578b", "options": []}) == []


def test_punctuation_only_and_flattened_table_are_measured_on_the_same_form():
    """⑧/�20 在同一個壓平形上量；`flattened_table` 回單一 dict（或 None），不是列表。"""
    from qbr import extract
    assert "；".isalnum() is False and "\u2160".isalnum() is True   # 3.14 的實测値
    only = {"stem": "s", "options": [{"key": "A", "text": "\u809d"},
                                     {"key": "B", "text": "\u3001"}]}
    # 肝（U+809D）是 alnum → 有內容；、（U+3001）不是 → 是洞。Ⅰ（U+2160）是 alnum → 有內容。
    assert [h["key"] for h in disputes.punctuation_only_options(only)] == ["B"]
    assert disputes.punctuation_only_options(
        {"stem": "s", "options": [{"key": "A", "text": "\u2160;"}]}) == []
    # `\u00b9` 是這套表对数字 1 的拼法（`\u2071` 在表裡是字母 i 的字形），
    # 兩種拼法在量測形上收斂到同一個 ASCII 串：`8 1 2`。
    flat = ("A為8 \u00b9 2\uff0cB為9 \u00b9 3\uff0c則A\u3001B"
            "相乘之積為多少\uff1f")
    hits = disputes.flattened_table({"stem": flat, "options": []})
    assert hits["headers"] == ["A", "B", "B"] and len(hits["numbers"]) == 6
    # markup 拼法同值（`<sup>` 對量測形而言只是包裝）；無 A-D 字首或行内有 `\n` 都不是表格
    marked = {"stem": extract.plain_sup_sub(flat.replace("\u00b9", "<sup>1</sup>")),
              "options": []}
    assert disputes.flattened_table(marked) == hits
    assert disputes.flattened_table({"stem": "1\u809d 2\u813e", "options": []}) is None
    assert disputes.flattened_table({"stem": "A\u70ba8\uff1bB\u70ba9\u3002", "options": []}) is None
    assert disputes.flattened_table({"stem": "A\u70ba8\nB\u70ba9\nC\u70ba10", "options": []}) is None
    assert extract.plain_sup_sub(extract.html_sup_sub("H\u2082O")) == "H2O"
