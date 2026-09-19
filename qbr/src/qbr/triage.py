"""Stage C - cheap CPU-only PDF triage (Protocol §8 Stage C, §19.3).

Answers one question before any model budget is spent: does this PDF carry a usable
native text layer, and if not, why? Classification decides whether the paper needs OCR
at all. No rasterization and no inference happen here.
"""

import hashlib
import os
import re

from . import repair
from .cjk import audit_text

DEFAULT_LIMITS = {
    "min_text_characters_per_page": 40,
    "min_pages_with_text_ratio": 0.6,
    "max_damage_ratio": 0.005,
    "max_simplified_ratio": 0.002,
    "max_anchor_gap": 2,
}

# The anchors a paper declares, counted with the same declared templates the segmenter uses.
#
# This used to be its own pattern - a number, whitespace, then `.` `、` or `．` - and that is
# the *generation-2* style only. Generation 1 prints `20 下列…` with no punctuation at all, so a
# whole generation-1 paper reported `question_anchors = 1` and the S3 gate then compared the 80
# items the segmenter found against that 1 and refused the paper with `count-mismatch`. It was
# answering a question about the wrong regex.
#
# The patterns below are deliberately the same shape the segmenter's own templates have, with
# no extra lookahead. A lookahead was tried and removed: `(?![ \t]*\d)` was meant to keep
# decimals out but also rejected `53.33歲油漆工`, which is question 53 whose stem opens with
# `33`. The segmenter accepts both and lets the continuity gate tell them apart, and doing it
# differently here produced a second opinion that disagreed with the pipeline it was auditing.
_ANCHOR = re.compile(r"(?:^|\n)[ \t]*(\d{1,3})[ \t]*[.、．]")
_ANCHOR_SPACE = re.compile(r"(?:^|\n)[ \t]*(\d{1,3})[ \t]+(?=[^ \t\u3000\n]{1,})")
# Generation 1 puts the number in a cell of its own, so the *raw* engine text has `1` alone on
# its line with the stem on the next one. `triage` reads that raw text while the segmenter reads
# the reading-order merge, which is why the same paper can raise 80 anchors in one view and none
# in the other - measured on 20 generation-1 papers, each reporting `question_anchors = 0` while
# the segmenter found 80 items, and each then refused by S3 for a count mismatch that was the
# counter's fault. All three declared styles are counted, and the style is not asserted.
_ANCHOR_LINE = re.compile(r"(?:^|\n)[ \t]*(\d{1,3})[ \t]*(?=\n)")
_YEAR_HEAD = re.compile(r"^[ \t]*\d{2,3}[ \t]*" + chr(0x5E74))          # `100 年第一次…`


def _anchors_of(text):
    """Every question number the paper appears to raise, under any declared numbering style.

    Three styles are declared and all three are counted, because which one a paper uses is not
    known before looking and a counter that knows only one of them is answering about the wrong
    regex:

      * `20.下列…` - generation 2. The `.` form also matched decimals, so `0.0311/min` and
        `89.0%` and `107.9` at the head of a line were read as questions 0, 89 and 107.
      * `20 下列…` - generation 1 after the reading-order merge.
      * `20` alone on its line - generation 1 as the raw engine emits it, number in its own cell.

    Decimals and glued stems cannot be told apart by a pattern: `53.33歲油漆工` is question 53
    whose stem begins with `33`, and `99.5秒` is a value. What distinguishes them is that a real
    question number belongs to the paper's own 1..N run, and `repair._expected_run` already
    answers exactly that question - it is the function the segmenter's continuity gate uses. The
    candidates are therefore offered to it, and only the run it accepts is returned. Reusing it
    rather than writing a second threshold here is the point: two implementations of "how many
    questions does this paper claim" would be two things to keep in step.
    """
    dotted = [int(x) for x in _ANCHOR.findall(text)]
    spaced = []
    for line in text.splitlines():
        if _YEAR_HEAD.match(line):
            continue
        match = _ANCHOR_SPACE.match(line)
        if match:
            spaced.append(int(match.group(1)))
    lined = [int(x) for x in _ANCHOR_LINE.findall(text)]

    # The style whose run reaches furthest is the paper's own, and `_expected_run` says how far
    # that is: a decimal `107.9` cannot extend the run the way real anchors do, and a style that
    # raises almost nothing (the `.` pattern on a generation-1 paper) claims almost nothing.
    best = []
    for candidate in (dotted, spaced, lined, dotted + spaced, dotted + lined, spaced + lined):
        want, _claimed = repair._expected_run(candidate)
        if not want:
            continue
        run = sorted({value for value in candidate if 1 <= value <= want})
        if len(run) > len(best):
            best = run
    return best


def sha256_file(path, block_size=1048576):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(block_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _anchor_gap(numbers):
    ordered = sorted(set(numbers))
    if len(ordered) < 2:
        return 0
    gaps = [b - a for a, b in zip(ordered, ordered[1:]) if b != a]
    return max(gaps) if gaps else 0


def triage_pdf(path, limits=None):
    import pymupdf

    bounds = dict(DEFAULT_LIMITS)
    if limits:
        bounds.update(limits)

    with open(path, "rb") as handle:
        blob = handle.read()

    document = pymupdf.open(stream=blob)
    page_count = len(document)
    page_texts = []
    word_total = 0
    image_total = 0
    image_bytes = 0
    fonts = set()
    for page in document:
        page_texts.append(page.get_text("text") or "")
        try:
            word_total += len(page.get_text("words") or ())
        except Exception:
            pass
        try:
            image_total += len(page.get_images(full=False) or ())
        except Exception:
            pass
        try:
            for info in page.get_image_info() or ():
                size = info.get("size") if hasattr(info, "get") else getattr(info, "size", 0)
                image_bytes += int(size or 0)
        except Exception:
            pass
        try:
            for entry in page.get_fonts(full=False) or ():
                fonts.add(str(entry[3] if len(entry) > 3 else entry))
        except Exception:
            pass
    document.close()

    text = "\n".join(page_texts)
    audit = audit_text(text)
    pages_with_text = 0
    for page_text in page_texts:
        if len(page_text.strip()) >= bounds["min_text_characters_per_page"]:
            pages_with_text += 1
    ratio_pages = pages_with_text / max(1, page_count)
    anchors = _anchors_of(text)
    gap = _anchor_gap(anchors)

    reasons = []
    if ratio_pages >= bounds["min_pages_with_text_ratio"]:
        if audit["damage_ratio"] > bounds["max_damage_ratio"]:
            klass = "LEGACY_ENCODING"
            reasons.append("damage_ratio=%.4f" % audit["damage_ratio"])
            if audit["replacement_characters"]:
                reasons.append("replacement=%d" % audit["replacement_characters"])
            if audit["lost_glyph_characters"]:
                reasons.append("lost_glyphs=%d" % audit["lost_glyph_characters"])
            if audit["control_characters"]:
                reasons.append("control=%d" % audit["control_characters"])
            # Private-use *markers* are named separately and never as damage. They are how the
            # typesetter prints `A` `B` `C` `D`, they are present in 1,134 of the corpus's 3,516
            # papers, and reporting them here is what made a whole generation of papers look
            # corrupt. They are still named, because a reader of the reason string should be
            # able to see that the paper uses a private-use font at all.
            if audit["pua_marker_characters"]:
                reasons.append("pua_markers=%d(not-damage)" % audit["pua_marker_characters"])
        elif audit["simplified_ratio"] > bounds["max_simplified_ratio"]:
            klass = "NATIVE_TEXT_LAYOUT_RISK"
            reasons.append("simplified_ratio=%.4f" % audit["simplified_ratio"])
        elif gap >= bounds["max_anchor_gap"]:
            klass = "NATIVE_TEXT_LAYOUT_RISK"
            reasons.append("anchor_gap=%d" % gap)
        elif audit["option_markers"] == 0:
            klass = "NATIVE_TEXT_LAYOUT_RISK"
            reasons.append("no_option_markers")
        else:
            klass = "NATIVE_TEXT_GOOD"
    elif word_total == 0 and image_total > 0:
        klass = "SCANNED_IMAGE"
        reasons.append("no_text_layer")
    elif image_total > 0 and pages_with_text:
        klass = "MIXED"
        reasons.append("pages_with_text=%d/%d" % (pages_with_text, page_count))
    else:
        klass = "UNKNOWN"
        reasons.append("pages_with_text=%d/%d" % (pages_with_text, page_count))

    return {
        "path": os.path.abspath(path),
        "sha256": sha256_file(path),
        "bytes": len(blob),
        "pages": page_count,
        "pages_with_text": pages_with_text,
        "words": word_total,
        "images": image_total,
        "image_bytes": image_bytes,
        "fonts": len(fonts),
        "characters": audit["characters"],
        "cjk_characters": audit["cjk_characters"],
        "damaged_characters": audit["damaged_characters"],
        "replacement_characters": audit["replacement_characters"],
        "pua_characters": audit["pua_characters"],
        "pua_marker_characters": audit["pua_marker_characters"],
        "lost_glyph_characters": audit["lost_glyph_characters"],
        "control_characters": audit["control_characters"],
        "simplified_only_hits": audit["simplified_only_hits"],
        "simplified_ratio": round(audit["simplified_ratio"], 6),
        "damage_ratio": round(audit["damage_ratio"], 6),
        "question_anchors": len(set(anchors)),
        "max_gap_in_anchors": gap,
        "option_markers": audit["option_markers"],
        "latin_words": audit["latin_words"],
        "triage_class": klass,
        "reasons": "|".join(reasons),
        "reference_coverage": audit["reference_coverage"],
    }
