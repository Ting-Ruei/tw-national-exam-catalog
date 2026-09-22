# -*- coding: utf-8 -*-
"""Confirming a dispute against the paper: the model transcribes, this code subtracts.

The request was that a disputed question - the classic being `⻑` (U+2ED1, a radical) stored where
the paper prints `長` (U+9577, an ideograph) - be **screenshotted and sent to the model to confirm**,
and that it be **repairable rather than merely reported**. Those are two different properties and
they are tested separately:

  * the *confirmation* must come from the paper, not from the detector that raised the doubt - a
    detector agreeing with itself is not evidence;
  * the *repair* must be a diff of two readings, `{field, from, to}`, so a person can check each
    field, and it must be **advisory**: no review event, no rewrite of the question.

`reread.compare` folds both sides (NFKC, whitespace removed) to decide *whether* they differ, which is
correct - a difference in width or spacing is not a difference in a character. But a folded string
cannot be written back over a field, so each change also carries the raw `stored`/`page` pair. Both
of those are asserted here, because getting one right and the other wrong is the failure mode that
looks like success: the diff reads correctly and the repair mangles the sentence.

The model is never asked whether the question is good. Every test replaces the model with a fixed
transcription, so what is tested is the subtraction, not a language model's mood.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

import confirm_dispute  # noqa: E402
from qbr import ai_findings, reread  # noqa: E402


# ------------------------------------------------------------------ helpers

def _question(number=4, stem="下列何者錯誤？", options=None, disputes=None):
    options = options or [{"key": letter, "text": "選項%s" % letter} for letter in "ABCD"]
    return {
        "candidate_key": "moex:108100:309:33:1:question:q%03d" % number,
        "question_number": number,
        "stem": stem,
        "options": options,
        "answer": "B",
        "answer_payload": {"accepted_values": ["B"]},
        "disputes": disputes or [{"kind": "substituted-ideograph", "severity": "warn",
                                  "note": "⻑ 應為 長",
                                  "substitutions": [{"char": "⻑", "means": "長"}]}],
        "metadata": {"normalized_subject_name": "放射線器材學", "normalized_category_name": "醫事放射師",
                     "exam_year": 108, "exam_ordinal": 2,
                     "question_pdf_relative": "國考題資料夾/10_official_pdf/x/1082_放射線器材學.pdf"},
    }


def _seen(**overrides):
    seen = {"stem": "下列何者錯誤？",
            "options": {"A": "選項A", "B": "選項B", "C": "選項C", "D": "選項D"}}
    seen.update(overrides)
    return seen


# ------------------------------------------------------------------ the subtraction

def test_a_substituted_character_becomes_one_field_level_change():
    # The case that started this: the extraction stored the radical `⻑`, the paper prints `長`.
    question = _question(options=[{"key": "A", "text": "x"}, {"key": "B", "text": "使用較⻑的OID"},
                                  {"key": "C", "text": "y"}, {"key": "D", "text": "z"}])
    seen = _seen(options={"A": "x", "B": "使用較長的OID", "C": "y", "D": "z"})
    report, changes = confirm_dispute.changes_between(question, seen)

    assert len(changes) == 1, changes
    change = changes[0]
    assert change["field"] == "option B"
    # The raw pair is what the editor writes back, so it must be the paper's own text - not the
    # folded comparison form, which has no spaces and full-width punctuation half-width.
    assert change["stored"] == "使用較⻑的OID"
    assert change["page"] == "使用較長的OID"


def test_a_question_the_paper_agrees_with_produces_no_change():
    # "not a defect" needs evidence as much as "is a defect" does. The honest answer for a dispute the
    # page does not support is an empty list - and then the finding says OK, not DEFECT.
    question = _question()
    report, changes = confirm_dispute.changes_between(question, _seen())
    assert changes == []
    finding = confirm_dispute.finding_from(changes, seen=_seen(), error=None)
    assert finding["verdict"] == "OK" and finding["what"] == "NONE"


def test_folding_only_decides_difference_and_never_becomes_the_repair():
    # A difference of spacing is a real difference in the stored bytes but must not be reported as a
    # character substitution - the comparison folds it away, so the paper "agrees", and no change is
    # proposed. This is the negative control for the folded/raw distinction: if the raw pair were used
    # to *decide*, this test would see a change.
    question = _question(stem="A B C？")
    seen = _seen(stem="ABC？")
    _report, changes = confirm_dispute.changes_between(question, seen)
    assert changes == [], changes


def test_each_differing_field_is_its_own_change_so_a_person_can_check_them_one_at_a_time():
    # A reviewer who trusts the `⻑`→`長` fix must not be made to accept a stem rewrite they never
    # read. So the diff is per field, never one blob.
    question = _question(stem="題幹⻑", options=[{"key": "A", "text": "選項A"},
                                               {"key": "B", "text": "選項B"},
                                               {"key": "C", "text": "選項C⻑"},
                                               {"key": "D", "text": "選項D"}])
    seen = _seen(stem="題幹長", options={"A": "選項A", "B": "選項B", "C": "選項C長", "D": "選項D"})
    _report, changes = confirm_dispute.changes_between(question, seen)
    assert sorted(c["field"] for c in changes) == ["option C", "stem"]


# ------------------------------------------------------------------ the finding's shape

def test_a_confirmation_is_written_as_an_advisory_finding_not_a_review():
    # The whole point of the record: same stream, same schema as every other model note, so one loader
    # and one panel show it. And it must carry no `action`/`reviewer` - GOV-05.
    question = _question(options=[{"key": "A", "text": "x"}, {"key": "B", "text": "較⻑"},
                                  {"key": "C", "text": "y"}, {"key": "D", "text": "z"}])
    report, changes = confirm_dispute.changes_between(
        question, _seen(options={"A": "x", "B": "較長", "C": "y", "D": "z"}))
    finding = confirm_dispute.finding_from(changes, seen=_seen(), error=None)
    record = ai_findings.make_record(
        question=question, finding=finding, model="incoai/Qwen3.8-27B-Splash", endpoint="u",
        prompt_system="s", prompt_user="u", population="dispute", crop="review-ui/crops/p/q004-dispute.png",
        changes=changes)

    assert record["population"] == "dispute"
    assert record["crop"] == "review-ui/crops/p/q004-dispute.png"
    assert record["changes"][0]["field"] == "option B"
    # A confirmation answers a detector that already exists, so the class is known and there is one
    # reading left to fix. `rule_worthy=True` would propose a second rule for a shape that has one.
    assert record["finding"]["rule_worthy"] is False
    assert "action" not in record and "reviewer" not in record


def test_a_confirmation_carries_a_prompt_that_is_not_the_blocked_one():
    # The population is a parameter of the prompt (the framing differs: a question a person flagged
    # vs one a detector flagged), so its version must differ from `blocked` - otherwise two different
    # measurements would be filed under one hash and later compared as if they were the same run.
    assert ai_findings.prompt_version("dispute") != ai_findings.prompt_version("blocked")
    system, user = ai_findings.build_prompt(_question(), population="dispute")
    assert "已經被人類標記" not in user
    assert "爭議" in user or "紙本" in user


def test_the_crop_is_queue_relative_so_it_resolves_when_the_queue_is_mounted_elsewhere():
    # The container mounts the queue at `/queue` and the laptop at wherever it lives; only a
    # queue-relative path resolves against both, which is why figure crops use that spelling too.
    path = confirm_dispute.crop_output_path("/q/review-ui/crops", "/papers/1082_放射線器材學.pdf", 4)
    assert path == "/q/review-ui/crops/1082_放射線器材學/q004-dispute.png"
    assert confirm_dispute.queue_relative(path, "/q") == \
        "review-ui/crops/1082_放射線器材學/q004-dispute.png"


def test_the_crop_directory_is_named_after_the_paper_the_same_way_figure_crops_are():
    # A rebuild adopts figure crops from `crops/<paper>/<name>`. A second directory scheme that only
    # this script knew would leave these screenshots behind on the next rebuild - present on disk and
    # invisible in the UI, which is the failure this project keeps re-learning.
    path = confirm_dispute.crop_output_path("/root/crops", "/x/y/1082_放射線器材學.pdf", 15)
    assert os.path.basename(path) == "q015-dispute.png"
    assert os.path.basename(os.path.dirname(path)) == "1082_放射線器材學"


def test_a_reading_that_fails_is_recorded_as_a_failure_not_as_agreement():
    # A failed render and a page that agrees both produce no changes. They must not be the same
    # record: one says "the paper supports the extraction", the other says "nobody looked".
    finding = confirm_dispute.finding_from([], seen=None, error="no-rows")
    assert finding["verdict"] is None and finding["error"] == "no-rows"
    assert finding["what"] is None


# ------------------------------------------------------------------ which disputes are worth asking about

def test_only_disputes_the_page_can_settle_are_asked_about():
    # Where a defect can be measured - a dangling answer, an option count that disagrees with the
    # paper's own declaration - there is nothing for a transcription to settle, and asking would
    # replace a certainty with an impression.
    assert "substituted-ideograph" in confirm_dispute.CONFIRMABLE_KINDS
    assert "answer-not-among-options" not in confirm_dispute.CONFIRMABLE_KINDS
    assert "option-count" not in confirm_dispute.CONFIRMABLE_KINDS


def test_the_dispute_details_are_kept_as_evidence_but_not_sent_to_the_model():
    # The crop shows the whole question and the dispute already computed where the doubt is. Sending
    # that to the transcription would risk the model reading the answer off the hint - so the
    # reasons are returned for the record and the prompt is built from the question alone.
    question = _question()
    reasons = confirm_dispute.dispute_reasons(question, [])
    assert reasons and reasons[0]["kind"] == "substituted-ideograph"
    assert reasons[0]["substitutions"][0]["means"] == "長"
    _system, user = ai_findings.build_prompt(question, population="dispute")
    assert "應為" not in user
    assert "substitutions" not in user


def test_a_question_without_a_paper_path_is_reported_as_such():
    question = _question()
    question["metadata"] = {"normalized_subject_name": "x"}
    assert confirm_dispute.paper_pdf_of(question) is None


# ------------------------------------------------------------------ re-reading the record

def test_the_report_groups_confirmations_by_the_dispute_that_raised_them(tmp_path=None):
    import tempfile
    path = os.path.join(tempfile.mkdtemp(), ai_findings.STREAM)
    question = _question(options=[{"key": "A", "text": "x"}, {"key": "B", "text": "較⻑"},
                                  {"key": "C", "text": "y"}, {"key": "D", "text": "z"}])
    _report, changes = confirm_dispute.changes_between(
        question, _seen(options={"A": "x", "B": "較長", "C": "y", "D": "z"}))
    finding = confirm_dispute.finding_from(changes, seen=_seen(), error=None)
    record = ai_findings.make_record(question=question, finding=finding, model="m", endpoint="u",
                                     prompt_system="s", prompt_user="u", population="dispute",
                                     changes=changes)
    record["evidence"] = {**record.get("evidence", {}),
                          "disputes": confirm_dispute.dispute_reasons(question, [])}
    ai_findings.append(path, record)
    assert confirm_dispute.report(path) == 0
    loaded = [r for r in ai_findings.load(path) if r.get("population") == "dispute"]
    assert len(loaded) == 1 and loaded[0]["changes"]


# ------------------------------------------------------------------ the transcription is not a verdict

def test_the_transcription_module_keeps_its_refusal_to_judge():
    # `reread.SYSTEM` must stay a transcription instruction. If it ever asked for an opinion, the
    # subtraction below would be a diff against an opinion and the whole design would silently change.
    assert "不要修正" in reread.SYSTEM or "轉錄" in reread.SYSTEM
    assert "錯誤" not in reread.SYSTEM.replace("不要修正", "")
