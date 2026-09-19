"""Stage D - dual deterministic extraction with two genuinely independent engines.

Engine A: PyMuPDF (MuPDF, C implementation) - blocks/lines/spans with device-space bboxes.
Engine B: pdfminer (pure-Python interpreter + its own layout analyser) - LTPage objects
          with bboxes. Different CMap handling, different layout analysis, no shared code.

(Protocol §8 Stage D warns: do not call two wrappers around the same backend
"independent". A third path, PyPDF's extractor, is kept as an optional cross-check but
is not used for the verdict because its plain mode duplicates/garbles CJK runs on these
files - measured, see reports/notes_stage_c_d.md.)

Outputs are observations, never truth (Protocol §4.2). Agreement is the evidence that
buys a cheap verification state; disagreement routes the unit to the escalation ladder.
No model inference happens in this module.
"""

import os
import re
import re as _re

# ---------------------------------------------------------------- engine A (PyMuPDF)
#
# A line is read as a *sequence of spans*, not as one blob of text. Merging them with `""`
# before anything else loses the one piece of information that says what the paper means:
# `[Na` at 11pt and `+]` at 5.5pt concatenate to `[Na+]`, which is right, but `B` at 11pt and
# `2` at 5.5pt set *below the baseline* also concatenate to `B2` - and `B₂` (a vitamin) and
# `B2` are different things.
#
# So each span is transliterated in place, according to whether its box sits above or below
# the body of its line, and the row keeps the result. Downstream code sees `[Na⁺]` and `B₂`
# and does not have to recover typography from geometry.
#
# The discriminator is the span's vertical shift off the line's baseline. Size alone is not one:
# this corpus sets its chemical subscripts at **0.82-0.85** of the body size (7.4pt against 9.0pt,
# 10.8 against 12.8), which a 0.75 ratio gate rejects, while the ordinary smaller text that must
# *not* be transliterated - `A.`/`B.` option markers at 0.82, English words in a smaller face at
# 0.84 - sits in the same ratio band at **zero** shift. Measured over the four categories:
# 145,639 spans smaller than their line's body, and of those with a ratio of 0.78 or more, 68,431
# sit at dcy 0 or -1 and 10,684 sit at +2 to +4, where dcy is the origin-y difference from the
# body's own origin. The two groups are separated by shift, not by size, so the ratio is only a
# coarse floor and the shift decides.
#
# The cost of the old gate is exact and was measured: `CH3(CH2)11OR` was extracted as `CH3(CH2)11OR`
# with the digits on the baseline, so every chemistry formula in the corpus lost its subscripts -
# `B2` and `B₂` came out identical, which is the one thing the span-wise reading exists to prevent.
_OFFSET_MAX_SIZE_RATIO = 0.90   # a coarse floor for "smaller than the body"; the shift decides
#: The ratio that decides which spans *are* the body. Deliberately tighter than the floor above,
#: because the body's own centre is the baseline every offset is measured against: widening this
#: to the floor would let a 0.82-ratio subscript pull the baseline toward itself, halving the
#: measured shift and hiding the very spans the floor was widened to catch. The old 0.75 was
#: doing both jobs and so could do neither well.
_BODY_SIZE_RATIO = 0.75
#: Thresholds for the loose style (7.4pt against 9pt; ratio 0.82, so not below `_BODY_SIZE_RATIO`).
_OFFSET_SUP_MAX_DCY = -1.9      # measured: -2.0 for `₂`; -1.2/-1.0 for `-1` and the option markers
_OFFSET_SUB_MIN_DCY = 1.9       # measured: +2.0 for `₂`,`₃`; smaller text with a letter form is at +1.0
                                #   and below, and `A.`/`B.` markers sit at -1.0
#: Thresholds for the tight style (5.5pt against 11pt; ratio 0.50). The shifts are smaller in
#: absolute points simply because the glyphs are, so these are the values measured on that style:
#: -3.87 for `³⁻`, +1.66 for `₄`. A single pair of numbers cannot serve both, which is why the
#: body-relative branch above chooses which pair applies.
_OFFSET_SUP_MAX_DCY_SMALL = -1.0
_OFFSET_SUB_MIN_DCY_SMALL = 0.8

_SUP_MAP = {"0": "\u2070", "1": "\u00b9", "2": "\u00b2", "3": "\u00b3", "4": "\u2074",
            "5": "\u2075", "6": "\u2076", "7": "\u2077", "8": "\u2078", "9": "\u2079",
            "+": "\u207a", "-": "\u207b", "\u2212": "\u207b", "=": "\u207c",
            "(": "\u207d", ")": "\u207e", "n": "\u207f"}
_SUB_MAP = {"0": "\u2080", "1": "\u2081", "2": "\u2082", "3": "\u2083", "4": "\u2084",
            "5": "\u2085", "6": "\u2086", "7": "\u2087", "8": "\u2088", "9": "\u2089",
            "+": "\u208a", "-": "\u208b", "\u2212": "\u208b", "=": "\u208c",
            "(": "\u208d", ")": "\u208e"}


def _span_centre(span):
    box = span.get("bbox") or (0, 0, 0, 0)
    return (float(box[1]) + float(box[3])) / 2.0


def _offset_kind(span, *, body_centre, body_size):
    """`"sup"`, `"sub"`, or None for a span that sits on the baseline.

    A run is an offset only when it is smaller than the line's body, shifted well clear of the
    baseline, **and made only of characters that have an offset form**. All three are required,
    and the third is what keeps ordinary small text out: this corpus sets `A.`/`B.` option
    markers and whole English phrases in a smaller face, and they sit at ratios the size floor
    admits. Measured over the four categories - 145,639 spans smaller than their line's body -
    the runs at |shift| >= 2.5 that are entirely offset characters are the chemical formulas
    (`₃` in `CH₃`, `₂` in `H₂O`, `₄` in `NH₄Cl`, `ₚ` in `Kₚ`, `⁻` in `10⁻⁵`), and 1,617 of the
    1,664 that are not are option markers and prose. Requiring the mapping also means a run this
    rule cannot express is left exactly as the paper printed it, rather than half-converted.
    """
    size = float(span.get("size") or 0.0)
    text = (span.get("text") or "").strip()
    if not body_size or not size or size >= body_size * _OFFSET_MAX_SIZE_RATIO:
        return None
    # Every character has to have an offset form. `any` was tried first and it corrupts text: a
    # span mixing mappable and unmappable characters gets *half* converted, and measured over the
    # corpus that turned the digits of ordinary numbers into superscripts - `41.` became `⁴¹.`,
    # `1c` became `₁c`, `-0.2t` became `⁻⁰.²t`, 240 spans in all. Requiring all of them means a run
    # this rule cannot express is left exactly as the paper printed it. The cost is measurable and
    # was measured: 4,546 runs convert and 4,550 do not, and the ones that do not are the corpus's
    # option markers (`A.`/`B.`/`C.`/`D.`, which must not convert), `®`, and the chemical symbols
    # that have no subscript form at all (`max`, `p`, `M`, `Cr`). Leaving those alone is honest;
    # half-converting them is not.
    if not text or not all(char in _SUP_MAP or char in _SUB_MAP for char in text):
        return None
    delta = _span_centre(span) - body_centre
    # Two subscript styles, and the papers use both. The tight one is 5.5pt against 11pt
    # (`3-`...`3` in `[PO₄³⁻]`, shift -3.87 / +1.66), where the absolute shift is small but the
    # size ratio is far below the body. The loose one is 7.4pt against 9pt (`CH₃(CH₂)₁₁OR`, shift
    # ±3.5), where the ratio is only 0.82 and the shift is what gives it away. A rule with one
    # threshold has to fail one of them - which is exactly what happened: at 0.75/±1.0 the 9pt
    # formulas were flattened, and at 0.90/±2.0 the 11pt ones were. So the test is the union, and
    # each branch carries the thresholds measured for its own style.
    small = size < body_size * _BODY_SIZE_RATIO
    if delta <= (_OFFSET_SUP_MAX_DCY_SMALL if small else _OFFSET_SUP_MAX_DCY):
        return "sup"
    if delta >= (_OFFSET_SUB_MIN_DCY_SMALL if small else _OFFSET_SUB_MIN_DCY):
        return "sub"
    return None


# A horizontal gap wider than this fraction of the type size separates two items; a gap
# narrower than it is inside a word. Measured across 14 papers: the two populations are
# cleanly separated. Glyphs that belong together sit at 0.0-0.09 of the size (a PUA option
# marker against its own word, `[Na` against its superscript `⁺`), while separate columns and
# separate options sit at 0.5-5.0 (58.0pt against a 12pt face on the two-column psychology
# paper; 24.5pt between the third and fourth option). The threshold is placed in the empty
# space between them, not at either edge, so neither a tight word nor a wide column is at
# risk. Getting this wrong in either direction is silent: a missing space glues `答案Ｄ` to
# `Ｂ` and destroys the answer grid, and an invented space splits `中毒現象`.
_ZERO_WIDTH = 0.5

# Two spans on one row are two items when the space between them is wider than this share of the
# text size. The measurement behind it is in `group_cells`: 4,172 super/subscript joins at a gap
# of 0-4 points, and 928 column separations at ten points or more, over two years of two
# categories. Half a body height sits between the two groups rather than inside either.
_COLUMN_GAP_RATIO = 0.5

# A raised run may stand a little clear of its host and still be a superscript. Measured: the
# joins at a gap of 1-4 points are these, and the ones at ten points and above are columns, so
# the allowance reaches most of the way to a column gap without crossing it.
_SUPERSCRIPT_GAP_RATIO = 0.9


def _transliterate(text, kind):
    table = _SUP_MAP if kind == "sup" else _SUB_MAP
    return "".join(table.get(ch, ch) for ch in text)


def _gap_joins(previous, following, *, ratio=None):
    """True when the space between two spans is inside a word rather than between items."""
    left = previous.get("bbox") or (0, 0, 0, 0)
    right = following.get("bbox") or (0, 0, 0, 0)
    gap = float(right[0]) - float(left[2])
    size = max(float(previous.get("size") or 0.0), float(following.get("size") or 0.0))
    if size <= 0.0:
        return gap <= 0.0
    return gap <= size * (ratio if ratio is not None else _COLUMN_GAP_RATIO)


def group_cells(spans):
    """The spans of one visual line -> its cells, split where a column gap falls.

    A visual line is one row of the page, and a row of these papers often carries more than one
    item: two options side by side (`缺鐵性貧血…　地中海貧血…`), or all four of a short set
    (`BFU-E  CFU-E  pronormoblast  reticulocyte`). The gap between those items is the same
    column gap that `read_spans` already measures to decide whether to put a space in, so the
    boundary is drawn from a fact about the page and not from what the words mean.

    This is what makes a line splittable, which the reader of the page needs in order to be
    able to say "these four items are the four options": without a cell the only handle is the
    whole line, and the line can be assigned to one option and no more, so the other three are
    either lost or counted twice.

    A cell set smaller than the rest of the line is kept as its own cell even when the gap
    would join it, because that is the geometry of a super- or subscript and the model is the
    one that decides what it is.
    """
    spans = [span for span in spans if span.get("text")]
    if not spans:
        return []
    biggest = max(spans, key=lambda span: float(span.get("size") or 0.0))
    body_size = float(biggest.get("size") or 0.0)
    body = [span for span in spans
            if float(span.get("size") or 0.0) >= body_size * _BODY_SIZE_RATIO]
    body_centre = (sum(_span_centre(span) for span in body) / len(body)) if body else 0.0
    cells, current = [], [spans[0]]
    for previous, span in zip(spans, spans[1:]):
        # A small glyph is joined to what precedes it when it is *raised or lowered against it*,
        # which is what a super- or subscript is, and the gap alone is then the wrong test: a
        # formula fragment sits at the very edge of its host (`HCO` ends at 88.1, the `3` begins
        # there) while a raised run can also stand a few points clear of it. Measured over two
        # years of two categories: 4,172 small-glyph joins at a gap of 0 to 4 points - the
        # superscripts - and 928 at ten points or more, which are columns of a row and not
        # offsets of a glyph.
        #
        # The size test on its own was overfitting, and this is what it cost: joining whenever
        # *either* neighbour was small let a 7 pt fragment standing between two options read as
        # no gap at all, so an 87-point column was crossed and options A, B and C came back as
        # one cell. The offset test is what a reader does - the smaller run belongs to the larger
        # run it is set against - and it needs no threshold on the gap beyond the column gap that
        # `_gap_joins` already measures.
        offset = abs(_span_centre(span) - _span_centre(previous))
        raised = offset > 0.15 * max(body_size, 0.01)
        if _gap_joins(previous, span) or (raised and _gap_joins(previous, span, ratio=_SUPERSCRIPT_GAP_RATIO)):
            current.append(span)
        else:
            cells.append(current)
            current = [span]
    cells.append(current)
    return cells


def _cells_of_page(page):
    """Every cell on a page, as (page_index, [spans])."""
    for line_index, spans in enumerate(group_visual_lines(_spans_of_page(page))):
        for cell in group_cells(spans):
            yield cell



def read_spans(spans):
    """One line's spans -> its text, with super/subscripts written as such.

    Returns the assembled text. A span with no smaller sibling to compare against is taken
    at face value, so a line set entirely in one size comes through unchanged.

    Spans are joined with `""` or `" "` according to the gap between them, not always with
    `""`. PyMuPDF reports each column of a two-column paper as its own span on the same
    visual line, and gluing them back together would turn four separate options - `田野研究`
    `大眾和專業評論` … - into one unreadable word. The same test keeps a superscript against
    its host, because the gap inside `[Na⁺]` is zero.
    """
    spans = [span for span in spans if span.get("text")]
    if not spans:
        return ""
    biggest = max(spans, key=lambda span: float(span.get("size") or 0.0))
    body_size = float(biggest.get("size") or 0.0)
    body = [span for span in spans if float(span.get("size") or 0.0) >= body_size * _BODY_SIZE_RATIO]
    if not body:
        return "".join(span.get("text", "") for span in spans)
    body_centre = sum(_span_centre(span) for span in body) / len(body)
    pieces = []
    for index, span in enumerate(spans):
        if index and not _gap_joins(spans[index - 1], span):
            pieces.append(" ")
        kind = _offset_kind(span, body_centre=body_centre, body_size=body_size)
        text = span.get("text", "")
        pieces.append(_transliterate(text, kind) if kind else text)
    return "".join(pieces)


def _spans_of_page(page):
    """Every text span on a page, in the order PyMuPDF reports it, minus the invisible ones.

    A span with no width occupies no space on the page, so it cannot be part of any word and it
    cannot separate two words either - it is a placeholder the typesetting program left behind.
    Measured over the corpus: every such span is drawn in the font `ZWAdobeF`, whose name says
    what it is, and each holds one of the strings `B`, `P` or `PB` - the marks of the Adobe
    glyph-naming convention, never content.

    They have to be dropped here rather than tolerated later, because a zero-width span sits at a
    point and the column gap is measured between span edges. A placeholder standing between two
    options therefore reads as *no gap at all*, and the options are joined into one cell. Measured
    on `1012_醫事檢驗師_生物化學與臨床生化學` question 34: a `ZWAdobeF` span at x=91.6 and another
    at x=183.1 bridged an 87-point column, and options A, B and C came back as a single cell.
    """
    raw = page.get_text("dict") or {}
    spans = []
    for block_index, block in enumerate(raw.get("blocks", ())):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                if not span.get("text"):
                    continue
                box = span.get("bbox") or (0, 0, 0, 0)
                if abs(float(box[2]) - float(box[0])) < _ZERO_WIDTH:
                    continue
                spans.append((block_index, span))
    return spans


def group_visual_lines(spans, *, min_overlap=0.5):
    """Spans -> visual lines, by vertical overlap rather than by PyMuPDF's line breaks.

    PyMuPDF's own `lines` are not the reader's lines on these papers. A line whose box rises
    above its neighbours - because it carries a superscript - is emitted as the *start of the
    next line*, and `PO₄³⁻` arrives as three separate lines whose y0 differ by five points.
    Sorting those by y0 interleaves them with the main text and the formula is destroyed.

    Two spans are on the same visual line when their boxes overlap vertically by at least
    half the height of the shorter one. That test is what a reader's eye does, it is
    indifferent to which direction a run is offset, and it keeps genuinely separate lines
    apart: question 46's wrapped `中毒現` and `象。` share no vertical extent at all.

    Returns a list of lists of spans, each already ordered left to right.
    """
    lines = []
    for block_index, span in spans:
        box = span.get("bbox") or (0, 0, 0, 0)
        top, bottom = float(box[1]), float(box[3])
        height = max(bottom - top, 0.01)
        placed = False
        for line in reversed(lines):
            # Deliberately *not* restricted to one block. PyMuPDF puts each fragment of
            # `C.[K⁺]－[PO₄³⁻]－[HCO₃⁻]` in a block of its own, so requiring a shared block
            # would leave the formula split exactly as before.
            overlap = min(bottom, line["bottom"]) - max(top, line["top"])
            if overlap >= min_overlap * min(height, line["height"]):
                line["spans"].append(span)
                line["top"] = min(line["top"], top)
                line["bottom"] = max(line["bottom"], bottom)
                line["height"] = max(line["height"], height)
                placed = True
                break
        if not placed:
            lines.append({"block": block_index, "spans": [span], "top": top,
                          "bottom": bottom, "height": height})
    # The lines are sorted by where they sit before being returned. Grouping by vertical overlap
    # says *which spans belong together*; it says nothing about the order of the groups, because a
    # group is created wherever its first span happens to be met and PyMuPDF does not report
    # spans in reading order. Measured on `1062_醫事檢驗師_生物化學與臨床生化學`: question 21's
    # options are printed as two columns, `A.α-胰島細胞` and `B.β-胰島細胞`, and the engine
    # reports the right-hand one first, so B was read before A - and the option reader, which
    # expects labels in the order the paper prints them, then found only three usable labels.
    # Eight such inversions were present on that one page.
    #
    # Sorting by `top` is what a reader does and is safe here for the reason the grouping is: a
    # superscript that rises above its host line overlaps it, so it is already in the same group
    # and cannot be reordered away from it.
    lines.sort(key=lambda line: (line["top"], line["bottom"]))
    for line in lines:
        line["spans"].sort(key=lambda span: float((span.get("bbox") or (0, 0, 0, 0))[0]))
    return [line["spans"] for line in lines]


def extract_lines_a(path):
    module = __import__("pymu" + "pdf")
    document = module.open(filename=path)
    rows = []
    for page_index, page in enumerate(document, start=1):
        for line_index, spans in enumerate(group_visual_lines(_spans_of_page(page))):
            text = read_spans(spans)
            if not text.strip():
                continue
            boxes = [span.get("bbox") or (0, 0, 0, 0) for span in spans]
            body = max(spans, key=lambda span: float(span.get("size") or 0.0))
            rows.append(
                {
                    "engine": "pymupdf",
                    "page": page_index,
                    "x0": min(float(box[0]) for box in boxes),
                    "y0": min(float(box[1]) for box in boxes),
                    "x1": max(float(box[2]) for box in boxes),
                    "y1": max(float(box[3]) for box in boxes),
                    "text": text,
                    "font": str(body.get("font", "")),
                    "size": float(body.get("size", 0.0) or 0.0),
                    "block": -1,
                    "line": line_index,
                }
            )
    document.close()
    return rows


def extract_cells_a(path):
    """One paper as cells: the smallest units a reader can point at, each with its own box.

    The same reading as `extract_lines_a`, stopped one step earlier. A line is one row of the
    page; a cell is one item in that row. The difference matters only where a row carries more
    than one item, which is common - two options side by side, four short options on one line -
    and is exactly where a line-level reading has no legal move to offer.

    Rows carry the same keys as `extract_lines_a` so that the masker, which decides what is page
    furniture from geometry and repetition, can be run over either.
    """
    module = __import__("pymu" + "pdf")
    document = module.open(filename=path)
    rows = []
    for page_index, page in enumerate(document, start=1):
        for line_index, spans in enumerate(group_visual_lines(_spans_of_page(page))):
            for cell_index, cell in enumerate(group_cells(spans)):
                text = read_spans(cell)
                if not text.strip():
                    continue
                boxes = [span.get("bbox") or (0, 0, 0, 0) for span in cell]
                body = max(cell, key=lambda span: float(span.get("size") or 0.0))
                rows.append(
                    {
                        "engine": "pymupdf",
                        "page": page_index,
                        "x0": min(float(box[0]) for box in boxes),
                        "y0": min(float(box[1]) for box in boxes),
                        "x1": max(float(box[2]) for box in boxes),
                        "y1": max(float(box[3]) for box in boxes),
                        "text": text,
                        "font": str(body.get("font", "")),
                        "size": float(body.get("size", 0.0) or 0.0),
                        "block": -1,
                        "line": line_index * 100 + cell_index,
                    }
                )
    document.close()
    return rows


def extract_images_a(path):
    """Native embedded image objects with page geometry (Protocol §9 preference 1).

    If a figure exists as an embedded object whose bbox corresponds to the question, the
    crop must come from that object - not from a re-rendered guess. `xref`, bbox and the
    digest of the extracted bytes are what keep the asset auditable.

    `xref` is taken from `page.get_image_info(xrefs=True)`, which reports the object and its
    placement box together, and **not** from a text block's `number`. That field is a block
    *index* within the page's own block list - it counts text blocks too - so looking an image up
    by it matched the wrong object or nothing at all. Measured on question 68 of
    `1152_醫事檢驗師_臨床血液學與血庫學`: the picture blocks carry `number` 21..30 while the page's
    images carry xref 6..15, so every `xref` this function returned was `None` and no crop could
    ever be cut from the object itself.
    """
    module = __import__("pymu" + "pdf")
    document = module.open(filename=path)
    shots = []
    for page_index, page in enumerate(document, start=1):
        try:
            placed = [entry for entry in page.get_image_info(xrefs=True)
                      if entry.get("bbox")]
        except Exception:
            placed = []
        if placed:
            # The placement list is authoritative: it is what the page actually draws, and each
            # entry carries the object it draws from.
            for entry in placed:
                box = entry["bbox"]
                xref = entry.get("xref")
                width = entry.get("width")
                height = entry.get("height")
                shots.append({
                    "page": page_index,
                    "block_number": None,
                    "x0": float(box[0]),
                    "y0": float(box[1]),
                    "x1": float(box[2]),
                    "y1": float(box[3]),
                    "xref": xref,
                    "ext": "",
                    "width": int(width) if width else None,
                    "height": int(height) if height else None,
                })
            continue
        raw = page.get_text("dict") or {}
        for block in raw.get("blocks", ()):
            if block.get("type") != 1:
                continue
            box = block.get("bbox", (0, 0, 0, 0))
            shots.append({
                "page": page_index,
                "block_number": block.get("number"),
                "x0": float(box[0]),
                "y0": float(box[1]),
                "x1": float(box[2]),
                "y1": float(box[3]),
                "xref": None,
                "ext": "",
                "width": None,
                "height": None,
            })
    document.close()
    return shots


def image_bytes_of(path, xref):
    """The embedded object itself, as its own bytes, with the suffix the file already has.

    This is the reference bank's design (`40_exports/question_bank_packages/.../assets/`: 549 of
    556 hand-cut assets are byte-identical to the picture object they came from, and none was a
    re-cut of a page). Cutting a picture out of a page renders the page around it, so the crop
    carries whatever else is printed nearby; taking the object gives the picture's own boundary,
    which is why the hand-cut assets are as small as 88x93 pixels and still whole.

    Returns `(bytes, suffix)` or `(None, reason)`.
    """
    if not xref:
        return None, "no-xref"
    module = __import__("pymu" + "pdf")
    document = module.open(filename=path)
    try:
        info = document.extract_image(int(xref))
    except Exception:
        return None, "extract-failed"
    finally:
        document.close()
    if not info or not info.get("image"):
        return None, "empty-object"
    return info["image"], "." + str(info.get("ext") or "png")


#: Formats a browser can render directly. Anything else is re-encoded before it is written.
#: The corpus contains 44 option pictures stored as JPEG 2000 (`jpx`), which PyMuPDF extracts
#: happily and no browser will display: `q053_option_A.jpx` and its siblings come back as a broken
#: image, and the reviewer sees the alt text beside an empty box. The bytes are fine; the container
#: is the problem.
WEB_IMAGE_FORMATS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "bmp"})


def web_safe_image(blob, suffix):
    """The picture's bytes in a format a browser renders, with the matching suffix.

    A crop that exists, is served with HTTP 200, and still shows nothing is the worst kind of
    defect: every check in the pipeline passes and the reviewer sees an empty box. That is exactly
    what JPEG 2000 does, so the container is normalized here rather than being discovered again in
    the UI.

    Returns `(bytes, suffix)`. An already-web format is passed through untouched, because the
    hand-cut reference bank is byte-identical to the embedded object and re-encoding would break
    that correspondence.
    """
    ext = (suffix or "").lstrip(".").lower()
    if ext in WEB_IMAGE_FORMATS:
        return blob, "." + ext
    try:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(blob)) as image:
            # JPEG 2000 in these papers is 1-bit line art; `LA` keeps the anti-aliasing that `1`
            # would throw away and that makes a chemical structure readable.
            converted = image.convert("RGBA" if image.mode in ("LA", "P", "PA") else "RGB")
            buffer = io.BytesIO()
            converted.save(buffer, format="PNG", optimize=True)
            return buffer.getvalue(), ".png"
    except Exception:
        # Unconvertible here is not a reason to lose the picture: it is written as it came so the
        # failure is visible on disk instead of silently absent.
        return blob, "." + (ext or "bin")


# --------------------------------------------------------- engine B (poppler / C++)
_XML_WORD = _re.compile(
    r'<word[^>]*\bxMin="(?P<x0>[-\d.]+)"[^>]*\byMin="(?P<y0>[-\d.]+)"'
    r'[^>]*\bxMax="(?P<x1>[-\d.]+)"[^>]*\byMax="(?P<y1>[-\d.]+)"[^>]*>(?P<text>[^<]*)</word>'
)
_XML_PAGE = _re.compile(r'<page[^>]*\bwidth="(?P<w>[-\d.]+)"[^>]*\bheight="(?P<h>[-\d.]+)"')
_XML_IMAGE = _re.compile(r'<image[^>]*\bxMin="(?P<x0>[-\d.]+)"[^>]*\byMin="(?P<y0>[-\d.]+)"[^>]*\bxMax="(?P<x1>[-\d.]+)"[^>]*\byMax="(?P<y1>[-\d.]+)"')


def poppler_xml(path, timeout=240):
    """Run `pdftotext -bbox` and return poppler's own XML rendering (C++ engine).

    `Popen` + `communicate` is used deliberately: it avoids the keyword-argument form
    that this toolchain keeps mangling into an invalid name.
    """
    import subprocess

    process = subprocess.Popen(
        ["pdftotext", "-bbox", path, "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out, err = process.communicate(timeout=timeout)
    if process.returncode != 0:
        raise RunTimeError("pdftotext failed: " + (err or b"").decode("utf-8", "replace")[:300])
    return out.decode("utf-8", "replace")


def extract_lines_b(path, **kwargs):
    """Independent second engine: poppler (C++), not MuPDF (C). Word-level geometry."""
    blob = poppler_xml(path, **kwargs)
    rows = []
    page_index = 0
    for chunk in blob.split("<page ")[1:]:
        page_index += 1
        head = _XML_PAGE.search("<page " + chunk)
        for match in _XML_WORD.finditer(chunk):
            text = (match.group("text") or "").strip()
            if not text:
                continue
            rows.append(
                {
                    "engine": "poppler",
                    "page": page_index,
                    "x0": float(match.group("x0")),
                    "y0": float(match.group("y0")),
                    "x1": float(match.group("x1")),
                    "y1": float(match.group("y1")),
                    "text": text,
                    "font": "",
                    "size": 0.0,
                    "block": -1,
                    "line": -1,
                    "page_height": float(head.group("h")) if head else 0.0,
                }
            )
    return rows


def extract_images_b(path, **kwargs):
    """Image placements reported by poppler's layout scan (device-space bboxes)."""
    blob = poppler_xml(path, **kwargs)
    shots = []
    page_index = 0
    for chunk in blob.split("<page ")[1:]:
        page_index += 1
        head = _XML_PAGE.search("<page " + chunk)
        for match in _XML_IMAGE.finditer(chunk):
            shots.append(
                {
                    "page": page_index,
                    "x0": float(match.group("x0")),
                    "y0": float(match.group("y0")),
                    "x1": float(match.group("x1")),
                    "y1": float(match.group("y1")),
                    "page_height": float(head.group("h")) if head else 0.0,
                }
            )
    return shots


# --------------------------------------------------------------------- normalisation

_FULLWIDTH_LOW = (0xFF01, 0xFF5E)
_LIGATURES = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
}
_SAFE = (
    (_re.compile(r"\r\n?"), "\n"),
    (_re.compile(r"[ \t]+\n"), "\n"),
    (_re.compile(r"\n{3,}"), "\n\n"),
    (_re.compile(r"\u3000"), " "),
)


def safe_normalize(text):
    """Comparison key only (Protocol §26 SAFE class). `raw_extraction_text` is never
    replaced by this output."""
    out = text
    for pattern, replacement in _SAFE:
        out = pattern.sub(replacement, out)
    folded = []
    for char in out:
        code = ord(char)
        if _FULLWIDTH_LOW[0] <= code <= _FULLWIDTH_LOW[1]:
            folded.append(chr(code - 0xFEE0))
        else:
            folded.append(_LIGATURES.get(char, char))
    return "".join(folded).strip()


def flatten(rows):
    ordered = sorted(rows, key=lambda r: (r["page"], round(r["y0"], 1), r["x0"]))
    return "\n".join(r["text"] for r in ordered if r["text"].strip())


# ------------------------------------------------------------ similarity / agreement
def _shingles(text, width=4, limit=80000):
    text = text[:limit]
    if len(text) < width:
        return {text} if text else set()
    return {text[i:i + width] for i in range(0, len(text) - width + 1)}


def similarity(text_a, text_b):
    """Dependency-free similarity: 4-gram shingle Jaccard plus a length factor."""
    if text_a == text_b:
        return 1.0
    if not text_a or not text_b:
        return 0.0
    left, right = _shingles(text_a), _shingles(text_b)
    union = left | right
    jaccard = len(left & right) / len(union) if union else 0.0
    length_factor = min(len(text_a), len(text_b)) / max(len(text_a), len(text_b))
    return round(0.75 * jaccard + 0.25 * length_factor, 6)


def _first_difference(a, b, window=90):
    lines_a = [x for x in a.splitlines() if x.strip()]
    lines_b = [x for x in b.splitlines() if x.strip()]
    seen = set(lines_b)
    for index, line in enumerate(lines_a):
        if line not in seen:
            other = lines_b[index] if index < len(lines_b) else ""
            return "A[%d]=%r B[%d]=%r" % (index, line[:window], index, other[:window])
    return ""


def _strip_all_whitespace(text):
    return _re.sub(r"\s+", "", text)


def _bag(text):
    counts = {}
    for char in text:
        counts[char] = counts.get(char, 0) + 1
    return counts


def _bag_similarity(left, right):
    """Order-insensitive character-multiset similarity: answers 'did both engines see
    the same glyphs?', which is the text-fidelity question. Ordering is scored
    separately because these Ghostscript-distilled print forms draw the same header
    several times, and the engines disagree about how many duplicates to emit."""
    if not left or not right:
        return 0.0
    shared = 0
    for char, count in left.items():
        other = right.get(char)
        if other:
            shared += count if count < other else other
    total = sum(left.values()) + sum(right.values())
    return round(2.0 * shared / total, 6) if total else 0.0


def _collapse_repeated_lines(text, min_repeats=2):
    """Comparison key only: drop a line that repeats the previous identical line.

    These MOEX PDFs were produced by a print form (`Producer: GPL Ghostscript`), so the
    same header/footer is drawn repeatedly in the content stream. Keeping the artefact
    in `raw_extraction_text` is required; ignoring it while comparing prevents the two
    engines' duplicate-drawing policies from being mistaken for a text disagreement.
    """
    kept = []
    previous = None
    run = 0
    for line in text.splitlines():
        if line == previous:
            run += 1
            if run >= min_repeats:
                continue
        else:
            previous = line
            run = 1
        kept.append(line)
    return "\n".join(kept)


def compare(text_a, text_b, identity=0.999, content_floor=0.999):
    """Classify cross-parser agreement, scoring text fidelity and layout separately
    (Protocol §11). Structure disagreement stays cheap to repair deterministically;
    only a real text disagreement is worth an escalation."""
    if text_a == text_b:
        return {
            "classification": "EXACT_AGREEMENT", "similarity": 1.0,
            "content_similarity": 1.0, "structure_similarity": 1.0, "first_difference": "",
        }
    left, right = safe_normalize(text_a), safe_normalize(text_b)
    if left == right:
        return {
            "classification": "SAFE_NORMALIZED_AGREEMENT", "similarity": 1.0,
            "content_similarity": 1.0, "structure_similarity": 1.0, "first_difference": "",
        }
    dense_left = _strip_all_whitespace(_collapse_repeated_lines(left))
    dense_right = _strip_all_whitespace(_collapse_repeated_lines(right))
    content_sim = _bag_similarity(_bag(dense_left), _bag(dense_right))
    structure_sim = similarity(_collapse_repeated_lines(left), _collapse_repeated_lines(right))
    if not dense_left or not dense_right:
        kind = "MISSING_IN_ONE_PARSER"
    elif content_sim >= content_floor:
        kind = "SAFE_NORMALIZED_AGREEMENT" if structure_sim >= identity else "STRUCTURAL_DISAGREEMENT"
    else:
        kind = "TEXT_DISAGREEMENT"
    return {
        "classification": kind,
        "similarity": structure_sim,
        "content_similarity": content_sim,
        "structure_similarity": structure_sim,
        "first_difference": _first_difference(dense_left, dense_right),
    }


def poppler_image_list(path, timeout=240):
    """Embedded image inventory from poppler (`pdfimages -list`)."""
    import subprocess

    process = subprocess.Popen(
        ["pdfimages", "-list", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out, err = process.communicate(timeout=timeout)
    rows = []
    for line in (out or b"").decode("utf-8", "replace").splitlines():
        if "xref:" not in line:
            continue
        fields = {"raw": line.strip()}
        page_part = line.split("page")[1].split(":")[0] if "page" in line else ""
        try:
            fields["page"] = int(page_part)
        except ValueError:
            fields["page"] = 0
        for token in line.split("xref:")[-1].split():
            key, sep, value = token.partition(":")
            if sep:
                fields[key] = value
        rows.append(fields)
    return rows


def extract_pair(path):
    """Run both engines over one PDF and report their observations plus agreement."""
    rows_a = extract_lines_a(path)
    rows_b = extract_lines_b(path)
    text_a = flatten(rows_a)
    text_b = flatten(rows_b)
    return {
        "path": os.path.abspath(path),
        "rows_a": rows_a,
        "rows_b": rows_b,
        "text_a": text_a,
        "text_b": text_b,
        "verdict": compare(text_a, text_b),
    }
