"""The table crop a reading asked for: located from the reading's own lines, or not cut at all.

The owner's report (2026-09-24) is why this exists: `moex:115020:305:0403:1:question:q065` prints a
four-row table as text, the extraction flattens it into one run-on string, and the reviewer's screen
showed that string - no crop had ever been cut, because `vision.figure_questions` triggers on an
embedded image object or on option markers printed with nothing after them and a text table has
neither.

Nothing here decides what a table is. The *reading* reports it (`ai_findings.TABLE_LINES_RULE`, quoted
back verbatim in `finding["transcription"]["table_lines"]`), and these tests pin both halves of that
contract: the reading's lines are what triggers the crop, and the band that gets cut is exactly the
measured lines the reading quoted. Every control is the same paper with the reading changed, so a
failure here is about the trigger and not about the page.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import crop_run_figures  # noqa: E402
from qbr import ai_findings, canon, extract, repair, reread, vision  # noqa: E402

#: The paper the synthetic PDF prints: a stem line, then a table of four columns and four rows. The
#: header's last column ends in a period, and the reading below quotes it as a comma - one character
#: out of seventeen, which is the size of the disagreement a real reading has with the extraction:
#: the paper prints `AUC（μg·h/mL）` on `q065` and the extraction stores `AUC (μg．h/mL)`.
HEADLINE = "65. Which dosage form gives the highest AUC?"
COLUMNS = [("Form", 72), ("Route", 200), ("Dose", 330), ("AUC.", 430)]
TABLE_ROWS = [["Tablet", "Oral", "100", "40"],
              ["Solution", "Oral", "100", "50"],
              ["Solution", "Intravenous", "25", "50"]]

#: What a reading of that table reports, verbatim.
QUOTED = ["Form Route Dose AUC,",
          "Tablet Oral 100 40",
          "Solution Oral 100 50",
          "Solution Intravenous 25 50"]

#: The same table reported a *cell* at a time: the reading quotes the header as one line and then
#: the four cells of each data row as four entries. This is the shape measured on the site trial
#: (2026-09-24, 2 of 5 table questions, e.g. `moex:106100:305:33:1:question:q070`, where the reading
#: reported `CYP2D6` / `10` / `1` for the one printed row `CYP2D6  10  1`): compared entry by entry
#: `containment('CYP2D6', 'CYP2D6 10 1')` is 1.0 and the reverse is 0.31, so every one of those
#: entries is "not found" and the table got no crop.
SPLIT_QUOTED = ["Form Route Dose AUC,", "Tablet", "Oral", "100", "40",
                "Solution Oral 100 50", "Solution Intravenous 25 50"]

#: The control for that: the same reading with one digit of the first data row wrong (`99` for the
#: printed `40`). Every piece it quotes is a piece of the printed row - `Tablet`, `Oral` and `100`
#: are all inside it and `Tablet Oral 100` is a *prefix* of it - so a join that located by pieces,
#: or that accepted one side of the containment, would bind this row to the page's `Tablet Oral
#: 100 40`. What refuses it is the whole joined run: measured, `TabletOral10099` matches
#: `TabletOral10040` in 13 of its 15 characters, 0.867 in **both** directions where a single quoted
#: line already needs 0.9, and no run of the reading that contains `99` is any row on the page.
WRONG_QUOTED = ["Form Route Dose AUC,", "Tablet", "Oral", "100", "99",
                "Solution Oral 100 50", "Solution Intravenous 25 50"]

#: `moex:106100:305:33:1:question:q070`'s reading, verbatim: nine entries for a table the paper
#: prints as five lines (three headings and two data rows). Kept as the reading gave it, markup
#: included - `canon.comparable` folds `<sub>` away, and the stored `table_lines` are the reading's
#: own words, so the identity of the crop is unchanged by how it was located.
ENZYME_QUOTED = ["代謝酵素", "最大排除速率(V<sub>max</sub>)，mg/h",
                 "Michaelis-Menten常數(K<sub>M</sub>)，mg/L",
                 "CYP2D6", "10", "1", "CYP3A4", "100", "50"]

#: The same question as the site's paper prints it (2026-09-24): the stem over three lines, the
#: table's header over three, its two data rows, and the four options. The header is the shape the
#: reading cannot see - the rightmost column's heading is printed over two lines, placed *around* the
#: line carrying the other two columns' headings:
#:
#:     Michaelis-Menten常數(KM)，                 the rightmost column's heading, first line
#:     代謝酵素   最大排除速率(Vₘₐₓ)，mg/h        the other two columns' headings
#:     mg/L                                       the rightmost column's heading, second line
#:
#: so the reading's third heading is the paper's **first and third** table lines, which are not
#: neighbours on the page, and the paper's order of the three headings is not the reading's order of
#: them. Both are what `q070` came back `not-found` for.
ENZYME_STEM = ["70.某藥在體內之排除完全經由CYP2D6及CYP3A4兩種酵素之代謝作用，已知CYP2D6及CYP3A4對此藥之代謝",
               "參數如下表，當藥物之穩定狀態血中濃度為0.1mg/L時，經由CYP2D6及CYP3A4代謝之排除速率約占總排除",
               "速率分別為何？"]
#: The table as `[(x, text), ...]` per printed line, in the order the page prints them. The first and
#: third lines carry the rightmost column alone, which is why they are measured as lines of their own
#: with the other columns' line between them. The page prints `Vmax` where the paper prints `Vₘₐₓ`
#: (see `synthetic_enzyme_paper`).
ENZYME_TABLE = [[(400, "Michaelis-Menten常數(KM)，")],
                [(72, "代謝酵素"), (260, "最大排除速率(Vmax)，mg/h")],
                [(400, "mg/L")],
                [(72, "CYP2D6"), (260, "10"), (400, "1")],
                [(72, "CYP3A4"), (260, "100"), (400, "50")]]
ENZYME_OPTIONS = ["A.82%及18%", "B.50%及50%", "C.33%及67%", "D.18%及82%"]


def synthetic_paper(path: Path) -> str:
    """A one-page paper whose question 65 body is a table printed as text."""
    import pymupdf
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 100), HEADLINE, fontsize=10)
    for index, row in enumerate([[label for label, _ in COLUMNS]] + TABLE_ROWS):
        y = 130 + 17 * index
        for (_, x), text in zip(COLUMNS, row):
            page.insert_text((x, y), text, fontsize=10)
    document.save(str(path))
    document.close()
    return str(path)


def synthetic_enzyme_paper(path: Path) -> str:
    """A one-page paper shaped like q070: stem, three-line header, two data rows, four options.

    Built here rather than read from the site's own paper, and the shape is what matters: the paper
    prints `CYP2D6  10  1` as **one** line (the extraction joins the three cells at the same height)
    while the reading reports it as three entries, and it prints the header's rightmost column over
    two lines around the other two columns' heading, which is why one entry of the reading is two
    lines of the page. The CJK text needs a font the PDF can round-trip (`china-ss`), because a paper
    whose own text does not survive extraction could not tell a located row from a missing one, and
    the page is wider than the papers are because the stem lines are long: a line printed past the
    page's right edge comes back truncated, and a truncated stem could not be told from a located row
    either.

    The paper prints the second heading's subscript as the Unicode characters `Vₘₐₓ`; this page prints
    `Vmax`, because the font it round-trips with carries no subscript letters at all. `canon.comparable`
    folds the paper's characters and the reading's `<sub>` markup to the same text, and the test
    asserts that fold directly, so nothing here depends on a font the machine may not have.
    """
    import pymupdf
    document = pymupdf.open()
    page = document.new_page(width=780, height=842)
    y = 130
    for text in ENZYME_STEM:
        page.insert_text((72, y), text, fontsize=10, fontname="china-ss")
        y += 20
    for line in ENZYME_TABLE:
        for x, text in line:
            page.insert_text((x, y), text, fontsize=10, fontname="china-ss")
        y += 20
    for text in ENZYME_OPTIONS:
        page.insert_text((72, y), text, fontsize=10, fontname="china-ss")
        y += 20
    document.save(str(path))
    document.close()
    return str(path)


def measured_lines(pdf: str):
    """The paper's own printed lines, as the crop pass measures them."""
    lines, _ = repair.mask_chrome(extract.extract_lines_a(pdf))
    return [row for row in lines if float(row["y0"]) > 110.0]


def band_of(rows):
    return [round(min(float(row["x0"]) for row in rows), 1),
            round(min(float(row["y0"]) for row in rows), 1),
            round(max(float(row["x1"]) for row in rows), 1),
            round(max(float(row["y1"]) for row in rows), 1)]


def queue_row(key="moex:synthetic:115020:305:question:q065", number=65):
    return {"candidate_key": key, "question_number": number, "image_refs": []}


def cut(rows, readings, pdf, root):
    return crop_run_figures.table_crops_for_rows(
        rows, readings, queue_root=str(root), crops_root=str(root / "review-ui" / "crops"),
        paper_of=lambda row: pdf)


def test_a_reported_table_is_cut_at_the_lines_the_reading_quoted(tmp_path):
    """The band is the measured lines the reading quoted, not a guess around them.

    Asserted against the page's own geometry (`extract.extract_lines_a`) rather than against pixels:
    the crop's box must be the union of those four lines' boxes, so a crop that is a slice of the
    table, or one that swallows the stem above it, fails here. This is also the control for the
    join: a reading that quotes one printed row per entry must be cut exactly as it was before
    entries could be joined, so its box is compared with the page, not with the reading.
    """
    pdf = synthetic_paper(tmp_path / "paper.pdf")
    measured = measured_lines(pdf)
    assert len(measured) == 4, "合成卷的四行表格必須被量成四行"
    expected = band_of(measured)

    rows = [queue_row()]
    readings = {rows[0]["candidate_key"]: {"lines": list(QUOTED)}}
    refs, records = cut(rows, readings, pdf, tmp_path)

    assert records[0].get("error") is None, records[0]
    assert records[0]["file"] == "q065_paper-table.png"
    assert len(rows[0]["image_refs"]) == 1
    ref = rows[0]["image_refs"][0]
    assert ref["asset_role"] == "figure-crop"
    assert ref["label"] == "paper-table"
    assert ref["table_lines"] == QUOTED
    assert ref["box"] == expected, (ref["box"], expected)
    assert ref["path"] == "review-ui/crops/paper/q065_paper-table.png"

    from PIL import Image
    path = tmp_path / "review-ui" / "crops" / "paper" / "q065_paper-table.png"
    assert path.is_file()
    with Image.open(path) as image:
        # The render is the band plus `crop_figure`'s 1 pt pad on every side, at 200 dpi.
        assert abs(image.width - (expected[2] - expected[0] + 2) * 200 / 72) <= 2, image.size
        assert abs(image.height - (expected[3] - expected[1] + 2) * 200 / 72) <= 2, image.size


def synthetic_two_page_table(path: Path) -> str:
    """同一張表被頁界切成兩半：第 1 頁印表頭與前兩列，第 2 頁印第 3 列。

    跨頁截圖的缺陷形狀就是這個（owner 2026-09-25：「跨頁截圖一定會錯」）：表在紙上跨頁，而一張
    截圖只截得到一頁。
    """
    import pymupdf
    document = pymupdf.open()
    first = document.new_page(width=595, height=842)
    first.insert_text((72, 100), HEADLINE, fontsize=10)
    for index, row in enumerate([[label for label, _ in COLUMNS]] + TABLE_ROWS[:2]):
        y = 130 + 17 * index
        for (_, x), text in zip(COLUMNS, row):
            first.insert_text((x, y), text, fontsize=10)
    second = document.new_page(width=595, height=842)
    last = TABLE_ROWS[2]
    for (_, x), text in zip(COLUMNS, last):
        second.insert_text((x, 130), text, fontsize=10)
    document.save(str(path))
    document.close()
    return str(path)


def test_a_table_that_spans_a_page_break_is_stitched_into_one_crop(tmp_path):
    """跨頁的表要縫成一張：兩頁各自一個檔案的話，審題者永遠只看到半張表。

    量到的實例：`moex:115020:305:0403:1:question:q065` 的截圖只有第 13 頁的框，題目的選項在第 14
    頁、沒有任何一張蓋到。這一條的負對照就是以前的行為——那時 `refs` 是**兩個**（`-p1.png`、
    `-p2.png`），所以 `len(payload) == 1` 在舊碼上不成立。
    """
    from PIL import Image
    pdf = synthetic_two_page_table(tmp_path / "paper.pdf")
    rows = [queue_row()]
    rows[0]["question_number"] = 65
    readings = {rows[0]["candidate_key"]: {"lines": list(QUOTED)}}
    refs, records = cut(rows, readings, pdf, tmp_path)

    assert records[0].get("error") is None, records[0]
    payload = rows[0]["image_refs"]
    assert len(payload) == 1, payload
    ref = payload[0]
    assert ref["pages"] == [1, 2], ref
    assert ref["page"] == 1
    assert ref["raw_ref"] == "q065_paper-table.png"
    assert "跨第 1、2 頁" in ref["description"], ref["description"]
    path = tmp_path / "review-ui" / "crops" / "paper" / "q065_paper-table.png"
    assert path.is_file()
    with Image.open(path) as image:
        one = ref["page_boxes"]["1"]
        two = ref["page_boxes"]["2"]
        # 這一張是**整題**縫的（不是只有表格那四行的框）：所以它一定比兩頁各自的框加起來高，
        # 而且不會高到把整頁都吞進來（`reread.crop_rows` 每一塊的外框只多 `vision.MARGIN`）。
        floor = ((one[3] - one[1]) + (two[3] - two[1])) * 200 / 72
        wide = max(box[2] - box[0] for box in (one, two)) * 200 / 72
        assert image.height >= floor, (image.height, floor)
        assert image.height <= floor + 90, (image.height, floor)
        assert image.width >= wide, (image.width, wide)
        assert image.width <= wide + 40, (image.width, wide)


def test_a_legacy_single_page_crop_of_a_cross_page_question_is_replaced(tmp_path):
    """站上量到的形狀：這一題跨頁，可是它的表截圖是**單頁**的舊紀錄（沒有 `pages`）。

    `moex:115020:305:0403:1:question:q065` 就是這樣：截圖只有第 13 頁的框，選項在第 14 頁、沒有
    任何一張蓋到。只比對「讀法是不是同一份」會讓這種舊截圖永遠留著，所以跨頁的題目要重切。
    """
    pdf = synthetic_two_page_table(tmp_path / "paper.pdf")
    rows = [queue_row()]
    rows[0]["question_number"] = 65
    rows[0]["image_refs"] = [{"label": "paper-table", "page": 1, "box": [72.0, 130.0, 440.0, 164.0],
                              "raw_ref": "q065_paper-table.png", "table_lines": list(QUOTED),
                              "source": "qbr_vision_crop", "asset_role": "figure-crop",
                              "path": "review-ui/crops/paper/q065_paper-table.png"}]
    readings = {rows[0]["candidate_key"]: {"lines": list(QUOTED)}}
    refs, records = cut(rows, readings, pdf, tmp_path)

    assert not records[0].get("unchanged"), records[0]
    payload = rows[0]["image_refs"]
    assert len(payload) == 1 and payload[0]["pages"] == [1, 2], payload
    assert "整題縫成一張" in payload[0]["description"], payload[0]["description"]


def test_a_table_the_reading_split_into_cells_is_still_the_same_table(tmp_path):
    """A reading that reports a printed row's cells as separate entries still names the same table.

    The positive half of the join: the paper, the band and the four measured lines are the ones
    `synthetic_paper` prints, and only the reading changed - it quotes `Tablet`, `Oral`, `100`, `40`
    instead of the one printed row `Tablet Oral 100 40`. The crop must be the union of the paper's
    four lines, exactly the box the whole-row reading gets, because a join that located the row
    *and* the rows around it would be a wider crop than the reading asked for.

    Run against the locator before the join existed this is `not-found:Tablet｜Oral｜100`.
    """
    pdf = synthetic_paper(tmp_path / "paper.pdf")
    measured = measured_lines(pdf)
    assert len(measured) == 4, "合成卷的四行表格必須被量成四行"
    expected = band_of(measured)

    rows = [queue_row()]
    readings = {rows[0]["candidate_key"]: {"lines": list(SPLIT_QUOTED)}}
    refs, records = cut(rows, readings, pdf, tmp_path)

    assert records[0].get("error") is None, records[0]
    assert len(rows[0]["image_refs"]) == 1
    ref = rows[0]["image_refs"][0]
    assert ref["box"] == expected, (ref["box"], expected)
    assert ref["table_lines"] == SPLIT_QUOTED, "存進 ref 的必須仍是讀法自己的字"
    assert (tmp_path / "review-ui" / "crops" / "paper" / "q065_paper-table.png").is_file()


def test_a_joined_row_that_disagrees_with_the_page_is_reported_and_not_cut(tmp_path):
    """The control: a join may compare, it may not guess.

    One digit of the reading's first data row is wrong (`99` where the paper prints `40`), and
    everything about this reading is otherwise the one above: every piece it quotes is a piece of
    the printed row (`Tablet`, `Oral`, `100` are all inside it, `Tablet Oral 100` is a prefix of it),
    so nothing but the joined run itself stands between this reading and a crop over a table it is
    wrong about - measured, `TabletOral10099` matches the printed `TabletOral10040` in 13 of its 15
    characters, 0.867 in both directions against the 0.9 the test above already requires of a single
    quoted line. Nothing may be cut: no reference is added to the row and no crop is written to disk.

    It is the control for *both* ways the reading and the page can disagree about a line, because
    both are ways of binding rows the reading is wrong about: the wrong digit is neither matched by
    the joined run (one entry per printed row) nor paid for by the length of the rows printed beside
    it - a split over several rows is accepted only when the quoted line cuts into one piece per row
    with each row agreeing with its own piece, and no cut agrees with `Tablet Oral 100 99` beside
    `Solution Oral 100 50`.
    """
    pdf = synthetic_paper(tmp_path / "paper.pdf")
    rows = [queue_row()]
    readings = {rows[0]["candidate_key"]: {"lines": list(WRONG_QUOTED)}}
    refs, records = cut(rows, readings, pdf, tmp_path)

    assert refs == {}
    assert records[0]["error"].startswith("not-found"), records[0]
    named = records[0]["error"].split(":", 1)[1].split("｜")
    assert named and all(part in WRONG_QUOTED for part in named), (
        "報告要指出是哪一條讀法引的字找不到：%s" % records[0]["error"])
    assert rows[0]["image_refs"] == []
    assert not (tmp_path / "review-ui" / "crops").exists(), "一個位元組都不切"


def test_the_q070_shape_is_located_from_the_readings_own_entries(tmp_path):
    """`moex:106100:305:33:1:question:q070`'s reading, against a paper of the same shape.

    Nine entries for five printed lines: three headings, then `CYP2D6` / `10` / `1` and `CYP3A4` /
    `100` / `50`, which the paper prints as two rows of three columns - under a header the paper
    prints in another order than the reading quoted it, with the reading's third heading wrapped over
    the paper's first and third table lines. The nine entries must locate the whole table: the box is
    the union of those five measured lines and of nothing else, so the stem above them and the four
    options below them stay outside the crop, while `table_lines` still carries the reading's nine
    entries verbatim, because they are the identity of the crop and not an artefact of how it was
    located.

    Run against the locator before the join existed this is `not-found:CYP2D6｜10｜1`; run against
    the version that had the join and still required the reading's order, one entry per printed line,
    it is `not-found:Michaelis-Menten常數(K<sub`. The reading is not what changed.
    """
    pdf = synthetic_enzyme_paper(tmp_path / "paper.pdf")
    measured = measured_lines(pdf)
    assert [row["text"] for row in measured] == [
        "70.某藥在體內之排除完全經由CYP2D6及CYP3A4兩種酵素之代謝作用，已知CYP2D6及CYP3A4對此藥之代謝",
        "參數如下表，當藥物之穩定狀態血中濃度為0.1mg/L時，經由CYP2D6及CYP3A4代謝之排除速率約占總排除",
        "速率分別為何？",
        "Michaelis-Menten常數(KM)，",
        "代謝酵素 最大排除速率(Vmax)，mg/h",
        "mg/L",
        "CYP2D6 10 1", "CYP3A4 100 50",
        "A.82%及18%", "B.50%及50%", "C.33%及67%", "D.18%及82%"], [row["text"] for row in measured]
    # The paper's own characters and the reading's markup are the same text: the site's paper prints
    # this heading with Unicode subscripts (`Vₘₐₓ`), which this page cannot, so the fold is asserted
    # where it belongs - on the two spellings.
    assert canon.comparable("最大排除速率(V\u2098\u2090\u2093)，mg/h") == canon.comparable(
        "最大排除速率(V<sub>max</sub>)，mg/h")

    table, options = measured[3:8], measured[8:]
    rows = [queue_row(key="moex:106100:305:33:1:question:q070", number=70)]
    readings = {rows[0]["candidate_key"]: {"lines": list(ENZYME_QUOTED)}}
    refs, records = cut(rows, readings, pdf, tmp_path)

    assert records[0].get("error") is None, records[0]
    assert len(rows[0]["image_refs"]) == 1
    ref = rows[0]["image_refs"][0]
    assert ref["box"] == band_of(table), (ref["box"], band_of(table))
    assert ref["box"][1] > max(float(row["y1"]) for row in measured[:3]), "題幹不可以進框"
    assert ref["box"][3] < min(float(row["y0"]) for row in options), "選項不可以進框"
    assert ref["table_lines"] == ENZYME_QUOTED
    assert (tmp_path / "review-ui" / "crops" / "paper" / "q070_paper-table.png").is_file()


def test_no_reading_no_crop_even_though_the_body_is_a_table(tmp_path):
    """The control: the same tabular page, with nothing reporting a table, gets **no** crop.

    The two assertions above the crop are the gap this rewrote the trigger for: `figure_questions`
    fires on an embedded image object or on option markers printed with nothing after them, and a
    table printed as text has neither, so the page measures and the machinery stays silent - which
    is why `moex:115020:305:0403:1:question:q065` reached the reviewer as one run-on line.
    """
    pdf = synthetic_paper(tmp_path / "paper.pdf")
    cells, _ = repair.mask_chrome(extract.extract_cells_a(pdf))
    band = reread.band_rows(cells, 65)
    items = [{"number": 65, "stem": HEADLINE,
              "options": {"A": "0.1", "B": "0.2", "C": "0.3", "D": "0.4"}}]
    assert vision.figure_questions(items, band, []) == []
    assert vision.crop_plan(items, band) == []

    rows = [queue_row()]
    refs, records = cut(rows, {}, pdf, tmp_path)
    assert refs == {}
    assert records == []
    assert rows[0]["image_refs"] == [], "沒有讀法說它是表格時，列不可以被加上裁切"


def test_a_reading_that_reports_no_table_yields_no_crop(tmp_path):
    """The other half of the control, at the level the reading arrives on: a reading that
    transcribed this very question without calling its body a table cuts nothing - the reading is
    the authority on what is a table, so its silence is final, not a missing datum to be guessed."""
    pdf = synthetic_paper(tmp_path / "paper.pdf")
    ui = tmp_path / "review-ui"
    ui.mkdir(parents=True)
    row = queue_row()
    (ui / "candidates.jsonl").write_text(json.dumps(row, ensure_ascii=False) + "\n",
                                         encoding="utf-8")
    ai_findings.append(str(ui / "question_ai_findings.jsonl"), {
        "candidate_key": row["candidate_key"], "question_number": 65,
        "finding": {"verdict": "OK", "what": "NONE", "transcription": {
            "stem": HEADLINE, "options": {"A": "0.1", "B": "0.2", "C": "0.3", "D": "0.4"}}}})

    readings = crop_run_figures.table_readings(str(tmp_path))
    assert readings == {}
    refs, records = cut([row], readings, pdf, tmp_path)
    assert refs == {} and records == []
    assert row["image_refs"] == []


def test_lines_that_are_not_on_the_page_are_reported_and_not_cut(tmp_path):
    """A reading whose lines cannot be found is reported. A crop of the wrong region still looks like
    a picture of a table, which is the worst outcome available here."""
    pdf = synthetic_paper(tmp_path / "paper.pdf")
    rows = [queue_row()]
    readings = {rows[0]["candidate_key"]: {"lines": ["Nothing Like This", "Nor This Either"]}}
    refs, records = cut(rows, readings, pdf, tmp_path)
    assert refs == {}
    assert records[0]["error"].startswith("not-found"), records[0]
    assert rows[0]["image_refs"] == []


def test_a_superseded_reading_replaces_the_crop_instead_of_adding_one(tmp_path):
    """Re-runnable: the same reading is not cut twice, and a newer reading's lines replace the old
    ref rather than stacking beside it."""
    pdf = synthetic_paper(tmp_path / "paper.pdf")
    rows = [queue_row()]
    key = rows[0]["candidate_key"]
    readings = {key: {"lines": list(QUOTED)}}
    cut(rows, readings, pdf, tmp_path)
    first = list(rows[0]["image_refs"])

    _, records = cut(rows, readings, pdf, tmp_path)
    assert records[0]["unchanged"] is True, records[0]
    assert rows[0]["image_refs"] == first, "同一份讀法不可以產生第二份裁切"

    # A reading that quotes only the data rows is a different reading of the same table.
    readings = {key: {"lines": QUOTED[1:]}}
    cut(rows, readings, pdf, tmp_path)
    table_refs = [ref for ref in rows[0]["image_refs"] if ref.get("label") == "paper-table"]
    assert len(table_refs) == 1
    assert table_refs[0]["table_lines"] == QUOTED[1:]
    assert table_refs[0]["box"] == band_of(measured_lines(pdf)[1:])


def test_a_table_is_cut_even_when_an_option_owns_a_crop(tmp_path):
    """Requirement 2: an option crop and the stem's table are different evidence and both are kept."""
    pdf = synthetic_paper(tmp_path / "paper.pdf")
    rows = [queue_row()]
    key = rows[0]["candidate_key"]
    rows[0]["image_refs"] = [{"asset_role": "option-image", "option_key": "A",
                              "path": "review-ui/crops/paper/q065_option_A.png", "exists": True}]
    readings = {key: {"lines": list(QUOTED)}}
    cut(rows, readings, pdf, tmp_path)
    roles = [(ref.get("asset_role"), ref.get("option_key")) for ref in rows[0]["image_refs"]]
    assert roles == [("option-image", "A"), ("figure-crop", None)], roles


def test_the_crop_is_one_contiguous_block_of_the_lines_the_reading_claims():
    """What replaced the reading-order guard: the shape of the block, not the order of it.

    The first version of this locator required every quoted line to be found *after* the one before
    it in print order. That guard is gone: `moex:106100:305:33:1:question:q070`'s paper prints the
    table's three header lines in another order than the reading quoted them, and requiring the
    reading's order refused exactly the crop the reading had asked for (see
    `test_the_q070_shape_is_located_from_the_readings_own_entries`). What replaces it is the shape of
    the block, and the three properties asserted here are that shape:

      * every measured line inside the box is a line some quoted entry claimed,
      * every quoted entry claims at least one line, and
      * the box's edges are the outermost claimed lines - so the crop is exactly the union of the
        lines the reading named.

    It is stronger than the guard it replaces in the one respect that matters: a chain of strictly
    increasing rows was free to *skip* a row between two of its steps, so a reading quoting two lines
    with a third printed between them was cut over that third line. That is now refused.

    The page is a heading, the row under it, and the same heading printed again under the next
    question. Quoting the two lines in print order, and quoting them in the other order now that
    order is not required, must locate the same block - and the controls below are what must still be
    refused, or not cut at all.
    """
    heading = {"page": 1, "x0": 70.0, "y0": 100.0, "x1": 400.0, "y1": 112.0, "text": "Form Route"}
    first = {"page": 1, "x0": 70.0, "y0": 120.0, "x1": 400.0, "y1": 132.0, "text": "Tablet Oral"}
    later = {"page": 1, "x0": 70.0, "y0": 140.0, "x1": 400.0, "y1": 152.0, "text": "Form Route"}

    def located(lines, quoted):
        """The band, and the measured lines that fall inside it."""
        bands, complaint = vision.quoted_lines_region(lines, quoted)
        assert complaint == "", complaint
        box = bands[1]
        return box, [row for row in lines
                     if box[1] <= (float(row["y0"]) + float(row["y1"])) / 2.0 <= box[3]]

    box, inside = located([heading, first, later], ["Form Route", "Tablet Oral"])
    assert box == (70.0, 100.0, 400.0, 132.0), box
    assert [row["text"] for row in inside] == ["Form Route", "Tablet Oral"], inside
    # The same two lines quoted in the other order: the page's second `Form Route` is below them and
    # nothing quoted it, so it stays out of the crop - the relaxation is about order, never about how
    # far the block reaches.
    assert located([heading, first, later], ["Tablet Oral", "Form Route"]) == (box, inside)

    # A stem line printed *above* the table is outside it for the same reason.
    stem = {"page": 1, "x0": 70.0, "y0": 80.0, "x1": 400.0, "y1": 92.0,
            "text": "70. Which dosage form gives the highest AUC?"}
    assert located([stem, heading, first], ["Tablet Oral", "Form Route"])[0] == (70.0, 100.0, 400.0,
                                                                                132.0)

    # A line printed between two lines the reading quoted, and quoted by nobody: the crop would have
    # to cover it, so the lines are not one block of the page and nothing is cut.
    below = {"page": 1, "x0": 70.0, "y0": 140.0, "x1": 400.0, "y1": 152.0, "text": "Tablet Oral"}
    gap = {"page": 1, "x0": 70.0, "y0": 120.0, "x1": 400.0, "y1": 132.0,
           "text": "71. The next question's stem"}
    bands, complaint = vision.quoted_lines_region([heading, gap, below],
                                                  ["Form Route", "Tablet Oral"])
    assert bands == {} and complaint.startswith("not-a-block"), (bands, complaint)

    # And a heading with no second line is not a table: the rule defines one as a heading *and* rows,
    # so a single quoted line has no region that could honestly be called the table.
    bands, complaint = vision.quoted_lines_region([heading, first, later], ["Form Route"])
    assert bands == {} and complaint.startswith("not-a-table"), complaint


def test_the_search_space_is_the_questions_own_rows():
    """`within_band` keeps the question's lines and drops the neighbour's, which is what stops a
    quoted row from matching the same row printed under the next question."""
    band = [{"page": 1, "y0": 100.0, "y1": 112.0}, {"page": 1, "y0": 200.0, "y1": 212.0}]
    lines = [{"page": 1, "y0": 100.0, "y1": 112.0, "text": "mine"},
             {"page": 1, "y0": 150.0, "y1": 162.0, "text": "the stem of this question"},
             {"page": 1, "y0": 260.0, "y1": 272.0, "text": "the next question"},
             {"page": 2, "y0": 100.0, "y1": 112.0, "text": "another page"}]
    kept = vision.within_band(lines, band)
    assert [row["text"] for row in kept] == ["mine", "the stem of this question"]


def test_table_lines_of_reads_the_reading_and_nothing_else():
    """The channel's shapes: the reading's list passes through, and every other shape is "no table"."""
    reading = {"finding": {"transcription": {"table_lines": ["a b", "c d"]}}}
    assert ai_findings.table_lines_of(reading) == ["a b", "c d"]
    assert ai_findings.table_lines_of({"finding": {"transcription": {"table_lines": []}}}) == []
    assert ai_findings.table_lines_of({"finding": {"transcription": {"table_lines": ["only one"]}}}) == []
    assert ai_findings.table_lines_of({"finding": {"transcription": {"table_lines": "a b"}}}) == []
    assert ai_findings.table_lines_of({"finding": {"verdict": "OK"}}) == []
    assert ai_findings.table_lines_of({"finding": None}) == []
    assert ai_findings.table_lines_of({}) == []
    assert ai_findings.table_lines_of(None) == []


def test_table_readings_takes_the_latest_reading_not_the_latest_record(tmp_path):
    """The audit pass writes records for the same key and they hold no reading; a later reading that
    reports no table retracts the earlier one."""
    queue = tmp_path / "review-ui"
    queue.mkdir(parents=True)
    (queue / "candidates.jsonl").write_text("", encoding="utf-8")
    path = str(queue / "question_ai_findings.jsonl")
    key = queue_row()["candidate_key"]
    ai_findings.append(path, {"candidate_key": key, "finding": {
        "verdict": "OK", "transcription": {"stem": "s", "table_lines": QUOTED}}})
    assert crop_run_figures.table_readings(str(tmp_path))[key]["lines"] == QUOTED
    ai_findings.append(path, {"candidate_key": key,
                              "finding": {"verdict": "DEFECT", "what": "GLYPH_DAMAGE"}})
    assert crop_run_figures.table_readings(str(tmp_path))[key]["lines"] == QUOTED
    ai_findings.append(path, {"candidate_key": key, "finding": {
        "verdict": "OK", "transcription": {"stem": "s"}}})
    assert crop_run_figures.table_readings(str(tmp_path)) == {}


if __name__ == "__main__":
    unittest.main()
