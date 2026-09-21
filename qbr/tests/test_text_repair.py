# -*- coding: utf-8 -*-
"""Two extraction defects found by reading the paper, and what they cost.

Both were found by a reviewer comparing question 45 and 46 of
1152_醫事檢驗師_生物化學與臨床生化學.pdf against the printed page, not by any check in the
pipeline. They are geometry defects in how extracted spans are assembled into text, and both
are silent: the run passed every gate while four questions had their options cut in half.

They are pinned here because the failure mode is invisible. Nothing about the output says
"this option is missing its formula"; it just looks like a question whose option A is `[Na`.

The fix lives in the extraction layer, not in a post-processing pass, and that placement is
itself the lesson. Three earlier attempts patched the assembled text and each one was
overfitted to a single engine: a rule that read `size` was blind on poppler, which reports no
sizes at all, and a rule that required spans to share a block was defeated by PyMuPDF, which
puts each fragment of `[K⁺]－[PO₄³⁻]－[HCO₃⁻]` in a block of its own. Grouping spans into
visual lines by vertical overlap depends on neither, which is why it holds on both engines.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

from qbr import extract, repair, paths  # noqa: E402

# The corpus is located, not written down. A hard-coded path here was the exact defect this
# package set out to remove, and it would only have moved the problem to whichever machine the
# path happened to be true on.
ASSET_ROOT = paths.asset_root()
PAPER = os.path.join(
    ASSET_ROOT, "10_official_pdf", "by_official_catalog", "醫事檢驗師", "115", "第2次",
    "1152_醫事檢驗師_生物化學與臨床生化學.pdf")
ANSWER_SHEET = PAPER.replace(".pdf", "_ANS.pdf")
requires_paper = pytest.mark.skipif(
    not os.path.isfile(PAPER), reason="official paper not on disk (not in git); set ASSET_ROOT")
candidates_jsonl = os.path.join("/tmp/qbr-golden-001", "review-ui", "candidates.jsonl")
requires_run = pytest.mark.skipif(not os.path.isfile(candidates_jsonl), reason="no run to inspect")


# ------------------------------------------------------- visual lines from spans

def _span(text, *, x0=0.0, x1=10.0, top=100.0, bottom=110.0, size=11.0):
    return {"text": text, "size": size, "font": "F", "bbox": (x0, top, x1, bottom)}


def test_spans_that_overlap_vertically_are_one_visual_line():
    """The reader's rule: two boxes are on one line when they overlap vertically.

    Question 45's formula arrives as fragments whose y0 differ by a point, because the
    superscript is raised. They overlap almost completely, so they are one line.
    """
    spans = [(0, _span("C.[K", x0=39.2, x1=61.2, top=633.79, bottom=644.81)),
             (0, _span("+", x0=61.2, x1=63.9, top=632.67, bottom=638.18, size=5.5)),
             (0, _span("]－[PO", x0=64.0, x1=97.1, top=633.79, bottom=644.81))]
    lines = extract.group_visual_lines(spans)
    assert len(lines) == 1
    assert "".join(span["text"] for span in lines[0]) == "C.[K+]－[PO"


def test_genuinely_separate_lines_are_kept_apart():
    """Question 46's stem wraps; the two rows share no vertical extent at all."""
    spans = [(0, _span("中毒現", top=681.8, bottom=692.8)),
             (0, _span("象。患者", top=702.8, bottom=713.8))]
    assert len(extract.group_visual_lines(spans)) == 2


def test_fragments_in_different_blocks_still_form_one_line():
    """PyMuPDF gives each fragment of a formula its own block.

    This is the trap that defeated the third fix: requiring a shared block looks reasonable
    and leaves `[Na⁺]` truncated on the real paper, because block 38 holds `C.[K` and block
    39 holds `+]`.
    """
    spans = [(38, _span("C.[K", x0=39.2, x1=61.2, top=633.79, bottom=644.81)),
             (39, _span("+]", x0=61.2, x1=63.9, top=632.67, bottom=638.18, size=5.5)),
             (40, _span("]－[PO", x0=64.0, x1=97.1, top=633.79, bottom=644.81))]
    lines = extract.group_visual_lines(spans)
    assert len(lines) == 1, "a shared block is not what makes a line a line"


def test_the_visual_line_is_ordered_left_to_right():
    spans = [(0, _span("丙", x0=50.0)), (0, _span("甲", x0=10.0)), (0, _span("乙", x0=30.0))]
    lines = extract.group_visual_lines(spans)
    assert [span["text"] for span in lines[0]] == ["甲", "乙", "丙"]


# ------------------------------------------------------- offsets inside a line

def test_a_superscript_becomes_a_unicode_superscript():
    """`+` at 5.5pt raised above an 11pt body is the superscript in `[K⁺]`."""
    spans = [_span("C.[K", x0=39.2, x1=61.2, top=633.79, bottom=644.81),
             _span("+", x0=61.2, x1=63.9, top=632.67, bottom=638.18, size=5.5),
             _span("]", x0=63.9, x1=65.0, top=633.79, bottom=644.81)]
    assert extract.read_spans(spans) == "C.[K⁺]"


def test_a_subscript_becomes_a_unicode_subscript():
    """`4` sitting low in `PO₄³⁻` is a subscript; the raised `3-` beside it is not."""
    spans = [_span("]－[PO", x0=64.0, x1=97.1, top=633.79, bottom=644.81),
             _span("4", x0=97.1, x1=99.8, top=638.20, bottom=643.71, size=5.5),
             _span("3-", x0=99.7, x1=105.2, top=632.67, bottom=638.18, size=5.5),
             _span("]", x0=105.2, x1=107.0, top=633.79, bottom=644.81)]
    assert extract.read_spans(spans) == "]－[PO₄³⁻]"


def test_a_body_sized_run_is_never_taken_for_an_offset():
    spans = [_span("A.[Na", x0=39.2, x1=66.7), _span("+]", x0=66.7, x1=80.0)]
    assert extract.read_spans(spans) == "A.[Na+]"


# ------------------------------- characters the paper draws twice, read twice
#
# The defect this block pins was measured, not guessed: over the 841 shipped source papers the
# old reader produced 595 adjacent doubled characters that neither PyMuPDF's own extractor nor
# poppler contains. The reader laid every span end to end, and on these papers a run can reach it
# as several spans that share their boundary character - the printer draws that glyph twice, once
# at the end of one span and once at the start of the next.
#
# The cost was not cosmetic. The doubled text went into the shipped queue (`血液液中`, `下列列`),
# and on the papers whose question numbers are drawn the same way it doubled the numbers too:
# question `3` read as `33`, `34` as `344`. The numbering rule accepts only a successor, so the
# run stopped there and the rest of the paper was withheld - `1031_醫師(二)_醫學(四)` was reported
# as **2 questions out of 80**. Six `醫師(二)` papers carried `count-mismatch` for this reason.

def test_a_character_shared_by_two_spans_is_not_read_twice():
    """The seam, in the paper's own numbers: `…貧血。血液` then `液中沒有…`.

    `1131_醫事檢驗師_臨床血液學與血庫學`, question 1. The `液` of the first span sits at
    [424.32, 435.35]; the `液` of the second at [424.32, 434.40] - one character, two boxes.
    """
    spans = [_span("少，而且好像越來愈厲害，尤其是貧血。血液", x0=215.40, x1=435.35, size=11.0),
             _span("液中沒有發現對抗紅血球", x0=424.32, x1=545.27, size=11.0)]
    assert extract.read_spans(spans) == "少，而且好像越來愈厲害，尤其是貧血。血液中沒有發現對抗紅血球"


def test_the_same_character_drawn_at_the_same_box_is_not_read_twice():
    """The other shape: a bare question number drawn twice at the *same* box.

    `1031_醫師(二)_醫學(四)`, whose numbers are two-column bare numerals. Question 3 arrives as
    span `3` at x=[52.68, 58.17] followed by span `3` at x=[52.68, 58.17]. Read end to end that
    is `33`, and `33` is not the successor of `2`, so the paper stopped at question 2.
    """
    spans = [_span("3", x0=52.68, x1=58.17),
             _span("3", x0=52.68, x1=58.17),
             _span(" ", x0=58.20, x1=61.25),
             _span("一位2歲男童於發燒", x0=72.30, x1=173.16)]
    text = extract.read_spans(spans)
    assert text == "3  一位2歲男童於發燒", text
    assert "33" not in text


def test_a_span_contained_in_the_one_before_it_is_not_read_twice():
    """Question 34 arrives as `34` then `4`, the second lying inside the first."""
    spans = [_span("34", x0=47.16, x1=58.17), _span("4", x0=52.68, x1=58.17)]
    assert extract.read_spans(spans) == "34"


def test_a_real_repetition_survives_because_its_boxes_do_not_coincide():
    """Negative control: `常常` is written twice on the page, at two positions.

    The old code read `常常` correctly and must go on doing so. A rule that deleted every
    repeated character would have passed the three tests above and broken this one.
    """
    spans = [_span("常常", x0=10.0, x1=32.0),
             _span("常常", x0=32.0, x1=54.0)]
    assert extract.read_spans(spans) == "常常常常"


def test_a_question_number_followed_by_its_stem_is_not_a_redraw():
    """Negative control: `3   3 歲兒童` - the number, a gap, then a value.

    The numeral is 3 and the stem opens with 3 (the age). Their boxes do not overlap, so nothing
    is dropped; and the text test alone would not be enough here, because `3` *is* in the line.
    """
    spans = [_span("3", x0=52.68, x1=58.17),
             _span(" ", x0=58.20, x1=72.28),
             _span("3 歲兒童的體重", x0=72.30, x1=150.0)]
    assert extract.read_spans(spans) == "3 3 歲兒童的體重"


def test_the_fake_bold_header_keeps_every_one_of_its_characters():
    """Negative control: the header is the same run drawn four times, and none may be deleted.

    `1131_醫事檢驗師_臨床血液學與血庫學` sets `科目名稱：臨床血液學與血庫` four times at
    x0 33.96 / 33.96 / 34.20 / 34.20. These are overlays, not seams: each span starts where the
    one before it started, and some of them lie *inside* it. A first attempt at the seam rule -
    which trimmed any covered width without checking direction - read this header as
    `科目名稱科目名稱科目名稱科目名稱：臨床血液學與血庫庫學`; it deleted `庫`, `學` and both
    `科目名稱`s' tails. Deleting real characters is worse than the duplication it removes, so
    this case is pinned.
    """
    spans = [_span("科目名稱", x0=33.96, x1=90.11, top=113.53, bottom=127.90, size=14.0),
             _span("科目名稱：臨床血液學與血庫", x0=33.96, x1=216.47, top=113.29, bottom=127.66, size=14.0),
             _span("科目名稱", x0=34.20, x1=90.35, top=113.29, bottom=127.66, size=14.0),
             _span("科目名稱", x0=34.20, x1=90.35, top=113.53, bottom=127.90, size=14.0),
             _span("臨床血液學與血庫", x0=104.04, x1=216.23, top=113.53, bottom=127.90, size=14.0),
             _span("臨床血液學與血庫", x0=104.04, x1=216.23, top=113.29, bottom=127.66, size=14.0),
             _span("臨床血液學與血庫", x0=104.28, x1=216.47, top=113.53, bottom=127.90, size=14.0),
             _span("庫學", x0=202.44, x1=230.39, top=113.29, bottom=127.66, size=14.0),
             _span("庫學 ", x0=202.44, x1=237.42, top=113.29, bottom=127.90, size=14.0)]
    text = extract.read_spans(spans)
    assert "科目名稱：臨床血液學與血庫" in text.replace("\n", "")
    for piece in ("科目名稱", "臨床血液學與血庫"):
        assert piece in text, "the redrawn header must survive; got %r" % text


# ------------------------------------------- which size is the body, and raised letters
#
# Three defects a reviewer reported as "flat English sub/superscripts, often", traced to one
# paper's question 55 and then to the two rules below. They are pinned against the real geometry
# of `1081_藥師(一)_藥劑學與生物藥劑學.pdf`, page 8, so the numbers here are the paper's own.


def test_the_body_is_the_widest_size_not_the_largest():
    """內文字級是「墨跡最寬」的那一級，不是「最大」的那一級。

    紙本把題號 `55.` 與選項標記印得**比內文大**（12.96pt 對 11.04pt）。用最大字級當 body，
    等於拿題號當基準：整行內文在它面前都顯得「比較小」，於是內文自己被當成 offset 候選。
    實測後果：`digoxin 100 mg` 這類整段內文 dcy 來到 -1.98，越過 -1.9 的門檻 ——
    一整片散文被讀成上標。

    真正的判準是寬度：body 是「承載最多墨跡」的字級。
    """
    # 幾何取自 1081 藥師(一) 藥劑學 Q55 的真實座標（x0/x1 為實際值，y 依 top/bottom 比例縮放）
    spans = [
        _span("55.", x0=28.0, x1=46.1, top=770.54, bottom=783.50, size=12.96),
        _span("某藥物⼝服後可以⽤", x0=46.1, x1=145.1, top=768.43, bottom=782.28, size=11.04),
        _span("C", x0=145.1, x1=153.1, top=770.83, bottom=781.87, size=11.04),
        _span("p", x0=153.0, x1=158.1, top=776.76, bottom=785.88, size=9.12),
        _span("=Ｂe", x0=158.2, x1=181.7, top=770.83, bottom=781.87, size=11.04),
        _span("-kt", x0=181.7, x1=191.8, top=768.00, bottom=777.12, size=9.12),
    ]
    # 題號 12.96 只在 3 個字元上（寬 18.1pt），內文 11.04 承載 448pt 的墨跡 → body = 11.04
    assert extract._body_size(spans) == 11.04
    # 於是 C 的下標 p 與 e 的上標 -kt 各自被讀成上下標，而題號本身不是
    assert extract.read_spans(spans) == "55.某藥物⼝服後可以⽤Cₚ=Ｂe⁻ᵏᵗ"


def test_width_beats_a_character_count_when_subscripts_are_multi_digit():
    """用「字元數」當 body 會把化學式反過來：多位數下標會累積出比主體更多的字元。

    `C₇H₁₅SO₃⁻`：主體是 `C` `H` `SO`（4 個字元、28.9pt），下標是 `7` `15` `3`（5 個字元、21.1pt）。
    比字元數 → 下標贏 → body 取錯 → 7/15/3 不再是下標。比寬度 → 主體贏。
    """
    spans = [
        _span("A.", x0=45.4, x1=57.8, top=209.73, bottom=223.91, size=12.81),
        _span("C", x0=57.8, x1=65.0, top=215.18, bottom=227.18, size=10.83),
        _span("7", x0=65.0, x1=69.6, top=219.75, bottom=229.75, size=9.03),
        _span("H", x0=69.6, x1=77.4, top=215.18, bottom=227.18, size=10.83),
        _span("15", x0=77.4, x1=86.4, top=219.75, bottom=229.75, size=9.03),
        _span("SO", x0=86.4, x1=100.2, top=215.18, bottom=227.18, size=10.83),
        _span("3", x0=100.2, x1=104.8, top=219.75, bottom=229.75, size=9.03),
        _span("-", x0=104.8, x1=107.8, top=212.36, bottom=222.36, size=9.03),
    ]
    assert extract._body_size(spans) == 10.83
    assert extract.read_spans(spans) == "A.C₇H₁₅SO₃⁻"


def test_a_raised_latin_letter_becomes_a_superscript():
    """上標的拉丁字母要能轉。先前轉換表只有數字與符號，所以 `e⁻ᵏᵗ` 一律印成 `e-kt`。

    這是使用者回報的那一類：藥動學的 `Ｃₚ=Ｂe⁻ᵏᵗ－Ａe⁻ᵏᵃᵗ`、`kₐ`、`Cₘₐₓ`、`Vₚ`、`kₘ`，
    每年、每一科都出現。
    """
    assert extract._SUP_MAP["k"] == "ᵏ"
    assert extract._SUP_MAP["t"] == "ᵗ"
    assert extract._SUB_MAP["p"] == "ₚ"
    assert extract._SUB_MAP["m"] == "ₘ"
    # 沒有 Unicode 形式的字母不得硬轉（`c` 沒有下標形式）
    assert "c" not in extract._SUB_MAP


def test_a_subscript_latin_word_is_a_subscript():
    """`Rh_null` 的 `null` 在紙上真的是下標（比 `Rh` 低 5.15pt，渲染後目視可見）。

    `Rhₙᵤₗₗ` 是對的讀法，`Rhnull` 才是缺陷 —— 所以「整段英文單字」不等於假陽性。

    幾何取整行，因為 body 是「整行裡墨跡最寬的字級」：只放 `Rh` 與 `null` 兩個 span 的簡化
    測試會把 body 算成 `null`（12.0pt 寬 > `Rh` 的 11.0pt），那就不是紙上的情形了。
    第一版正是這樣寫的，然後失敗 —— 失敗的是測試，不是程式。
    """
    spans = [
        _span("C.", x0=40.6, x1=49.5, top=462.65, bottom=475.01, size=9.00),
        _span("Rh", x0=49.6, x1=61.0, top=460.73, bottom=473.09, size=9.00),
        _span("null", x0=61.1, x1=72.7, top=465.88, bottom=476.10, size=7.44),
        _span("者測不到", x0=72.7, x1=108.7, top=463.02, bottom=472.23, size=9.00),
        _span("LW", x0=108.7, x1=122.3, top=460.73, bottom=473.09, size=9.00),
        _span("抗原", x0=122.3, x1=140.3, top=463.02, bottom=472.23, size=9.00),
    ]
    assert extract._body_size(spans) == 9.00
    assert extract.read_spans(spans) == "C.Rhₙᵤₗₗ者測不到LW抗原"


def test_the_two_recorded_regressions_still_cannot_convert():
    """`41.` 與 `HbA1c` 這兩個已經記錄過的迴歸，不得因為新增字母而回來。

    兩者都靠同一條規則擋住：run 的**每一個**字元都要有「那個方向」的上下標形式。
    `.` 沒有上標形式，`c` 沒有下標形式。

    這裡特別檢查「方向」：`1c` 若允許跨表查（`1` 在下標表、`c` 在上標表）就會通過，
    而它是下標 span —— 那正是 `HbA₁c` 契約要防的半轉換。
    """
    assert extract._mappable_offset("41.", "sup") is False
    assert extract._mappable_offset("HbA", "sub") is False
    assert extract._mappable_offset("1c", "sub") is False     # 同一個 run，方向要一致
    assert extract._mappable_offset("2", "sub") is True
    assert extract._mappable_offset("3-", "sup") is True
    assert extract._mappable_offset("-", "sup") is True
    # 空白的 run 本身不算轉換（否則空白會變成自己的上標）
    assert extract._mappable_offset(" ", "sup") is False


def test_a_raised_run_containing_a_space_is_still_a_superscript():
    """`Ａe⁻ᵏᵃᵗ` 在 PDF 裡是**一個** span `-ka t`，中間有空白。

    空白沒有上下標形式，但它站在被抬高的 run **裡面**，所以應該跟著一起抬高，
    而不是讓整段退回平的。實測全掃描：允許空白後只多出這一個 run。
    """
    assert extract._mappable_offset("-ka t", "sup") is True
    assert extract._transliterate("-ka t", "sup") == "⁻ᵏᵃ ᵗ"


def test_a_whitespace_only_span_does_not_move_the_baseline():
    """沒有墨跡的 span 不參與基線的計算。

    這一條是「修好一層不等於修好管線」的實例。上面的 body 修正讓 `Leᵇ` 讀對了，但
    `1041_醫事檢驗師_臨床血液學與血庫學` 的 `anti-Leᵃ` 仍然讀成 `anti-Lea` —— 而**兩個引擎
    因此變得不一致**，所以它必須修，不只是好看。

    原因不是門檻，是結構：那一行有一個寬 2.5pt 的空白 span 在 `x=420.4`、`y=35.69`，
    而題目內文在 `y≈48`。空白沒有墨跡，沒有墨跡就沒有基線；它是頁面殘留，不該代表這一行。
    把它算進 body 中心的平均，中心被抬高約 1pt，而上下標的門檻只有 ~1.9pt ——
    `a` 的位移從真實的 -1.99 被拉成 -0.99，剛好掉出門檻。

    同一行在 `extract_cells_a` 裡切成 cell，那個遠方空白自成一個 cell，所以 cells 那條
    路徑讀得到 `ᵃ`。兩條路徑必須說同一句話，這就是本測試的幾何。
    """
    spans = [
        _span("D.", x0=40.6, x1=49.5, top=42.77, bottom=55.13, size=9.00),
        _span("新生兒體內血清同時測得 ", x0=49.6, x1=150.9, top=45.90, bottom=55.11, size=9.00),
        _span("anti-Le", x0=151.0, x1=178.5, top=43.61, bottom=55.97, size=9.00),
        _span("a", x0=178.4, x1=182.6, top=41.80, bottom=52.02, size=7.44),
        _span("之可能性極高", x0=182.6, x1=236.6, top=45.90, bottom=55.11, size=9.00),
        # 遠方的頁面殘留：只有空白，卻帶著 body 的字級
        _span(" ", x0=420.4, x1=422.9, top=35.69, bottom=48.05, size=9.00),
    ]
    assert extract.read_spans(spans) == "D.新生兒體內血清同時測得 anti-Leᵃ之可能性極高  "


def test_the_two_paths_agree_about_the_same_glyph():
    """同一行的 cell 檢視與 line 檢視，不得對同一個字符說不同的話。

    這兩個函式共用 `read_spans`，差別只在 spans 的集合大小。若集合大小會改變一個字符
    是不是上標，則「骨架 vs 幾何」的比對就會把同一段文字報成不一致 —— 而那個不一致是
    管線自己製造的，不是紙張的性質。
    """
    line = [
        _span("D.", x0=40.6, x1=49.5, top=42.77, bottom=55.13, size=9.00),
        _span("新生兒體內血清同時測得 ", x0=49.6, x1=150.9, top=45.90, bottom=55.11, size=9.00),
        _span("anti-Le", x0=151.0, x1=178.5, top=43.61, bottom=55.97, size=9.00),
        _span("a", x0=178.4, x1=182.6, top=41.80, bottom=52.02, size=7.44),
        _span("之可能性極高", x0=182.6, x1=236.6, top=45.90, bottom=55.11, size=9.00),
        _span(" ", x0=420.4, x1=422.9, top=35.69, bottom=48.05, size=9.00),
    ]
    whole = extract.read_spans(line)
    cells = "".join(extract.read_spans(cell) for cell in extract.group_cells(line))
    assert "Leᵃ" in whole and "Leᵃ" in cells


def test_an_empty_line_reads_as_an_empty_string():
    assert extract.read_spans([]) == ""


# ------------------------------------------------------- joining lines of Chinese text

def test_two_chinese_lines_join_without_a_space():
    """The paper wraps mid-sentence; a space there is an invented gap."""
    assert repair.join_lines("所產生的中毒現", "象。患者血液") == "所產生的中毒現象。患者血液"


def test_a_latin_word_keeps_its_space_before_chinese():
    assert repair.join_lines("anion gap", "上升之代謝性酸中毒") == "anion gap 上升之代謝性酸中毒"


def test_fullwidth_letters_are_cells_not_chinese():
    """`答案Ｄ`, `Ｂ`, `Ａ` are grid cells; gluing them destroys the answer table."""
    assert repair.join_lines("答案Ｄ", "Ｂ") == "答案Ｄ Ｂ"
    assert repair.join_lines("Ｂ", "Ａ") == "Ｂ Ａ"


def test_fullwidth_digits_are_cells_not_chinese():
    assert repair.join_lines("41", "42") == "41 42"


def test_chinese_next_to_a_fullwidth_punctuation_still_joins():
    assert repair.join_lines("下列何者", "？") == "下列何者？"


# ------------------------------------------------------- measured on the real paper

@requires_paper
def test_the_ion_formulas_survive_extraction():
    """Question 45's four options are ion formulas; each must come out whole.

    Checked on the assembled lines, which is where the visual-line grouping acts. Whether
    they then land in the *option* rather than the stem is the segmenter's job, and is
    asserted separately against the parsed items.
    """
    text = repair.text_from_rows(extract.extract_lines_a(PAPER))
    for formula in ("[Na⁺]＋[K⁺]－[Cl⁻]", "[K⁺]＋[Ca²⁺]－[Cl⁻]",
                    "[K⁺]－[PO₄³⁻]－[HCO₃⁻]", "[Na⁺]－[Cl⁻]－[HCO₃⁻]"):
        assert formula in text, formula


@requires_paper
def test_no_assembled_line_has_a_bare_unclosed_bracket():
    """The defect truncated options to `[Na` and `[K`; a dangling bracket is the signature."""
    text = repair.text_from_rows(extract.extract_lines_a(PAPER))
    for line in text.splitlines():
        assert line.count("[") == line.count("]"), line


@requires_paper
def test_a_wrapped_chinese_sentence_has_no_space_at_the_wrap():
    """The wrap is joined when the stem is assembled, not when the rows are read.

    Question 46's stem wraps after `中毒現`; the paper has no space there. `text_from_rows`
    leaves the two lines separate - they are genuinely different baselines - and the stem is
    joined later, by the same `join_lines` rule. Asserting this on the row text would be
    asserting it in a place the fix does not operate.
    """
    import three_way
    parsed = three_way.analyse_items(PAPER)
    stem = next(item["stem"] for item in parsed["items"] if int(item["number"]) == 46)
    assert "中毒現象" in stem
    assert "中毒現 象" not in stem


@requires_paper
def test_the_ion_formulas_reach_the_options():
    """Question 45's options, as the parser finally reports them."""
    import three_way
    parsed = three_way.analyse_items(PAPER)
    item = next(item for item in parsed["items"] if int(item["number"]) == 45)
    options = list(item["options"].values()) if isinstance(item["options"], dict) else list(item["options"])
    joined = "\n".join(options)
    assert "[Na⁺]＋[K⁺]－[Cl⁻]" in joined
    assert "[K⁺]－[PO₄³⁻]－[HCO₃⁻]" in joined


# ------------------------------------------------------- the answer sheet needs the other engine

@requires_paper
@pytest.mark.skipif(not os.path.isfile(ANSWER_SHEET), reason="answer sheet not on disk")
def test_the_answer_sheet_is_read_by_the_engine_that_keeps_the_grid():
    """The answer sheet is a table, and only one of the two engines reports its cells.

    PyMuPDF reads the whole row as one string - `答案ＢＤＣＣＢ...` - leaving nothing to split
    on, and `parse_answer_table` then recovers four answers instead of eighty. Poppler reads
    each cell as its own word with the ten points of gap intact, and all eighty come back.
    """
    from qbr import canon
    text = repair.text_from_rows(extract.extract_lines_b(ANSWER_SHEET))
    table = canon.parse_answer_table(text)
    assert len(table) == 80, f"poppler should recover every answer, got {len(table)}"
    assert table[1] == ("B",), "the first answer on the sheet is B"
    assert table[9] == ("D",)


@requires_run
def test_the_built_questions_carry_the_repaired_text():
    import json
    with open(candidates_jsonl, encoding="utf-8") as handle:
        rows = {json.loads(line)["question_number"]: json.loads(line) for line in handle if line.strip()}
    assert rows[45]["options"][0]["text"] == "[Na⁺]＋[K⁺]－[Cl⁻]"
    assert rows[45]["options"][2]["text"] == "[K⁺]－[PO₄³⁻]－[HCO₃⁻]"
    assert "中毒現象" in rows[46]["stem"]
    assert "中毒現 象" not in rows[46]["stem"]


# --- a question follows its predecessor by one ---------------------------------------------
# The numbering is the paper's, and it is consecutive. This is the third rule about numbering and
# the only one about distance: measured over four categories, 32,721 adjacent gaps between accepted
# question numbers, and every one of them is exactly 1.

def _segment(text):
    from qbr import repair
    records, _residual, _diag = repair.segment_best(text)
    return records


def test_a_wrapped_value_is_not_read_as_a_far_away_question():
    """`234.8）` is the wrap of `（碘化銀分子量為`, not question 234.

    Measured on `1142_藥師(一)_藥學(二)(包括藥物分析與生藥學(含中藥學))`: the spurious 234 closed
    question 3 before its options and the paper was read as 4 questions out of 80. The same defect
    read `1091_醫事檢驗師_臨床血液學與血庫學` as 24 out of 80 on `90.1 fL、reticulocyte 0.7`.
    """
    text = "\n".join([
        "1.第一題", "A.甲", "B.乙", "C.丙", "D.丁",
        "2.第二題", "A.甲", "B.乙", "C.丙", "D.丁",
        "3.碘化銀飽和溶液於25℃時的濃度為1.23×10⁻⁸ 莫耳／升，則其溶解度積（Ksp）為何？（碘化銀分子量為",
        "234.8）",
        "A.1.11×10⁻⁴", "B.1.23×10⁻⁸", "C.2.46×10⁻⁸", "D.1.5×10⁻¹⁶",
        "4.第四題", "A.甲", "B.乙", "C.丙", "D.丁",
    ])
    records = _segment(text)
    numbers = [record["number"] for record in records]
    assert 234 not in numbers, numbers
    assert numbers == [1, 2, 3, 4], numbers
    # And the wrapped value is kept in the question it belongs to, where a reader expects it.
    assert "234.8）" in " ".join(records[2]["lines"])


def test_a_question_whose_stem_opens_with_a_decimal_is_still_a_question():
    """`2.56+100.2731+...` is question 2; the rule is about distance, not about punctuation."""
    text = "\n".join([
        "1.第一題", "A.甲", "B.乙", "C.丙", "D.丁",
        "2.56+100.2731+4.3-20.005=87.1281，若考慮有效數字，答案應為幾位？",
        "A.3", "B.4", "C.5", "D.6",
        "3.第三題", "A.甲", "B.乙", "C.丙", "D.丁",
    ])
    numbers = [record["number"] for record in _segment(text)]
    assert numbers == [1, 2, 3], numbers


def test_a_number_that_is_not_the_successor_is_a_value():
    """The numbering is consecutive, so a number that is not the next one is a value.

    Measured over four categories: every one of the 32,721 adjacent gaps between accepted question
    numbers is exactly 1. There is no paper in the corpus with a gap in its numbering, which is why
    the rule is "the successor" rather than a distance threshold - a threshold wide enough to allow
    a real gap lets `4.8%` through as question 4, and `4.8%` is the case that cost
    `1101_醫事檢驗師_臨床血液學與血庫學` question 2's four options.
    """
    text = "\n".join([
        "1.第一題", "A.甲", "B.乙", "C.丙", "D.丁",
        "2.第二題的數據：Hb A₂",
        "4.8%。以鐵劑治療三星期無改善，則最有可能罹患下列何種疾病？",
        "A.甲", "B.乙", "C.丙", "D.丁",
        "3.第三題", "A.甲", "B.乙", "C.丙", "D.丁",
    ])
    records = _segment(text)
    numbers = [record["number"] for record in records]
    assert numbers == [1, 2, 3], numbers
    # The value stays in question 2, and question 2 keeps its four options.
    assert "4.8%。" in " ".join(records[1]["lines"])
    assert sorted(records[1]["options"]) == ["A", "B", "C", "D"]


def test_option_mark_alone_on_its_line_takes_the_next_line_as_its_body():
    """選項標記自己一行時，下一行是它的內容，不是題幹的尾巴。

    量測 `1152_藥師(一)_藥學(一)` Q53：文字層是 `A.` / `alfuzosin` / `B.` / `doxazosin` / …，
    四個藥名被塞進題幹，四個選項全部變成空字串。
    """
    from qbr import repair
    lines = [x for n in range(1, 53)
             for x in ("%d.第%d題的題幹文字？" % (n, n), "A.甲", "B.乙", "C.丙", "D.丁")]
    lines += ["53.下列quinazoline 類α₁-adrenergic antagonists，何者之親脂性最高？",
              "A.", "alfuzosin", "B.", "doxazosin", "C.", "prazosin", "D.", "terazosin"]
    lines += [x for n in range(54, 81)
              for x in ("%d.第%d題的題幹文字？" % (n, n), "A.甲", "B.乙", "C.丙", "D.丁")]
    records, _residual, _diag = repair.segment_mixed("\n".join(lines))
    item = [r for r in records if r.get("number") == 53][0]
    assert item["options"] == {"A": "alfuzosin", "B": "doxazosin",
                               "C": "prazosin", "D": "terazosin"}
    assert "alfuzosin" not in item["stem"], "藥名不該留在題幹"


def test_a_wrapped_option_keeps_its_continuation():
    """選項文字在換行處被切斷時，續行屬於該選項，不是題幹的尾巴。

    量測 `1002_物理治療師_骨科疾病物理治療學` Q10：紙本印的是
    `A.…導致步態` / `異常` / `B.…`，續行 `異常` 被送進題幹，出貨的選項 A 停在
    `導致步態`，而題幹變成 `…何者正確？異常`。這是本管線最大的一類未標記缺陷。

    這一條在修復前必須失敗（charter 的負向對照）：把 `_continues_an_option` 拿掉，
    續行會回到題幹，這一條就會紅。
    """
    from qbr import repair
    lines = [x for n in range(1, 4)
             for x in ("%d.第%d題的題幹文字夠長了嗎？" % (n, n), "A.甲", "B.乙", "C.丙", "D.丁")]
    lines += ["4.下列有關薦髂關節病變的敘述，何者正確？",
              "A.薦髂關節疼痛可能對臀中肌造成反射性抑制，導致步態",
              "異常",
              "B.當發現左邊的前上腸骨棘均較右邊的位置偏高",
              "C.發生在恥骨聯合的問題不會影響到薦髂關節",
              "D.此關節有多條肌肉經過，所以常發生病變"]
    lines += [x for n in range(5, 81)
              for x in ("%d.第%d題的題幹文字夠長了嗎？" % (n, n), "A.甲", "B.乙", "C.丙", "D.丁")]
    records, _residual, _diag = repair.segment_mixed("\n".join(lines))
    q4 = [r for r in records if r.get("number") == 4][0]
    assert q4["options"]["A"] == "薦髂關節疼痛可能對臀中肌造成反射性抑制，導致步態異常", \
        "選項 A 的續行必須留在 A"
    assert "異常" not in "".join(q4["stem"]), "續行不該被塞進題幹"
    assert q4["options"]["B"] == "當發現左邊的前上腸骨棘均較右邊的位置偏高"


def test_a_line_opening_a_question_is_still_a_question():
    """選項之後的數字錨點仍然是下一題，不可以被當成上一個選項的續行。

    這是續行規則最容易過度觸發的地方：紙本上「選項的續行」與「下一題的題幹」都是
    沒有標記的文字列。分辨它們靠的是數字錨點，不是語意。
    """
    from qbr import repair
    lines = [x for n in range(1, 80)
             for x in ("%d.第%d題的題幹文字夠長了嗎？" % (n, n), "A.甲", "B.乙", "C.丙", "D.丁")]
    records, _residual, _diag = repair.segment_mixed("\n".join(lines))
    assert len(records) >= 79, "每一題都必須在，沒有被前一個選項吃掉"


def test_a_page_footer_after_an_option_belongs_to_no_option():
    """選項之後若出現頁尾／節標題，它不屬於任何選項。

    樣本裡沒有這種列（40 卷中 169 條續行候選、29 條短碎片、0 條頁尾），所以這道護欄
    是為全集而設，不是為了樣本。頁尾用的是真的會出現在紙上的形狀（`第 3 頁`）。
    """
    from qbr import repair
    lines = [x for n in range(1, 4)
             for x in ("%d.第%d題的題幹文字夠長了嗎？" % (n, n), "A.甲", "B.乙", "C.丙", "D.丁")]
    lines += ["4.下列關於骨盆的敘述，何者正確？",
              "A.薦髂關節疼痛可能對臀中肌造成反射性抑制",
              "B.當發現左邊的前上腸骨棘均較右邊的位置偏高",
              "第 2 頁",
              "C.發生在恥骨聯合的問題不會影響到薦髂關節",
              "D.此關節有多條肌肉經過，所以常發生病變"]
    lines += [x for n in range(5, 81)
              for x in ("%d.第%d題的題幹文字夠長了嗎？" % (n, n), "A.甲", "B.乙", "C.丙", "D.丁")]
    records, _residual, _diag = repair.segment_mixed("\n".join(lines))
    q4 = [r for r in records if r.get("number") == 4][0]
    assert q4["options"]["B"] == "當發現左邊的前上腸骨棘均較右邊的位置偏高", \
        "頁尾不該被黏到 B 的尾巴"
    assert q4["options"]["C"] == "發生在恥骨聯合的問題不會影響到薦髂關節"


def test_a_row_of_bare_option_marks_does_not_swallow_the_stem():
    """一排只有標記的列（沒有內容行）不可以吃掉後面的題幹。"""
    from qbr import repair
    lines = [x for n in range(1, 12)
             for x in ("%d.第%d題的題幹文字？" % (n, n), "A.甲", "B.乙", "C.丙", "D.丁")]
    lines += ["12.下列何者正確？", "A.", "B.", "C.", "D.",
              "13.下一題的題幹文字在這裡？", "A.甲", "B.乙", "C.丙", "D.丁"]
    lines += [x for n in range(14, 81)
              for x in ("%d.第%d題的題幹文字？" % (n, n), "A.甲", "B.乙", "C.丙", "D.丁")]
    records, _residual, _diag = repair.segment_mixed("\n".join(lines))
    q13 = [r for r in records if r.get("number") == 13]
    assert q13, "第 13 題必須仍然存在"
    assert "下一題的題幹文字在這裡" in "".join(q13[0]["stem"])


def test_both_subscript_styles_are_read_as_subscripts():
    """兩種下標風格都要抓到，因為紙本兩種都用。

    (a) 緊的：5.5pt 對 11pt（`[PO₄³⁻]`），比例 0.50，位移小但比例遠低於母體。
    (b) 鬆的：7.4pt 對 9pt（`CH₃(CH₂)₁₁OR`），比例 0.82，比例不夠低、要靠位移認出來。

    單一門檻一定失掉其中一種：0.75/±1.0 把 (b) 壓平，0.90/±2.0 把 (a) 壓平。
    所以判準是兩者的聯集，各自帶自己量到的門檻。
    """
    # (a) 緊的風格
    spans = [_span("]－[PO", x0=64.0, x1=97.1, top=633.79, bottom=644.81),
             _span("4", x0=97.1, x1=99.8, top=638.20, bottom=643.71, size=5.5),
             _span("3-", x0=99.7, x1=105.2, top=632.67, bottom=638.18, size=5.5),
             _span("]", x0=105.2, x1=107.0, top=633.79, bottom=644.81)]
    assert extract.read_spans(spans) == "]－[PO₄³⁻]"
    # (b) 鬆的風格：7.4pt 對 9pt（幾何取自 `1032_藥師(一)_藥劑學` 的真實座標）
    spans = [_span("CH", x0=65.3, x1=78.3, top=726.04, bottom=738.41, size=9.0),
             _span("3", x0=78.4, x1=82.5, top=731.20, bottom=741.42, size=7.44)]
    assert extract.read_spans(spans) == "CH₃"


def test_smaller_text_that_is_not_a_formula_is_left_alone():
    """小字不等於下標：選項標記與小一號的英文字都要保持原樣。"""
    spans = [_span("有關藥品有效期限之決定，下列", x0=39.2, x1=160.0,
                   top=700.0, bottom=711.0, size=12.8),
             _span("antibiotics", x0=160.0, x1=210.0, top=700.4, bottom=710.6, size=10.8)]
    assert extract.read_spans(spans) == "有關藥品有效期限之決定，下列antibiotics"
