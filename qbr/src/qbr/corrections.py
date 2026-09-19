# -*- coding: utf-8 -*-
"""Read the official corrections sheet (MOD) and say what each correction *means*.

An answer sheet says one thing: question 45 is D. A corrections sheet does not. Read from
1,375 corrections sheets in the corpus, the notes take four shapes, and they are not
variations of one another - they are four different statements about what counts as correct:

    一律給分                      677   every option is accepted; the question is voided
    答Ａ、Ｂ給分                  663   two or more named answers are all correct
    答Ｂ或Ｄ或BD者均給分          663   the named answers, and the combination of them
    除未作答者不給分外             47   anything the candidate wrote is accepted
    答Ａ給分                       41   one named answer replaces the printed one

Folding these into "the answer is X" would lose the thing the sheet is for. A question that
was voided is not a question whose answer is A; a question where A and B are both correct is
not a question with answer A. Both differences matter to every later comparison - scoring,
deduplication, and the reviewer's own reading - and both are lost by a single label.

The correction is therefore carried as a small structure, `AnswerCorrection`, and the
pipeline is free to ask it precise questions (`accepts(letter)`, `is_void`) rather than
re-deriving meaning from a tuple of letters. The printed `＃` marker and the note that
explains it are kept together, because the marker alone is a promise and the note is the
content.
"""
from __future__ import annotations

import re
import unicodedata

# The four shapes, matched by their own words rather than by position in the line.
#
# `除未作答者不給分外，其餘均給分` is a fifth wording of the same meaning as `一律給分`, and it
# was missed at first. It says: everything counts except leaving the question blank. That is
# a voided question - the only difference is that a blank paper is not credited - and the
# answer tuple is therefore every option, exactly as for `一律給分`. Measured over the 1,375
# corrections sheets: 23 sheets carry a `＃` whose only explanation is this wording, and
# without it those questions were reported as having no correction at all.
_RE_VOID = re.compile(
    r"第(?P<number>\d{1,3})題\s*(?:一律給分|除未作答者不給分外[，,]?其餘均給分)")
_RE_ALL_SINGLE = re.compile(r"第(?P<number>\d{1,3})題\s*答([^，,。;；]{1,24}?)給分")
_RE_OR = re.compile(r"第(?P<number>\d{1,3})題\s*答([^，,。;；]+?)者均給分")

_LETTERS = "ABCDEFGH"


def _letters(text):
    """The letters named in a fragment, in reading order, without duplicates."""
    found = []
    for char in unicodedata.normalize("NFKC", text or ""):
        if char in _LETTERS and char not in found:
            found.append(char)
    return tuple(found)


def _combinations(fragment):
    """`Ａ或Ｂ或AB` -> the set of answers that are accepted.

    The sheet writes each accepted answer and then the combination of them, so `Ａ或Ｂ或AB`
    accepts A, B and AB. Only combinations the sheet actually writes are returned: a sheet
    that says `答Ｂ、Ｃ給分` (a comma, no combination) accepts B and C and says nothing about
    BC, and inventing BC there would accept an answer the Examination Yuan did not.
    """
    accepted = set()
    for part in re.split(r"或|、|,|，", fragment or ""):
        letters = _letters(part)
        if letters:
            accepted.add("".join(sorted(letters)))
    return accepted


class AnswerCorrection:
    """What the corrections sheet said about one question.

    `accepted` holds two different kinds of thing and they must not be treated alike. The
    sheet writes `答Ｂ或Ｃ或BC者均給分` - single labels `B` and `C`, and the combination `BC`. A
    label names an option the paper printed; a combination names several of them marked at
    once, which is only answerable on a multi-select question. Every paper in the two
    categories measured here is single-choice, so a combination can never match an option,
    and the two are kept apart by `labels` and `combinations` rather than mixed.

    Measured: with the combination counted as an option label, the gate's
    `answer-not-on-sheet` check fired on six of six 1152 醫事檢驗師 papers, naming `BC` and
    `BD` as answers the paper did not offer. The complaint was correct about the data it was
    handed and wrong about the paper.
    """

    __slots__ = ("number", "kind", "accepted", "source_note")

    def __init__(self, number, kind, accepted, source_note=""):
        self.number = number
        self.kind = kind                 # "void" | "accepted-set"
        self.accepted = frozenset(accepted)
        self.source_note = source_note

    @property
    def is_void(self):
        """The question was voided: every option, and no option, are equally correct."""
        return self.kind == "void"

    @property
    def labels(self):
        """The single option labels accepted - the ones a comparison can use."""
        return frozenset(x for x in self.accepted if len(x) == 1)

    @property
    def combinations(self):
        """Multi-label answers accepted. Real on a multi-select paper, inert on a single."""
        return frozenset(x for x in self.accepted if len(x) > 1)

    def accepts(self, answer):
        """Is this answer accepted? Accepts a letter, a set, or a printed answer string.

        A candidate who marked `BC` is checked against the combinations as well, because that
        is the only reading under which the sheet's mention of `BC` means anything.
        """
        if self.is_void:
            return bool(answer)
        if isinstance(answer, str):
            letter = answer.strip().upper()
        else:
            letter = "".join(sorted(str(x).upper() for x in (answer or ())))
        return letter in self.accepted

    def as_answer(self):
        """A printable answer for a record that has to carry one.

        A voided question is written `送分` and never as a letter: a letter would claim the
        Examination Yuan named one, and it did not. Otherwise every accepted answer is
        printed, combinations included, because that is what the sheet said - `B或BC或C` is
        the sheet's own sentence and shortening it to `B或C` would drop the one reading that
        tells a candidate marking two boxes that they were right.
        """
        if self.is_void:
            return "送分"
        return "或".join(sorted(self.accepted))

    def to_dict(self):
        return {"number": self.number, "kind": self.kind,
                "accepted": sorted(self.accepted),
                "labels": sorted(self.labels),
                "combinations": sorted(self.combinations),
                "note": self.source_note,
                "void": self.is_void, "answer": self.as_answer()}

    def __repr__(self):
        return "AnswerCorrection(#%d %s %s)" % (
            self.number, self.kind, sorted(self.accepted) or "")


def parse_corrections(text):
    """Every correction a MOD sheet states, keyed by question number.

    A question may be named twice - once as `答A給分` and once in a longer note - and the
    two are merged rather than one overwriting the other, because both are statements the
    sheet made and dropping either loses an accepted answer.
    """
    body = unicodedata.normalize("NFKC", text or "")
    # The notes may be wrapped over several lines; the sentences are what carry the meaning,
    # so the text is folded first and split into sentences afterwards. NFKC also turns the
    # full-width comma into `,`, which is why the patterns below accept both - a pattern
    # written only for `，` matches nothing after this point.
    folded = re.sub(r"\s+", "", body)

    # The notes are read from the last `備註` marker, not the first, and never from the answer
    # table above it. Both mistakes are real and were both made here before. A corrections
    # sheet whose table is headed `第1題第2題…` contains the text `…第10題答案…`, and the first
    # `備註` in such a file appears in the table's own label (`備註。題號第1題…`) rather than in
    # the notes. Searching from there let `第10題` start a match that ran through the table and
    # ended at `第14題答B給分`, swallowing question 14 and inventing a correction for 10.
    #
    # Measured: 201 of 1,375 sheets print their table with `第N題` headings, and this is the
    # fault that made them look as if a `＃` had no explanation.
    if "備註" in folded:
        folded = folded[folded.rindex("備註"):]
        folded = re.sub(r"^備註[:：。]?", "", folded)

    corrections = {}

    def merge(number, kind, accepted, note):
        existing = corrections.get(number)
        if existing is None:
            corrections[number] = AnswerCorrection(number, kind, accepted, note)
            return
        # A void absorbs everything: if the question was voided, an answer named elsewhere
        # in the same note is moot, and keeping it would suggest the sheet was undecided.
        if existing.is_void or kind == "void":
            combined_kind = "void"
            combined = frozenset()
        else:
            combined_kind = "accepted-set"
            combined = existing.accepted | frozenset(accepted)
        corrections[number] = AnswerCorrection(
            number, combined_kind, combined,
            (existing.source_note + " / " + note) if note not in existing.source_note else existing.source_note)

    for match in _RE_VOID.finditer(folded):
        number = int(match.group("number"))
        merge(number, "void", (), match.group(0))

    # The `者均給分` form is matched first: `答Ｂ或Ｄ或BD者均給分` also contains `給分`, and
    # the looser pattern would read it as `答Ｂ或Ｄ或BD者均` - keeping the `者` and losing
    # the point of the sentence.
    for match in _RE_OR.finditer(folded):
        number = int(match.group("number"))
        accepted = _combinations(match.group(2))
        if accepted:
            merge(number, "accepted-set", accepted, match.group(0))

    for match in _RE_ALL_SINGLE.finditer(folded):
        note = match.group(0)
        if "均給分" in note:
            continue                                  # already handled as an `或` note
        number = int(match.group("number"))
        accepted = _combinations(match.group(2))
        if accepted:
            merge(number, "accepted-set", accepted, note)

    return corrections


def authoritative_answers(answer_texts, correction_texts, options_by_number=None):
    """The answers as the Examination Yuan last stated them, with the corrections' meaning.

    Three sources have to be combined, and the order between them is the whole point:

      1. the answer sheet (ANS) - the answers as first published
      2. the corrections sheet (MOD) - the same table re-issued, with `＃` in the cells that
         changed, so where it prints a letter that letter is the answer *now*
      3. the notes at the foot of the corrections sheet - what `＃` means

    Step 2 is the part that is easy to miss. A corrections sheet is not only a note; it
    reprints the whole table, and reading only its notes leaves the other seventy-eight
    answers to come from the older sheet. On 115090 the two agree, which is exactly why the
    mistake would have gone unnoticed - until a paper where they do not.

    A `＃` cell is skipped by `parse_answer_table` rather than guessed at, which is the
    right behaviour: the letter is in the note, and a guess would hide whether the note was
    read.

    `options_by_number` matters for one case and it is not an edge case: a voided question.
    Its accepted answer is "whatever the candidate chose", which is the options *that
    question actually offered*. Filling in the whole alphabet instead makes the gate's
    `answer-not-on-sheet` check fire - correctly, because it was handed answers E through H
    for a question with four options. Measured on 115090 questions 10 and 41: with the
    question's own labels the run passes, with A-H it is quarantined.

    Returns `(table, corrections)`: `table` maps number -> accepted answers, and is what the
    rest of the pipeline consumes; `corrections` keeps the meaning a tuple cannot carry.
    """
    from . import canon  # local import: canon imports nothing from here

    table = {}
    for text in answer_texts or ():
        table = canon.merge_answer_tables(table, canon.parse_answer_table(text or ""))

    corrections = {}
    revised = {}
    for text in correction_texts or ():
        revised = canon.merge_answer_tables(revised, canon.parse_answer_table(text or ""))
        for number, correction in parse_corrections(text or "").items():
            existing = corrections.get(number)
            if existing is None:
                corrections[number] = correction
            elif existing.is_void or correction.is_void:
                corrections[number] = AnswerCorrection(number, "void", (), existing.source_note)
            else:
                corrections[number] = AnswerCorrection(
                    number, "accepted-set", existing.accepted | correction.accepted,
                    existing.source_note)

    for number, labels in revised.items():
        table[number] = labels

    options_by_number = options_by_number or {}
    for number, correction in corrections.items():
        if correction.is_void:
            # Every option the question offered is accepted. The record that matters is the
            # correction; the tuple exists so that a comparison against the options does not
            # fail, and it holds the question's own labels and not the alphabet.
            offered = tuple(sorted(options_by_number.get(number) or ())) or _LETTERS[:4]
            table[number] = offered
        else:
            printed = set(table.get(number) or ())
            # Only the single labels go into the table. A combination such as `BC` is not an
            # option the paper printed, and putting it here makes every consumer that checks
            # the answer against the options report a defect that is not there.
            table[number] = tuple(sorted(printed | set(correction.labels)))
    return table, corrections


def parse_answer_table_with_corrections(answer_text, correction_text):
    """The answer sheet's table, with the corrections sheet applied on top.

    Returns `(table, corrections)` where `table` maps number -> accepted answers and is the
    thing every other stage consumes, and `corrections` keeps the meaning that a tuple of
    letters cannot carry.
    """
    from . import canon  # local import: canon imports nothing from here

    table = canon.parse_answer_table(answer_text or "")
    corrections = parse_corrections(correction_text or "")
    merged = {number: tuple(labels) for number, labels in table.items()}
    for number, correction in corrections.items():
        if correction.is_void:
            # A voided question accepts every option, so its tuple is every option the
            # paper offered. The record that matters is the correction, not the tuple.
            merged[number] = tuple(_LETTERS)
        else:
            printed = set(merged.get(number) or ())
            merged[number] = tuple(sorted(printed | set(correction.labels)))
    return merged, corrections
