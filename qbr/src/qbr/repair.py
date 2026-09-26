"""Level-0 deterministic repair and question segmentation (escalation ladder, rung 0).

Measured motivation, from the Stage-1 pilot over 30 official papers:

1. Twelve papers looked like a *content* disagreement between the two independent
   engines. Every case was confined to the header/footer band: these MOEX PDFs are
   print forms distilled by Ghostscript from an HTML form (`pdfinfo` reports
   `PDFCreator Version 1.2.3` / `GPL Ghostscript 9.04`, with a print-form URL in
   `Title`), so the title line and the metadata lines are drawn repeatedly and the two
   engines interleave those draws differently.
2. Question numbering style is **not uniform across the corpus**. Observed in the native
   text layer: the number alone on its own line (`1`, `2`, `3` - table cells of the print
   form), and number-plus-delimiter forms (`1.`, `1、`). A single global anchor regex is
   therefore wrong by construction - which is exactly what Protocol §9 warns about
   ("original question-number format may differ by source; do not apply one global
   regex"). `detect_anchor_style()` chooses the style per paper and *validates* the
   choice against number continuity instead of assuming it.

All rules here are declared, deterministic, and auditable: the function returns the
transformed text plus a trail of what it removed, so nothing disappears silently.
Bracket/CJK punctuation characters are built from code points rather than literals so
the patterns cannot be corrupted by an editing round-trip.
"""

import json
import os as _os
import collections
import re
import unicodedata

_RULES_VERSION = "moex-level0/2026-09-12.1"

# --- character classes assembled from code points (deliberately not literals) ---------
def _chars(*codes):
    return "".join(chr(code) for code in codes)


OPEN = _chars(0xFF08, 0x0028)           # fullwidth and halfwidth left parenthesis
CLOSE = _chars(0xFF09, 0x0029)          # right parenthesis
DOT = _chars(0x002E, 0x3002, 0xFF0E)    # . 。 ．
COMMA = _chars(0x3001, 0xFF0C, 0x002C)  # 、 ， ,
COLON = _chars(0xFF1A, 0x003A)          # ： :
FULLWIDTH_PIPE = _chars(0xFF5C)          # ｜
ideographic_marker = _chars(0x3000)      # ideographic space
BARS = _chars(0x007C, 0xFF5C)           # | ｜

_WHITE = r"\s\t" + ideographic_marker + _chars(0x0020, 0x3000)
_DIGITS = "0-9"
# The CJK ideograph blocks, declared once, in the same ranges `cjk.py` censuses
# them. A number followed by one of these has a question after it; a number
# standing alone on a line of its own is an anchor. The glued-anchor rule below
# turns on that distinction, so the class is declared here with the others
# rather than spelled out at the place of use.
_HAN = (chr(0x4E00) + "-" + chr(0x9FFF)
        + chr(0x3400) + "-" + chr(0x4DBF)
        + chr(0xF900) + "-" + chr(0xFAFF))

_YEAR = re.compile("^[" + _WHITE + "]*[" + _DIGITS + "]{2,3}[" + _WHITE + "]*" + _chars(0x5E74))
# What may follow the `年` of a paper's running head and still be a running head. The head is the
# exam's own title - `115年第一次專門職業及技術人員高等考試…` - so after the year comes the
# sitting or the kind of exam. A question stem that opens with a number and the word 年 continues
# with the subject instead: `56 年老女性…`, `7 年齡介於65～79歲…`, `12 年金給付水準…`. Measured
# over all 3,516 shipped papers: 2,186 `NNN年…` lines are heads and every one of them continues
# with 第 or 專, and the 19 that continue with anything else are all question stems. The set is
# therefore not fitted - it is the two words the print form actually uses, with the other
# candidates (公/特/高/普) measured to add nothing over these two.
_YEAR_HEAD_CONTINUATION = (0x7B2C, 0x5C08)   # 第 專
_ANCHOR_RESIDUE = re.compile("^[" + _WHITE + "]*[" + _DIGITS + "]{1,3}$")

# Deliberately explicit, source-anchored rule set for this corpus.
_META_PREFIXES = (
    _chars(0x8003, 0x7BC9, 0x540D, 0x7A31),      # 考試名稱
    _chars(0x8A66, 0x984C, 0x7B49, 0x5225),      # 試題類別
    _chars(0x8A66, 0x984C, 0x8AAA, 0x6609),      # 試題說明
    _chars(0x985E, 0x79CE, 0x53E8),               # 類科
    _chars(0x79D1, 0x76EE),                       # 科目
    _chars(0x5C0D, 0x8665),                       # 座號
    _chars(0x4EE3, 0x8665),                       # 代號
    _chars(0x9801, 0x6B21),                       # 頁次
    _chars(0x8003, 0x5F4C, 0x6642, 0x9593),       # 考試時間
    _chars(0x6CE8, 0x610F, 0x4E8B, 0x9805),       # 注意事項
    _chars(0x6E2C, 0x5142, 0x984C),               # 答案卷
    _chars(0x9010, 0x984C, 0x8AAA, 0x6609),       # 题目大纲  (simplified form: never official here)
)
_ANSWER_WORDS = (
    _chars(0x6E2C, 0x984C),                        # 答案
    _chars(0x89E3, 0x6790),                        # 解（answer/explanation cue）
    _chars(0x6E2C, 0x6587),                        # 課文
    _chars(0x984C, 0x5E72, 0x683C, 0x5F0F),       # 答案格式
)
_NOTICE_PREFIXES = (_chars(0x203B), _chars(0x6CE8, 0x610F), _chars(0x7981, 0x6B62), _chars(0x8AC1, 0x6B62))

_OPTION_LABELS = ("A", "B", "C", "D", "E", "F")

_OPTION_HEAD = re.compile(
    "^[" + _WHITE + "]*[" + OPEN + "]?[" + _WHITE + "]*([" + "".join(_OPTION_LABELS) + "".join(_OPTION_LABELS).lower() + "])"
    "[" + _WHITE + "]*[" + CLOSE + DOT + COMMA + COLON + "][" + _WHITE + "]*"
)

# --- CJK numeral anchors (constructed-response sections of official papers) ----------
_CJK_NUMERALS_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "patterns", "cjk_numerals.json")


def _load_numerals():
    try:
        with open(_CJK_NUMERALS_PATH, encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload["digit_values"], payload["ten_value"]
    except Exception:
        return {}, 10


_DIGIT_VALUES, _TEN_VALUE = _load_numerals()
_CJK_NUM_CHARS = "".join(_DIGIT_VALUES) if _DIGIT_VALUES else _chars(0x4E00, 0x4E8C, 0x4E09)


def cjk_number_to_int(token):
    """`二十三 -> 23`, `七 -> 7`. Returns None when the token is not a CJK numeral."""
    token = token.strip()
    if not token or any(ch not in _DIGIT_VALUES and ch != chr(0x5341) for ch in token):
        return None
    if chr(0x5341) not in token:
        digits = [ch for ch in token if ch in _DIGIT_VALUES]
        if len(digits) == 1:
            return _DIGIT_VALUES[digits[0]]
        return None
    parts = token.split(chr(0x5341))
    left, right = parts[0], parts[-1]
    value = 0
    if len(parts) != 2:
        return None
    if left == "":
        value += 10
    elif left in _DIGIT_VALUES:
        value += _DIGIT_VALUES[left] * 10
    else:
        return None
    if right == "":
        return value or None
    if right in _DIGIT_VALUES:
        return value + _DIGIT_VALUES[right]
    return None


def _cjk_anchor_match(line):
    stripped = line.strip()
    match = _CJK_ANCHOR_HEAD.match(stripped)
    if not match:
        return None
    value = cjk_number_to_int(match.group(1))
    return (value, match.end()) if value is not None else None


_CJK_ANCHOR_HEAD = re.compile(
    "^[" + _WHITE + "]*([" + _CJK_NUM_CHARS + "]{1,3})[" + _WHITE + "]*[" + DOT + COMMA + CLOSE + OPEN + _chars(0xFF0E) + "][" + _WHITE + "]*"
)

_ANCHOR_TEMPLATES = {
    "bare_number_line": "^[" + _WHITE + "]*(\\d{1,3})[" + _WHITE + "]*$",
    "number_dot": "^[" + _WHITE + "]*(\\d{1,3})[" + _WHITE + "]*[" + DOT + "][" + _WHITE + "]*",
    "number_comma": "^[" + _WHITE + "]*(\\d{1,3})[" + _WHITE + "]*[" + COMMA + "][" + _WHITE + "]*",
    "number_colon": "^[" + _WHITE + "]*(\\d{1,3})[" + _WHITE + "]*[" + COLON + "][" + _WHITE + "]*",
    "number_right_paren": "^[" + _WHITE + "]*(\\d{1,3})[" + _WHITE + "]*[" + CLOSE + "][" + _WHITE + "]*",
    "paren_number": "^[" + _WHITE + "]*[" + OPEN + "][" + _WHITE + "]*(\\d{1,3})[" + _WHITE + "]*[" + CLOSE + "]",
    "bracket_number": "^[" + _WHITE + "]*" + _chars(0x3010) + "[\\d]{1,3}" + _chars(0x3011),
    # A bare number, then whitespace, then content: `20 下列…`. This is the form the print
    # form produces once the reading-order merge has pulled a number cell onto the same line
    # as its question. It was missing from the declared set, so such a paper matched none of
    # the known styles: the segmenter recovered 1 record out of 80 and the remaining 79 were
    # silently counted as residual lines (measured 2026-09-13). Declared now, and the
    # continuity gate below is what makes declaring it safe.
    #
    # The lookahead asks for one further non-blank character, not two. Two was measured and
    # is wrong: a stem may open with a one-character run before its first space - `16 在 DSM-5…`,
    # `64 3 歲小兒…` - and such an anchor was then not recognised at all, leaving question 16
    # reported as a gap in an otherwise continuous 1..40 run. Across 300 papers of this style,
    # 40 anchors have exactly one leading character. Relaxing it to one gains anchors on 50
    # papers and loses none (measured over 600 papers), because the year guard below is what
    # now keeps the running head out, and it does so by meaning rather than by length.
    "bare_number_space": ("^[" + _WHITE + "]*(\\d{1,3})[" + _WHITE + "]+(?=[^" + _WHITE + "]{1,})"),
    "cjk_number_line": None,  # handled by _cjk_anchor_match, see _style_matches()
}


# ---------------------------------------------------------------- the printer's marks of the options
#
# A printed paper does not mark its options with the letters a reader would type. It marks
# them with draws out of the End-User-Defined plane (U+E000..), the bullets of the typesetter's
# own font. The embedded ToUnicode maps of these files send those draws to nothing, so the
# marks arrive in the text layer as private-use codepoints no reader can read, and the whole of
# an item - the question and its options together - arrives as one merged line. The segmenter
# below recognises an option by a line that *begins* with its label, and so finds none at all:
# measured, 76.8% of the judged records carried `missing-labels:A,B,C,D`, with the options
# standing inside the stem, diluting every comparison made with it
# (see `canon.containment`, and defect 9 of `reports/DEFECTS-AND-FIXES.md`).
#
# Those marks are not characters, they are boundaries; and boundaries may be divided at, on a
# rule declared here and checked against the papers themselves:
#
#   * where the labels of a paper are readable, an item has four options in 1,954 of 1,960
#     cases (the six remaining have three);
#   * where the labels are not readable, an item carries exactly four runs of private-use marks
#     - the shape (1,1,1,1), one mark between each pair of options - in 1,431 of 1,455 cases.
#
# A division is therefore admitted only when the number of runs corresponds to the number of
# options the *same paper* declares through its own readable items. A paper that declares none
# is divided at four, and the weaker warrant is written on the face of the record
# (`support: "corpus"`) so that whoever reads it may see on what ground the division was made.
# Nothing is taken away: the pieces are the very characters that stood in the line, the line
# itself is kept in `record["lines"]`, and no character of the record is rewritten - which is
# the standing rule of this sandbox, and the reason the legacy character maps were retired.

_PUA_LOW, _PUA_HIGH = 0xE000, 0xF8FF
_OPTION_COUNT_BY_CORPUS = 4                    # measured above, and declared
_BULLET_RUN_LONGEST = 3                        # a run of marks is a bullet, not a paragraph

# A private-use character has two jobs in these papers and they must not be read alike.
#
#   * It is the *option marker*: the label of an option, standing at the start of it, which the
#     typesetter drew from a private-use font instead of printing `A` `B` `C` `D`.
#   * It is a *lost glyph*: a character the font could not map, standing **inside** a word.
#
# `bullet_runs` counted both as marks, and the count is what decides whether an item may be
# divided. On `1002_醫事檢驗師_生物化學與臨床生化學` question 11 prints four option markers and
# two lost glyphs (`丙酮酸羧\ue2c6` and `輔\ue2c6`, which render as 酶), so five or six runs were
# seen where the rule wants exactly four, the division was refused, and the options stayed in
# the stem. Measured over the 30 generation-1 papers of medical technologist: 11 of them have
# lost glyphs, and each one loses the options of every question that contains one.
#
# The two are told apart by the paper rather than by the character: a marker *stands at a
# boundary* - before it is whitespace or the start of the text - while a lost glyph has a
# letter or an ideograph immediately before it, because it is the character that went missing.
# The test is on the neighbourhood, not on the code point, so it holds for every private-use
# character the papers happen to use and does not need a list of them.
_HAN_BEFORE = re.compile(r"[" + _HAN + "A-Za-z0-9\u00c0-\u024f]")


def is_bullet_char(char):
    """Whether a character is one of the typesetter's private-use marks."""
    return _PUA_LOW <= ord(char) <= _PUA_HIGH


def is_lost_glyph(text, position):
    """Whether the private-use character at `position` is a missing character, not a marker.

    A character with a letter or an ideograph immediately before it is standing where the
    missing glyph was; one at a boundary is labelling what follows it.
    """
    if position <= 0:
        return False
    return bool(_HAN_BEFORE.match(text[position - 1]))


def bullet_runs(text, *, markers_only=False, alphabet=None):
    """The places of the runs of marks in `text`, as [(start, stop), ...].

    `alphabet` limits the marks to the codepoints the paper uses for its option labels, which is
    the paper's own statement of which characters are boundaries. Without it every private-use
    character counts, which is what "are there private-use characters here at all" means and what
    the damage alarm asks.

    `markers_only` is the older test and is kept for the callers that ask the character-level
    question; it drops a run sitting inside a word, which is a lost glyph rather than a mark. It
    is no longer what division uses: where a flattened superscript fragment precedes a marker,
    the marker looks as though it sits inside a word. See `option_alphabet`.
    """
    runs = []
    start = None
    for position, char in enumerate(text or ""):
        marked = (ord(char) in alphabet) if alphabet else is_bullet_char(char)
        if marked:
            if start is None:
                start = position
        elif start is not None:
            runs.append((start, position))
            start = None
    if start is not None:
        runs.append((start, len(text)))
    if not markers_only:
        return runs
    return [(a, b) for a, b in runs if not is_lost_glyph(text, a)]


def _option_count_of_paper(records):
    """How many options the readable items of one paper declare, or None when none declares."""
    counts = [len(record.get("options") or {}) for record in records if (record.get("options") or {})]
    if not counts:
        return None
    return collections.Counter(counts).most_common(1)[0][0]


_GLUED_ANCHOR = re.compile("(?<=[" + _WHITE + "])([" + _DIGITS + "]{1,3})["
                       + _WHITE + "]+(?=[" + _HAN + "])")


def _split_glued_records(records):
    """Divide an item at a number standing inside it that is the very next of the run.

    The reading-order merge that puts a page together is what does the harm here: a question
    number that was a cell of its own on the page is pulled onto the line of the question
    before it, and the two become one item. Measured over 45 papers and 2,664 items, 123 items
    (4.6%) carry the number that follows their own, in 21 of the 45 papers; the shape of it is
    `…附睪 \ue18f睪丸 59 利用自動化儀器…`, where 58 swallowed 59 whole.

    A division is admitted on three conditions, and refused on any of them:
      * the number inside the text is exactly the next of the item's own number - a stray
        figure in the text (`100 克脂肪`, `400×`) is nothing of the kind;
      * that next number is not already a question of the paper, which is what makes the
        division safe rather than a source of duplicates;
      * both halves are left standing as questions, the first of at least two characters.
    And the continuity gate that follows is the audit of it: a division made wrong shows
    itself there as a hole or a repetition, and the paper is refused.
    """
    have = {record.get("number") for record in records}
    out = []
    for record in records:
        number = record.get("number")
        stem = record.get("stem") or ""
        out.append(record)
        if not isinstance(number, int) or len(stem) < 6:
            continue
        if (number + 1) in have:
            continue                                   # it is a question of the paper already
        for match in _GLUED_ANCHOR.finditer(stem):
            if match.start() < 4:
                continue
            if int(match.group(1)) != number + 1:
                continue
            head = stem[:match.start()].strip()
            tail = stem[match.end():].strip()
            if len(head) < 2 or len(tail) < 2:
                break
            record["stem"] = head
            record["lines"] = (record.get("lines") or [])[:1]
            twin = {
                "number": number + 1,
                "stem": tail,
                "options": {},
                "option_order": [],
                "lines": [tail],
                "source": "glued-anchor",
                "split-from": number,
            }
            out.append(twin)
            have.add(number + 1)
            break
    out.sort(key=lambda record: (record.get("number") if isinstance(record.get("number"), int) else 0))
    return out


def option_alphabet(text):
    """The codepoints this paper uses to mark its options, as a set.

    A typesetter's font draws the four option labels with four draws, and gives them four
    codepoints. Those codepoints are *consecutive*, because a font assigns its glyphs in order,
    and each is used *exactly once per question*, because each labels one option of one question.
    Measured over the 48 papers of the two categories that use a private-use font at all: in all
    48, the option markers are exactly the longest run of consecutive codepoints whose counts are
    equal, and the run is four codes long in every one.

    This replaces telling a marker from a lost glyph by looking at the character before it. That
    test asked about the engine's output - whether a letter happened to precede the codepoint -
    and it fails exactly when a flattened superscript fragment lands in front of a marker, which
    is common in the chemistry papers: `HCO₃⁻  ⁻P\ue18dH₂CO₃` puts a `P` before `\ue18d`, so the
    marker was read as a lost glyph, only two runs were seen where the rule wants four, and the
    options of question 34 of `1012_醫事檢驗師_生物化學與臨床生化學` stayed in the stem.

    **A paper may use more than one such family, and returning only the longest one loses the
    others.** Measured on `1001_醫事檢驗師_臨床血清免疫學與臨床病毒學`: the main labels are
    `\ue18c`-`\ue18f`, 78 times each, and there is a *second* family `\ue000`-`\ue003` used by the
    multi-answer questions (Q49 prints `\ue000` twice, `\ue001` twice, `\ue002` twice). Returning
    only `\ue18c`-`\ue18f` meant Q49's and Q71's marks were not recognised as option labels at all,
    their options stayed in the stem, and the gate reported them as questions with no options.

    The two codepoints that appear once or twice without an equal-count run - `\ue129`-`\ue12b` -
    are still excluded, by the equal-count requirement rather than by being listed. A paper that
    uses no such font returns an empty set, and the division then does not happen at all, which is
    what should happen to a paper whose options are printed as letters.

    **The count need not be equal when the run's length is the option count.** A typesetter assigns
    one glyph per option label, so a family of exactly four consecutive codepoints IS the alphabet
    even if one of them is used one time fewer than the others. Measured on
    `1001_醫事檢驗師_臨床血清免疫學與臨床病毒學`: the second family is `\ue000`-`\ue003` with counts
    2, 2, 2, **1** - the fourth label is printed once because only one question uses all four. The
    equal-count rule rejected it, the fourth mark was not recognised, Q71's options stayed in the
    stem, and the gate reported the question as having no options. Requiring the *length* to match
    the option count states the same fact about the print form and does not depend on how many
    questions happen to use the family.
    """
    counts = collections.Counter()
    for char in text or "":
        if is_bullet_char(char):
            counts[ord(char)] += 1
    families = []
    run = []
    for code in sorted(counts):
        run = run + [code] if run and code == run[-1] + 1 else [code]
        if len(run) < 2:
            continue
        values = {counts[each] for each in run}
        equal = len(values) == 1 and values.pop() >= 2
        # One glyph per option label, so a run as long as the option count is the alphabet.
        sized = len(run) == _OPTION_COUNT_BY_CORPUS
        if not (equal or sized):
            continue
        families.append(list(run))
    # Every qualifying family is an option alphabet, not only the longest: a paper may print one
    # family for its single-answer questions and another for its multi-answer ones, and both are
    # labels rather than characters the font failed to map.
    return {code for family in families for code in family}


def option_alphabet_families(text):
    """The candidate option alphabets of a paper, each a list, most-used first.

    A paper can print **two** private-use families and they do different jobs. Measured on
    `1001_醫事檢驗師_臨床生理學與病理學` Q65:

        泌尿系統感染的尿液分析以下列那些物質的增加為主？
        \ue000細菌  \ue001白血球  \ue002紅血球  \ue003葡萄糖
        \ue18c僅\ue000\ue001\ue002  \ue18d僅\ue002\ue003  \ue18e僅\ue000\ue001  \ue18f僅\ue000\ue002

    `\ue000`-`\ue003` label the four *sub-items the question asks about*; `\ue18c`-`\ue18f` label the four
    *options the candidate chooses between*. Both families are runs of four, so a paper-level
    alphabet cannot tell them apart - and taking both together made eight runs, which matches
    nothing, so the question came out with no options at all.

    **The distinction is by use, and use is countable.** The option labels are printed once per
    question, so the family with the larger total is the alphabet and the other is content. That
    is a statement about the print form, not a guess: measured, `\ue18c`-`\ue18f` occur 78 times
    each on that paper (one per question) while `\ue000`-`\ue003` occur on a handful.
    """
    counts = collections.Counter()
    for char in text or "":
        if is_bullet_char(char):
            counts[ord(char)] += 1
    families = []
    run = []
    for code in sorted(counts):
        run = run + [code] if run and code == run[-1] + 1 else [code]
        if len(run) < 2:
            continue
        values = {counts[each] for each in run}
        equal = len(values) == 1 and values.pop() >= 2
        sized = len(run) == _OPTION_COUNT_BY_CORPUS
        if equal or sized:
            families.append((sum(counts[c] for c in run), list(run)))
    families.sort(key=lambda item: item[0], reverse=True)
    return [list(run) for _total, run in families]


def split_at_bullets(text, declared=None, alphabet=None):
    """Divide an item at the marks that divide its options, if - and only if - they correspond.

    Returns `(stem, [option texts])` when the runs correspond to `declared` (the number of
    options the paper's own readable items declare, falling back to the corpus's four), or
    `None` when they do not. Refusal is the default: an item with a single mark in it, with a
    mark that stands where no option could be, or with a piece too short to be an option, is
    left exactly as it was found, and keeps the flag that says so.
    """
    if not text:
        return None
    runs = bullet_runs(text, alphabet=alphabet)
    if len(runs) < 2 or any(stop - start > _BULLET_RUN_LONGEST for start, stop in runs):
        return None
    wanted = declared or _OPTION_COUNT_BY_CORPUS
    if len(runs) != wanted:
        return None
    pieces = []
    previous = 0
    for start, stop in runs:
        pieces.append(text[previous:start])
        previous = stop
    pieces.append(text[previous:])
    pieces = [piece.strip(" \t\u3000") for piece in pieces]
    if any(len(piece) < 1 for piece in pieces) or len(pieces[0]) < 2:
        return None                                   # the stem must still be a question
    return pieces[0], pieces[1:]


def starts_with_number(line):
    stripped = line.strip()
    # `isdecimal` and not `isdigit`: the latter is True for `①`, `②` and `²`, which these papers
    # print as sub-item markers, and a caller that then calls `int()` raises.
    return bool(stripped) and stripped[0].isdecimal()


def is_year_line(line):
    """`115年第一次…` headers start with digits but are not question numbers.

    The old form of this rule was `^NNN年` and nothing else, so it also swallowed question stems
    that open with a number and the word 年: `56 年老女性發生肱骨…`, `7 年齡介於65～79歲…`,
    `12 年金給付水準…`. That is not a small mistake - the line was then treated as chrome and
    skipped by the numbering scan, so the paper lost every question from that point on. Measured
    over all 3,516 shipped papers, 19 papers were cut short this way, `1092_法醫師_一般醫學` to
    43 questions of 100 and `1062_諮商心理師…` to 31 of 40.

    What a head has that a stem does not is the rest of the exam's own title: after the year
    comes the sitting or the kind of exam (`第`, `專`). Measured: 2,186 `NNN年…` lines are heads
    and all 2,186 continue with one of those two; the other 19 are all stems. So the rule is the
    continuation and not a length or a threshold - and it holds on unseen papers because it names
    what the print form prints, not what any one paper happens to contain.

    The comparison is made against the NFKC form, because these papers print the year with a
    compatibility ideograph in some places: `年` (U+F98E) rather than `年` (U+5E74). The two are
    different codepoints that only NFKC brings together, which is a property of the typesetting
    and not of any engine - the same class of damage as the private-use bullets.
    """
    folded = unicodedata.normalize("NFKC", line.strip())
    if not _YEAR.match(folded):
        return False
    match = re.match("^[" + _WHITE + "]*[" + _DIGITS + "]{2,3}[" + _WHITE + "]*" + _chars(0x5E74), folded)
    if not match:
        return False
    rest = folded[match.end():]
    return bool(rest) and ord(rest[0]) in _YEAR_HEAD_CONTINUATION


def is_option_line(line):
    return bool(_OPTION_HEAD.match(line))


def option_label(line):
    match = _OPTION_HEAD.match(line)
    return match.group(1).upper() if match else None


def option_body(line):
    match = _OPTION_HEAD.match(line)
    if not match:
        return line.strip()
    return line[match.end():].strip()


def _starts_with_any(line, prefixes):
    stripped = line.strip()
    return any(stripped.startswith(prefix) for prefix in prefixes)


def is_metadata_line(line):
    stripped = line.strip()
    if not stripped:
        return False
    if is_year_line(stripped):
        return True
    if _starts_with_any(stripped, _META_PREFIXES):
        return True
    if _starts_with_any(stripped, _ANSWER_WORDS) and len(stripped) <= 24:
        return True
    if _starts_with_any(stripped, ("(" + BARS + FULLWIDTH_PIPE + ")+")):
        return False
    return False


def is_notice_line(line):
    return _starts_with_any(line, _NOTICE_PREFIXES) or _starts_with_any(line, tuple("(" + c + ")" for c in _NOTICE_PREFIXES))


def is_page_footer(line):
    stripped = line.strip()
    return bool(_ANCHOR_RESIDUE.match(stripped)) or bool(
        re.match(
            "^[" + _WHITE + "]*(" + _chars(0x7B2C) + ")?[" + _WHITE + "]*(\\d{1,4})[" + _WHITE + "]*" + _chars(0x9801),
            stripped,
        )
    ) or bool(re.match("^[" + _WHITE + "]*(\\d{1,4})[" + _WHITE + "]*[/][" + _WHITE + "]*(\\d{1,4})[" + _WHITE + "]*$", stripped))


def is_punctuation_only(line):
    stripped = line.strip()
    if not stripped or len(stripped) > 24:
        return False
    return all(not ch.isalpha() and not ch.isdigit() for ch in stripped)


def _style_matches(style, line):
    """Return `(number, consumed_length)` or None for one candidate line."""
    if style == "cjk_number_line":
        found = _cjk_anchor_match(line)
        return found
    compiled = _ANCHOR_TEMPLATES_STYLE.get(style)
    if compiled is None:
        return None
    match = compiled.match(line.strip())
    if not match:
        return None
    try:
        number, consumed = int(match.group(1)), match.end()
    except (IndexError, ValueError, TypeError):
        return None
    if style == "bare_number_space" and is_year_line(line):
        # The running head of the paper - `100 年第一次專門…` - is the same shape as a questioned
        # line after its number cell has been merged onto it (`20 下列…`), and without this the
        # style reads the year as question 100. `is_year_line` is the single place that decides
        # what a head is; a second, looser test used to sit here and it disagreed with this one
        # often enough to cut 29 question stems out of 19 papers (measured, over all 3,516).
        return None
    return number, consumed


_ANCHOR_TEMPLATES_STYLE = {
    name: (None if pattern is None else re.compile(pattern))
    for name, pattern in _ANCHOR_TEMPLATES.items()
}


def detect_anchor_styles(lines, max_number=250, min_count=3):
    """Every numbering style that plausibly explains `lines`, ranked best first.

    Scoring is a declared preference over (coverage of the 1..N run, count of anchors
    raised, monotonicity, reach). Returning the whole ranking - and not only the winner -
    is deliberate: the caller may segment the text under each candidate and keep the one
    that actually yields questions, which is the only way to discover a style that fits a
    paper none of the templates describes well on its own.
    """
    ranked = []
    for name in _ANCHOR_TEMPLATES:
        numbers = []
        for line in lines:
            if is_year_line(line):
                continue
            found = _style_matches(name, line)
            if not found:
                continue
            value = found[0]
            if 0 < value <= max_number:
                numbers.append(value)
        if len(numbers) < max(2, min_count):
            continue
        unique = sorted(set(numbers))
        coverage = len(unique) / max(1.0, float(unique[-1]))
        monotone = sum(1 for first, second in zip(numbers, numbers[1:]) if second >= first)
        monotone_ratio = monotone / max(1, len(numbers) - 1)
        ranked.append({
            "style": name,
            "pattern": _ANCHOR_TEMPLATES.get(name),
            "compiled": _ANCHOR_TEMPLATES_STYLE.get(name),
            "score": (round(coverage, 4), len(numbers), round(monotone_ratio, 4), unique[-1]),
            "count": len(numbers),
            "first": numbers[0],
            "last": numbers[-1],
            "distinct": len(unique),
            "coverage": round(coverage, 4),
            "monotone_ratio": round(monotone_ratio, 4),
            "duplicates": len(numbers) - len(unique),
            "gaps": _gaps(unique),
        })
    ranked.sort(key=lambda candidate: candidate["score"], reverse=True)
    return ranked


def detect_anchor_style(lines, max_number=250):
    """The style that best explains `lines`, or None. See `detect_anchor_styles`."""
    ranked = detect_anchor_styles(lines, max_number=max_number, min_count=3)
    return ranked[0] if ranked else None


def _gaps(sorted_numbers, limit=3):
    missing = []
    if not sorted_numbers:
        return missing
    expected = sorted_numbers[0]
    for value in sorted_numbers:
        while expected < value:
            missing.append(expected)
            expected += 1
            if len(missing) >= limit:
                return missing
        expected = value + 1
    return missing


# REMOVED: `collapse_repeated_draws`.
#
# It deleted any line whose text had appeared within the previous twelve lines, to undo the
# print form's habit of drawing its header block several times per page. It was written before
# `mask_chrome` existed, and once chrome is identified by *recurring on other pages at the same
# vertical position* - a property of the document rather than of a distance - the distance rule
# has nothing left to do but damage.
#
# Measured over the 282 papers of medical technologist and pharmacist: it removed 394 lines,
# and the ones that mattered were option rows that legitimately repeat. `Apo A-I / Apo B-100 /
# Apo B-48 / Apo C-II` is the option set of two different questions nine lines apart - Q11 at
# the foot of page 3 and Q59 at the head of page 4 - and deleting the second left Q59 with no
# options at all. With the rule gone, questions holding fewer than four options fall from 293
# to 101, every other figure is unchanged (clean papers 276, questions 22,147, empty stems 0,
# residual lines 1,460), and the 101 that remain are questions whose options are inside an
# image and are meant to have none.


def normalize_pretty(text, *, drop_header_footer=True, header_band=14):
    """Return `(repaired_text, dropped_audit_trail)`.

    Only structural chrome of the official print form is removed, using declared rules.
    The raw extraction stays untouched in the caller - this is a comparison/segmentation
    view, never a replacement for the raw evidence.

    Chrome is removed twice over and neither pass needs a third. `mask_chrome` works on the
    rows and finds what recurs at a fixed position across pages; the header band here finds
    what the print form puts at the top of a page. A distance-based third rule used to sit
    between them and has been removed - see the note above where it was.
    """
    lines = text.splitlines()
    dropped = []
    out = []
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        if drop_header_footer:
            in_band = index <= header_band
            if in_band and (is_metadata_line(stripped) or is_page_footer(stripped)):
                dropped.append({"line": index, "rule": "header/footer", "text": stripped[:120]})
                continue
            if is_notice_line(stripped):
                dropped.append({"line": index, "rule": "notice", "text": stripped[:120]})
                continue
            if is_punctuation_only(stripped):
                dropped.append({"line": index, "rule": "punctuation-only", "text": stripped[:120]})
                continue
        out.append(line)
    return "\n".join(out), dropped


# Declared gate for the segmentation itself (protocol §16: a threshold must come from a
# declaration, never from the data it polices). Both are recorded in the diagnostics, so a
# rejected paper is visible as a rejected paper instead of being published as a short one.
ALIGN_RECOVER_MIN_RATIO = 0.90   # of the 1..N run must have been raised as anchors
RESIDUAL_LINE_BUDGET = 0.35      # unassigned lines allowed over all lines of the paper


def _expected_run(distinct, alignment_min=ALIGN_RECOVER_MIN_RATIO):
    """The 1..N run this paper's anchors actually claim, and how far they reach.

    `max(numbers)` alone is not a usable expectation: one line of chrome that happens to
    begin with a three-digit year, or one option body that begins with `85 mmHg`, lifts the
    ceiling above the real question count and the gate then reports 21 gaps in a complete
    paper. Two figures are therefore reported, and the gap between them is the finding:

    * `claimed_max` - the highest number that appeared at all;
    * the returned `want` - the longest run that is essentially complete: the whole claim
      when at least `alignment_min` of it was seen, else the largest prefix cut for which
      that still holds.

    A paper whose anchors reach 40 but of which only 29 were raised is thus not read as a
    29-question paper: it is read as a short reading of a 40-question paper, and flagged.
    """
    if not distinct:
        return 0, 0
    ordered = sorted(set(distinct))
    top = ordered[-1]
    if len(ordered) >= alignment_min * top:
        return top, top
    for want in reversed(ordered):
        seen = sum(1 for value in ordered if value <= want)
        if seen >= alignment_min * want:
            return want, top
    return ordered[0], top


def _option_mark_in(line, marks):
    """Whether `line` carries one of the paper's option marks anywhere in it."""
    if not marks:
        return False
    return any(ord(char) in marks for char in line or "")


#: Characters that end a sentence. A stem that ends with one of these has been completed by the
#: typesetter and what follows is a new question; a stem that does not has been wrapped.
_SENTENCE_END = "？?。！!：:；;"


def _next_text(source_lines, index):
    """The first non-blank line after `index`, or the empty string."""
    for candidate in source_lines[index + 1:]:
        if candidate.strip():
            return candidate.strip()
    return ""


def _looks_like_a_wrap(line, found, following, *, inside_question, seen_option_mark,
                       previous_text, option_marks):
    """Whether `line` is the tail of the previous question's sentence, not a question of its own.

    All five conditions are required, and each is a fact about the text rather than a distance:

    1. **A question is already being read.** A number before the first question is in the title
       block, where the paper's own instructions mention question numbers (`請依上文回答第1 題至第2 題`).
    2. **That question has shown no option mark yet.** Once its options have begun, its stem is
       finished with; a number after that opens a question.
    3. **The next line carries at least two option marks.** A wrap is followed by the options the
       wrapped stem belongs to; two marks are a row of labels where one is a stray character.
    4. **The text before it leaves a sentence unfinished.** A completed sentence means the
       question's stem is complete; a stem that has not ended is one the typesetter wrapped.
    5. **The text of this line is at most 14 characters.** The tail of a wrapped sentence is a
       phrase; the head of a question is a clause.

    The line's *own* text may end with `？`: the tail of a wrapped question is still a question
    (`…經過` + `2 天後，藥物濃度為何？`). What must not have ended is the sentence *before* it.

    Measured over the whole corpus: of **89,463** successor anchors, exactly **two** satisfy all
    five. One is the defect this rule exists for - `1001_藥師_藥劑學(包括生物藥劑學)` question 1,
    whose stem ends `…反應速率常數為1.0 mg/mL/hr，經過` and is wrapped as `2 天後，藥物濃度為何？`,
    so question 2 took question 1's options and the paper's real question 2 was swallowed. The
    other is `15 115 119` on `1112_公共衛生師_生物統計學`, a reading-order artifact of a table;
    there the loss is one row of table figures, not a question.

    This is deliberately *not* the indent test. `2 天後` is printed at the same indent as the
    options, so no threshold on x separates it from an anchor - what separates them is that the
    sentence it sits in has not ended.
    """
    number, consumed = found
    if not inside_question or seen_option_mark:
        return False
    body = line[consumed:].strip() if consumed < len(line) else ""
    if not body or len(body) > 14:
        return False
    if not previous_text or previous_text[-1] in _SENTENCE_END:
        return False
    return sum(1 for char in following if ord(char) in option_marks) >= 2


def _continues_an_option(line, record, option_marks):
    """Whether `line` is the tail of the option printed just before it, not more of the stem.

    The symmetric case of `_looks_like_a_wrap`, and it needs the same discipline: the question is
    whether this line *opens* anything, not where it sits on the page. It continues an option only
    when all four hold:

    1. **An option has already been printed.** Before that there is no option to continue, and the
       stem is still being read.
    2. **The line opens nothing.** No mark of the paper's alphabet anywhere in it, and no anchor at
       its head. A line that opens an option or a question is those, by definition.
    3. **It is not chrome.** A page footer, a declared part heading, a metadata line and a
       punctuation-only run are all things that arrive between options and belong to none of them.
       Measured on 40 sampled papers, of the lines reaching this branch 169 are continuation
       candidates, 29 are short fragments and 1 is punctuation-only - no footers, no headings, no
       metadata - so these guards are for the corpus rather than for the sample.
    4. **It carries text.** A blank line has no continuation in it.

    Deliberately *not* a length test, unlike the stem's rule (which may use `<= 14` because a stem's
    wrap is a short phrase before the options begin). Measured over the corpus, the continuation of
    an option is frequently long: the intersection cases include a 24-character tail on
    `1151_醫師(二)_醫學(三)` q13 C. A length cap here would refuse most of the real losses.

    Anchors are excluded without consulting the style: a line whose head is a number and a dot is
    the next question whatever the style's spelling, and `_style_matches` has already had its
    chance to claim the line before this is reached.
    """
    if not record or not record.get("option_order"):
        return False
    stripped = (line or "").strip()
    if not stripped:
        return False
    if _option_mark_in(stripped, option_marks):
        return False
    if _BARE_ANCHOR.match(stripped):
        return False
    if (is_page_footer(stripped) or is_section_head(stripped)
            or is_metadata_line(stripped) or is_punctuation_only(stripped)):
        return False
    return True


#: A number and a dot at the head of a line: the next question's anchor in any style this module
#: spells. Used only to keep `_continues_an_option` from swallowing a question.
_BARE_ANCHOR = re.compile(r"^\d{1,3}\s*\.")


def segment_questions(text, style=None, *, validate=True, expected=None,
                     alignment_min=ALIGN_RECOVER_MIN_RATIO,
                     residual_budget=RESIDUAL_LINE_BUDGET):
    """Split repaired text into question records using the *detected* numbering style.

    Returns `(records, residual_lines, diagnostics)`. A record never loses its origin:
    `lines` keeps every source line that was attached to it.

    The choice of style is *validated*, not assumed. `diagnostics` now always carries
    `ok` / `reasons` / `count` / `expected` / `residual_ratio`, so that a paper where only
    1 anchor was raised out of 80 questions is reported as failed instead of being read as
    a paper of one question (measured defect, 2026-09-13).
    """
    source_lines = text.splitlines() if isinstance(text, str) else [str(x) for x in (text or [])]
    body = text if isinstance(text, str) else "\n".join(source_lines)
    chosen = style or detect_anchor_style(source_lines)
    #: The paper's own option marks, for the wrap test below. Read from the whole paper rather
    #: than from the one item, because which codepoints are labels is a fact about the print form.
    option_marks = {code for family in option_alphabet_families(body) for code in family}
    diagnostics = {"style": None if not chosen else chosen["style"], "detail": chosen}
    if not chosen:
        diagnostics.update({"ok": False, "reasons": ["no-style-detected"], "count": 0,
                          "expected": 0, "missing": [], "residual_ratio": 1.0, "sequence": {}})
        return [], [line.strip() for line in source_lines if line.strip()], diagnostics
    records = []
    current = None
    residual = []
    # A paper numbers its questions 1..N and uses each number once. That is what a numbered
    # examination is: eighty questions numbered one to eighty, so that an answer sheet can name
    # them. A second line opening with a number that has already opened a question is therefore
    # not a second question - it is a measured value that happens to begin the line, and the
    # number is part of the measurement.
    #
    # This is the rule `_expected_run` already relies on, applied at the point of decision. It
    # was not applied here before, and the cost was measured over the two categories: nineteen
    # lines in nineteen papers opened with a number that had already been used, and every one of
    # them was a value - `7.5×10⁹/L`, `11.3～14.6秒`, `1.2 mg/dL`, `4.8%`, `65.23歲`. Each became a
    # spurious extra question, which pushed the item count above eighty and made S3 refuse the
    # whole paper for `count-mismatch`, and in twelve of those papers the spurious question had
    # no options so it was reported a second time as `options-not-four`. One value, three
    # complaints, and the paper withheld.
    #
    # `53.33歲油漆工` - question 53 whose stem opens with `33` - is the case that keeps this from
    # being a decimal rule, and it is unaffected: 53 has not been used before, so 53 is an
    # anchor and `33` is part of its stem, exactly as before. The rule is about the paper's
    # numbering, not about punctuation.
    # A third rule about the paper's numbering, and the one that is about *distance* rather than
    # repetition: a question follows its predecessor by one. A numbered examination is numbered
    # 1, 2, 3, ... - that is what lets an answer sheet name the questions - so a line opening with
    # a number far beyond the one being looked for is not the next question, whatever it says.
    #
    # Measured over the four categories, 32,721 adjacent gaps between accepted question numbers:
    # every single one is exactly 1. The only two larger gaps in the whole corpus are both spurious
    # and both are wrapped values read as anchors - `234.8）` on
    # `1142_藥師(一)_藥學(二)(包括藥物分析與生藥學(含中藥學))` and `90.1 fL、reticulocyte 0.7`
    # on `1091_醫事檢驗師_臨床血液學與血庫學`. On the first, question 3's stem ends
    # `（碘化銀分子量為` and the value wraps to the next line; the spurious 234 closed question 3
    # before its options and the paper was read as **4 questions out of 80**. On the second the
    # paper was read as 24 out of 80.
    #
    # The limit is deliberately far above any real count - the longest paper in the corpus is 80
    # questions - so it cannot refuse a question any paper actually has. It is not a fitted
    # threshold: it is the observation that no question number is ever three digits when the paper
    # is numbered from 1.
    seen_anchors = set()
    last_number = 0
    #: Whether the question being read has already shown an option mark, and whether a line has
    #: already ended a sentence. Both are facts about the text read so far, kept here so that the
    #: wrap test below can be stated without looking at the engine's geometry.
    seen_option_mark = False
    #: The text of the line read before this one, for the wrap test. It is the previous line and
    #: not the whole stem: the question is whether *the sentence just before* was left open.
    previous_text = ""
    #: An option mark printed on a line of its own with nothing after it.
    #:
    #: The typesetter sets some options as the mark, then the option's text on the next line. Read
    #: as ordinary text, that next line is the stem's and the option comes out empty - and the
    #: option's own words end up appended to the question. Measured on `1152_藥師(一)_藥學(一)` Q53:
    #: the text layer is `A.` / `alfuzosin` / `B.` / `doxazosin` / ..., the four drug names were put
    #: into the stem, and all four options came out as the empty string, so the question read
    #: `…最長？ alfuzosin doxazosin prazosin terazosin` with nothing to choose between. The marker
    #: is a claim about the next line, so the next line is the option's body - and only the next
    #: line, because a mark with an empty body followed by another mark is a row of bare labels.
    pending_option = None
    for raw_index, raw_line in enumerate(source_lines):
        line = raw_line.strip()
        if not line:
            continue
        if _option_mark_in(line, option_marks):
            seen_option_mark = True
        if chosen["style"] != "cjk_number_line" and is_year_line(line):
            # The same guard `detect_anchor_styles()` applies: detection and segmentation
            # must agree on what is not an anchor, or a header line such as
            # `100 年第一次…` becomes question no. 100 of an 80-question paper and the
            # continuity gate condemns a paper that was in fact parsed completely.
            if current is not None:
                current["stem"].append(line)
                current["lines"].append(line)
            else:
                residual.append(line)
            previous_text = line
            continue
        found = _style_matches(chosen["style"], line)
        seen_number = None
        if found and found[0] is not None:
            seen_number = found[0]
        if found and seen_number is not None and seen_number != last_number + 1:
            # Not the next question: this is a value, and the line continues whatever is being
            # read. The same treatment as a repeated number, and for the same reason - it keeps the
            # text in the record it belongs to rather than inventing a question around it.
            #
            # The paper numbers its questions consecutively, and that is the whole of the rule.
            # Measured over the four categories, every one of the 32,721 adjacent gaps between
            # accepted question numbers is exactly 1 - there is no paper in the corpus with a gap
            # in its numbering. The three failures this fixes were all values read as questions:
            # `4.8%。以鐵劑治療三星期無改善` on `1101_醫事檢驗師_臨床血液學與血庫學` was taken for
            # question 4 and stole question 2's four options; `234.8）` and
            # `90.1 fL、reticulocyte 0.7` were taken for questions 234 and 90 and cut 80-question
            # papers down to 4 and 24.
            #
            # It is deliberately not a distance threshold. A threshold would have to be fitted and
            # would let `4.8%` through, because a jump of two is indistinguishable from a jump of
            # two hundred by distance alone - what separates them is that neither is a *successor*.
            found = None
        if (found and current is not None and not seen_option_mark
                and _looks_like_a_wrap(
                    line, found, _next_text(source_lines, raw_index),
                    inside_question=current is not None,
                    seen_option_mark=seen_option_mark,
                    previous_text=previous_text,
                    option_marks=option_marks)):
            # The number is the successor, but the line is the tail of the previous question's
            # sentence and not a question of its own. Measured on
            # `1001_藥師_藥劑學(包括生物藥劑學)`: question 1's stem ends `…反應速率常數為1.0
            # mg/mL/hr，經過` and the typesetter wraps it as `2 天後，藥物濃度為何？`. Read as
            # question 2, that line took question 1's four options, and the paper's real question 2
            # was swallowed by it - so the paper came out with one question whose options named the
            # wrong numbers of mg/mL. See `_looks_like_a_wrap` for the measurement.
            found = None
        if found and seen_number in seen_anchors:
            # Already used: this line continues whatever is being read. Falling through to the
            # body-text branch below rather than dropping the line keeps the value in the record
            # it belongs to, which is where a reader expects to find it.
            found = None
        if found:
            number, consumed = found
            if number is not None and 0 < number <= 500:
                seen_anchors.add(number)
                last_number = max(last_number, number)
                seen_option_mark = False
                pending_option = None
                if current is not None:
                    records.append(current)
                tail = line[consumed:].strip() if chosen["style"] != "cjk_number_line" else line[consumed:].strip()
                # The line just read *is* the text before the next one, so the wrap test sees the
                # anchor's own tail. Resetting to the empty string here would make every wrap test
                # fail on exactly the line it is meant to judge.
                previous_text = line
                current = {
                    "number": number,
                    "stem": [tail] if tail else [],
                    "options": {},
                    "option_order": [],
                    "lines": [line],
                    "source": "anchor",
                }
                continue
        if current is not None:
            label = option_label(line)
            if label in _OPTION_LABELS:
                body = option_body(line)
                if label in current["options"]:
                    current.setdefault("option_collisions", []).append((label, body[:80]))
                else:
                    current["option_order"].append(label)
                current["options"][label] = (current["options"].get(label, "") + " " + body).strip()
                current["lines"].append(line)
                pending_option = label if not body else None
                previous_text = line
                continue
            if pending_option is not None:
                # The option mark above this line carried no text, so this line is that option's
                # body. Without this the text is handed to the stem and the option is left empty.
                current["options"][pending_option] = join_lines(
                    current["options"].get(pending_option, ""), line)
                current["lines"].append(line)
                pending_option = None
                previous_text = line
                continue
            if _continues_an_option(line, current, option_marks):
                # The symmetric case of the stem's wrap test above. Once an option has begun,
                # a line that opens nothing (no mark, no number) is that option's continuation,
                # not the stem's - the typesetter wrapped the option's own sentence and the
                # option is printed in two rows.
                #
                # Without this the option stops at the wrap and the continuation is appended to
                # the stem, so the stem reads `…何者正確？異常 iliac spine）…` while option A
                # reads `…導致步態`. Measured on 160 sampled papers (12,840 questions), the
                # intersection of the two engines' own readings - a loss BOTH independent readings
                # report, so it is not one parser's line-splitting - is **385 fields on 265
                # questions (2.06%), across 72 of the 160 papers**, and not one of them was
                # flagged: `quality_status=pass`, `disputes=None`. Both engines print the
                # continuation; only this loop dropped it.
                #
                # Only the LAST option that has appeared can be the one continued: `current`
                # keeps options in `option_order`, and the paper prints an option's rows before
                # the next option's mark, so nothing else is open.
                last = current["option_order"][-1] if current.get("option_order") else None
                current["options"][last] = join_lines(current["options"].get(last, ""), line)
                current["lines"].append(line)
                previous_text = line
                continue
            current["stem"].append(line)
            current["lines"].append(line)
            previous_text = line
            continue
        residual.append(line)
        previous_text = line
    if current is not None:
        records.append(current)
    # The three passes over the read items, in this order and for this reason:
    # join the lines of each stem, then divide the items the merge had glued together (a
    # number that is the very next of the run, standing inside the text of its predecessor),
    # then divide at the printer's marks that bound the options. The last of them counts the
    # options the paper declares, and so may not run before the first: a half-divided item
    # declares a shape that is not its own.
    for record in records:
        # Lines of a stem are joined by the same rule as the rows inside a line: a Chinese
        # sentence wrapped by the typesetter is one sentence, and putting a space at the
        # wrap invents a gap that is not on the paper (`中毒現` + `象。` -> `中毒現 象`).
        stem = ""
        for line in record["stem"]:
            stem = join_lines(stem, line)
        record["stem"] = stem.strip()
    records = _split_glued_records(records)
    declared = _option_count_of_paper(records)
    # The paper's own alphabet of option marks, read from the paper and not from any one item:
    # the codepoints the typesetter assigned to the four labels, which is a fact about the whole
    # print form. A run inside a single item cannot say which codepoints are labels and which are
    # lost characters, and that is exactly the case that had the options of chemistry questions
    # left in their stems.
    families = option_alphabet_families(body)
    alphabet = {code for family in families for code in family}
    for record in records:
        if record["options"]:
            continue
        # Try each candidate alphabet and keep the one that divides the item into exactly the
        # declared number of options. A paper with two private-use families needs this: the
        # question's own sub-item labels and its option labels are both runs of four, and only the
        # division that yields four options is the reading the print form supports. Refusal stays
        # the default - an item no family divides is left exactly as found.
        divided = None
        for family in families:
            divided = split_at_bullets(record["stem"], declared, alphabet=set(family))
            if divided:
                break
        if not divided:
            continue
        stem, options = divided
        record["stem"] = stem
        record["options"] = dict(zip(_OPTION_LABELS, options))
        record["option_order"] = list(_OPTION_LABELS[:len(options)])
        record.setdefault("flags", []).append("options-from-bullet-runs")
        record["bullet-evidence"] = {
            "runs": len(options),
            "declared-option-count": declared,
            "support": "paper" if declared else "corpus",      # whose count was trusted
        }
    numbers = [record["number"] for record in records]
    total_lines = len(numbers) + len(residual)
    want, claimed_max = (expected, expected) if expected else _expected_run(numbers, alignment_min)
    present = set(numbers)
    missing = [value for value in range(1, max(want, 1) + 1) if value not in present]
    diagnostics["sequence"] = {
        "count": len(numbers),
        "first": numbers[0] if numbers else None,
        "last": numbers[-1] if numbers else None,
        "duplicates": len(numbers) - len(present),
        "missing_before_first": _gaps(sorted(present)) if numbers else [],
    }
    diagnostics["count"] = len(numbers)
    diagnostics["expected"] = want
    diagnostics["claimed_max"] = claimed_max
    diagnostics["missing"] = missing[:40]
    diagnostics["residual_ratio"] = round(len(residual) / float(total_lines), 4) if total_lines else 0.0
    reasons = []
    if not records:
        reasons.append("no-anchors")
    else:
        if want and len(present) < alignment_min * want:
            reasons.append("count-%d-below-%.2f-of-expected-%d" % (len(present), alignment_min, want))
        if len(missing) > max(2, int((1 - alignment_min) * max(want, 1))):
            reasons.append("gaps-%d-in-1..%d" % (len(missing), want))
        if diagnostics["sequence"]["duplicates"] > max(1, int(0.02 * len(numbers))):
            reasons.append("duplicate-anchors-%d" % diagnostics["sequence"]["duplicates"])
        if claimed_max > want and len(present) < alignment_min * claimed_max:
            # The anchors reach further than this reading goes: a short reading of a longer
            # paper, said out loud instead of published as a paper that is simply short.
            reasons.append("read-%d-of-%d-claimed" % (len(present), claimed_max))
    if total_lines and diagnostics["residual_ratio"] > residual_budget and len(records) < 0.6 * max(want, 1):
        reasons.append("residual-%.2f-over-budget-%.2f" % (diagnostics["residual_ratio"], residual_budget))
    diagnostics["ok"] = not reasons
    diagnostics["reasons"] = reasons
    return records, residual, diagnostics


def segment_best(text, max_number=250, extra_views=()):
    """Segment `text` under the numbering style that actually yields the most questions.

    `detect_anchor_style()` on one view of the text is not enough: the print form lifts
    question numbers out of their cells, so a number can be a row of its own for one engine
    and part of a merged line for the other. The candidate styles are therefore collected
    over every view offered (`text`'s own lines plus `extra_views`, e.g. the raw extraction
    rows), and each candidate is then *tried* by segmenting the real text with it; the one
    that raises the most records with the fewest residual lines wins - measured, not
    assumed. Nothing is dropped on the way: the full candidate list stays in the
    diagnostics so a rejected paper can be audited.
    """
    text_lines = text.splitlines() if isinstance(text, str) else [str(x) for x in (text or [])]
    body = text if isinstance(text, str) else "\n".join(text_lines)
    views = [text_lines]
    for view in extra_views:
        cleaned = [str(row) for row in (view or []) if str(row).strip()]
        if cleaned:
            views.append(cleaned)
    candidates, seen = [], set()
    for view in views:
        for style in detect_anchor_styles(view, max_number=max_number, min_count=2):
            mark = (style["style"], style["count"], style["last"])
            if mark in seen:
                continue
            seen.add(mark)
            candidates.append(style)
    candidates.sort(key=lambda style: style["score"], reverse=True)
    best = None
    tried = []
    for style in candidates[:6]:
        records, residual, diagnostics = segment_questions(body, style=style)
        # Choice is by, in this declared order: (1) a reading that raises no question at
        # all is never the answer, whatever its hole count; (2) fewest holes *relative* to
        # the run it claims - an absolute hole count would let a style that found nothing
        # (0 holes) defeat one that recovered 37 of 40; (3) most questions recovered; then
        # fewest lines left unassigned. A short but complete reading (a 申論 paper of three
        # items) therefore still beats a long one full of holes, and a reading that raised
        # 79 clean anchors is never displaced by one that raised 3.
        want = diagnostics.get("expected") or 0
        holes = len(diagnostics.get("missing") or [])
        metric = (1 if records else 0, -round(holes / float(max(want, 1)), 4),
                  min(len(records), want or len(records)), -len(residual),
                  style["coverage"], style["count"])
        tried.append({"style": style["style"], "count": style["count"], "coverage": style["coverage"],
                      "records": len(records), "residual": len(residual), "ok": diagnostics.get("ok"),
                      "reasons": diagnostics.get("reasons")})
        if best is None or metric > best[0]:
            best = (metric, records, residual, diagnostics)
    if best is None:
        return [], [line.strip() for line in text_lines if line.strip()], {
            "style": None, "detail": None, "candidates": [], "tried": [],
            "ok": False, "reasons": ["no-style-candidate"], "count": 0, "expected": 0,
            "missing": [], "residual_ratio": 1.0,
        }
    _metric, records, residual, diagnostics = best
    diagnostics["candidates"] = [{"style": style["style"], "count": style["count"],
                                 "coverage": style["coverage"], "last": style["last"]}
                                for style in candidates[:6]]
    diagnostics["tried"] = tried
    return records, residual, diagnostics


# --- declared part headings: one official paper is often two papers stapled together ----
# Measured on 1001_營養師_膳食療養學.pdf: it carries `甲、申論題部分：（50 分）` with items
# 一..四 *and* `乙、測驗題部分：（50 分）` with `共40題` numbered 1..40. One style detected over
# the whole file can therefore only ever read one of the two halves - which is the second
# root cause (besides the missing `bare_number_space` style) of the 51.4% of gold records
# that were judged against a question other than the one they were about.
_SECTION_LETTERS = (_chars(0x7532), _chars(0x4E59), _chars(0x4E19), _chars(0x4E01))  # 甲 乙 丙 丁
_SECTION_WORDS = (
    _chars(0x7533, 0x8AD6, 0x984C, 0x90E8, 0x5206),   # 申論題部分
    _chars(0x6E2C, 0x9A57, 0x984C, 0x90E8, 0x5206),   # 測驗題部分
    _chars(0x9078, 0x64C7, 0x984C, 0x90E8, 0x5206),   # 選擇題部分
    _chars(0x6DF7, 0x5408, 0x984C, 0x90E8, 0x5206),   # 混合題部分
)
_QUESTION_WORD = _chars(0x984C)                        # 題, required to keep 甲、… headings rare
_SECTION_DELIMS = DOT + COMMA + COLON
_SECTION_LETTER_HEAD = re.compile(
    "^[" + _WHITE + "]*(?:" + "".join(_SECTION_LETTERS) + ")[" + _WHITE + "]*["
    + _SECTION_DELIMS + "][" + _WHITE + "]*")


def _fold_probe(text):
    """Compatibility-folded, space-free view of a line, for *matching only*.

    NFKC folds CJK Compatibility Ideographs onto their ordinary ideographs and removes the
    spacing artefacts of the print form. This view decides whether a line is a section
    head; it is never written back. The rule of this sandbox (§3, detection only) stands:
    a stored character is never rewritten, but a comparison must not be blinded by a
    code-point difference that is invisible to the eye - measured here, `甲、申論題部分`
    carries 論 as U+F941 (compatibility) in one paper and as U+8AD6 in another, and the
    unfoldered match silently missed the whole essay part of that paper.
    """
    folded = unicodedata.normalize("NFKC", text or "")
    return re.sub("[" + _WHITE + "]+", "", folded)


def is_section_head(line):
    """True for a declared part heading (`甲、申論題部分：…`, `乙、測驗題部分：…`).

    The keyword is sought in the first 24 characters of the folded line rather than in the
    whole line: a part heading is a chrome line too, and the reading-order merge can weld a
    `代號：3108` column onto its front. Seeking a whole line would let an ordinary sentence
    that merely mentions 測驗題部分 become a part boundary.
    """
    stripped = (line or "").strip()
    if not stripped:
        return False
    probe = _fold_probe(stripped)
    if not probe:
        return False
    for word in _SECTION_WORDS:
        if probe.find(word) != -1 and probe.find(word) <= 24:
            return True
    if _SECTION_LETTER_HEAD.match(stripped) and _QUESTION_WORD in probe:
        return True
    return False


def section_item_type(heading):
    """The item type a part heading declares, or None."""
    text = _fold_probe(heading)
    if _chars(0x7533, 0x8AD6) in text:                      # 申論
        return "constructed_response"
    if (_chars(0x6E2C, 0x9A57) in text or _chars(0x9078, 0x64C7) in text
            or _chars(0x6DF7, 0x5408) in text):              # 測驗 / 選擇 / 混合
        return "mct"
    return None


def split_sections(lines):
    """Split a paper into its declared parts: [(heading, [lines])].

    Anything before the first heading keeps a `(None, …)` part, so a paper without part
    headings - the great majority of pure multiple-choice papers - is passed through
    unchanged instead of being chopped up.
    """
    sections, heading, bucket = [], None, []
    for line in lines:
        if is_section_head(line):
            if bucket or heading is not None:
                sections.append((heading, bucket))
            heading, bucket = line.strip(), []
            continue
        bucket.append(line)
    if bucket or heading is not None:
        sections.append((heading, bucket))
    return sections


def segment_mixed(text, extra_views=(), max_number=250):
    """Segment a whole paper, style detected *per declared part*.

    Each part returned by `split_sections()` gets its own style ranking, so an essay part
    numbered 一、二、… and a multiple-choice part numbered 1 2 … in the same file are both
    read. Every record carries its `section` heading and an `item_type` hint, and the
    diagnostics carry one entry per part, so the reading of a mixed paper can be audited
    part by part.
    """
    lines = text.splitlines() if isinstance(text, str) else [str(x) for x in (text or [])]
    body = text if isinstance(text, str) else "\n".join(lines)
    views = list(extra_views or ())
    parts = split_sections(lines)
    if len(parts) <= 1:
        records, residual, diagnostics = segment_best(body, max_number=max_number,
                                                     extra_views=views)
        for record in records:
            record.setdefault("section", None)
            record.setdefault("item_type", None)
        diagnostics.setdefault("parts", [])
        diagnostics["paper_item_type"] = "single-part"
        return records, residual, diagnostics
    records, residual, part_reports = [], [], []
    reasons, styles = [], []
    for heading, part_lines in parts:
        blob = "\n".join(part_lines)
        if not blob.strip():
            continue
        part_records, part_residual, part_diag = segment_best(blob, max_number=max_number,
                                                              extra_views=views)
        kind = section_item_type(heading)
        for record in part_records:
            record["section"] = heading
            record["item_type"] = kind
        records.extend(part_records)
        residual.extend(part_residual)
        styles.append(part_diag.get("style"))
        reasons.extend("%s:%s" % ((heading or "(prelude)")[:12], why)
                     for why in (part_diag.get("reasons") or []))
        part_reports.append({
            "heading": heading,
            "item_type": kind,
            "style": part_diag.get("style"),
            "count": part_diag.get("count"),
            "expected": part_diag.get("expected"),
            "claimed_max": part_diag.get("claimed_max"),
            "missing": part_diag.get("missing"),
            "residual": len(part_residual),
            "ok": part_diag.get("ok"),
            "reasons": part_diag.get("reasons"),
            "tried": part_diag.get("tried"),
        })
    kinds = [p["item_type"] for p in part_reports if p["item_type"]]
    paper_type = "mixed" if len(set(kinds)) > 1 else (kinds[0] if kinds else None)
    # A cover is not a part that failed: what stands before the first declared heading is a
    # prelude (title block, instructions) and has no questions to raise. Judging a paper by a
    # part that was never meant to carry items would refuse every mixed paper in the corpus.
    decisive = [p for p in part_reports if p["heading"] or (p["count"] or 0)]
    total_lines = len(records) + len(residual)
    diagnostics = {
        "style": "+".join(str(s or "none") for s in styles),
        "detail": part_reports[0]["tried"][0] if part_reports and part_reports[0]["tried"] else None,
        "parts": part_reports,
        "paper_item_type": paper_type,
        "count": len(records),
        "expected": sum(p["expected"] or 0 for p in part_reports),
        "claimed_max": sum(p.get("claimed_max") or 0 for p in part_reports),
        "missing": [m for p in part_reports for m in (p["missing"] or [])],
        "residual_ratio": round(len(residual) / float(total_lines), 4) if total_lines else 0.0,
        "ok": all(p["ok"] for p in decisive) if decisive else False,
        "reasons": reasons,
        "candidates": [], "tried": [p["tried"] for p in part_reports],
    }
    return records, residual, diagnostics


# ---------------------------------------------------------------------------
# Geometry-based chrome masking (header/footer removal that both engines agree on)
# ---------------------------------------------------------------------------
# Why this exists instead of an index-based rule: the two engines emit very different
# line granularity for the same page (measured: 316 vs 125 rows for one 10-page paper).
# Dropping "the first 14 lines" is therefore not a symmetric operation and manufactures
# fake content disagreements. A chrome line is instead identified by a property both
# engines observe independently: *the same text at the same vertical position on many
# pages*. That criterion is engine-agnostic, so the two masked texts are comparable.
def _chrome_key(text):
    return re.sub("[" + _WHITE + "]+", "", text or "")


def _chrome_key_folded(text):
    """A chrome key with runs of digits replaced by `#`.

    A running head or footer prints the *same* text on every page except the one number that
    changes: `頁次：6－1` on page 1, `頁次：6－2` on page 2, and so on. The exact key therefore
    never repeats, and the rule that looks for repetition never fires - measured on
    `1011_醫事檢驗師_臨床血液學與血庫學`: `頁次：6－3` survived the mask and was then read as the
    continuation of option D of question 27, so option D carried the text `CD4-/CD8-頁次：6－3`.

    Folding the digits makes the key repeat, which is what the rule needs. Measured over all 282
    papers of medical technologist and pharmacist: folding finds exactly two keys - `頁次：#－#`
    on 48 papers and `代號：#` on 48 papers - and every occurrence of both is at a fixed position
    on every body page.

    A key is only foldable when it still contains a letter, which is what keeps this from being
    a licence to delete question content: a stem, an option body or a figure axis label that
    happens to repeat at one height would have to be *pure punctuation and digits* to be caught,
    and no such key exists in the corpus (measured: the set is empty). The letter is required
    because the chrome being folded is a label - `頁次` (page), `代號` (paper code) - and a table
    of numbers with no word in it is as likely to be content as chrome.
    """
    folded = re.sub(r"[0-9０-９]+", "#", _chrome_key(text))
    return folded if any(character.isalpha() for character in folded) else ""


def _is_bullet_only(text):
    """Whether a row carries nothing but the typesetter's marks.

    A row of marks and whitespace only is usually the chrome of a form that drew its bullets
    with a private-use font - the marks are the whole row, so the row is punctuation and not
    content.

    **But one row of four marks is a real option row when the options *are* marks.** Measured on
    `1001_醫事檢驗師_臨床血清免疫學與臨床病毒學` Q49, a multi-answer question whose options name
    sets of the question's own sub-items:

        Q49 下列那些感染是藉由老鼠傳染的？\ue000砂粒病毒 \ue001漢他病毒 \ue002西尼羅病毒（West Nile virus）
        \ue18c\ue000\ue001   \ue18d\ue000\ue002   \ue18e\ue001\ue002   \ue18f\ue000\ue001\ue002

    The option row holds no letters, digits or ideographs at all - every character of it is a
    mark - so the rule above deleted it, the question kept its options in the stem, and the gate
    reported a question with no options. The same shape appears on 16 rows over 59 papers, all of
    them picture-option questions (`下列何圖為最正確？`) where the picture object is not in the
    text layer at all.

    What separates the two is **how many distinct marks the row uses**. Chrome is a single drawing
    repeated, or a stray mark the font failed to map; an option row spells out the alternatives
    with *different* marks, one per option, so it uses at least two. Measured over the corpus: 328
    mark-only rows, of which 27 use a single distinct code (chrome - `\uf073`, `\ue12a`, a lone
    `\ue18d` on a line whose option text went elsewhere) and the rest use two or more. A row with
    one distinct mark stays chrome.
    """
    stripped = _chrome_key(text)
    if not stripped:
        return True
    if not all(
        (0xE000 <= ord(ch) <= 0xF8FF) or (0xFFF000 <= ord(ch) <= 0x10FFFF) or ord(ch) in (0x203B,)
        for ch in stripped
    ):
        return False
    return len({ch for ch in stripped if 0xE000 <= ord(ch) <= 0xF8FF}) < 2


def mask_chrome(rows, *, min_pages=3, y_tol=4.0, body_floor=None):
    """Return `(kept_rows, dropped_audit)`.

    `rows` are engine rows carrying page/x0/y0/text. A row is chrome when it is printed on
    every page of the paper except the first, at (nearly) the same vertical position - or
    when it is only a private-use-area bullet glyph / punctuation run. Question anchors are
    never dropped, whichever rule would otherwise match.

    "Every page except the first" is a property of the print form, not a fitted threshold.
    A form that repeats a header or footer repeats it on *all* the pages it applies to; the
    cover page carries the title instead. Measured over the 282 papers of medical technologist
    and pharmacist: 48 of the 54 rows that recur at a fixed position occur on exactly
    `pages[1:]`, and the 6 that do not are not chrome at all - they are option labels.

    The rule used to be "recurs on at least three pages". On `1062_醫事檢驗師_生物化學與臨床生化學`,
    whose questions wrap at the foot of the page, the leftover `A.` `C.` `D.` of three
    truncated option sets all landed on `y=805.6`, which is exactly where the form prints its
    footer - so three of twelve pages was enough, and the labels were deleted as chrome. Five
    papers lost 20 option sets that way. The failing rows were real chrome and real question
    content at the same position on different pages, and no distance or count can separate
    them; what separates them is that the footer is on every page and an option label is not.
    """
    pages = []
    for row in rows:
        if row.get("page") not in pages:
            pages.append(row.get("page"))
    if len(pages) < 2:
        min_pages = max(2, min_pages - 1)
    # The page every other page's chrome shares. With one page there is no body page at all
    # and with two the body is the second, which is why the count rule is kept *alongside*
    # this one rather than replaced by it: on a one-page document every key occurs once, and
    # a subset test against the empty set would call the whole sheet chrome. Both conditions
    # are required - printed on every body page, and printed more than once.
    body_pages = set(pages[1:]) if len(pages) >= 2 else set()

    positions = {}
    folded_positions = {}
    for row in rows:
        key = _chrome_key(row.get("text"))
        if not key or len(key) > 60:
            continue
        positions.setdefault(key, []).append((row.get("page"), float(row.get("y0") or 0.0)))
        # The same test on a digit-folded key, so a footer whose only change from page to page is
        # its page number is recognised as the one running head it is. Both keys are recorded for
        # every row and either one matching is enough; the folded key is never more permissive in
        # the *position* test, only in the text it is willing to call the same text.
        folded = _chrome_key_folded(row.get("text"))
        if folded and len(folded) <= 60:
            folded_positions.setdefault(folded, []).append(
                (row.get("page"), float(row.get("y0") or 0.0)))

    chrome_keys = set()
    for table in (positions, folded_positions):
        for key, hits in table.items():
            distinct_pages = {page for page, _ in hits}
            if not distinct_pages >= body_pages or len(distinct_pages) < min_pages:
                continue
            ordinates = sorted(y for _, y in hits)
            spread = ordinates[-1] - ordinates[0]
            tight = spread <= y_tol or (ordinates[len(ordinates) // 2] - ordinates[0]) <= y_tol
            if tight:
                chrome_keys.add(key)

    kept, dropped = [], []
    for row in rows:
        key = _chrome_key(row.get("text"))
        folded = _chrome_key_folded(row.get("text"))
        reason = None
        if (key and key in chrome_keys) or (folded and folded in chrome_keys):
            reason = "recurring-at-fixed-position"
        elif _is_bullet_only(row.get("text")):
            reason = "bullet-or-punctuation-only"
        elif is_notice_line(row.get("text") or ""):
            reason = "notice"
        elif body_floor is not None and float(row.get("y0") or 0.0) < body_floor and not _style_matches("bare_number_line", row.get("text") or ""):
            reason = "above-body-floor"
        if reason and not _looks_like_anchor(row.get("text") or ""):
            dropped.append({"page": row.get("page"), "y0": row.get("y0"), "rule": reason, "text": (row.get("text") or "")[:120]})
            continue
        kept.append(row)
    return kept, dropped


def _looks_like_anchor(text):
    return any(_style_matches(name, text) for name in _ANCHOR_TEMPLATES)


# ---------------------------------------------------------- joining lines of CJK text
#
# A Chinese paragraph wraps mid-sentence, and the two halves are different rows. Joining them
# with a space invents a gap the paper does not have: `所產生的中毒現` + `象。` became
# `中毒現 象`. Chinese is not written with spaces, so a space between two CJK characters is
# always wrong - but a space between a Latin word and a CJK one is not (`anion gap 上升`),
# which is why the rule is stated as a property of the boundary and not as "never insert a
# space".
#
# Super- and subscripts are *not* handled here. They are resolved in `extract.read_spans`,
# while the span geometry is still available, because by the time a row exists the per-span
# sizes are gone. There used to be a second, geometry-guessing implementation in this module
# and it got `[PO₄³⁻]` wrong in both directions; there is now one place that decides, and it
# is the place that can still see the spans.
#
# The boundary class for "no space here" is CJK ideographs plus CJK and fullwidth
# *punctuation* - and deliberately **not** fullwidth letters or digits (`Ａ` U+FF21, `５`
# U+FF15). Those look fullwidth but behave like Latin tokens: the answer sheet is a grid
# whose cells are `答案Ｄ`, `Ｂ`, `Ａ` on one baseline, and treating them as Chinese glued
# the whole row into `答案ＤＢＡＡＣ` and destroyed the table. A space between two cell
# values is the separator that makes the row readable, so it has to survive.
_CJK = re.compile(
    "[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"      # ideographs
    "\u3000-\u303f"                                  # CJK punctuation: 。、「」
    "\uff01-\uff0f\uff1a-\uff20\uff3b-\uff40\uff5b-\uff65]"  # fullwidth punct, not A-Z/0-9
)


def join_lines(left, right):
    """Join two extracted lines the way the paper set them.

    A space goes between them only when one side is not CJK. `中毒現` + `象。` is one word
    split by the typesetter and reads `中毒現象`; `anion gap` + `上升` is two tokens and keeps
    its space.
    """
    left, right = (left or "").rstrip(), (right or "").lstrip()
    if not left:
        return right
    if not right:
        return left
    if _CJK.search(left[-1]) and _CJK.search(right[0]):
        return left + right
    return left + " " + right


def text_from_rows(rows):
    """Deterministic reading order: page, then top-to-bottom, then left-to-right."""
    ordered = sorted(
        enumerate(rows),
        key=lambda pair: (
            pair[1].get("page") or 0,
            round(float(pair[1].get("y0") or 0.0) / 2.0),
            float(pair[1].get("x0") or 0.0),
            pair[0],
        ),
    )
    lines = []
    current = None
    bucket = None
    for _index, row in ordered:
        page = row.get("page")
        y_bucket = round(float(row.get("y0") or 0.0) / 2.0)
        if (page, y_bucket) != (current, bucket):
            lines.append((row.get("text") or "").strip())
            current, bucket = page, y_bucket
        else:
            lines[-1] = join_lines(lines[-1], (row.get("text") or "").strip())
    return "\n".join(line for line in lines if line)
