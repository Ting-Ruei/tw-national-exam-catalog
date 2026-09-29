# -*- coding: utf-8 -*-
"""Which generation of paper is this, and therefore which rules apply to it.

The corpus is not one corpus. The Examination Yuan changed its typesetting twice in the
years covered here, and the paper that came out of the change is a different document in
ways that matter to every later stage:

    generation 1   100-102   the number is followed by a space; option labels are drawn
                             from the private use area (about 323 of them per paper); a
                             paper is 240-277 extracted lines
    generation 2   103-115   the number is followed by a period; option labels are the
                             letters themselves; a paper is 413-430 extracted lines

Measured on the 192 醫事檢驗師 question papers (see `qbr/ENGINE_STRATEGY.md` §10). The
change is not gradual. Year 102 carries twelve papers and they are *both* kinds, six of
each, which is what makes it a transition rather than a boundary: a rule written for one
generation is wrong for half of that year.

That is the reason this module exists rather than a wider regular expression. A single rule
that covers both generations has to be loose enough to accept both shapes, and looseness is
what produced every silent failure this project has had: a style that accepts `16 在 DSM-5…`
also accepts `100 年第一次…`, and a rule tuned until the 115 paper reads correctly says
nothing about the 100 paper. Each generation gets its own rules, and each rule is measured
against the generation it was written for.

The generation is *reported*, never used to reject. A paper whose generation cannot be
determined still goes through the pipeline with the default rules, and the fact that it
could not be classified is carried in the result. A router that silently drops what it does
not recognise is worse than no router.
"""
from __future__ import annotations

import re

# The generations, declared with the evidence that separates them. Each entry names the
# properties that were measured, not a year range chosen by eye: a paper is classified by
# what it looks like, and the years here are only a summary of what was found.
GENERATIONS = {
    "gen1_pua_space": {
        "years": (100, 101, 102),
        "style": "bare_number_space",
        "option_labels": "private-use",
        "pua_floor": 50,               # measured ~323; the other generation has exactly 0
        "line_ceiling": 340,           # measured 240-277 against 413-430
        "description": "題號後接空白，選項以私用區字元繪製",
    },
    "gen2_dot_letter": {
        "years": (103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115),
        "style": "number_dot",
        "option_labels": "ascii-letter",
        "pua_floor": 0,
        "line_ceiling": None,
        "description": "題號後接句點，選項使用一般字母",
    },
}

DEFAULT_GENERATION = "gen2_dot_letter"
_PUA = re.compile(r"[\ue000-\uf8ff]")


def _pua_count(text):
    return len(_PUA.findall(text or ""))


def classify(*, text="", lines=None, style=None, year=None):
    """Which generation produced this paper, with the evidence that decided it.

    The decision is made on the paper's own properties - the number of private-use
    characters and the length of the reading - and only falls back to the year when those
    are inconclusive. That order matters: the year is a label on the folder, and a folder
    can be misfiled, while the private-use marks are on the page.

    Returns a dict carrying `generation`, `confidence`, `basis` and the measurements, so
    that a misclassification can be argued with rather than merely disbelieved.
    """
    pua = _pua_count(text)
    line_count = len(lines) if lines is not None else None

    evidence = {"pua_characters": pua, "line_count": line_count, "style": style,
                "year": year}

    # The private-use marks are the sharpest signal available: one generation draws its
    # option labels as glyphs nobody can type, the other prints letters. Measured over 192
    # papers, the counts are ~323 and 0 - there is no middle to get wrong.
    if pua >= GENERATIONS["gen1_pua_space"]["pua_floor"]:
        return {**evidence, "generation": "gen1_pua_space", "confidence": "high",
                "basis": "private-use-option-labels-%d" % pua}
    if pua == 0 and line_count is not None and line_count >= 340:
        return {**evidence, "generation": "gen2_dot_letter", "confidence": "high",
                "basis": "ascii-option-labels-and-%d-lines" % line_count}

    # Inconclusive on the page: use the style the segmenter settled on, then the year.
    if style == "bare_number_space":
        return {**evidence, "generation": "gen1_pua_space", "confidence": "medium",
                "basis": "segmenter-style-%s" % style}
    if style == "number_dot":
        return {**evidence, "generation": "gen2_dot_letter", "confidence": "medium",
                "basis": "segmenter-style-%s" % style}
    if year is not None:
        for name, spec in GENERATIONS.items():
            if year in spec["years"]:
                return {**evidence, "generation": name, "confidence": "low",
                        "basis": "year-%d" % year}
    return {**evidence, "generation": DEFAULT_GENERATION, "confidence": "none",
            "basis": "default"}


def generation_for_path(path, *, year=None):
    """Convenience: read the paper and classify it. Used by the driver, not by the rules."""
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from qbr import extract, repair  # noqa: E402

    rows = extract.extract_lines_a(path)
    text = repair.text_from_rows(rows)
    repaired, _ = repair.normalize_pretty(text)
    _records, _residual, diagnostics = repair.segment_questions(repaired)
    return classify(text=text, lines=rows, style=diagnostics.get("style"), year=year)
