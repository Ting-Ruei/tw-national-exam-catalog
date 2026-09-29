# -*- coding: utf-8 -*-
"""The admissibility check is the thing that makes one prompt enough, so it is tested directly.

Every test here is about a property of a *repartition* and knows nothing about the subject of a
paper, which is exactly the claim being made: a fabricated character fails whatever the question
was about, and a dropped cell fails whatever the question was about. The tests are written as
the two ways a model reading can be wrong - it made a character up, or it lost a cell - plus the
two ways a reading could be wrong that are not the model's fault (it named a cell that does not
exist, or it emitted a token this format cannot read).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qbr import canon, vision, reflow  # noqa: E402


TABLE = [
    (1, "115 年第二次考試", ""),
    (2, "[number] 1", "number"),
    (3, "下列何者為酯解酶？", ""),
    (4, "[opt:A] 脂肪酶", "opt:A"),
    (5, "[opt:B] 澱粉酶", "opt:B"),
    (6, "[opt:C] 轉胺酶", "opt:C"),
    (7, "[opt:D] 乳酸脫氫酶", "opt:D"),
    (8, "頁次：1－1", ""),
]
# The table is stored without the hint text inside the cell; the hint is a separate field. The
# fixtures above carry the rendered form for readability, so they are normalised here.
TABLE = [(line_id, text.split("] ", 1)[1] if "] " in text else text, hint)
         for line_id, text, hint in TABLE]


def reading(**overrides):
    base = {
        "questions": [{"number": 1, "stem": [3],
                       "options": {"A": [4], "B": [5], "C": [6], "D": [7]}}],
        "chrome": [1, 2, 8],
    }
    base.update(overrides)
    return base


def test_a_faithful_repartition_is_admissible():
    ok, report = reflow.verify(reading(), TABLE)
    assert ok, report
    assert report["assigned"] == len(TABLE)
    assert not report["invented_characters"]
    assert not report["unassigned_count"]


def test_a_fabricated_character_is_refused():
    """The model writing a word it was not given is the failure this check exists for."""
    broken = reading()
    broken["perm"] = {"3": "下列何者為脂肪酶？"}         # 酯解 -> 脂肪, characters never supplied
    ok, report = reflow.verify(broken, TABLE)
    assert not ok
    assert report["invented_characters"] == {3: "肪脂"}
    assert report["dropped_characters"] == {3: "解酯"}


def test_a_fabricated_character_in_a_top_level_perm_is_refused():
    """The `perm` map is addressed by cell id, so it has to be checked on its own.

    This is the hole the first version of the check had: perms were only inspected when they
    were attached to a slot, so the documented `perm` field could have carried a fabricated
    character past the guardrail. Measured: a perm of `下列何者為水解酶？` for a cell printed
    `下列何者為酯解酶？` was reported admissible.
    """
    broken = reading()
    broken["questions"][0]["stem"] = [3]
    broken["perm"] = {"3": "下列何者為水解酶？"}
    ok, report = reflow.verify(broken, TABLE)
    assert not ok
    assert report["invented_characters"] == {3: "水"}


def test_a_perm_for_a_cell_that_does_not_exist_is_refused():
    broken = reading()
    broken["perm"] = {"99": "任何字"}
    ok, report = reflow.verify(broken, TABLE)
    assert not ok
    assert report["unknown_lines"] == [("perm[99]", 99)]


def test_a_top_level_perm_is_actually_used_when_composing():
    """The check and the composer must agree on what a perm means, or one of them is fiction."""
    broken = reading()
    broken["perm"] = {"3": "者何為下列酯解酶？"}
    ok, _ = reflow.verify(broken, TABLE)
    assert ok
    items = reflow.to_items(broken, TABLE)
    assert items[0]["stem"] == "者何為下列酯解酶？"


def test_reordering_a_cell_is_allowed():
    """Moving a cell's own characters is the one edit the model needs, and it must pass."""
    broken = reading()
    broken["perm"] = {"3": "者何為下列酯解酶？"}         # same characters, different order
    ok, report = reflow.verify(broken, TABLE)
    assert ok, report


def test_dropping_whitespace_is_allowed():
    """Reflow is partly about spacing, so spacing is not what the check compares."""
    broken = reading()
    broken["perm"] = {"1": "115年第二次考試"}
    ok, report = reflow.verify(broken, TABLE)
    assert ok, report


def test_losing_a_cell_is_refused():
    """A cell nobody claimed may be a question that was dropped, so it is not ignored."""
    broken = reading()
    broken.pop("chrome")
    ok, report = reflow.verify(broken, TABLE)
    assert not ok
    assert report["unassigned_count"] > 0


def test_claiming_a_cell_twice_is_refused():
    """One cell cannot be both an option and part of the stem."""
    broken = reading()
    broken["questions"][0]["stem"] = [3, 4]
    ok, report = reflow.verify(broken, TABLE)
    assert not ok
    assert report["duplicated_lines"] == {4: 2}


def test_a_cell_that_does_not_exist_is_refused():
    """A line number outside the table is not a reading of this paper."""
    broken = reading()
    broken["chrome"] = [1, 2, 8, 99]
    ok, report = reflow.verify(broken, TABLE)
    assert not ok
    assert report["unknown_lines"] == [("chrome[3]", 99)]


def test_a_reading_with_no_questions_is_refused():
    ok, report = reflow.verify(reading(questions=[]), TABLE)
    assert not ok


def test_hints_are_measured_not_assumed():
    """The option hint comes from the paper's own marker codepoints, so it is a measurement."""
    rows = [{"text": "\ue18c脂肪酶", "size": 11.0}, {"text": "\ue18d澱粉酶", "size": 11.0},
            {"text": "7", "size": 11.0}, {"text": "小的字", "size": 7.0}]
    table = reflow.line_table(rows, body_size=11.0, alphabet=(0xE18C, 0xE18D))
    hints = [hint for _, _, hint in table]
    assert hints == ["opt:A", "opt:B", "number", "small"]


def test_cells_split_a_row_that_carries_two_options():
    from qbr import extract
    spans = [
        {"text": "缺鐵性貧血", "size": 11.0, "bbox": (0, 0, 60, 11)},
        {"text": "地中海貧血", "size": 11.0, "bbox": (200, 0, 260, 11)},
    ]
    cells = extract.group_cells(spans)
    assert len(cells) == 2
    assert extract.read_spans(cells[0]) == "缺鐵性貧血"
    assert extract.read_spans(cells[1]) == "地中海貧血"


def test_a_superscript_stays_with_its_host_cell():
    """The gap test must not split `[Na⁺]`, or the marker the model needs is destroyed."""
    from qbr import extract
    spans = [
        {"text": "PO", "size": 11.0, "bbox": (0, 0, 20, 11)},
        {"text": "4", "size": 7.0, "bbox": (20, 5, 24, 12)},
        {"text": "3-", "size": 7.0, "bbox": (24, 5, 32, 12)},
    ]
    cells = extract.group_cells(spans)
    assert len(cells) == 1
    # The reading may render the offset run as subscripts (`PO₄₃₋`); what matters here is that
    # the three spans were not split apart, because a marker the model needs cannot be joined
    # back together once the geometry that joined it has been thrown away.
    assert extract.read_spans(cells[0]).startswith("PO")


def test_an_offset_run_the_table_cannot_spell_is_reported_instead_of_lost():
    """Geometry says superscript; Unicode has no superscript period; the run must not vanish.

    This is the measured cause of the flattened formulas a reviewer kept blocking. `read_spans`
    folds a raised run back into its host line - correct, since the formula must read as one line -
    and that fold is exactly what destroys the evidence. So the run is recorded while the spans
    still exist.
    """
    from qbr import extract
    spans = [
        {"text": "C=80e", "size": 11.0, "bbox": (0, 0, 40, 11)},
        {"text": "-0.35t", "size": 7.0, "bbox": (40, 1, 60, 8)},
    ]
    found = extract.refused_offsets(spans)
    assert [item["text"] for item in found] == ["-0.35t"]
    assert found[0]["kind"] == "sup" and found[0]["blockers"] == ["."]
    # And the reading really did drop the offset: that is why the evidence has to be kept.
    assert "⁻" not in extract.read_spans(spans)


def test_small_print_at_a_low_baseline_is_not_a_flattened_offset():
    """The negative control: `dextrose`, `P`, `D`, `M`, `β` must not be reported.

    These are geometrically lowered - sitting in a small face a little below the baseline - and the
    offset table also refuses them. But they are table entries and single-letter abbreviations, not
    formulas, and calling them subscripts would be inventing an offset. Measured on the same paper
    as the true class: six genuine flattened formulas, five of these.
    """
    from qbr import extract
    spans = [
        {"text": "glucose", "size": 11.0, "bbox": (0, 0, 40, 11)},
        {"text": "dextrose", "size": 7.0, "bbox": (40, 8, 70, 15)},
        {"text": "P", "size": 7.0, "bbox": (70, 8, 75, 15)},
        {"text": "M", "size": 7.0, "bbox": (75, 8, 80, 15)},
    ]
    assert extract.refused_offsets(spans) == []


def test_the_flattened_formula_predicate_has_exactly_one_implementation():
    """「這個 run 是不是壓平的公式」只能有一個答案。

    這個判準被拆出来（`_flattened_offset`）而不是寫在 `refused_offsets` 裡面，因為第二個用它
    的地方一定會出現：佇列現在**報告**這個 run，而修法是在**閱讀層拼出 markup**（紙張把
    `-0.17t` 抬高，只是 Unicode 沒有上標句點；markup 有）。兩份「看起來像公式」的判準會
    漂移，然後就會出現同一種缺陷的兩種說法——量到的實例就是 204 題顯示了一個已經被修掉的爭議。

    負對照：把判定搬回 `refused_offsets` 自己寫（只把 `_flattened_offset` 當空殼），這條測試
    就要紅。这里直接比較兩者對同一組 span 的答案。
    """
    from qbr import extract
    spans = [
        {"text": "C=80e", "size": 11.0, "bbox": (0, 0, 40, 11)},
        {"text": "-0.35t", "size": 7.0, "bbox": (40, 1, 60, 8)},
    ]
    body_size = extract._body_size(spans)
    body = [s for s in spans if s["size"] >= body_size * extract._BODY_SIZE_RATIO]
    centre = extract._body_centre(body)
    direct = extract._flattened_offset(spans, 1, body_centre=centre, body_size=body_size)
    reported = extract.refused_offsets(spans)
    assert direct is not None and len(reported) == 1
    assert direct == reported[0]
    # 負對照：不是公式就不可以通過（小字、低基線的表格詞）。
    words = [{"text": "glucose", "size": 11.0, "bbox": (0, 0, 40, 11)},
             {"text": "dextrose", "size": 7.0, "bbox": (40, 8, 70, 15)}]
    centre = extract._body_centre([words[0]])
    assert extract._flattened_offset(words, 1, body_centre=centre,
                                     body_size=11.0) is None
    assert extract.refused_offsets(words) == []


def test_the_baseline_is_measured_from_the_dominant_size_not_the_offset_runs():
    """The negative control for a defect that hid a whole class: the average moved the baseline.

    `_body_centre` is handed every span at or above 0.75x the body size, so that a run well below
    the body is still there to be measured against. An offset run is only a *little* smaller than
    its host - 9.03pt against 10.83pt is 0.83, above the 0.75 floor - so the offsets were being
    averaged into the baseline they are supposed to be measured from. The old behaviour is the
    assertion below: with the offset spans included the centre sits at 447.65, a genuinely raised
    run measures only -1.61 against the -1.9 threshold, and the paper reads flat.

    This is the shape the queue reported as `flat-offset` (`cm-1` left flat) and it was an
    extraction defect that no repair rule could have fixed, because the text on the page was right
    and the reading was wrong. Measured on `1141_醫事放射師_醫學物理學與輻射安全` Q14: readable offset
    characters go 30 -> 47 with the baseline taken from the dominant size, and over 30 sampled
    papers 161 -> 176, with no run newly refused.
    """
    from qbr import extract
    # The real geometry of Q14 option A, all seven spans as PyMuPDF reports them: the `A.` marker
    # at 12.81pt (y-centre 445.65), the prose `0.5 R m` / ` Ci` / ` h` at 10.83pt (y-centre
    # 449.93), and the raised `2` / `-1 ` / `-1` at 9.03pt (y-centre 446.04) - a raise of 3.89
    # points, plainly a superscript.
    spans = [
        {"text": "A.", "size": 12.81, "bbox": (0, 439.2, 12, 452.0)},
        {"text": "0.5 R m", "size": 10.83, "bbox": (12, 444.5, 52, 455.4)},
        {"text": "2", "size": 9.03, "bbox": (52, 441.5, 57, 450.6)},
        {"text": " Ci", "size": 10.83, "bbox": (57, 444.5, 72, 455.4)},
        {"text": "-1 ", "size": 9.03, "bbox": (72, 441.5, 82, 450.6)},
        {"text": "h", "size": 10.83, "bbox": (82, 444.5, 88, 455.4)},
        {"text": "-1", "size": 9.03, "bbox": (88, 441.5, 98, 450.6)},
    ]
    # The negative control: the *old* behaviour was a plain mean over every span at or above the
    # floor, which averaged the raised runs into the baseline and moved it to 447.66, so the same
    # run measured only -1.61 and stayed flat. Recomputed here rather than called, because the
    # function has since been fixed - a negative control that called it would pass either way.
    old_body = [span for span in spans if span["size"] >= 10.83 * 0.75]
    old_centre = sum(extract._span_centre(span) for span in old_body) / len(old_body)
    old_shift = extract._span_centre(spans[4]) - old_centre
    assert old_shift > -1.9, "the old average must be the failing case"
    # And the reading the page justifies: `Ci-1 h-1` is the superscript the paper printed.
    assert extract.read_spans(spans) == "A.0.5 R m\u00b2 Ci\u207b\u00b9 h\u207b\u00b9"


# --- the paper states its own structure -----------------------------------------------------
# The skeleton reads only what the paper prints. These tests fix the two ways it was wrong before
# the corpus was measured, because both were silent: the first made an entire year unreadable and
# the second made options come back in the wrong order.

def _table(pairs):
    return [(index, text, hint) for index, (text, hint) in enumerate(pairs, start=1)]


def _table_with_geometry(rows):
    """A table carrying the geometry `cells_with_pages` attaches: `(id, text, hint, page, x0)`.

    One cell per line unless two share an `x0` and are adjacent, which is what "on the same line"
    means to the rules that read geometry. Each row is given its own vertical band so a table built
    here behaves like a printed one: `leftmost` asks whether anything on the *same line* starts
    further left, and giving every row the same band would make every cell share a line with every
    other.
    """
    table = reflow.CellTable()
    for index, (line_id, text, hint, page, x0) in enumerate(rows):
        top = index * 20.0
        table.append((line_id, text, hint or ""))
        table.geometry[line_id] = (page, float(x0), top, top + 10.0)
    return table


def test_a_question_number_may_be_a_cell_of_its_own():
    """Older papers set the number alone; newer ones set `1.題幹` as one cell. Both are printed.

    The number is part of the stem's cells because the skeleton says which cells a question owns,
    not which parts of its text are content; stripping the number is the composer's job, and both
    the number-only paper and the `1.題幹` paper produce the same text once it is done.
    """
    table = _table([("1", "number"), ("下列何者正確？", ""),
                    ("A.甲", "opt:A"), ("B.乙", "opt:B"), ("C.丙", "opt:C"), ("D.丁", "opt:D")])
    skeleton = reflow.skeleton(table)
    assert skeleton["complete"], skeleton["missing"]
    assert skeleton["questions"][1]["stem"] == [1, 2]


def test_an_option_may_be_printed_side_by_side_on_one_row():
    """`A.α-胰島細胞` and `B.β-胰島細胞` on one row are two options, not one."""
    table = _table([("1.題幹", "number"), ("A.甲", "opt:A"), ("B.乙", "opt:B"),
                    ("C.丙", "opt:C"), ("D.丁", "opt:D")])
    skeleton = reflow.skeleton(table)
    assert skeleton["questions"][1]["options"] == {"A": [2], "B": [3], "C": [4], "D": [5]}


def test_an_option_that_wraps_keeps_its_own_label():
    """A wrapped option continues its label; it is not the next option."""
    table = _table([("1.題幹", "number"), ("A.很長的選項", "opt:A"), ("甲續行", ""),
                    ("B.乙", "opt:B"), ("C.丙", "opt:C"), ("D.丁", "opt:D")])
    skeleton = reflow.skeleton(table)
    assert skeleton["questions"][1]["options"]["A"] == [2, 3]
    assert skeleton["questions"][1]["options"]["D"] == [6]


def test_a_year_heading_is_not_a_question_number():
    """`105年第一次…` begins with digits and must not open a question."""
    table = _table([("105年第一次考試試題", ""), ("1.題幹", "number"), ("A.甲", "opt:A"),
                    ("B.乙", "opt:B"), ("C.丙", "opt:C"), ("D.丁", "opt:D")])
    skeleton = reflow.skeleton(table)
    assert skeleton["complete"]
    assert skeleton["chrome"] == [1]


def test_a_question_stem_that_opens_with_a_number_and_the_word_nian_is_not_a_heading():
    """`56 年老女性…` is question 56; `56年第一次…` would be a heading.

    The old rule was `^NNN年` and nothing more, so every question stem that opens with a number
    and 年 was thrown away as chrome and the paper lost the rest of its questions. Measured over
    all 3,516 shipped papers: 19 papers were cut short this way, `1092_法醫師_一般醫學` to 43 of
    100, `1062_諮商心理師…` to 31 of 40, `1002_醫師(二)_醫學(五)` to 56 of 80.

    What separates them is what follows the 年. A heading continues with the exam's own title
    (`第`, `專`); a stem continues with its subject. These four cases are the whole rule.
    """
    from qbr import repair
    assert repair.is_year_line("115年第一次專門職業及技術人員高等考試")
    assert repair.is_year_line("115 年第一次專門職業及技術人員高等考試")
    assert repair.is_year_line("105年專門職業及技術人員高等考試")
    assert not repair.is_year_line("56  年老女性發生肱骨頸部骨折時的敘述，何者正確？")
    assert not repair.is_year_line("7 年齡介於65～79 歲健康男性的血清尿酸")
    assert not repair.is_year_line("12  年金給付水準通常以「替代率」來計算")
    assert not repair.is_year_line("50年代脊髓灰白質炎流行中達到高峰")


def test_a_stem_that_opens_with_a_number_and_nian_keeps_its_paper():
    """Negative control: the paper is read whole, not cut at the first such stem.

    A reading that merely stopped calling the line chrome would still be wrong if the numbering
    scan then took `56` for a question number before question 55 - so the whole path is exercised:
    the line is neither chrome nor an anchor, and it is carried into the question being read.
    """
    from qbr import repair
    text = "\n".join([
        "114年第一次專門職業及技術人員高等考試醫師考試",
        "1  第1題的題幹，下列何者正確？",
        "A.甲", "B.乙", "C.丙", "D.丁",
        "2  年老女性發生肱骨頸部骨折時的敘述，何者正確？",
        "A.戊", "B.己", "C.庚", "D.辛",
        "3  第3題的題幹，下列何者正確？",
        "A.壬", "B.癸", "C.子", "D.丑",
    ])
    records, _residual, _diagnostics = repair.segment_best(text)
    numbers = [record["number"] for record in records]
    assert numbers == [1, 2, 3], numbers
    assert "年老女性" in records[1]["stem"]


def test_a_wrapped_formula_is_not_the_next_question_number():
    """A wrapped line that opens with digits sits in the option column, not at the margin.

    Measured on `1142_藥師(一)_藥學(二)(包括藥物分析與生藥學(含中藥學))`: question 3 asks for a
    solubility product, the formula `1.23×10⁻⁸` wraps, and the wrapped line is the cell `234.8）`.
    Reading order took it for question 234 and closed question 3 before its options, losing
    questions 4 to 80 - 77 of 80. `1091_醫事檢驗師_臨床血液學與血庫學` lost 56 the same way on
    `90.1 fL、reticulocyte 0.7`. The paper's own margin separates them: question 1 begins at x=28.0
    and the wrapped line sits at x=39.2.
    """
    def options(base, first, second):
        return [(base + i, text, f"opt:{label}", 1, 39.2) for i, (label, text)
                in enumerate(zip("ABCD", (first, second, "C", "D")))]

    table = _table_with_geometry(
        [(1, "1.以EDTA 滴定法定量碳酸鈣時，溶液之pH 值應調整至下列何者？", None, 1, 28.0)]
        + options(2, "A.4", "B.6")
        + [(6, "2.下列何者不是非水滴定分析中常見之溶劑？", None, 1, 28.0)]
        + options(7, "A.丙酸", "B.氯仿")
        + [(11, "3.碘化銀飽和溶液於25℃時的濃度為1.23×10⁻⁸ 莫耳／升，則其溶解度積（Ksp）為何？", None, 1, 28.0),
           (12, "234.8）", None, 1, 39.2)]         # the wrapped formula: indented
        + options(13, "A.1.11×10⁻⁴", "B.1.23×10⁻⁸")
        + [(17, "4.下列何者不適用酸滴定法分析其含量？", None, 1, 28.0)]
        + options(18, "A.六次甲基四胺", "B.三乙醇胺"))
    skeleton = reflow.skeleton(table)
    assert 234 not in skeleton["questions"], "the wrapped formula became a question number"
    # The wrapped line is folded into question 3, which is what it is part of, and question 4
    # still reads - the failure was not a wrong reading of the wrapped line but the loss of
    # everything after it.
    assert skeleton["count"] >= 4, skeleton["missing"]
    assert 4 in skeleton["questions"], skeleton["missing"]
    assert sorted(skeleton["questions"][4]["options"]) == ["A", "B", "C", "D"]


def test_a_question_indented_after_the_first_page_is_still_read():
    """The margin test applies only to a number that would otherwise be taken as a jump.

    Measured: 864 question starts sit at x=31 while their paper's question 1 is at x=22, because
    the paper indents after the first page. Refusing every number right of the margin would lose
    them, so a cell that says exactly the number being looked for is accepted wherever it sits.
    """
    table = _table_with_geometry([
        (1, "1.第一題", None, 1, 22.0),
        (2, "A.甲", "opt:A", 1, 31.0),
        (3, "B.乙", "opt:B", 1, 31.0),
        (4, "C.丙", "opt:C", 1, 31.0),
        (5, "D.丁", "opt:D", 1, 31.0),
        (6, "2.第二題開始於縮排後的邊界", None, 1, 31.0),
        (7, "A.甲", "opt:A", 1, 40.0),
        (8, "B.乙", "opt:B", 1, 40.0),
        (9, "C.丙", "opt:C", 1, 40.0),
        (10, "D.丁", "opt:D", 1, 40.0),
    ])
    skeleton = reflow.skeleton(table)
    assert skeleton["count"] == 2, skeleton["missing"]
    assert 2 in skeleton["questions"]


def test_no_geometry_means_the_margin_rule_does_not_apply():
    """A table built by hand has no geometry, and the rule has to stand aside rather than refuse."""
    table = _table([("1.題幹", None), ("A.甲", "opt:A"), ("B.乙", "opt:B"),
                    ("C.丙", "opt:C"), ("D.丁", "opt:D"),
                    ("2.題幹", None), ("A.甲", "opt:A"), ("B.乙", "opt:B"),
                    ("C.丙", "opt:C"), ("D.丁", "opt:D")])
    assert reflow.skeleton(table)["complete"], reflow.skeleton(table)["missing"]


def test_a_number_further_ahead_than_expected_is_reported():
    """A skipped number is a finding about the paper, not a reason to keep hunting for the gap."""
    table = _table([("1.甲題", "number"), ("A.一", "opt:A"), ("B.二", "opt:B"),
                    ("C.三", "opt:C"), ("D.四", "opt:D"),
                    ("3.丙題", "number"), ("A.一", "opt:A"), ("B.二", "opt:B"),
                    ("C.三", "opt:C"), ("D.四", "opt:D")])
    skeleton = reflow.skeleton(table)
    assert not skeleton["complete"]
    assert "question-2" in skeleton["missing"]


def test_a_question_with_four_labels_but_no_text_is_complete():
    """A figure question prints `A.` `B.` `C.` `D.` and nothing else. The paper is not damaged.

    Measured on `1152_醫事檢驗師_臨床血液學與血庫學` question 16: four cells holding only the
    markers, because the options are blood-smear photographs. The skeleton is right to call this
    complete, and the empty text is what marks the question as one needing a crop.
    """
    table = _table([("1.題幹", "number"), ("A.", "opt:A"), ("B.", "opt:B"),
                    ("C.", "opt:C"), ("D.", "opt:D")])
    skeleton = reflow.skeleton(table)
    # The skeleton reads marks, so this paper's question 16 is complete; the *absence of text* is
    # what the package stage turns into a figure flag, and it is a different statement.
    assert skeleton["complete"], skeleton["missing"]
    assert skeleton["questions"][1]["options"] == {"A": [2], "B": [3], "C": [4], "D": [5]}


# --- the vision stage: what is a figure, and where is it -----------------------------------
# The trigger has to be a measurement, because the word 圖 is in 523 questions of the medical
# technologist range and almost none of them needs a crop, while four blood-smear photographs can
# appear under a stem that never says 圖 at all. These tests fix the three ways the first version
# of the trigger was wrong, each of which was caught by a control rather than by inspection.

def _row(number, *, page=1, x0=0.0, y0=0.0, x1=100.0, y1=10.0, text=""):
    return {"page": page, "x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": text}


def _skeleton_item(number, *, stem=(1,), options=None, page=1):
    return {"number": number, "stem": "題幹", "cells": {"stem": list(stem), "options": options or {}},
            "options": {label: "" for label in (options or {})}}


def test_an_option_with_no_text_claims_the_image_beside_it():
    """The marker and its picture touch, so an overlap test finds nothing and the crop is bare.

    Measured on question 16 of `1152_醫事檢驗師_臨床血液學與血庫學`: the marker `A.` ends at x=50
    and the blood smear begins at x=50.
    """
    # The stem sits on page 3, the four bare markers on page 4 beside their pictures. This is the
    # measured layout of question 16, and it is what keeps the two triggers apart: the stem has no
    # picture in its own region, so the only reason this question is returned is its options.
    rows = [_row(1, page=3, y0=0, y1=10, text="16.題幹"),
            _row(2, page=4, x0=39, x1=50, y0=0, y1=10, text="A."),
            _row(3, page=4, x0=39, x1=50, y0=70, y1=80, text="B."),
            _row(4, page=4, x0=39, x1=50, y0=140, y1=150, text="C."),
            _row(5, page=4, x0=39, x1=50, y0=210, y1=220, text="D.")]
    images = [{"page": 4, "x0": 50, "y0": 45, "x1": 118, "y1": 104},
              {"page": 4, "x0": 50, "y0": 115, "x1": 115, "y1": 174},
              {"page": 4, "x0": 50, "y0": 185, "x1": 118, "y1": 244},
              {"page": 4, "x0": 50, "y0": 255, "x1": 118, "y1": 314}]
    item = _skeleton_item(16, stem=(1,), options={"A": [2], "B": [3], "C": [4], "D": [5]})
    found = vision.figure_questions([item], rows, images)
    assert len(found) == 1
    # Every picture is inside the crop, which is what the question is asking about. The reason is
    # `embedded-image` rather than `options-without-text` because the question **owns** these four:
    # three of them lie in its own rows and the fourth is the nearest rows to it. The marker-only
    # trigger is what fires when no picture of the page belongs to this question at all.
    assert found[0]["reasons"] == ["embedded-image"]
    box = found[0]["box"]
    for image in images:
        assert box[0] <= image["x0"] and box[1] <= image["y0"]
        assert box[2] >= image["x1"] and box[3] >= image["y1"], "a bare-marker option's picture" \
            " must be inside the crop, which is the whole point of the trigger"
    # The crop reaches the pictures, not just the letters.
    assert found[0]["box"][2] >= 118


def test_a_continuation_page_keeps_the_picture_above_its_first_marker():
    """When an option's marker is on the previous page its picture is at the head of this one.

    Measured on question 80 of `1112_藥師(一)_藥理學與藥物化學`: `A.` is the last thing on page 18
    and its chemical structure is the first thing on page 19, y=29-223, while `B.` starts at
    y=232. A crop drawn from page 19's markers alone began at y=229 and cut the structure off, and
    the model then reported, correctly, that the option's structure was not in the crop. The rule
    is not "crop the whole page": it is that a page which continues a question cannot know where
    that question's content begins, so everything above its first cell belongs to it.
    """
    rows = [_row(1, page=18, y0=644, y1=658, text="80.題幹"),
            _row(2, page=18, y0=670, y1=684, text="A."),
            _row(3, page=19, y0=232, y1=246, text="B."),
            _row(4, page=19, y0=413, y1=427, text="C."),
            _row(5, page=19, y0=605, y1=619, text="D.")]
    images = [{"page": 19, "x0": 58, "y0": 29, "x1": 332, "y1": 223},
              {"page": 19, "x0": 57, "y0": 229, "x1": 210, "y1": 404}]
    item = _skeleton_item(80, stem=(1,), options={"A": [2], "B": [3], "C": [4], "D": [5]})
    found = vision.figure_questions([item], rows, images)
    assert len(found) == 1
    assert found[0]["page"] == 19
    assert found[0]["box"][1] <= 29           # the first structure is inside the crop
    assert found[0]["box"][3] >= 619


def test_a_structure_beside_its_marker_is_claimed_though_they_only_touch():
    """A structure is set in the column beside its letter, so a gap test on the union finds none.

    Measured: the four markers of question 80 of `1112_藥師(一)_藥理學與藥物化學` are at x=45-58
    and the four structures at x=57-332. Matching each *cell* to its own picture is what makes
    that reachable; matching the union of the markers to every picture on the page does not,
    because the union has no vertical extent to test when the options are a page-third apart.
    """
    rows = [_row(1, page=2, y0=0, y1=14, text="1.題幹"),
            _row(2, page=2, x0=45, x1=58, y0=100, y1=114, text="A."),
            _row(3, page=2, x0=45, x1=58, y0=300, y1=314, text="B.")]
    images = [{"page": 2, "x0": 57, "y0": 29, "x1": 260, "y1": 210},
              {"page": 2, "x0": 57, "y0": 229, "x1": 250, "y1": 410}]
    item = _skeleton_item(1, stem=(1,), options={"A": [2], "B": [3]})
    found = vision.figure_questions([item], rows, images)
    assert len(found) == 1
    assert found[0]["box"][2] >= 250          # both structures are inside the crop


# --- a response that arrives in the wrong shape is not an empty response -------------------
# Three separate ways a crop's answer could be lost, all found by reading the records rather than
# by trusting the summary: a truncated fenced JSON, a JSON without the key being asked about, and
# a request that never arrived. Each needs a different response, so each must be named.

def test_a_truncated_fence_is_reported_as_truncated():
    """The engine ran out of tokens; the crop was right and the budget was wrong.

    Measured on question 22 of `1092_藥師(二)_調劑學與臨床藥學`, which was reported as `contains:
    null` with no error. Re-run with a larger budget it returned `figure` at 0.95 confidence and
    named all three clean-room diagrams.
    """
    body = ('```json\n{"contains": "figure", "describes": "三張圖示：甲為清淨室配置",'
            ' "readable_values": ["A.氣壓X＞Y"')
    assert vision._json_of(body) == (None, "truncated-json")


def test_a_json_without_the_question_being_asked_is_not_a_verdict():
    """A dict is not a verdict unless it answers what the crop was cut to ask."""
    parsed, complaint = vision._json_of('{"describes": "一張圖"}')
    assert parsed is None and complaint == "verdict-missing-contains"


def test_an_empty_response_is_named_and_not_confused_with_a_failure():
    assert vision._json_of("") == (None, "empty-response")
    assert vision._json_of("我不知道") == (None, "no-json-in-response")


def test_a_good_verdict_parses_with_no_complaint():
    parsed, complaint = vision._json_of('{"contains": "figure", "confidence": 0.9}')
    assert complaint is None and parsed["contains"] == "figure"


def test_a_picture_with_text_beside_it_is_claimed_across_the_page_width():
    """A picture to the right of the text, in the question's own rows, is the question's picture.

    This test used to assert the opposite - that a picture at x=300 was "in another column" and
    must stay out - and the assertion was wrong for this corpus. Measured over 4,662 pages of the
    429 papers: the largest empty horizontal band inside a page's content has width **0.0 pt**, and
    not one page has a gutter wider than 30 pt, while the median content width is 504.8 pt. These
    papers are single-column. There is no second column for a picture to belong to, so a picture at
    x=300 whose centre lies in the question's own rows is this question's picture.

    The rule was also costing real figures: 342 pictures are wider than the text printed beside
    them, so a crop drawn from the text's rectangle clips them horizontally. The crop is the union
    of the question's rows and the picture's *own* box, which is wide enough to hold what is printed
    and claims nothing from an empty margin.
    """
    rows = [_row(1, y0=0, y1=14, text="1.題幹"),
            _row(2, x0=45, x1=58, y0=100, y1=114, text="A.")]
    images = [{"page": 1, "x0": 300, "y0": 20, "x1": 460, "y1": 200}]
    item = _skeleton_item(1, stem=(1,), options={"A": [2]})
    found = vision.figure_questions([item], rows, images)
    assert len(found) == 1
    # The band reaches the full content width, so the picture is inside the crop.
    assert found[0]["box"] == (0.0, 0.0, 460.0, 200.0)


def test_a_picture_on_another_page_is_not_claimed():
    """The band is per page, so a picture on a page the question never reaches is not claimed.

    This is the guard that survives: a box is a rectangle on one page, and the page boundary is
    structural.
    """
    rows = [_row(1, page=1, y0=0, y1=14, text="1.題幹"),
            _row(2, page=1, x0=45, x1=58, y0=100, y1=114, text="A."),
            _row(3, page=2, y0=0, y1=14, text="2.別的題目")]
    images = [{"page": 2, "x0": 45, "y0": 200, "x1": 460, "y1": 500}]
    item = _skeleton_item(1, stem=(1,), options={"A": [2]})
    found = vision.figure_questions([item], rows, images)
    # The question may still be returned by the marker-only trigger, which is about its own page.
    # What must not happen is the page-2 picture entering a box measured on page 1.
    for entry in found:
        assert entry["page"] == 1
        assert entry["box"][2] < 300 and entry["box"][3] < 200, \
            "a picture on a page the question has no cells on is not its picture"


def test_a_picture_after_the_last_question_on_a_page_is_claimed():
    """A picture printed below a stem belongs to it, even when no cell touches the picture.

    This test used to assert the opposite, and the assertion was wrong. It was written from the
    shape of the code - the region is a rectangle, so a picture outside it is not in it - rather
    than from a paper. Then question 68 of
    `1152_醫事檢驗師_微生物學與臨床微生物學(包括細菌與黴菌)` came back with no figure at all
    while its own stem names 圖1 and 圖2: the stem ends at y=627, the two photographs sit at
    y=629-812, and the four options print at the head of the next page, so no cell of the question
    touches a picture.

    A question owns the pictures nearest to its own rows, and a picture printed after the text but
    before the next question's number is nearer to that text than to the question below it - which is
    how the paper reads. Measured over the corpus: 91 pictures lie in a question's part of the page
    without overlapping its cells, and they are the figures of 47 papers - pharmacokinetic
    compartment diagrams, warfarin's four stereochemistry structures, TLC plates, ultrasound scans.
    Every one of them was being dropped, and every one of them is what its question is asking about.

    The competing rule - claim a picture only when the question does not finish on this page - was
    measured and rejected: it drops 42 of the 91, including question 38 of
    `1001_醫事檢驗師_臨床生理學與病理學`, whose whole content is the scan under its stem.
    """
    rows = [_row(1, y0=0, y1=10, text="1.題幹"), _row(2, x0=39, x1=50, y0=0, y1=10, text="A."),
            _row(3, x0=39, x1=50, y0=70, y1=80, text="B."),
            _row(4, x0=39, x1=50, y0=140, y1=150, text="C."),
            _row(5, x0=39, x1=50, y0=210, y1=220, text="D.")]
    far = [{"page": 1, "x0": 50, "y0": 700, "x1": 400, "y1": 900}]
    item = _skeleton_item(1, stem=(1,), options={"A": [2], "B": [3], "C": [4], "D": [5]})
    found = vision.figure_questions([item], rows, far)
    assert len(found) == 1
    assert found[0]["box"][3] == 900           # the picture below the question is its own


def test_the_next_question_on_the_page_is_nearest_to_the_picture_below_it():
    """A later question's picture is not swept into the question above it.

    The guard used to be the band boundary - a question's band ended at the next question's number.
    It is now the ownership rule, and it is still a structural boundary rather than a tuned
    distance: the picture's centre is 290 pt below question 2's own rows and 580 pt below question
    1's, so question 2 owns it and question 1's crop stops at its own rows. Measured over the whole
    corpus, exactly one page in 4,633 has two question numbers within 20 pt of each other, so a
    page's rows belong to one question.

    負對照＝舊行為：舊碼把這張圖掛在第一題（`boxes[1][3]` 會是 900），所以這一行在舊碼上會失敗。
    """
    rows = [_row(1, y0=0, y1=10, text="1.題幹"), _row(2, x0=39, x1=50, y0=0, y1=10, text="A."),
            _row(3, x0=39, x1=50, y0=70, y1=80, text="B."),
            _row(4, x0=39, x1=50, y0=140, y1=150, text="C."),
            _row(5, x0=39, x1=50, y0=210, y1=220, text="D."),
            # Question 2 begins here, so nothing below belongs to question 1.
            _row(6, y0=400, y1=410, text="2.下一題")]
    rows[5]["text"] = "2.下一題"
    other = [{"page": 1, "x0": 50, "y0": 700, "x1": 400, "y1": 900}]
    item1 = _skeleton_item(1, stem=(1,), options={"A": [2], "B": [3], "C": [4], "D": [5]})
    item2 = _skeleton_item(2, stem=(6,), options={})
    found = vision.figure_questions([item1, item2], rows, other)
    boxes = {entry["number"]: entry["box"] for entry in found}
    assert 1 not in boxes or boxes[1][3] < 400, "question 1 must not claim below question 2"


def test_a_picture_printed_above_a_questions_number_is_that_questions_picture():
    """The picture and the question number below it belong together, not the question above.

    The question above is the one whose **text is complete without the picture**: its options are
    text, so a picture in the gap below them is not one of its options. Measured on the live queue
    2026-09-25: a question's band ran from its own number down to the *next* question's number, so a
    paper that prints a picture above a question's number had that picture claimed by the question
    above - a question with no figure of its own, cropped for one - while the question the picture
    belongs to was not cropped at all. That is what the owner saw as 「有些題目沒有圖片但是卻截別題的
    來貼上」, and 844 of 3,263 crops reached outside their own question's rows. The picture is now
    given to the **nearest** question: its centre is 136 pt below question 1's own rows and 50 pt
    above question 2's.

    The other shape - a question whose options *are* pictures - is the next test, and the difference
    between them is the option text, not the distance.

    負對照＝舊行為：舊碼把這張圖掛在第一題（第二題根本沒被回傳），所以 `1 not in boxes` 這一行
    在舊碼上會失敗。
    """
    rows = [_row(1, y0=100, y1=114, text="1.題幹"),
            _row(2, x0=39, x1=50, y0=140, y1=154, text="A.甲"),
            _row(3, x0=39, x1=50, y0=180, y1=194, text="B.乙"),
            _row(4, x0=39, x1=50, y0=220, y1=234, text="C.丙"),
            # 這一題的圖印在這裡，第二題的題號就在它正下方。
            _row(5, y0=420, y1=434, text="2.下一題")]
    images = [{"page": 1, "x0": 50, "y0": 320, "x1": 400, "y1": 420}]
    item1 = {"number": 1, "stem": "1.題幹",
             "cells": {"stem": [1], "options": {"A": [2], "B": [3], "C": [4]}},
             "options": {"A": "甲", "B": "乙", "C": "丙"}}
    item2 = _skeleton_item(2, stem=(5,))
    boxes = {entry["number"]: entry
             for entry in vision.figure_questions([item1, item2], rows, images)}
    assert 1 not in boxes, "第一題自己有文字選項，這一張不是它的，不該為它裁一張"
    assert boxes[2]["reasons"] == ["embedded-image"]
    assert boxes[2]["box"][1] <= 320 and boxes[2]["box"][3] >= 420


def test_a_picture_a_hair_below_the_last_row_is_that_questions_picture():
    """A sub-line gap is measurement noise; a 40pt gap is a gap.

    Measured on the live queue 2026-09-25, question 68 of `100030:102:0106`: its own row is
    y=305.1-317.3 and its three option pictures start at y=318.1, 0.8 pt below it, while the next
    question's row begins at y=383.1 - so measuring the distance from the picture's centre to each
    band hands the pictures to the question below. The tolerance is `FIGURE_ADJACENCY` (2 pt, the
    sub-line spacing this file already uses) and it only ever extends the **top-edge** test: it
    cannot reach a picture printed in the real gap, 53 pt below its question's rows on
    `106020:305:11` question 65.

    負對照＝沒有容差的版本：第一張圖的中心離第二題的列比較近，所以 `1 in boxes` 會失敗。
    """
    rows = [_row(1, y0=100, y1=114, text="1.題幹"),
            _row(2, x0=39, x1=50, y0=130, y1=144, text="A.甲")]
    item1 = {"number": 1, "stem": "1.題幹",
             "cells": {"stem": [1], "options": {"A": [2]}},
             "options": {"A": "甲"}}
    # 第二題的列貼在圖的下緣附近（列底 144，圖 144.8-190，第二題從 185 開始），所以「離誰的中心近」
    # 會判給第二題；x 放到圖的右邊，免得走的是「壓到選項」那條路。
    near = rows + [_row(3, x0=500, x1=560, y0=185, y1=199, text="2.下一題")]
    hair = [{"page": 1, "x0": 50, "y0": 144.8, "x1": 400, "y1": 190}]
    boxes = {entry["number"]: entry
             for entry in vision.figure_questions([item1, _skeleton_item(2, stem=(3,))],
                                                  near, hair)}
    assert 1 in boxes and boxes[1]["box"][3] >= 190, "0.8pt 是量測誤差，這張是上面那一題的"
    assert 2 not in boxes, "第二題自己的列裡沒有這張圖"
    # 差 40pt 的真間隔：第二題遠在下面，這張圖不是第一題的。
    far = rows + [_row(3, x0=500, x1=560, y0=240, y1=254, text="2.下一題")]
    gap = [{"page": 1, "x0": 50, "y0": 184.0, "x1": 400, "y1": 230}]
    boxes = {entry["number"]: entry
             for entry in vision.figure_questions([item1, _skeleton_item(2, stem=(3,))],
                                                  far, gap)}
    assert 1 not in boxes, "40pt 是間隔，不是相鄰：這張圖不是第一題的"
    assert 2 in boxes and boxes[2]["box"][1] <= 184


def _gap_grid_fixture(lower_stem="2.下一題", upper_options=("A.", "B.")):
    """上一題的列 100-144（選項只有標記），圖在間隔裡 200-260，下一題的列 280-294。"""
    rows = [_row(1, y0=100, y1=114, text="1.題幹"),
            _row(2, x0=39, x1=50, y0=130, y1=144, text=upper_options[0]),
            _row(3, x0=39, x1=50, y0=150, y1=164, text=upper_options[1]),
            _row(4, y0=280, y1=294, text=lower_stem)]
    item1 = {"number": 1, "stem": "1.題幹",
             "cells": {"stem": [1], "options": {"A": [2], "B": [3]}},
             "options": {"A": upper_options[0], "B": upper_options[1]}}
    item2 = _skeleton_item(2, stem=(4,))
    item2["stem"] = lower_stem
    return rows, [item1, item2]


def test_a_picture_in_the_gap_is_the_upper_questions_option_grid():
    """上一題的選項是圖、下一題的題幹沒提到圖 ⇒ 這張圖是上一題的選項圖格。

    站上實測（2026-09-25，唯讀）：`moex:108100:305:33` q53 的讀文是「…下列何者最能清楚及正確顯示該藥
    之血漿中藥物濃度對時間之關係？ A. B. C. D.」（四個選項是圖，讀文裡只剩標記），下一題 q54 問二室模
    式的斜率、沒提到圖，圖 y533.8-560.3 就在 q54 題號 y562.6 上面 2.3pt；`moex:100030:102:0106` q68
    連選項 cell 都沒有（四個手部副木圖就是它的選項），下一題 q69 問坐墊、也沒提到圖。反過來
    `moex:107100:305:11` q66 的題幹寫「下列何者為**下圖**化合物排出人體外的最主要型態？」——下一題提
    到圖，圖就該判給它（下面第三段）。

    三個條件每一個都要成立；這裡一段一個：成立、下一題提到圖（負對照）、上一題的選項有內容。
    """
    rows, items = _gap_grid_fixture()
    images = [{"page": 1, "x0": 50, "y0": 200, "x1": 400, "y1": 260}]
    boxes = {entry["number"]: entry
             for entry in vision.figure_questions(items, rows, images)}
    assert 1 in boxes and boxes[1]["box"][3] >= 260, "三個條件都成立時，圖留在上面那一題"
    assert 2 not in boxes, "下一題沒提到圖，不該掛這張"
    # 條件 2 的反面：下一題的題幹提到圖（樣本 `moex:107100:305:11` q66），圖就判給它。
    rows, items = _gap_grid_fixture(lower_stem="2.下列何者為下圖化合物？")
    boxes = {entry["number"]: entry
             for entry in vision.figure_questions(items, rows, images)}
    assert 2 in boxes and boxes[2]["box"][1] <= 200, "下一題自己說要看圖，圖是它的"
    assert 1 not in boxes
    # 條件 3 的反面：上一題的選項有內容（樣本 `moex:106020:305:11` q65 的 A. B. C. D. 旁邊是文字），
    # 就不是「選項是圖」的題目，交給距離判定，中心離下一題比較近。
    rows, items = _gap_grid_fixture(upper_options=("A.甲", "B.乙"))
    boxes = {entry["number"]: entry
             for entry in vision.figure_questions(items, rows, images)}
    assert 2 in boxes and boxes[2]["box"][1] <= 200
    assert 1 not in boxes, "上一題有文字選項，題幹又沒說要看圖，這張不是它的"


def test_the_guard_does_not_fire_when_the_upper_questions_entry_is_on_another_page():
    """條件 4：上一題的裁圖在別頁，這張圖交給它就等於從每一張裁圖裡消失（站上三例見 `_option_grid_owner`）。

    這裡的上一題在第一頁只有一行（100-114），在第二頁有一大塊列（100-400），所以它那一張裁圖挑在
    第二頁；圖在第二頁的間隔裡，交給它就沒有一張裁圖收得到。守門規則不成立時由距離判定：圖離下面
    那一題比較近，判給下面那一題，圖還在（看得見），不是消失。
    """
    rows = [_row(1, y0=300, y1=314, text="1.題幹續"),
            _row(2, y0=350, y1=364, text="A.甲"),
            _row(3, y0=400, y1=414, text="B.乙"),
            _row(4, y0=450, y1=464, text="C.丙"),
            _row(5, y0=600, y1=614, text="D.丁")]
    rows_page_2 = [_row(6, page=2, y0=100, y1=114, text="1.題幹"),
                   _row(7, page=2, x0=39, x1=50, y0=130, y1=144, text="A."),
                   _row(8, page=2, x0=39, x1=50, y0=150, y1=164, text="B."),
                   _row(9, page=2, y0=280, y1=294, text="2.下一題")]
    item1 = {"number": 1, "stem": "1.題幹",
             "cells": {"stem": [1, 6], "options": {"A": [2, 7], "B": [3, 8], "C": [4], "D": [5]}},
             "options": {"A": "A.", "B": "B."}}
    item2 = _skeleton_item(2, stem=(9,))
    images = [{"page": 2, "x0": 50, "y0": 200, "x1": 400, "y1": 260}]
    boxes = {entry["number"]: entry
             for entry in vision.figure_questions([item1, item2], rows + rows_page_2, images)}
    assert 2 in boxes and boxes[2]["box"][1] <= 200, "上一題的裁圖在別頁，圖要判給收得到它的那一題"
    assert 1 not in boxes, "第一題只裁第二頁那一頁，它不該把第二頁的這張圖算成第一頁的"


def test_the_extension_does_not_reach_over_a_picture_the_next_question_asks_for():
    """下一題的題幹說要看圖時，上一題「選項是圖」的延伸不生效，那張圖判給下一題。

    站上實測（2026-09-25，唯讀）：`moex:107100:305:11` q65 沒有任何選項 cell，帶狀接到 q66 的題號，
    把 q66 題幹寫的「下圖化合物」兩張圖（y 484.6-520.9、491.8-518.8）收走；q66 的題幹要的就是這兩張。
    """
    rows = [_row(1, y0=100, y1=114, text="1.題幹"),
            _row(2, y0=300, y1=314, text="2.下列何者為下圖化合物？")]
    item1 = {"number": 1, "stem": "1.題幹", "cells": {"stem": [1], "options": {}}, "options": {}}
    item2 = {"number": 2, "stem": "2.下列何者為下圖化合物？",
             "cells": {"stem": [2], "options": {}}, "options": {}}
    images = [{"page": 1, "x0": 50, "y0": 180, "x1": 400, "y1": 240}]
    boxes = {entry["number"]: entry
             for entry in vision.figure_questions([item1, item2], rows, images)}
    assert 2 in boxes and boxes[2]["box"][1] <= 180, "下一題的題幹要這張圖，延伸不能收走它"
    assert 1 not in boxes
    # 反面：下一題沒有提到圖（中醫師 q40 那一頁的形狀：下一題 q41 說的是「右圖」，不是 `FIGURE_CUES`），
    # 延伸照樣生效，圖留在上面那一題。
    item2["stem"] = "2.下一題"
    boxes = {entry["number"]: entry
             for entry in vision.figure_questions([item1, item2], rows, images)}
    assert 1 in boxes and boxes[1]["box"][3] >= 240, "下一題沒提到圖，這一題的帶狀照樣伸過去"
    assert 2 not in boxes


def test_a_picture_that_reaches_the_lower_questions_rows_is_not_the_upper_grid():
    """條件 1：圖的下緣已經進到下一題自己的列裡，就不是「間隔裡的圖」，守門規則不適用。"""
    rows, items = _gap_grid_fixture()
    images = [{"page": 1, "x0": 50, "y0": 240, "x1": 400, "y1": 300}]
    boxes = {entry["number"]: entry
             for entry in vision.figure_questions(items, rows, images)}
    assert 2 in boxes and boxes[2]["box"][1] <= 240, "圖已經壓到下一題自己的列"
    assert 1 not in boxes


def test_a_question_whose_options_are_pictures_keeps_its_own_grid():
    """A picture-option question's pictures are its options, so they stay with it across the gap.

    Measured on question 40 of `1021_中醫師_中醫基礎醫學(二)(包括中醫方劑學、中醫藥物學)` page 4:
    its own row is the stem alone (y=237-249, no option cells at all - the labels `(A)`-`(D)` are
    printed inside the pictures), its four option pictures sit at y=259-407 and y=444-575, and the
    next question's number is at y=586.8. The picture nearest the *next* question's number is
    `(C)`/`(D)`, 261 pt below its own stem and 76 pt above the number below it, so a rule that only
    looks at the distance hands them to the question below - the same 「截別題的圖」 this change is
    here to remove. They are its own: the question carries no option text at all, so its pictures
    *are* its options and its band is its whole territory down to the next question's number.

    負對照＝只看距離的版本：把 `pictured_options` 那三行拿掉，這張圖會被判給第二題，下面這個
    `1 in boxes` 就會失敗（站上 3,296 張圖裡有 13 張是這個形狀）。
    """
    rows = [_row(1, y0=237, y1=250, text="40.題幹"),
            _row(2, y0=586, y1=600, text="41.下一題")]
    images = [{"page": 1, "x0": 91, "y0": 259, "x1": 220, "y1": 407},
              {"page": 1, "x0": 87, "y0": 444, "x1": 233, "y1": 575}]
    item1 = _skeleton_item(1, stem=(1,))                 # no option cells: 選項是圖
    item2 = _skeleton_item(2, stem=(2,))
    boxes = {entry["number"]: entry
             for entry in vision.figure_questions([item1, item2], rows, images)}
    assert 1 in boxes, "這一題的選項就是圖，兩張都是它的"
    assert boxes[1]["box"][1] <= 259 and boxes[1]["box"][3] >= 575
    assert 2 not in boxes, "第二題自己的列裡沒有這張圖，就不該掛它"


def test_a_picture_only_the_previous_questions_band_reached_is_not_handed_to_it():
    """The picture nearest the question below while the question above only *reached* for it.

    The question above has complete text options and its own rows end far above the picture; only
    its old band (its number down to the next number) touched it. Measured on the live queue
    2026-09-25: 67 of the 3,867 pictures inside its figure crops were claimed by two questions at
    once, which is one question showing another's picture on the screen.

    負對照＝舊行為：舊碼把第一題的帶狀畫到第二題的題號，所以這張圖同時掛上兩題，`1 not in
    boxes` 會失敗。
    """
    rows = [_row(1, y0=100, y1=114, text="1.題幹"),
            _row(2, x0=39, x1=50, y0=140, y1=154, text="A.甲"),
            _row(3, x0=39, x1=50, y0=180, y1=194, text="B.乙"),
            _row(4, y0=450, y1=464, text="2.下一題"),
            _row(5, x0=39, x1=50, y0=520, y1=534, text="A.丙")]
    images = [{"page": 1, "x0": 50, "y0": 400, "x1": 400, "y1": 560}]
    item1 = {"number": 1, "stem": "1.題幹",
             "cells": {"stem": [1], "options": {"A": [2], "B": [3]}},
             "options": {"A": "甲", "B": "乙"}}
    item2 = {"number": 2, "stem": "2.下一題",
             "cells": {"stem": [4], "options": {"A": [5]}},
             "options": {"A": "丙"}}
    boxes = {entry["number"]: entry["box"]
             for entry in vision.figure_questions([item1, item2], rows, images)}
    assert 2 in boxes and boxes[2][1] <= 400, "中心在第二題自己的列裡，這一張是它的"
    assert 1 not in boxes, "第一題只有舊帶狀重疊到它，自己不該掛這張圖"


def test_a_picture_overlapping_two_questions_equally_has_exactly_one_owner():
    """One picture, one question - and the rule names the same owner from either side.

    Measured on the live queue 2026-09-25: *every* question whose band a picture overlapped claimed
    it, so a picture lying between two questions was cut into both crops. The fixture is the equal
    case - 50 pt of the picture in question 1's own rows and 50 pt in question 2's - and the rule has
    to name exactly one owner. It is the same rule seen from the other side rather than a preference
    for the question above: the second call's picture has its centre inside question 2's rows, and
    there question 2 owns it and question 1 does not.

    負對照＝舊行為：舊碼會把第一張圖同時掛上兩題（`boxes[2]` 存在），所以第二個 `assert` 會失敗。
    """
    rows = [_row(1, y0=100, y1=114, text="1.題幹"),
            _row(2, x0=39, x1=50, y0=200, y1=214, text="A.甲"),
            _row(3, x0=39, x1=50, y0=330, y1=350, text="B.乙"),
            _row(4, y0=450, y1=464, text="2.下一題"),
            _row(5, x0=39, x1=50, y0=520, y1=534, text="A.丙")]
    item1 = {"number": 1, "stem": "1.題幹",
             "cells": {"stem": [1], "options": {"A": [2], "B": [3]}},
             "options": {"A": "甲", "B": "乙"}}
    item2 = {"number": 2, "stem": "2.下一題",
             "cells": {"stem": [4], "options": {"A": [5]}},
             "options": {"A": "丙"}}
    even = [{"page": 1, "x0": 50, "y0": 300, "x1": 400, "y1": 500}]   # 兩題各 50pt
    boxes = {entry["number"]: entry["box"]
             for entry in vision.figure_questions([item1, item2], rows, even)}
    assert 1 in boxes and boxes[1][1] <= 300 and boxes[1][3] >= 500
    assert 2 not in boxes, "第二題自己的列裡沒有這張圖，就不該掛它"

    lower = [{"page": 1, "x0": 50, "y0": 470, "x1": 400, "y1": 590}]  # 中心 530 在第二題的列裡
    boxes = {entry["number"]: entry["box"]
             for entry in vision.figure_questions([item1, item2], rows, lower)}
    assert 2 in boxes and boxes[2][1] <= 470 and boxes[2][3] >= 590
    assert 1 not in boxes, "換一邊也一樣：這一張是第二題的"


def test_a_question_is_cropped_once_not_once_per_page():
    """A question whose stem and options are on two pages must not produce two crops.

    The first version emitted one entry per page, so page 3 of question 16 of
    `1152_醫事檢驗師_臨床血液學與血庫學` - a stem and no pictures - was cropped, and the model
    described it, correctly, as containing no figure. The score then showed a false positive that
    was a defect in the trigger, not in the model.
    """
    rows = [_row(1, page=3, text="16.題幹"), _row(2, page=4, x0=39, x1=50, text="A."),
            _row(3, page=4, x0=39, x1=50, y0=70, y1=80, text="B."),
            _row(4, page=4, x0=39, x1=50, y0=140, y1=150, text="C."),
            _row(5, page=4, x0=39, x1=50, y0=210, y1=220, text="D.")]
    images = [{"page": 4, "x0": 50, "y0": 45, "x1": 118, "y1": 104}]
    item = _skeleton_item(16, stem=(1,), options={"A": [2], "B": [3], "C": [4], "D": [5]})
    found = vision.figure_questions([item], rows, images)
    assert len(found) == 1
    assert found[0]["page"] == 4              # the page carrying the options and the picture


def test_an_embedded_image_is_found_without_the_stem_saying_圖():
    """`下圖` is not the trigger; the image object is.

    The figure has to be *in* the question's own region, which is what the overlap test measures:
    a picture sitting on the page but not within the question's cells belongs to some other
    question, and claiming it would crop the wrong thing.
    """
    rows = [_row(1, y0=40, y1=60, text="1.下列何者正確？")]
    images = [{"page": 1, "x0": 40, "y0": 40, "x1": 400, "y1": 300}]
    found = vision.figure_questions([_skeleton_item(1)], rows, images)
    assert len(found) == 1
    assert found[0]["reasons"] == ["embedded-image"]
    assert found[0]["box"][3] >= 300          # the crop reaches the whole figure


def test_a_rule_or_a_logo_is_not_a_figure():
    """Three thousand image objects in the corpus have zero height: they are rules, not figures."""
    rows = [_row(1, text="1.題幹")]
    images = [{"page": 1, "x0": 40, "y0": 40, "x1": 240, "y1": 40}]
    assert vision.figure_questions([_skeleton_item(1)], rows, images) == []


def test_splitting_an_empty_option_across_pages_is_reported():
    """A crop is one rectangle on one page, so a split question cannot be cropped honestly.

    Measured: question 35 of `1051_醫事檢驗師_臨床血清免疫學與臨床病毒學` prints `A.` at the foot
    of page 4 and `B.` `C.` `D.` at the head of page 5. Of the 19 questions with bare-marker
    options in the medical technologist range, 6 split across a page break.
    """
    rows = [_row(1, page=4, y0=770, y1=783, text="35.題幹"),
            _row(2, page=4, x0=41, x1=49, y0=785, y1=797, text="A."),
            _row(3, page=5, x0=41, x1=49, y0=78, y1=90, text="B."),
            _row(4, page=5, x0=38, x1=50, y0=116, y1=130, text="C."),
            _row(5, page=5, x0=41, x1=50, y0=166, y1=179, text="D.")]
    images = [{"page": 5, "x0": 49, "y0": 28, "x1": 88, "y1": 215}]
    item = _skeleton_item(35, stem=(1,), options={"A": [2], "B": [3], "C": [4], "D": [5]})
    found = vision.figure_questions([item], rows, images)
    assert len(found) == 1
    assert found[0]["split"] is True
    assert found[0]["option_pages"] == [4, 5]


# --- the two readings, and which one is paid for -------------------------------------------
# The cheap reading (thinking off) is 33x faster and is usually right, and it fails by drifting:
# its questions stop matching the paper's skeleton while every cell is still assigned. So the
# trigger for the expensive reading is disagreement with the skeleton, not a property of the paper.
# A rule that predicted which papers need thinking was written and then deleted: it predicted the
# worst paper in the corpus was safe, and no property in the measured table separates that paper
# from the best one.

def _reading(number, stem, options):
    return {"number": number, "stem": stem, "options": options,
            "cells": {"stem": [number], "options": {}}}


def test_an_agreement_needs_no_second_reading():
    paper = [_reading(1, "甲題", {"A": "一", "B": "二", "C": "三", "D": "四"})]
    assert reflow.disagreements(paper, paper) == []


def test_a_shifted_question_is_a_disagreement():
    """The failure mode that thinking off produces: the text moves to the previous question."""
    paper = [_reading(1, "甲題", {"A": "一", "B": "二"}), _reading(2, "乙題", {"A": "三", "B": "四"})]
    reading = [_reading(1, "甲題", {"A": "一", "B": "二"}), _reading(2, "乙題A.三", {"A": ""})]
    assert reflow.disagreements(reading, paper) == [2]


def test_spacing_is_not_a_disagreement():
    """Whether a space was printed is typesetting; whether a character was is the claim."""
    paper = [_reading(1, "甲題", {"A": "一 二"})]
    reading = [_reading(1, "甲 題", {"A": "一二"})]
    assert reflow.disagreements(reading, paper) == []


def test_a_question_one_side_lacks_is_a_disagreement():
    paper = [_reading(1, "甲題", {}), _reading(2, "乙題", {})]
    reading = [_reading(1, "甲題", {})]
    assert reflow.disagreements(reading, paper) == [2]


def test_the_reasoning_budget_is_separate_from_the_answer_budget():
    """Reasoning and the answer share `max_tokens`, and the one time that was ignored it cost
    forty minutes and produced no answer at all (measured: 156,400 tokens, all reasoning)."""
    off = reflow.completion_budget(80, 421, think=False)
    on = reflow.completion_budget(80, 421, think=True)
    assert on > off
    assert off == reflow.answer_budget(80, 421)
    assert on == off + reflow.reasoning_budget(421)
    # The measured worst case must fit inside the ceiling, or the ceiling is the binding constraint
    # again - which is exactly what happened at 156,400.
    assert on > 156_400


def test_a_circled_numeral_is_not_a_question_number():
    """`①`.isdigit() is True and `int('①')` raises, which took down a whole batch.

    These papers print `①②③④` as sub-item markers inside a stem - measured on
    `1102_醫事檢驗師_臨床生理學與病理學` - and the skeleton walks every cell of every paper, so a
    single circled numeral raised `invalid literal for int() with base 10: '①'` and ended the run.
    """
    table = _table([("1.題幹①", "number"), ("A.甲", "opt:A"), ("B.乙", "opt:B"),
                    ("C.丙", "opt:C"), ("D.丁", "opt:D")])
    skeleton = reflow.skeleton(table)          # must not raise
    assert skeleton["complete"]
    table2 = _table([("①", ""), ("1.題幹", "number"), ("A.甲", "opt:A"), ("B.乙", "opt:B"),
                     ("C.丙", "opt:C"), ("D.丁", "opt:D")])
    assert reflow.skeleton(table2)["complete"]
    # And a hint is not claimed for it, because it is not a number a reader could count.
    assert reflow.line_table([{"text": "①", "size": 11.0}])[0][2] == ""


def test_the_prompt_carries_no_field_the_model_does_not_need():
    """A free-text field nobody needs is a way to get the answer wrong.

    Measured on one paper, same prompt, `temperature=0`, changing only the `年度` field:
    `'115'` → 3 disagreements with the skeleton, `'115 年第 2 次'` → 1, `'1152'` → 60, `''` → 52.
    Repeating one value four times gave the identical answer each time, so it is sensitivity to the
    wording, not randomness. The field was removed rather than constrained.
    """
    table = _table([("1.題幹", "number"), ("A.甲", "opt:A"), ("B.乙", "opt:B"),
                    ("C.丙", "opt:C"), ("D.丁", "opt:D")])
    messages = reflow.build_messages(table, subject="生物化學", year="1152", count=1)
    prompt = messages[1]["content"]
    assert "1152" not in prompt
    assert "生物化學" not in prompt
    assert "總格數" in prompt                  # the fields it does need are still there


# --- what counts as a figure: height, not area ---------------------------------------------
# The first version used an area floor and the controls caught it. The corpus says the two
# populations do not overlap at all, so this is a measurement rather than a threshold.

def test_a_subscript_rendered_as_an_image_is_not_a_figure():
    """Measured: `PCO₂`'s subscript is an image object 29 x 15 pt = 439 pt².

    That cleared a 400 pt² area floor, so question 23 of `1121_醫事檢驗師_臨床生理學與病理學` was
    cropped and the model said `text` at 0.99 confidence - correctly. Seventeen questions were
    wrong this way, every one in the subject that prints blood-gas values.
    """
    rows = [_row(1, y0=550, y1=567, text="23.血液氣體 pH 7.26、")]
    subscript = [{"page": 1, "x0": 321, "y0": 550, "x1": 350, "y1": 565}]     # 29 x 15
    assert vision.figure_questions([_skeleton_item(23)], rows, subscript) == []


def test_a_real_figure_is_found_by_its_height_whatever_its_area():
    """A spectrum can be wide and flat; height is what says a reader would call it a picture."""
    rows = [_row(1, y0=40, y1=60, text="1.下圖為紅外光譜")]
    wide = [{"page": 1, "x0": 40, "y0": 40, "x1": 540, "y1": 80}]            # 500 x 40
    found = vision.figure_questions([_skeleton_item(1)], rows, wide)
    assert len(found) == 1
    assert found[0]["reasons"] == ["embedded-image"]


def test_the_height_floor_sits_in_the_gap_the_corpus_measured():
    """7,190 objects are under 24 pt tall (glyphs) and 663 are 24 pt or more (figures)."""
    assert vision.MIN_FIGURE_HEIGHT == 24.0
    rows = [_row(1, y0=40, y1=60, text="1.題幹")]
    just_under = [{"page": 1, "x0": 40, "y0": 40, "x1": 140, "y1": 63.9}]
    just_over = [{"page": 1, "x0": 40, "y0": 40, "x1": 140, "y1": 64.1}]
    assert vision.figure_questions([_skeleton_item(1)], rows, just_under) == []
    assert len(vision.figure_questions([_skeleton_item(1)], rows, just_over)) == 1



# --- one alphabet, and it is the paper's most-used family -------------------------------------
# A paper can print two private-use families and only one of them labels the options. The union of
# both is what `cells_with_pages` used to take, and it is wrong on exactly the papers where it
# matters: the sub-item family sorts first, so the option marks come out `opt:E`/`opt:F` and the
# skeleton - which looks for the label `A` - finds no options in the whole paper.

def _bulleted_rows(paper):
    """The paper's cells as `cells_with_pages` sees them, one cell per printed line."""
    return [{"text": text, "size": 12.0, "page": 1, "x0": 39.0,
             "y0": index * 20.0, "y1": index * 20.0 + 14.0}
            for index, text in enumerate(paper, start=1)]


def _a_paper_with_two_families(questions=5):
    """`questions` questions, each with four options from the option family, and one question that
    also prints four sub-items from the second family.

    The shape matters: a paper prints four option labels **per question** while its sub-items
    appear only where a question asks over them, which is why the option family is the one with the
    larger total. Measured on `1001_醫事檢驗師_臨床生理學與病理學`: the option labels occur 79 times
    each and the sub-item marks 7-9 times.
    """
    paper = []
    for number in range(1, questions + 1):
        paper += [f"{number}. 下列何者正確？", "\ue18c甲", "\ue18d乙", "\ue18e丙", "\ue18f丁"]
    paper += ["下列那些正確？\ue000砂粒病毒 \ue001漢他病毒 \ue002西尼羅病毒 \ue003拉薩病毒",
              "\ue18c\ue000\ue001", "\ue18d\ue000\ue002",
              "\ue18e\ue001\ue002", "\ue18f\ue000\ue001\ue002"]
    return paper


def test_a_paper_with_two_private_use_families_labels_its_options():
    """The option family is the most-used one, and that is what the alphabet must be.

    Sorting the union puts `\ue000` first and the option marks then come out `opt:E`/`opt:F`, so no
    question of the paper parses its options at all.
    """
    alphabet = reflow.cells_with_pages(_bulleted_rows(_a_paper_with_two_families()))[2]
    assert tuple(alphabet) == (0xE18C, 0xE18D, 0xE18E, 0xE18F)
    # Negative control: the union is the eight codepoints, and the real option mark would then be
    # labelled `E` - which is the whole defect. Named here so a change back to the union fails this
    # test for the right reason rather than by accident.
    union = tuple(sorted({0xE000, 0xE001, 0xE002, 0xE003,
                          0xE18C, 0xE18D, 0xE18E, 0xE18F}))
    assert tuple(alphabet) != union
    # `canon.LABELS` is `(A,B,C,D,E,F)`, so index 4 - which is where `\ue18c` lands in the sorted
    # union - is the label `E`. That is the whole defect, spelled out.
    assert canon.LABELS[union.index(0xE18C)] == "E"


def test_the_second_family_is_still_kept_for_the_question_that_is_printed_with_it():
    """`option_alphabet_families` keeps every family, so the question stated with the second one is
    still divisible - the fix moves the *label assignment*, not the family list.

    Measured on `1001_醫事檢驗師_臨床血清免疫學與臨床病毒學` Q49/Q71: their options are expressions
    over the sub-item marks (`\ue18c\ue000\ue001`) while the marks that label the options are still
    `\ue18c`-`\ue18f`.
    """
    from qbr import repair
    families = repair.option_alphabet_families("".join(_a_paper_with_two_families()))
    assert list(families[0]) == [0xE18C, 0xE18D, 0xE18E, 0xE18F]
    assert {0xE000, 0xE001, 0xE002, 0xE003} <= {code for family in families for code in family}


# --- which engine, and which switch (the address and the switch must agree) -------------------

def test_the_default_engine_is_occamy_and_its_switch_travels_with_it():
    """The switch is asked of the *selected* engine, not written down next to the address.

    Measured on 2026-09-29 against the running servers: the same prompt with
    `chat_template_kwargs.enable_thinking=false` (the old, hard-coded spelling) spent 2.0 s and 82
    completion tokens of which 137 characters were reasoning - **byte-identical** to sending no
    switch at all, i.e. it was accepted with HTTP 200 and ignored. `reasoning_effort: none` (what
    the table holds for Occamy) spent 0.3 s and 4 tokens with an empty reasoning channel. A
    "thinking off" run that silently thinks is the expensive failure this asserts against.
    """
    body = __import__("json").loads(__import__("json").dumps(
        reflow._engines.body_for(reflow._engine(), [{"role": "user", "content": "x"}], max_tokens=8)))
    assert reflow.ENGINE_NAME == "occamy-6bit"
    assert reflow._engine()["url"] == reflow._engines.BUILTIN_ENDPOINTS["occamy-6bit"]["url"]
    assert body["reasoning_effort"] == "none"
    assert "chat_template_kwargs" not in body


def test_naming_another_engine_moves_the_address_as_well_as_the_switch(monkeypatch):
    """`QBR_REFLOW_ENGINE` must repoint the request, not only borrow the other engine's switch.

    The negative control for the test above: if the module-level address were copied in
    unconditionally, a `QBR_REFLOW_ENGINE=mtplx-35b` run would send MTPLX's switch to **Occamy's**
    address. Measured: exactly that happened - the request went to 18130 and came back refused by
    its 65536-token budget (0.1 s, 0 items read) instead of reaching 18120.
    """
    import importlib
    monkeypatch.setenv("QBR_REFLOW_ENGINE", "mtplx-35b")
    reloaded = importlib.reload(reflow)
    try:
        engine = reloaded._engine()
        assert engine["url"] == reloaded._engines.BUILTIN_ENDPOINTS["mtplx-35b"]["url"]
        body = reloaded._engines.body_for(engine, [{"role": "user", "content": "x"}], max_tokens=8)
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert "reasoning_effort" not in body
    finally:
        monkeypatch.delenv("QBR_REFLOW_ENGINE", raising=False)
        importlib.reload(reflow)


def test_a_refused_request_is_not_reported_as_an_unparsed_answer(monkeypatch):
    """The two failures need different next moves, so they must not share a label.

    Measured 2026-09-29: Occamy refuses reflow's own 80-question budget with HTTP 400
    (`Request needs 165654 context tokens (9654 prompt + 156000 max generation), but MAX_KV_SIZE is
    65536`) because the request never fit the server's context budget. `read_paper` filed that as
    `unparsed`, i.e. the same record as a model that answered and could not be read - and only one
    of those two is fixed by a bigger budget.
    """
    refusal = ("request failed: HTTP Error 400: {\"detail\":\"Request needs 165654 context tokens "
               "(9654 prompt + 156000 max generation), but MAX_KV_SIZE is 65536.\"}")
    import types
    monkeypatch.setattr(reflow, "repair", types.SimpleNamespace(mask_chrome=lambda rows: (rows, [])))
    monkeypatch.setattr(reflow, "cells_with_pages", lambda kept: (TABLE, [1], ()))
    monkeypatch.setattr(reflow, "_body_size", lambda kept: 10.0)
    monkeypatch.setattr(reflow, "_sheet_count", lambda pdf, count=None: 1)
    monkeypatch.setattr(reflow, "build_messages", lambda *a, **k: [{"role": "user", "content": "x"}])
    monkeypatch.setattr(reflow, "ask", lambda *a, **k: (None, refusal, {}, 0.1))
    result = reflow.read_paper("any.pdf", rows=[])
    assert result["admissible"] is False
    assert result["report"]["error"] == "request-failed"
    assert "MAX_KV_SIZE" in result["report"]["detail"]

    # Negative control: a model that answered unreadably keeps the old label.
    monkeypatch.setattr(reflow, "ask", lambda *a, **k: (None, "I could not read this page.", {}, 4.0))
    assert reflow.read_paper("any.pdf", rows=[])["report"]["error"] == "unparsed"
