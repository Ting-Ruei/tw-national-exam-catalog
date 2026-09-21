# -*- coding: utf-8 -*-
"""Emit a Review UI candidate JSONL from canonical records.

The Review UI already knows how to read a candidate file: `serve_question_review_ui.py`
loads it, joins it against a review log, and writes human decisions into an append-only
event log. None of that needs to change. What it needs is a candidate file in the shape it
already reads, and that is all this module produces.

The shape is the one the corpus has used all along:

    candidate_key       moex:115090:308:0504:1:question:q001
    source_registry_key moex:115090:308:0504:1:question
    stem                the text as extracted, unabridged
    options             [{key, text, image, markup, raw_order}, ...]
    answer              a string, because that is what the reader expects
    answer_payload      {answer, raw_answer, accepted_values, is_special_correction}
    quality_status      "pass" | "blocked" | "needs_review"
    metadata            the lineage the UI shows beside the question

The one place this file is not a byte-for-byte copy of the legacy candidate format is
`quality_status`: it is derived from the gate's own findings rather than from a parser's
opinion, and `metadata.parser_status` says which. That is the honest label — the UI shows
both, and a reviewer can see that this candidate came from the deterministic path rather
than from MinerU.
"""
from __future__ import annotations

import json
import os

from . import subitems

from . import groups

from . import disputes

# The Review UI reads `answer` as a display string. A multi-label answer (a corrected
# sheet that gives two letters) becomes "A,B"; a special correction is flagged rather than
# silently flattened.
SPECIAL_ANSWER_MARKERS = ("#", "送分", "從寬", "均給分")

#: Fields on a review event that are **not** part of its identity.
#:
#: `_carried_from` is added by `build_review_queue.py` when a queue carries a record forward, so the
#: same decision has a different value in two queues. Comparing it made every carried record look
#: new: measured, a first rebuild carried 392 records and rebuilding from that queue carried 629 -
#: the same 181 questions twice. The opposite mistake is a key narrower than the record (e.g. just
#: `candidate_key`), which silently drops a reviewer's later decisions; measured, `sf8`'s 52 records
#: all appeared missing from `sf9` although `sf9` held them all.
NON_IDENTITY_FIELDS = ("_carried_from",)


def record_identity(record):
    """An event's identity: its whole content, minus the fields that record where it was found.

    Deliberately "everything except", not "these fields": a decision is identified by what it says,
    and a list of kept fields is a list somebody has to remember to extend when an event gains a
    field. This lives here so the carry logic (`build_review_queue.py`) and the push helper
    (`scripts/push_reviews_to_station.sh`) compare events the same way - two definitions of "the same
    event" is two places for a review record to be lost or duplicated.
    """
    return json.dumps({key: value for key, value in record.items()
                       if key not in NON_IDENTITY_FIELDS},
                      sort_keys=True, ensure_ascii=False)


def _answer_string(labels):
    if not labels:
        return ""
    return ",".join(str(label) for label in labels)


def _quality_status(gate, number):
    """What the deterministic gate said about this one question.

    `blocked` is reserved for findings that belong to this question specifically. A paper
    that failed the gate wholesale would not have reached this module at all — S3 refuses
    the run — so a per-question block here means the gate named this number.
    """
    blocking = list(gate.get("blocking") or [])
    if number in set(gate.get("numbering_gaps") or []) or number in set(gate.get("empty_stem") or []):
        return "blocked"
    if number in {row.get("number") for row in (gate.get("options_not_four") or [])}:
        return "blocked"
    if number in set(gate.get("duplicate_option_labels") or []):
        return "blocked"
    if number in set(gate.get("answer_not_on_sheet") or []):
        return "needs_review"
    if any(code.startswith("count-mismatch") or code == "no-items" for code in blocking):
        return "needs_review"
    return "pass"


def candidate_from_question(question, *, gate, source="qbr_deterministic", extra_metadata=None):
    """One package question -> one Review UI candidate row.

    Only the question paper is named, never the answer sheet. Reviewing the question against
    the paper and checking the answer key against the answer sheet are two separate acts of
    reading, and a person does one at a time.
    """
    metadata = question.get("metadata") or {}
    number = int(question["question_number"])
    labels = list(question.get("answer") or [])
    # The paper's own legend for its private-use marks, read from this question's stem. A
    # multi-answer question prints its sub-items in the stem as `\ue000砂粒病毒 …` and then states
    # its options as expressions over those marks (`\ue18c\ue000\ue001`). A browser has no glyph for
    # a private-use codepoint, so without this the options were drawn as rows of tofu - the
    # question was unreadable because the marks were never resolved, not because the reading was
    # wrong. `subitems` rewrites them into the words the paper itself gives them.
    legend = subitems.legend_of(question.get("stem"))
    resolved_legend = False
    options = []
    for position, option in enumerate(question.get("options") or [], 1):
        text = option.get("text")
        if subitems.has_resolvable_marks(text, legend):
            text = subitems.resolve(text, legend)
            resolved_legend = True
        options.append({
            "key": option.get("key"),
            "text": text,
            "image": option.get("image"),
            "markup": None,
            "raw_order": position,
        })
    # A lost glyph is just as much a defect in an option as in the stem - `脂解\ue2c6（lipase）` is
    # 脂解酶 - so the address is collected from the whole question, not only its stem. Reporting
    # only the stem would let a reviewer approve a question whose *options* are unreadable.
    lost_source = "  ".join([question.get("stem") or ""]
                            + [option["text"] or "" for option in options])
    merged = {
        "adapter_version": metadata.get("adapter_version"),
        "answer_authority_source": metadata.get("answer_authority_source"),
        "answer_display": metadata.get("answer_display"),
        "answer_pdf_relative": metadata.get("answer_pdf_relative"),
        "category_code": metadata.get("category_code"),
        # Carried through so the reviewer is told which questions the text layer could not spell
        # out completely, and where. The character renders on the page, so the question is
        # readable - but a reader comparing the screen against the paper deserves to know which
        # words the extraction is unsure of, rather than discovering it by noticing a gap.
        "deterministic_flags": metadata.get("deterministic_flags"),
        "exam_code": metadata.get("exam_code"),
        "exam_ordinal": metadata.get("exam_ordinal"),
        "group_name": question.get("normalized_category_name"),
        "normalized_category_name": question.get("normalized_category_name"),
        "normalized_subject_name": question.get("normalized_subject_name"),
        "official_category_name": question.get("official_category_name"),
        "official_subject_name": question.get("official_subject_name"),
        "parser_status": _quality_status(gate, number),
        "parser_version": metadata.get("parser_version"),
        "question_pdf_relative": metadata.get("question_pdf_relative"),
        "review_status": metadata.get("review_status"),
        "subject_code": metadata.get("subject_code"),
        "year": metadata.get("year"),
    }
    if extra_metadata:
        merged.update(extra_metadata)
    # A voided question is the one case where the answer column must not be read as letters.
    # The corrections sheet said "第10題一律給分", and `A,B,C,D` is how that is stored so a
    # comparison against the options does not fail - but a reader who sees `A,B,C,D` learns
    # the opposite of what the sheet said. The display string is what a person should see.
    display = metadata.get("answer_display") or _answer_string(labels)
    voided = bool(metadata.get("answer_display")) and display == "送分"
    return {
        "answer": display,
        "answer_payload": {
            "accepted_values": labels,
            "answer": display,
            "is_special_correction": voided,
            "raw_answer": _answer_string(labels),
        },
        "answer_source_registry_key": (metadata.get("external_registry_key") or "").replace(
            ":question", ":answer") or None,
        "candidate_key": question["source_question_key"],
        "canonical_question_key": question["source_question_key"],
        "explanation": None,
        "group_ref": question.get("group_ref"),
        "group_position": question.get("group_position"),
        "group_size": question.get("group_size"),
        "shared_stem": question.get("shared_stem"),
        "image_refs": [],
        "issue_count": 0,
        "metadata": merged,
        "options": options,
        "quality_status": _quality_status(gate, number),
        "question_number": number,
        "question_number_occurrence": 1,
        "question_type": question.get("question_type"),
        "source_registry_key": question["source_registry_key"],
        "stem": question.get("stem"),
        "stem_image": question.get("stem_image"),
        "stem_markup": None,
        # True when the options were rewritten from the paper's sub-item marks, so a reviewer knows
        # the option text on screen is the paper's own words for the marks and not the marks.
        "subitem_legend": ({mark: words for mark, words in legend.items()}
                           if resolved_legend else None),
        # A lost glyph is a real defect at a known position: the paper prints a character the text
        # layer cannot spell (`轉氨\ue2c6` is 轉氨酶). The address of the defect is reported so the
        # reviewer can fix it against the paper; the character is never guessed at.
        "lost_glyphs": subitems.lost_glyphs(lost_source, legend) or None,
        "lost_glyph_note": subitems.describe_lost_glyphs(lost_source, legend),
        # The places where the reading is **not settled**. Filled in by `write_candidates`, which is
        # the only place that sees the whole paper at once - a `dangling-answer` needs the options,
        # and an `engine-disagreement` needs the paper's two counts. A question with no disputes
        # carries `None` rather than `[]`, because "not looked at" and "looked at, nothing found"
        # are different states and the queue must be able to tell them apart.
        "disputes": None,
    }


def disputes_for_paper(rows):
    """Attach each question's disputes, over the whole paper at once.

    Per-paper rather than per-question because one of the checks is about the paper's *shape*: the
    number of distinct option marks it printed is a property of the paper, and a question whose
    options were read as three when the paper printed four can only be seen by comparing the two.

    Public because it is called twice by design, at two different moments: `write_candidates` runs
    it as soon as the rows exist, and `scripts/crop_run_figures.py` runs it again after binding the
    option pictures. The second call is the authoritative one - an option with no text is usually
    correct because the option *is* the picture - and the first exists so that a queue which is
    never cropped still carries its disputes rather than none.
    """
    for row in rows:
        # An option that is a picture has no text and that is correct, so the keys with a bound
        # option-image are passed in. The crop run writes them into `image_refs` with the
        # `option_key`, and `write_candidates` runs after it, so the set is available here.
        images = {str(ref.get("option_key") or "").upper()
                  for ref in (row.get("image_refs") or [])
                  if ref.get("asset_role") == "option-image" and ref.get("option_key")}
        found = disputes.of_question(row, option_images=images)
        row["disputes"] = found or None
        row["dispute_severity"] = disputes.worst_severity(found) or None
    return rows


def write_candidates(path, questions, *, gate, source="qbr_deterministic", extra_metadata=None):
    """Write the candidate JSONL the Review UI reads. Returns the rows written.

    Grouping happens here, before the rows are built, because a group is a property of the *paper*
    and this function receives exactly one paper. Binding it afterwards, from a finished queue,
    would mean grouping across papers - and a `承上題` on the first question of the next paper would
    then attach to the last question of the previous one.

    The shared stem is written onto the group's members, not only its head, because the reviewer
    reads one question at a time: a continuation that does not carry the stem it continues from is
    unjudgeable, and the review UI would have to re-derive it from the paper.
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    ordered = sorted(questions, key=lambda q: q["question_number"])
    paper_key = str(ordered[0].get("source_registry_key") or "") if ordered else ""
    groups.bind(ordered, paper_key=paper_key)
    rows = [candidate_from_question(question, gate=gate, source=source,
                                    extra_metadata=extra_metadata)
            for question in ordered]
    # After the rows exist, because a dispute is a statement about the *reading* and every field it
    # reads (options, answer, lost glyphs) is only final once the row is built.
    disputes_for_paper(rows)
    keys = [row["candidate_key"] for row in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate candidate_key; the Review UI keys on it")
    temporary = path + ".partial"
    with open(temporary, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temporary, path)                     # atomic: a half-written queue is worse than none
    return rows


def write_issues(path, questions, *, gate):
    """An empty issue CSV, in the header shape the Review UI parses.

    The deterministic path states its findings through the gate, not through a parser issue
    list, so there are no parser issues to report. The file is written anyway, with its
    header, because the UI reads it unconditionally and an absent file would be a crash
    rather than a statement of "no parser issues".
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    header = "candidate_key,issue_code,severity,owner_stage,message,issue_json\n"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(header)
    return path
