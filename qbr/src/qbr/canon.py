"""Normalisation of the three sources into one comparable canonical record.

The three sources compared are:

  L  legacy machine output   (exam.question_candidates.normalized_candidate_json,
                               produced by the MinerU-era pipeline)
  H  human-approved record    (exam.question_review_events.corrected_candidate_json,
                               latest event per candidate)
  P  the new method           (the official PDF text layer, extracted by qbr.extract/repair)

Rules applied (documented here, applied consistently, and never silently):

  1. Unicode normalisation: NFKC, so full-width Latin and punctuation are folded to their
     half-width forms. CJK ideographs are not decomposable and therefore pass through
     unchanged - a variant ideograph is a different code point and stays visible as one.
  2. Invisible characters (control, zero-width, BOM, soft hyphen) are removed.
  3. Whitespace runs are collapsed to a single space; the ideographic space becomes a space.
     Leading/trailing presentation punctuation is stripped.
  4. Option labels are unified: "A." "A、" "A．" "(A)" "（A）" "A)" "A：" all normalise to
     the label "A". A run of options is split at the label boundaries, in ascending order,
     restarting at "A" (so a label inside prose does not create a phantom option).
  5. If an option run was merged into the stem by the legacy parser, it is separated out
     and the remainder stays as the stem. This is recorded as a flag, not hidden.
  6. The answer set is normalised to a sorted tuple of labels.
  7. Nothing is corrected: these functions only make the three forms comparable. Where a
     form looks wrong, it is reported with a flag so that it can be adjudicated.

The comparison rule (agreed with the data owner): P - the PDF text layer obtained by the
new method - is the authority. Deviations of L and H from P are measured separately.
"""

import re
import unicodedata

LABELS = ("A", "B", "C", "D", "E", "F")

_CONTROLS = "".join("\\x%02x" % code for code in list(range(0x00, 0x09)) + [0x0B, 0x0C] + list(range(0x0E, 0x20)) + [0x7F])
_INVISIBLE = re.compile("[" + _CONTROLS + "]")
_ZERO_WIDTH = re.compile(
    "[" + "".join("\\u%04x" % code for code in (0x200B, 0xFEFF, 0x00AD, 0x202A, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064)) + "]+"
)
_SPACES = re.compile(r"\s+")
_EDGE_PUNCT = re.compile(r"^[\s。．，、；;：:．\-—–_]+|[\s。．，、；;：:．\-—–_]+$")

# --- presentation markup of the legacy pipeline, folded away for comparison -------------
# Why this belongs to the canonical form and not to a repair rule: the human reviewers who
# cleaned a formula wrote `GABA<sub>A</sub> 受體` precisely because the meaning is the
# subscript A - the markup is how that is recorded in the text field. The PDF text layer has
# no such tags at all (it carries the glyph itself), so an un-folded comparison charged the
# reviewer with altering the official text: 24.9% of the gold sample was reported as
# `human-drift-from-pdf` for this reason alone (measured 2026-09-13). Nothing is rewritten
# in the stored record; the tags are removed only in the comparison view.
_MARKUP_TAGS = (
    "sub", "sup", "u", "b", "i", "em", "strong", "small", "big", "tt", "code",
    "span", "div", "p", "font", "center", "ul", "li", "table", "tr", "td", "th",
    "img", "math", "mi", "mn", "mo", "mtext", "ms", "mfrac", "msqrt", "msup", "msub",
    "chem", "ce", "cf", "mx",
)
_MARKUP_VOID = ("br", "hr", "img", "wbr")
# The void elements must be in the alternation too, or `<br>` matches nothing at all and
# survives the fold - measured the hard way, on the first run over the gold sample.
_MARKUP_ALL = tuple(dict.fromkeys(list(_MARKUP_TAGS) + list(_MARKUP_VOID)))
# The whitespace around a tag belongs to the tag, not to the word: a tag that only
# disappears leaves a space behind, and that space would then be counted as a
# difference between the reading and the official text (`GABA<sub>A</sub> 受體` against
# `GABAA受體`). A void element is the exception - it stands for a line break, so it
# leaves one space in its place.
_MARKUP_TAG = re.compile(r"\s*</?\s*(" + "|".join(_MARKUP_ALL) + r")\b[^<>]*>\s*", re.IGNORECASE)
_MARKUP_OPEN = re.compile(r"<\s*(" + "|".join(_MARKUP_ALL) + r")\b", re.IGNORECASE)
_MARKUP_CLOSE = re.compile(r"</\s*(" + "|".join(_MARKUP_ALL) + r")\s*>", re.IGNORECASE)
_ENTITIES = re.compile(r"&(#[0-9]{1,7}|x[0-9A-Fa-f]{1,6}|nbsp|amp|lt|gt|quot|apos|mdash|ndash|hellip|middot|deg|plusmn|times|divide|alpha|beta|gamma|delta);")
_ENTITY_MAP = {
    "nbsp": " ", "amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'",
    "mdash": "\u2014", "ndash": "\u2013", "hellip": "\u2026", "middot": "\u00B7",
    "deg": "\u00B0", "plusmn": "+/-", "times": "\u00D7", "divide": "\u00F7",
    "alpha": "\u03B1", "beta": "\u03B2", "gamma": "\u03B3", "delta": "\u03B4",
}


def _entity_repl(match):
    """One character reference or named entity, resolved; unknown ones left alone."""
    token = match.group(1)
    if token in _ENTITY_MAP:
        return _ENTITY_MAP[token]
    try:
        code = int(token[1:], 16) if token[0] in "xX" else int(token, 10)
    except ValueError:
        return match.group(0)
    if 0 < code <= 0x10FFFF:
        try:
            return chr(code)
        except (ValueError, OverflowError):
            return match.group(0)
    return match.group(0)


def strip_markup(text):
    """Remove presentation markup that is recognisable as markup - and only that.

    A tag is dropped when it is a void element (`<br>`, `<hr>`, `<img ...>`), when the
    field pairs it (`<sub>A</sub>`), or when the same name opens at least twice without a
    single close, which is how the legacy pipeline wrote a subscript: `H<sub>2<sub>O`.
    An isolated angle bracket stays exactly where it is. A chemistry or physics option
    reading `A<B 且 C>D` must not lose its inequality signs to a tag-stripping pass, and a
    field that genuinely carries a tag does carry the counterpart form of it as well.
    """
    if not text:
        return text or ""

    def _one_pass(source):
        lowered = source.lower()
        opens = [m.lower() for m in _MARKUP_OPEN.findall(lowered)]
        closes = [m.lower() for m in _MARKUP_CLOSE.findall(lowered)]

        def _drop(match):
            name = match.group(1).lower()
            if name in _MARKUP_VOID:
                return " "
            if name in opens and name in closes:
                return ""                       # paired: <sub>A</sub>
            if opens.count(name) >= 2 and not closes:
                return ""                       # opened twice, never closed: H<sub>2<sub>O
            return match.group(0)              # an inequality, not markup

        return _MARKUP_TAG.sub(_drop, source)

    stripped = _one_pass(text) if "<" in text else text
    # Entities are unescaped whether or not a single angle bracket stands in the field: a
    # hard space written `&nbsp;` is as much a spacing artefact as one written as a space.
    stripped = _ENTITIES.sub(_entity_repl, stripped)
    if "<" in stripped:
        stripped = _one_pass(stripped)           # markup hidden as &lt;sub&gt;
    return stripped


# A label must be followed by a delimiter, and must start a segment, so that a lone
# latin letter inside prose (e.g. "C 肽", "A 抗原") is not mistaken for a label.
_LABEL = re.compile(r"(?:^|(?<=[\s。．，、；;：:（）\)]))[\(\[]?\s*([A-Fa-f])\s*[\)\]]?\s*(?:[.．、。:：])\s*")

# The word the official sheets put over the column of question numbers. 答案 sheets print
# 題號; a 更正答案 sheet - which is a re-published, corrected key table, not a fresh one -
# prints 題序 instead, and was read as an empty table until this was declared: measured, 7 of
# 12 sampled correction sheets parsed to nothing at all, so the corrections they carry never
# reached the comparison and the original key went unchallenged.
# The words the official sheets put over the column of question numbers. 答案 sheets print
# 題號; a 更正答案 sheet - a re-published, corrected key table, not a fresh one - prints 題序
# instead. Both must be declared, and in both scripts: measured, 7 of 12 sampled correction
# sheets parsed to nothing at all while this stood undeclared, so the corrections they carry
# never reached the comparison and the superseded key went unchallenged.
_TABLE_HEAD_WORDS = ('題號', '题号', '題序', '题序')
_ANSWER_MARKS = ('答案', '解答', '答', 'key', 'answer')
_ANSWER_LEAD_CODES = tuple(range(0x30, 0x3A)) + (0x20, 0x09, 0x3010, 0x3011, 0x005B, 0x005D, 0x0028, 0x0029,
                      0x007B, 0x007D, 0x3008, 0x3009, 0xFF1A, 0x003A, 0x3001, 0x3002,
                      0xFF0C, 0x002E, 0x002D, 0x300C, 0x300D, 0x2028, 0x2029, 0x202A,
                      0x202B, 0x202C, 0x202D, 0x202E, 0x202F)
_ANSWER_LEAD_CLASS = "".join(re.escape(chr(code)) for code in _ANSWER_LEAD_CODES)
_ANSWER = re.compile(
    "(?:" + "|".join(sorted(_ANSWER_MARKS, key=len, reverse=True)) + ")["
    + _ANSWER_LEAD_CLASS + "]{0,6}([A-Fa-f]+)(?:[" + _ANSWER_LEAD_CLASS + "]|$)",
    re.IGNORECASE,
)


def fold(value):
    """Canonical form of one text field. Deterministic; idempotent; never raises."""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    text = unicodedata.normalize("NFKC", value)
    text = strip_markup(text)                       # <sub>A</sub> -> A, but A<B stays
    text = _INVISIBLE.sub("", text)
    text = _ZERO_WIDTH.sub("", text)
    text = text.replace("\u3000", " ")
    text = _SPACES.sub(" ", text)
    text = _EDGE_PUNCT.sub("", text)
    return text.strip()


# ---------------------------------------------------------------- the image used in comparison
#
# `fold` is the canonical form of a *field*: what is written down, presentation included, since a
# reviewer is entitled to see the markup the reviewer corrected against. Comparison of two
# records needs a harsher image, and it is declared here in full rather than improvised at the
# place of use, because two things that are not the text at all stand between a question and the
# reading of it:
#
#   * the private-use planes. The bullets that mark the options of a printed paper
#     (\ue18c \ue18d \ue18e \ue18f and their fellows, U+E000..) are printer's marks; they carry
#     structure, not characters, and the embedded ToUnicode maps of these files send them to
#     nothing. Measured: they stand in the way of 76.8% of the judged records as
#     `missing-labels:A,B,C,D`. Left inside a comparison they are a run of noise between the
#     option texts, and they are what pulled a genuine question below the alignment floor.
#   * whitespace, which in this corpus records only where the line happened to break.
#
# Taking them out is a decision about comparison, and about comparison only: what is stored keeps
# its glyphs, and no character of the record is rewritten (the rule of this sandbox, and the
# reason the character maps of the legacy pipeline were retired from it).

_PRIVATE_USE = ((0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD))


def is_bullet(char):
    """Whether a character is one of the typesetter's private-use marks (the option bullets)."""
    point = ord(char)
    return any(low <= point <= high for low, high in _PRIVATE_USE)


def bullet_runs_of(text):
    """Where the printer's private-use marks stand in a string, as [(start, stop), ...].

    Declared here rather than borrowed from `repair`, because both the division of an item at
    them (which is the segmenter's business, and lives there) and the saying of whether a stem
    is still polluted by them (which is the comparison's business, and lives here) need to know
    where they are, and the two must not disagree about what one of them is.
    """
    runs = []
    start = None
    for position, char in enumerate(text or ""):
        if is_bullet(char):
            if start is None:
                start = position
        elif start is not None:
            runs.append((start, position))
            start = None
    if start is not None:
        runs.append((start, len(text)))
    return runs


def strip_private(text):
    """Take out the codepoints no reader can read, leaving the text that a reader can."""
    if not text:
        return ""
    kept = []
    for character in text:
        point = ord(character)
        if any(low <= point <= high for low, high in _PRIVATE_USE):
            continue
        kept.append(character)
    return "".join(kept)


def comparable(value):
    """The single string two texts are reduced to before they are ever compared."""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = strip_private(fold(value))
    return _SPACES.sub("", text)


def containment(part, whole):
    """How much of `part` is to be found inside `whole`, as a fraction of `part`.

    A similarity is the wrong question when one of the two texts is a piece of the other, and it
    is the common case here: the item the segmenter recovers carries the question *and* its options
    in one string (the options have no readable bullets to split them on), while the record being
    aligned against it is the question alone. Compared as they stand the ratio is diluted by the
    options and comes out at 0.5, and a record that names the very question it is the copy of is
    quarantined for want of a better measure. This is that measure: the fraction of the shorter
    text the matching blocks account for.
    """
    from difflib import SequenceMatcher
    a, b = comparable(part), comparable(whole)
    if not a:
        return 0.0
    if not b:
        return 0.0
    if a in b:
        return 1.0
    matcher = SequenceMatcher(autojunk=False)
    matcher.set_seq2(b)
    matcher.set_seq1(a)
    # A matching block is the triple (start-in-the-first, start-in-the-second, length): it is
    # the *length* that says how much was matched. The first two are positions in two different
    # sequences and mean nothing apart, and a measure built on their difference is not a
    # fraction at all - it can exceed one, and did: the ratios this measure reported ran to
    # 1.22, 1.79 and 19.84 for pairs of questions between which there was nothing of the
    # kind, and the alignment floor of 0.90 let every one of them through, judged.
    matched = sum(size for _first, _second, size in matcher.get_matching_blocks())
    return min(1.0, matched / float(len(a)))


def parse_labels(text):
    """Return [(label, text, start)] found in `text`, in ascending label order.

    The run restarts at "A": a second occurrence of "A" starts a new item, which is how a
    human reader segments an exam page. Labels that are out of order are ignored.
    """
    if not text:
        return []
    hits = [(m.group(1).upper(), m.start(), m.end()) for m in _LABEL.finditer(text)]
    picked = []
    expected = 0
    for label, start, end in hits:
        if label == "A" and picked:
            picked = []
            expected = 0
        if not picked:
            if label not in LABELS:
                continue
            expected = LABELS.index(label)
        if LABELS.index(label) == expected:
            picked.append((label, start, end))
            expected += 1
            if expected >= len(LABELS):
                break
    out = []
    for index, (label, start, end) in enumerate(picked):
        stop = picked[index + 1][1] if index + 1 < len(picked) else len(text)
        out.append((label, text[end:stop].strip(), start))
    return out if len(out) >= 2 else []


def separate(stem_text):
    """Split a stem that has an option run merged into it. Returns (stem, options)."""
    text = stem_text or ""
    found = parse_labels(text)
    if not found:
        return text.strip(), {}
    cut = found[0][2]
    stem = text[:cut]
    options = {}
    for label, body, _start in found:
        options[label] = body
    return stem.strip(), options


def extract_answer(text):
    """The answer set of a block of text, as a tuple of labels, or None."""
    if not text:
        return None
    match = _ANSWER.search(text)
    if not match:
        return None
    raw = next((group for group in match.groups() if group), "")
    labels = tuple(sorted({ch.upper() for ch in raw if ch.upper() in LABELS}))
    return labels or None


def as_label_set(value):
    """Any spelling of an answer key - "B", ["B","A"], "AB", "（A）（B）" - to a sorted tuple."""
    if value is None:
        return None
    items = value if isinstance(value, (list, tuple)) else [value]
    letters = []
    for entry in items:
        for ch in str(entry):
            top = ch.upper()
            if top in LABELS and top not in letters:
                letters.append(top)
    return tuple(sorted(letters)) or None


def _options_from_blob(blob):
    """Accept the several shapes an option container may have in a JSON blob."""
    if isinstance(blob, dict):
        items = blob.items()
    elif isinstance(blob, list):
        items = []
        for entry in blob:
            if isinstance(entry, dict):
                label = str(entry.get("label") or entry.get("key") or entry.get("letter") or "").strip().upper()
                body = entry.get("text")
                if body is None:
                    body = entry.get("value") or entry.get("content")
                items.append((label, body))
            elif isinstance(entry, str):
                items.append(("", entry))
    else:
        return {}
    out = {}
    for label, body in items:
        label = (str(label).strip().upper()[:1] or "")
        if label and label not in LABELS:
            label = ""
        text = fold(body if isinstance(body, str) else ("" if body is None else str(body)))
        if not label:
            inner = parse_labels(text)
            if inner:
                for sub_label, sub_body, _ in inner:
                    out[sub_label] = fold(sub_body)
                continue
            label = chr(ord("A") + len(out))
        if label in out and text:
            out[label] = (out[label] + " " + text).strip()
        else:
            out[label] = text
    return {k: v for k, v in out.items() if k in LABELS}


def _answer_from_blob(container, source):
    """Answer of a blob: explicit field first, then a mark inside the stem."""
    if isinstance(container, dict):
        for key in ("answer", "correct_answer", "answer_key", "answers", "key"):
            if key in container:
                value = container.get(key)
                if isinstance(value, (list, tuple)):
                    raw = "".join(str(v) for v in value)
                else:
                    raw = str(value or "")
                labels = tuple(sorted({ch.upper() for ch in raw if ch.upper() in LABELS}))
                if labels:
                    return labels
    text = container.get("stem") if isinstance(container, dict) else None
    return extract_answer(text or "")


def record(source, blob, pdf=None, answer_authority=None):
    """Build one canonical record.

    source is "L", "H" or "P"; blob is the parsed JSON (for L and H) or the item dict
    produced by qbr.repair.segment_mixed (for P); pdf is the paired official record when
    available (category, subject, year, file, page) and is only carried along.
    `answer_authority` is the answer as printed on the official answer sheet (or, where one
    exists, on the 更正答案 sheet) - the only source that may decide what the answer is.
    """
    flags, warnings = [], []
    if source == "P":
        stem_raw = (blob or {}).get("stem") or ""
        options_raw = (blob or {}).get("options") or {}
        number = (blob or {}).get("number")
        answer_raw = None
        # An item whose options were not divided at the printer's marks keeps them inside its
        # stem, and then the stem is not a stem: it is a question with a list of answers
        # appended. Comparing such a string against the question alone measures the appendage,
        # not the question, and calls it a change of the text. Said so on the face of the
        # record, and `compare` refuses to pass judgement on those two fields because of it.
        if not options_raw and _SPACES.sub("", fold(stem_raw)) and bullet_runs_of(stem_raw):
            flags.append("stem-carries-unsplit-bullets")
    else:
        data = blob if isinstance(blob, dict) else {}
        stem_raw = data.get("stem") or data.get("stem_text") or data.get("question") or ""
        options_raw = data.get("options")
        number = data.get("number") or data.get("question_number")
        answer_raw = data

    stem = fold(stem_raw)
    options = _options_from_blob(options_raw)

    if options_raw in (None, {}, []) and stem:
        separated_stem, separated_options = separate(stem)
        if separated_options:
            options = separated_options
            stem = separated_stem
            flags.append("options-merged-into-stem")

    if not options:
        # last resort: the run may still sit at the end of the stem
        tail_stem, tail_options = separate(stem_raw if isinstance(stem_raw, str) else str(stem_raw))
        if tail_options:
            options = tail_options
            if fold(stem_raw) != fold(tail_stem):
                flags.append("options-merged-into-stem")
            stem = fold(tail_stem)

    # The answer of the P leg is *not* read out of the question text. `extract_answer()` on
    # the stem is the machine guessing the key from the same text it is being judged on -
    # self-referential, exactly what the protocol forbids (§16) - and measured, it returned
    # nothing at all for 99% of the records, which is why the answer column of the
    # comparison reported "4,966 legacy errors fixed by hand" when in truth the authority
    # simply was absent. The authority for an answer is the official answer sheet; the
    # caller injects it through `answer_authority`.
    if source in ("L", "H"):
        answer = _answer_from_blob(answer_raw, source)
    else:
        answer = None
    if source == "P" and answer_authority is not None:
        answer = as_label_set(answer_authority)
    labels = tuple(sorted(k for k in options if k in LABELS))
    if labels:
        missing = tuple(ch for ch in LABELS[: max(len(LABELS), len(labels))] if ch not in labels)
        if missing and len(labels) < 4:
            warnings.append("missing-labels:%s" % ",".join(missing))
        if any(not options.get(label) for label in labels):
            warnings.append("empty-option")
    else:
        warnings.append("no-options")

    return {
        "source": source,
        "number": number,
        "stem": stem,
        "options": {k: options[k] for k in LABELS if k in options},
        "answer": list(answer) if answer else None,
        "flags": flags,
        "warnings": warnings,
        "pdf": pdf or {},
    }


def serialise(record_, field):
    """Stable textual form of one field, used for equality between records."""
    if field == "options":
        return "|".join("%s=%s" % (k, record_["options"][k]) for k in LABELS if k in record_.get("options") or {})
    if field == "answer":
        value = record_.get("answer")
        # None stays None: "this witness has no answer at all" is a finding, whereas ""
        # would be "this witness answers with no letter", and the two must not be read alike.
        return None if value is None else ",".join(value)
    return record_.get(field) or ""


AGREE = 0.90   # 相似比阈值：比较二者，相似达到此标准即判为一致


def _similar(a, b, field):
    """是否（is）判定两个字段是否一致。长（stem）句用相似比，短（options/answer）句用严格相等。"""
    if a is None or b is None:
        return a == b
    if a == b:
        return True
    if field == "stem":
        return ratio(a, b) >= AGREE
    return False


def _presence(value):
    """None for a field that carries nothing, the value for a field that speaks.

    An absent witness and a silent one are not the same witness as one that testifies to
    something else. Until this was distinguished here, a human record with no stem written in
    it fell through to `human-drift-from-pdf` - the reviewer, having written nothing at all,
    was recorded as having changed the official text. Measured on the sample of 7,169:
    1,074 of the 1,075 "drifts" of the stem, and 848 of the 854 of the options, were of this
    kind, a testimony of emptiness read as a testimony of disagreement. They are now
    `not-reviewed`, which is what they are, and the drift that remains is drift.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return None if not comparable(value) else value
    if isinstance(value, (list, tuple, dict, set)):
        return None if not value else value
    return value


def _verdict(l, h, p, field=None):
    if p is None:
        return "no-P"
    if h is None:
        return "not-reviewed" if _similar(l, p, field) else "legacy-only-differs"
    if _similar(l, h, field) and _similar(h, p, field):
        return "agree"
    if _similar(p, h, field) and not _similar(l, p, field):
        return "legacy-error-fixed-by-human"
    if _similar(p, l, field) and not _similar(h, p, field):
        return "human-drift-from-pdf"
    if _similar(l, h, field) and not _similar(p, l, field):
        return "error-in-both-vs-pdf"
    return "divergent"


# The two readings of one item must be about the same item before either may be judged.
# Measured on the gold sample: with no floor under the match ratio, 51.4% of the records
# were compared against a question other than the one they named, and the whole verdict
# table of that run is noise (34.7% of stems "divergent", 24.1% "error in both" - figures
# that collapse to 0.3% and 0% once the alignment is required to be real).
ALIGN_MIN = 0.90
UNALIGNED = "unaligned-quarantine"
# The two fields of a witness that was heard, but whose question could not be read out of
# it: the stem of a PDF item whose options were not divided at the printer's marks is a
# question with a list of answers appended to it, and on such a stem no comparison of the
# question means anything. Refused, with the ground of the refusal carried along with the
# record - which is what distinguishes the refusal from an agreement, and from a dispute.
INCOMPARABLE = "incomparable-unsplit-options"


def compare(rec_l, rec_h, rec_p, answer_authority=None, aligned=True):
    """Field-by-field comparison of the three canonical records.

    `answer_authority` is the answer printed on the official answer sheet, and it alone
    decides the answer field; `aligned` says whether the PDF witness was established to be
    speaking about this very question. A paper that is not aligned is quarantined whole -
    no field of it is judged, because a judgement passed on the wrong question is worse
    than no judgement at all (§16.3: quarantine, never guess).
    """
    have_p = rec_p is not None and (rec_p.get("stem") or rec_p.get("options"))
    if answer_authority is not None and rec_p is not None:
        rec_p = dict(rec_p)
        rec_p["answer"] = list(record("P", {}, answer_authority=answer_authority)["answer"] or [])
    out = {}
    polluted = bool(rec_p is not None and "stem-carries-unsplit-bullets" in (rec_p.get("flags") or []))
    if not aligned:
        for field in ("stem", "options", "answer"):
            out[field] = UNALIGNED
        out["has-pdf"] = bool(have_p)
        out["alignment"] = "rejected"
        out["flags"] = sorted(set((rec_l or {}).get("flags", []) + (rec_h or {}).get("flags", [])
                                 + ((rec_p or {}).get("flags", []) if have_p else [])))
        out["warnings"] = ["alignment-below-floor"]
        return out
    for field in ("stem", "options", "answer"):
        l = _presence(serialise(rec_l, field) if rec_l else None)
        h = _presence(serialise(rec_h, field) if rec_h else None)
        p = _presence(serialise(rec_p, field) if have_p else None)
        out[field] = _verdict(l, h, p, field)
    if polluted:
        # The identity of the question may well have been established - the number, and the
        # text of the item, put the witness on the right question - and yet the two fields that
        # a divided stem would have made sense of are not in a state to be judged: the options
        # stand inside the stem, because the marks that should have divided them were not read.
        # Refused, and the reason written on the record, rather than a verdict passed on two
        # strings that are not comparable (defect 11, reports/DEFECTS-AND-FIXES.md).
        out["stem"] = INCOMPARABLE
        out["options"] = INCOMPARABLE
        out["incomparable-because"] = "stem-carries-unsplit-bullets"
    out["has-pdf"] = bool(have_p)
    out["alignment"] = "accepted"
    out["flags"] = sorted(set((rec_l or {}).get("flags", []) + (rec_h or {}).get("flags", []) + (rec_p or {}).get("flags", [])))
    out["warnings"] = sorted(set((rec_l or {}).get("warnings", []) + (rec_h or {}).get("warnings", []) + (rec_p or {}).get("warnings", [])))
    return out


def ratio(a, b):
    """Similarity of two canonical strings, for ranking only - never as a gate."""
    from difflib import SequenceMatcher

    if not a or not b:
        return 0.0
    matcher = SequenceMatcher()
    matcher.autojunk = False
    matcher.set_seq2(b)
    matcher.set_seq1(a)
    return matcher.ratio()


def merge_answer_tables(*tables):
    """One answer key out of several sheets, later sheets overriding earlier ones.

    Ordered as the official hierarchy of the sheets themselves: 答案 first, then 更正答案,
    which prevails wherever it speaks for a question number it covers. Nothing is invented:
    a number no sheet answers is simply absent from the merged key, and the caller reports
    it as such instead of guessing from the neighbour.
    """
    merged = {}
    for table in tables:
        for number, labels in (table or {}).items():
            if labels:
                merged[int(number)] = tuple(labels)
    return merged


def parse_answer_table(text):
    """Read the official 題號/答案 grid of an answer sheet.

    These sheets print one header row of question numbers and one row of answer letters
    (sometimes wrapped across several lines). Numbers and letters are collected in reading
    order and zipped positionally; a count mismatch is kept visible through `None` entries
    rather than guessed.
    """
    lines = [fold(line) for line in (text or "").splitlines()]
    lines = [line for line in lines if line]
    start = None
    for position, line in enumerate(lines):
        compact = line.replace(" ", "").replace("\u3000", "")
        if any(word in compact for word in _TABLE_HEAD_WORDS):
            start = position
            break
    if start is None:
        return {}
    numbers, units, notes = [], [], {}
    # 修正（宏觀辨「答案卷」）：答案行須以「空白」為介質，切分為「答案單元」，
    # 使「多選」（如「AB」）之答案「整體保留」，戒「逐字母拆散」。
    # 原 findall(r"[A-F]") 之弊：將多選「AB」拆為「A」「B」，與題號 zip 時錯位，
    # 致「答案」與「題號」不對應，產生「誤差」。故改以「以空白切分」之法。
    for line in lines[start:start + 24]:
        digits = re.findall(r"\d{1,3}", line)
        marks = re.findall(r"[A-F]", line.upper())
        # 判斷（宏觀辨「行」）：以「題號」二字開頭者為「題號行」，
        # 以「答案」二字開頭者為「答案行」；「備註」等其他行，皆過濾之，
        # 不收集，以免「雜質」（如「備註：第71題答A或C…」之字母）混入「答案」，
        # 造成「題號與答案之配對」之「誤差」。此乃「除雜提純」之大要也。
        compact = line.replace(" ", "").replace("\u3000", "")
        digits = re.findall(r"\d{1,3}", line)
        # 「題號」行：以「題號」二字開頭，收集其「數字」。
        if compact.startswith(_TABLE_HEAD_WORDS):
            numbers += digits
        # 「答案」行：僅以「答案」「解答」開頭者，方得收集其「字母」。
        elif compact.startswith("答案") or compact.startswith("答案") or compact.startswith("解答"):
            # 除雜：除去行首之「答案」「解答」等中文前綴（非字母雜質）。
            body = re.sub(r"^[解答][案題]?\s*[:：]?\s*", "", line.strip())
            # 分離：依「空白」為介質，切分為「單元」。
            cells = [c for c in re.split(r"\s+", body) if c]
            # 提純＋檢驗：過濾雜質——「元素符號」（如「＃」）以 None 佔位，
            # 以保持「題號」與「答案」之一一「對應」（配位）。
            got = []
            for cell in cells:
                if re.fullmatch(r"[A-Fa-f]+", cell):
                    got.append(cell.upper())
                elif not re.fullmatch(r"\d+", cell):
                    got.append(None)  # 佔位符號（如「＃」），以 None 佔位
            if got:
                units += got
        # 「備註」行：某些「多選」題之「補充」——須「提取」其「答案」。
        # 以「第X題…答…者均給分」為「反應式」，以「或」為「催化劑」，
        # 「萃取」字母之「離子」，「分液」而「蒸餾」，歸諸「題號」。
        # 此即「宏觀辨答案卷、微觀析多選題」之「提純」要訣也。
        elif ("備" in compact and "註" in compact) or "均給" in compact:
            for m in re.finditer(r"第(\d{1,3})題.*?答(.+?)者", line):
                qnum = int(m.group(1))
                letters = set()
                for part in re.split(r"或", m.group(2)):
                    letters.update(ch.upper() for ch in re.findall(r"[A-Fa-f]", part))
                picked = tuple(sorted(c for c in letters if c in LABELS))
                if picked:
                    notes[qnum] = picked
    table = {}
    # 定量：對每個「答案單元」作「排序」「去重」，與「題號」「一一」「對應」。
    for position, token in enumerate(numbers[: len(units)]):
        try:
            cell = units[position]
            if cell is None:
                # 「＃」占位符：其「標準答案」「暫缺」，須循「備註」以「提取」之。
                # 據「題號」之數，按「圖示」之位——從「備註」之「週期」取該題之「多選」答案。
                cell = notes.get(int(token))
                if cell is None:
                    continue  # 「備註」亦無從考據者，棄之
            labels = tuple(sorted({c for c in cell if c in LABELS}))
            if labels:
                table[int(token)] = labels
        except ValueError:
            continue
    return table
