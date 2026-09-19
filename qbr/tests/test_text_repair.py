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

from qbr import extract, repair  # noqa: E402

PAPER = ("/Users/tim/AI workspace/ai_learning_platform/tw-national-exam-catalog/國考題資料夾"
         "/10_official_pdf/by_official_catalog/醫事檢驗師/115/第2次"
         "/1152_醫事檢驗師_生物化學與臨床生化學.pdf")
ANSWER_SHEET = PAPER.replace(".pdf", "_ANS.pdf")
candidates_jsonl = os.path.join("/tmp/qbr-golden-001", "review-ui", "candidates.jsonl")
requires_paper = pytest.mark.skipif(not os.path.isfile(PAPER), reason="official paper not on disk")
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
