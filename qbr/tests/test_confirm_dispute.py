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


# --------------------------------------------------- the resident loop's work list

#: The shape `_append_finding` writes when the page read **failed**. `finding_from` builds a finding
#: on every path - the error one has `error` set and `transcription` absent - so "has a finding" is not
#: the same question as "was the page read".
def _error_record(question):
    finding = confirm_dispute.finding_from([], seen=None, error="request failed")
    return ai_findings.make_record(
        question=question, finding=finding, model="m", endpoint="u", prompt_system="s",
        prompt_user="u", population="dispute", error="request failed", changes=[])


def _ok_record(question, seen):
    _report, changes = confirm_dispute.changes_between(question, seen)
    finding = confirm_dispute.finding_from(changes, seen=seen, error=None)
    return ai_findings.make_record(
        question=question, finding=finding, model="m", endpoint="u", prompt_system="s",
        prompt_user="u", population="dispute", changes=changes)


def test_a_failed_page_read_is_not_counted_as_confirmed(tmp_path=None):
    # The defect this is the negative control for: the first version asked only for a non-empty
    # `finding`, and every error path writes one. Five questions whose crop the model could not read
    # (a transient endpoint outage) were therefore marked done and would never be asked again -
    # silently, because "skipped" and "already read" looked identical. With the old rule this list
    # would contain the key and the loop would empty itself while work remained.
    import tempfile
    path = os.path.join(tempfile.mkdtemp(), ai_findings.STREAM)
    question = _question()
    ai_findings.append(path, _error_record(question))
    assert confirm_dispute.confirmed_keys(path) == {}, \
        "讀不到不是確認：一個暫時的端點故障不該讓題目永久跳過"


def test_a_page_that_was_actually_read_counts_as_confirmed(tmp_path=None):
    # The other half, so the previous test cannot be satisfied by counting nothing at all. A page the
    # model read and agreed with (`changes == []`) is confirmed just as much as one it disagreed with.
    import tempfile
    path = os.path.join(tempfile.mkdtemp(), ai_findings.STREAM)
    question = _question()
    ai_findings.append(path, _ok_record(question, _seen()))
    confirmed = confirm_dispute.confirmed_keys(path)
    assert set(confirmed) == {question["candidate_key"]}
    # The value is the reading the confirmation was made against, which is what lets the loop
    # re-open the question automatically when a repair changes the text underneath it.
    assert confirmed[question["candidate_key"]] == ai_findings.reading_fingerprint(question)


def test_the_work_list_is_blocked_and_confirmable_and_not_yet_read(tmp_path=None):
    # The resident loop selected nothing for a whole evening because its list was "blocks no detector
    # explains", and after three new dispute kinds landed every block had a detector. The honest list
    # is "a person blocked it, and a page read can settle its dispute", which does not shrink to zero
    # just because classification succeeded.
    import tempfile
    root = tempfile.mkdtemp()
    queue_dir = os.path.join(root, "review-ui")
    os.makedirs(queue_dir, exist_ok=True)
    blocked_row = _question(number=4)
    other_row = _question(number=5)
    with open(os.path.join(queue_dir, "candidates.jsonl"), "w", encoding="utf-8") as handle:
        for row in (blocked_row, other_row):
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with open(os.path.join(queue_dir, "question_review_events.jsonl"), "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"candidate_key": blocked_row["candidate_key"],
                                 "action": "block", "reviewer": "local"}) + "\n")
        handle.write(json.dumps({"candidate_key": other_row["candidate_key"],
                                 "action": "accept", "reviewer": "local"}) + "\n")

    blocked = confirm_dispute.blocked_keys(queue_dir)
    assert blocked == {blocked_row["candidate_key"]}, "只有 block 的，accept 的不算"
    rows = confirm_dispute.disputed_questions(queue_dir, None, None, 0, blocked=blocked)
    assert [r["candidate_key"] for r in rows] == [blocked_row["candidate_key"]]


def test_a_question_whose_reading_already_has_a_confirmation_is_skipped(tmp_path=None):
    # Idempotence by construction: the loop re-runs every 30 minutes forever, so without this the same
    # questions would be re-rendered and re-transcribed every round for an answer that cannot change.
    import tempfile
    root = tempfile.mkdtemp()
    queue_dir = os.path.join(root, "review-ui")
    os.makedirs(queue_dir, exist_ok=True)
    row = _question(number=4)
    with open(os.path.join(queue_dir, "candidates.jsonl"), "w", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    store = os.path.join(queue_dir, ai_findings.STREAM)
    ai_findings.append(store, _ok_record(row, _seen()))
    already = confirm_dispute.confirmed_keys(store)
    rows = confirm_dispute.disputed_questions(queue_dir, None, None, 0, already=already)
    assert rows == [], "已讀過的讀法不該每輪重拍重問"
    # And a *changed* reading re-opens it: that is the property that makes the skip safe rather than a
    # way to lose a question. The stored text moves, the fingerprint no longer matches, and the
    # question is selected again without anyone remembering to reset a flag.
    moved = dict(row)
    moved["stem"] = row["stem"] + "（重新抽取過）"
    with open(os.path.join(queue_dir, "candidates.jsonl"), "w", encoding="utf-8") as handle:
        handle.write(json.dumps(moved, ensure_ascii=False) + "\n")
    rows = confirm_dispute.disputed_questions(queue_dir, None, None, 0, already=already)
    assert len(rows) == 1, "讀法變了就要重新問（不是靠人去記得重設一個旗標）"


def test_the_resident_loop_is_pinned_to_the_work_list_that_will_not_empty_itself():
    # A shell script is the one part of this system no unit test runs, and the defect it carried was
    # invisible from the outside: the loop started every 30 minutes, printed a log line, and did
    # nothing. The fix is two flags; if a future edit drops one, the loop silently returns to
    # "select nothing and report completion". So the flags are asserted against the script text and
    # against the argument parser, which is the only kind of test that can catch a regression in a
    # file that is never imported.
    import subprocess
    daemon = os.path.join(PKG, "scripts", "repair_daemon.sh")
    with open(daemon, encoding="utf-8") as handle:
        text = handle.read()
    assert "--blocked-only" in text, \
        "常駐迴圈的工作清單必須限於人已 block 的題，否則它會回到「選不到題卻說完成」"
    assert "--skip-confirmed" in text, \
        "一個 30 分鐘迴圈必須跳過已讀過的讀法，否則每輪重拍重問同一批"
    assert "--model" in text and "${LANE}" in text, \
        "lane 宣告了就要真的傳下去，不然 LANE=... 等於裝飾"
    # And the flags exist: a script mentioning a flag the parser does not define fails at run time,
    # in a loop, where nobody is watching the stderr.
    args = subprocess.run([sys.executable, "-c",
                           "import sys; sys.argv=['x','--queue','/nonexistent']\n"
                           "sys.path[:0]=['%s','%s']\n"
                           "import confirm_dispute as c, json; print(json.dumps(sorted(\n"
                           "    a for a in vars(c.parse_args()) if a.startswith('blocked') or a.startswith('skip'))))\n"
                           % (os.path.join(PKG, "src"), os.path.join(PKG, "scripts"))],
                          capture_output=True, text=True)
    assert args.returncode == 0, args.stderr
    assert json.loads(args.stdout) == ["blocked_only", "skip_confirmed"]
