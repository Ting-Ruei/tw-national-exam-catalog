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

_OFFSET_WHITESPACE = frozenset(" \t\u00a0")

#: Characters that sit **inside a formula the paper prints as an offset**, but for which Unicode has
#: no raised or lowered form. These are the reason a whole run comes out flat.
#:
#: This is the measured cause of the class a reviewer kept blocking and the model kept naming
#: `SUPERSCRIPT_FLATTENED`. On `1051_藥師(一)_藥劑學(包括生物藥劑學)` the geometry says six runs are
#: superscripts and the table refuses all six; the only characters blocking them are `.` (seven
#: occurrences) and `/` (one). The papers print `C=80e-0.35t` with `-0.35t` raised, and there is no
#: superscript period in Unicode, so `_mappable_offset` returns False, `_offset_kind` returns None,
#: and the reader is shown `e-0.35t` - a formula that has lost its layer. Same for a subscript
#: fraction `1/2`.
#:
#: What is **not** here matters as much: `dextrose`, `P`, `D`, `M`, `β` are also geometrically
#: lowered and also refused, and they are *not* this class - they are small print at a slightly low
#: baseline (table entries, single-letter abbreviations), and calling them subscripts would be
#: inventing an offset. The distinction is not the letter vs the punctuation: it is that the
#: punctuation sits **inside a run that is otherwise a formula** (it contains a digit), while those
#: words are not formulas at all. Hence the second condition in `refused_offsets` below.
_OFFSET_BLOCKING_PUNCTUATION = frozenset(".,/·×÷")

_SUP_MAP = {"0": "\u2070", "1": "\u00b9", "2": "\u00b2", "3": "\u00b3", "4": "\u2074",
            "5": "\u2075", "6": "\u2076", "7": "\u2077", "8": "\u2078", "9": "\u2079",
            "+": "\u207a", "-": "\u207b", "\u2212": "\u207b", "=": "\u207c",
            "(": "\u207d", ")": "\u207e", "n": "\u207f"}
_SUB_MAP = {"0": "\u2080", "1": "\u2081", "2": "\u2082", "3": "\u2083", "4": "\u2084",
            "5": "\u2085", "6": "\u2086", "7": "\u2087", "8": "\u2088", "9": "\u2089",
            "+": "\u208a", "-": "\u208b", "\u2212": "\u208b", "=": "\u208c",
            "(": "\u208d", ")": "\u208e"}

#: Superscript Latin letters. The papers raise whole variable names, not only digits:
#: `Cp=Ｂe⁻ᵏᵗ−Ａe⁻ᵏᵃᵗ` (pharmacokinetics, every year) and `0.23t` in an exponent. Without these the
#: run contains a letter, fails the all-characters-mappable rule below, and the formula is printed
#: flat - which is what a reviewer reported, repeatedly, across subjects.
#:
#: What is *not* here is as deliberate as what is. A letter is listed only when Unicode has a real
#: superscript form for it; nothing is approximated by markup, a caret or a raised digit. That keeps
#: the existing contract intact by construction rather than by a special case: `41.` still cannot
#: convert (`.` has no form), and `HbA1c` still cannot (there is no subscript `c`), so neither of the
#: two regressions this table's strictness was introduced to prevent can come back through it.
_SUP_LETTERS = {
    "a": "\u1d43", "b": "\u1d47", "c": "\u1d9c", "d": "\u1d48", "e": "\u1d49",
    "f": "\u1da0", "g": "\u1d4d", "h": "\u02b0", "i": "\u2071", "j": "\u02b2",
    "k": "\u1d4f", "l": "\u02e1", "m": "\u1d50", "n": "\u207f", "o": "\u1d52",
    "p": "\u1d56", "r": "\u02b3", "s": "\u02e2", "t": "\u1d57", "u": "\u1d58",
    "v": "\u1d5b", "w": "\u02b7", "x": "\u02e3", "y": "\u02b8", "z": "\u1dbb",
}
#: Subscript Latin letters, same rule. `null` in `Rh_null` is one of these - the paper sets it 5.15pt
#: below the baseline of `Rh` and it is legible as a subscript in the rendered page, so `Rhₙᵤₗₗ` is
#: the reading and `Rhnull` was the defect. The absent `c` is what keeps `HbA1c` as printed.
_SUB_LETTERS = {
    "a": "\u2090", "e": "\u2091", "h": "\u2095", "i": "\u1d62", "j": "\u2c7c",
    "k": "\u2096", "l": "\u2097", "m": "\u2098", "n": "\u2099", "o": "\u2092",
    "p": "\u209a", "r": "\u1d63", "s": "\u209b", "t": "\u209c", "u": "\u1d64",
    "v": "\u1d65", "x": "\u2093",
}
_SUP_MAP = dict(_SUP_MAP, **_SUP_LETTERS)
_SUB_MAP = dict(_SUB_MAP, **_SUB_LETTERS)


def _span_centre(span):
    box = span.get("bbox") or (0, 0, 0, 0)
    return (float(box[1]) + float(box[3])) / 2.0


def _body_centre(body, *, fallback=0.0):
    """Where the line's baseline sits, measured from the body spans that carry ink.

    Whitespace-only spans are left out, and that is a statement about paper rather than a
    threshold. A space has no ink, so it has no baseline to speak of; a run of spaces at the body
    size can sit anywhere on the sheet and still be the body size. This corpus prints exactly
    that: `1041_醫事檢驗師_臨床血液學與血庫學` puts a two-and-a-half-point space span at
    `x=420.4` and `y=35.69` while the question it belongs to is set at `y≈48`. Averaging it in
    with the real text lifts the measured centre by about a point - and one point is the whole
    margin between reading `Leᵃ` and dropping it, because the offset thresholds are ~1.9pt. The
    measured effect: `anti-Le` `a` moves from dcy -0.99 (missed) to -2.42 (read as the
    superscript the paper prints), and across the medical-technologist papers this reads 34 more
    runs without changing which sizes are called the body.

    Two readings of the same line would otherwise disagree about the same glyph - the cells view
    puts the stray space in a cell of its own, the line view keeps it in the body - so this is also
    what keeps `extract_cells_a` and `extract_lines_a` saying the same thing.
    """
    inked = [span for span in body if (span.get("text") or "").strip()]
    measured = inked or body
    if not measured:
        return fallback
    return sum(_span_centre(span) for span in measured) / len(measured)


def _body_size(spans):
    """The line's body type size, chosen by how much ink each size carries.

    The body of a line is the size that most of it is set in, and the honest measure of "most of
    it" is **width of ink**, not the largest size present and not a count of characters.

    Taking the largest was wrong, and it is why a reviewer saw flat formulas rather than raised
    ones. A paper prints its question number and its option markers **larger than its body text**
    (`55.` and `C.` at 12.96pt against 11.04pt of prose), so "largest" picks the marker, every
    span of real text looks smaller than the body, and the line is read as though its entire text
    were an offset. Measured on `1081_藥師(一)_藥劑學與生物藥劑學` Q55: with the largest size as the
    body, the prose fragment `digoxin 100 mg` sits at dcy -1.98 and crosses the -1.9 threshold -
    a whole phrase read as a superscript. Nothing caught it only because the phrase contains
    letters, which the offset table then refused; the moment that table grew letters, the paper's
    prose started turning into superscripts (`tissue`, `volume`, `plateau`, 3,259 runs in a
    partial sweep). The apparent choice between reading the formulas and keeping the prose was an
    artifact of the wrong body.

    Counting characters is wrong too, in a way that is easy to miss. In `C₇H₁₅SO₃⁻` the
    multi-digit subscripts (`7`, `15`, `3`) accumulate more characters than the four body glyphs
    they hang from, so the body would come out as the subscript size and the formula would invert.
    Width does not have that failure: the subscripts are set small *and* are only three glyphs, so
    `10.83` carries 28.9pt of ink against their 21.1pt.

    Both engines answer it. poppler reports no type size at all (its `size` is always 0.0), but its
    word boxes carry the same evidence in their width and height, which is why the rule is stated
    over the box rather than over the font.

    When every span shares one size - the common case - this returns it, so nothing changes for
    lines that have no offsets at all.
    """
    ink = {}
    for span in spans:
        box = span.get("bbox") or (0, 0, 0, 0)
        size = round(float(span.get("size") or 0.0), 2)
        ink[size] = ink.get(size, 0.0) + abs(float(box[2]) - float(box[0]))
    if not ink:
        return 0.0
    return max(ink.items(), key=lambda pair: pair[1])[0]


def _mappable_offset(text, kind):
    """Whether this run is one the offset table for *this kind* can express, in full.

    The table is the one the run will actually be transliterated with, and that is the whole
    point. Checking "one of the two tables" instead lets `1c` through - `1` is in the subscript
    table and `c` is in the *superscript* one - and `1c` is exactly the run the `HbA₁c` contract
    forbids converting, because there is no subscript `c`. A character that the run's own table
    cannot express means the run would come out half-converted, which is the failure this rule
    exists to prevent.

    Whitespace is admitted, and it is not a concession. A raised run can hold a space where the
    paper spaced a two-character exponent - `Ａe⁻ᵏᵃᵗ` is stored as the single span `-ka t` - and
    refusing the run for that space prints the formula flat. A space has no offset form and needs
    none: it travels *with* the run it sits inside, and the offset is recorded in the glyphs.

    At least one character must be one the table does change, or nothing is being said: a lone
    space would otherwise become a "superscript" of itself.

    Measured over pharmacist and medical-technologist papers, admitting whitespace adds exactly one
    run - `-ka t` - and refuses `41.`, `1c` and `HbA` exactly as before.
    """
    table = _SUP_MAP if kind == "sup" else _SUB_MAP
    mappable = False
    for char in text:
        if char in table:
            mappable = True
        elif char not in _OFFSET_WHITESPACE:
            return False
    return mappable


def _offset_direction(span, *, body_centre, body_size):
    """`"sup"`, `"sub"`, or None from geometry alone - before asking whether it can be spelled.

    Split out of `_offset_kind` because two different questions had been fused into one answer.
    "Where does this run sit" is a fact about the page; "can Unicode spell that" is a fact about the
    offset table. Fusing them threw the first away whenever the second said no, and the first is
    exactly the evidence a flattened formula needs - measured on the pharmacist paper, six runs are
    geometrically offsets and the table can spell none of them. Returning the direction even when
    the table refuses is what lets `refused_offsets` report the run instead of silently printing it
    flat. The three size/shift tests are the ones documented in the old docstring, unchanged.
    """
    size = float(span.get("size") or 0.0)
    text = (span.get("text") or "").strip()
    if not body_size or not size or size >= body_size * _OFFSET_MAX_SIZE_RATIO:
        return None
    if not text:
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

    When the mapping refuses, the run's *direction* is still known - `refused_offsets` reads it
    through `_offset_direction` and reports the run, so the information is not lost even though this
    function's answer is None.
    """
    text = (span.get("text") or "").strip()
    kind = _offset_direction(span, body_centre=body_centre, body_size=body_size)
    if kind is None:
        return None
    # The run is an offset geometrically; now ask whether it can be *said* as one. Refusing here
    # leaves the run exactly as the paper printed it rather than half-converted, which is the rule
    # `41.` and `HbA1c` depend on.
    return kind if _mappable_offset(text, kind) else None


def refused_offsets(spans):
    """Runs the page's geometry calls offsets and the offset table cannot say, with their address.

    Returns a list of `{"text", "kind", "blockers", "bbox", "size", "why"}`, one per run.

    This is evidence a later stage can act on, and it has to be gathered **here**, where the page
    still exists. Once the text has been read, `e-0.35t` (a formula that lost its layer) and
    `e-0.35t` (a formula the paper printed flat) are the same string - no text-only rule can tell
    them apart, which is why `disputes.py` cannot detect this class and why this function exists.
    It is the same shape as `lost_glyphs`: a defect whose evidence is on the paper, reported at the
    position it occurs, with the character never guessed at.

    The run is returned **only when it looks like a formula** - it contains a digit, and every
    character blocking it is one Unicode prints no offset for but a formula contains. A run blocked
    by an ordinary letter (`dextrose`, `P`, `D`, `M`, `β`) is small print at a low baseline, not a
    flattened offset, and reporting it would make the class fire on every table in the corpus.
    Measured on the pharmacist paper above: six runs kept (`-1.5t`, `-1.386t`, `-0.0866t`, `-0.1t`,
    `-0.46t`, `1/2`), five refused (`dextrose`, `P`, `D`, `M`, `β`).
    """
    if not spans:
        return []
    body_size = float(_body_size(spans) or 0.0)
    body = [span for span in spans
            if float(span.get("size") or 0.0) >= body_size * _BODY_SIZE_RATIO]
    if not body:
        return []
    body_centre = _body_centre(body)
    found = []
    for span in spans:
        text = (span.get("text") or "").strip()
        kind = _offset_direction(span, body_centre=body_centre, body_size=body_size)
        if kind is None or not text:
            continue
        if _mappable_offset(text, kind):
            continue
        table = _SUP_MAP if kind == "sup" else _SUB_MAP
        blockers = [char for char in text
                    if char not in table and char not in _OFFSET_WHITESPACE]
        if not blockers:
            continue
        # The run has to be a formula for this to be a flattened offset rather than small print.
        if not any(char.isdigit() for char in text):
            continue
        if not all(char in _OFFSET_BLOCKING_PUNCTUATION for char in blockers):
            continue
        found.append({"text": text, "kind": kind, "blockers": blockers,
                      "bbox": list(span.get("bbox") or (0, 0, 0, 0)),
                      "size": float(span.get("size") or 0.0),
                      "why": "offset-table-cannot-express"})
    return found


def refused_offsets_of_page(spans):
    """`refused_offsets` grouped per visual line, so a caller can say which line it was on."""
    out = []
    for line in group_visual_lines(spans):
        for item in refused_offsets(line):
            box = item["bbox"]
            out.append(dict(item, y0=min(float(box[1]), float(box[3]))))
    return out


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

# Two spans are the same draw when their boxes agree to within this many points. Measured on the
# duplicate draws: the fake-bold overlays sit 0.24pt apart horizontally and the doubled question
# numerals sit 0.00pt apart, while genuine neighbours - the next numeral, the next option - are
# never closer than the width of a character. A tolerance of one point therefore separates a
# redraw from a neighbour with two orders of magnitude of room on the redraw side and no way to
# reach a real neighbour on the other.
_REDRAW_TOLERANCE = 1.0

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
    biggest = _body_size(spans)
    body_size = float(biggest or 0.0)
    body = [span for span in spans
            if float(span.get("size") or 0.0) >= body_size * _BODY_SIZE_RATIO]
    body_centre = _body_centre(body) if body else 0.0
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

    A span that **overlaps** its predecessor is trimmed first, by `_covered_prefix_length`: the
    overlapping characters are already in the text, and appending them again invents a character
    the paper does not carry. This is the second time this function has had to be told that two
    boxes can share a position - the first was `_gap_joins`, which stops a column gap from being
    read as no gap; this one stops a shared character from being read as two. Both are properties
    of the boxes, and neither is a property of the words.
    """
    spans = [span for span in spans if span.get("text")]
    if not spans:
        return ""
    biggest = _body_size(spans)
    body_size = float(biggest or 0.0)
    body = [span for span in spans if float(span.get("size") or 0.0) >= body_size * _BODY_SIZE_RATIO]
    if not body:
        return "".join(span.get("text", "") for span in spans)
    body_centre = _body_centre(body)
    pieces = []
    previous = None
    for span in spans:
        text = span.get("text", "")
        if previous is not None:
            if _is_a_redraw(previous, span, text):
                continue
            trimmed = _covered_prefix_length(previous, span, text)
            if trimmed:
                text = text[trimmed:]
                if not text:
                    continue
            elif not _gap_joins(previous, span):
                pieces.append(" ")
        kind = _offset_kind(span, body_centre=body_centre, body_size=body_size)
        pieces.append(_transliterate(text, kind) if kind else text)
        previous = span
    return "".join(pieces)


def _is_a_redraw(previous, span, text, *, tolerance=_REDRAW_TOLERANCE):
    """True when `span` sits inside `previous` and carries text that is already there.

    A **geometry** rule with one text test on top, and the text test is what makes it safe: a
    span is dropped only when its characters are provably present in the line already, so
    dropping it cannot lose anything.

    These papers draw some runs more than once - a faked bold sets the same string two to four
    times at slightly different offsets - and they draw some question numbers twice at the very
    same spot. Read end to end, each extra draw becomes an extra character. Measured on
    `1031_醫師(二)_醫學(四)`, whose question numbers are bare two-column numerals:

    * question 3 arrives as span `3` at x=[52.68, 58.17] followed by span `3` at
      x=[52.68, 58.17] - the same box twice, read as `33`;
    * question 34 arrives as `34` at x=[47.16, 58.17] followed by `4` at x=[52.68, 58.17] -
      the second inside the first, read as `344`.

    Both were fatal, because a question number that is not the successor of the one before it
    is rejected by the numbering rule and the rest of the paper is dropped. Before this rule
    that paper read as **2 questions out of 80**; with it, 33 do - and the remainder are a later,
    separate defect, not this one.

    The test is `inside` rather than `coincident` so that both shapes are caught, and the text
    half is what keeps a real repetition: a paper that genuinely prints `3 3` puts the two
    glyphs at different x positions, so the second is not inside the first, and a word that
    genuinely repeats - `常常`, `慢慢` - likewise has its second copy starting where the first
    ends, not inside it. Only a draw lying within the draw before it, carrying characters that
    are already in the line, is dropped.
    """
    if not text:
        return False
    left = previous.get("bbox") or (0, 0, 0, 0)
    right = span.get("bbox") or (0, 0, 0, 0)
    if float(right[0]) < float(left[0]) - tolerance:
        return False
    if float(right[2]) > float(left[2]) + tolerance:
        return False
    return text.strip() in previous.get("text", "")


def _covered_prefix_length(previous, span, text):
    """How many characters of `span` are already in the text, having been read from `previous`.

    A **geometry** rule, in the same class as `_gap_joins`: it asks where two boxes sit, never
    what they say. It exists because this reader lays every span end to end, and on these papers
    a long run is often emitted as several spans that share their boundary character - the printer
    draws the same glyph twice, once at the end of one span and once at the start of the next, so
    the shared character was being read twice and `血液中` came out as `血液液中`, `下列` as
    `下列列`.

    Both shapes are visible on `1131_醫事檢驗師_臨床血液學與血庫學` (113 年第 2 次):

    * A **seam**: span `…尤其是貧血。血液` at x=[215.40, 435.35] then span `液中沒有發現對抗紅血球`
      at x=[424.32, 545.27]. The `液` of the first sits at [424.32, 435.35], the `液` of the second
      at [424.32, 434.40]. One character, two boxes - and appending them made `血液液`.
    * An **overlay**: the header `科目名稱：臨床血液學與血庫` is drawn four times by the fake-bold
      printer, at x0 33.96 / 33.96 / 34.20 / 34.20 and y alternating by 0.24pt. Every span of an
      overlay starts left of the one before it, because it is the same run redrawn, not a
      continuation.

    Only the seam is trimmed, because only the seam has the property that says so: the following
    span starts **right** of the previous one and overlaps its tail. An overlay's spans march left
    as often as right, so the `right[0] <= left[0]` guard rejects them, and the fake-bold text
    survives intact. Without that guard the header read `科目名稱科目名稱科目名稱科目名稱：臨床血液學與血庫庫學`
    - deleting real characters is worse than the duplication being removed.

    The count is a rounding of geometry, not a fitted threshold: characters are laid out at a
    uniform pitch inside a span, so the covered width over the per-character width is how many
    characters are covered, and coverage of less than half a character is left alone - which is
    what keeps a subscript merely overhanging its host from losing its first character. Coverage
    of the *whole* span is left alone too: that is a redraw, not a seam.

    Evidence that this is our defect and not the paper's: PyMuPDF's own `get_text("text")` returns
    `血液中` once, and engine B (poppler, which does its own overlap handling) reads the paper with
    zero doubled characters. Two independent readings agreed; only this function disagreed.

    This was not cosmetic. The affected papers had their **question numbers** doubled as well -
    `72` read as `722` - and a number that is not the successor of the one before it is rejected
    by the numbering rule, so the run stops there and the rest of the paper is dropped. That is
    the whole of the `count-mismatch` refusal on the six `醫師(二)` papers, which is why they
    looked like six unrelated broken papers rather than one broken reader.
    """
    if not text:
        return 0
    left = previous.get("bbox") or (0, 0, 0, 0)
    right = span.get("bbox") or (0, 0, 0, 0)
    covered = float(left[2]) - float(right[0])
    if covered <= 0.0:
        return 0
    if float(right[0]) <= float(left[0]):
        # The following span does not start to the right of the previous one, so it is not a
        # continuation of it - it is the same run drawn again. Leave it whole.
        return 0
    width = float(right[2]) - float(right[0])
    if width <= 0.0:
        return 0
    per_character = width / len(text)
    if per_character <= 0.5:
        return 0
    count = int(round(covered / per_character))
    if count >= len(text):
        # The whole span already sits inside the previous one: a redraw, not a seam.
        return 0
    return count


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
                    # Gathered while the spans still exist. `read_spans` folds a raised run back
                    # into its host line so the formula reads as one line - which is right, and is
                    # also what destroys the evidence that the run *was* raised. A run the offset
                    # table cannot spell is therefore recorded here, at the one moment its geometry
                    # is visible; after this the reader's string and a genuinely flat formula are
                    # indistinguishable.
                    "flattened_offsets": refused_offsets(spans),
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
