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
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

import confirm_dispute  # noqa: E402
from qbr import ai_findings, reread, scan_state  # noqa: E402


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


def test_an_agreeing_reading_does_not_claim_the_pictures_were_checked():
    """業主 2026-09-25：「有些題目原本沒圖卻截了上下題圖片；AI 截圖檢查只看當下這題、沒上下資訊，
    於是回報『找不到問題』」。

    機械相減只比**文字**（題幹與選項）。這一題的圖若量不到歸屬（`crop_run_figures` 寫的
    `ownership: unverified`，站上 414 張），那個 OK 不可以被讀成「圖片沒問題」——它沒看過那張圖是
    誰的。所以判讀的 `where` 要把這件事說出來（`finding.where` 由畫面直接顯示）。
    """
    question = _question()
    question["image_refs"] = [{"asset_role": "figure-crop", "path": "x.png", "page": 2,
                              "ownership": "unverified", "box": [47.0, 325.1, 549.2, 586.4]}]
    finding = confirm_dispute.finding_from(
        [], seen=_seen(), error=None, caveat=ai_findings.figure_caveat(question))
    assert finding["where"].startswith("紙本與抽取一致")
    assert "無法確認屬於哪一題" in finding["where"]


def test_a_question_whose_pictures_were_measured_keeps_the_plain_wording():
    """負對照：這一題沒有圖（或圖的歸屬量到了）時，句子必須逐字與修好之前一樣——多加一句話就是
    對一個沒有爭議的判讀加註。"""
    assert ai_findings.figure_caveat(_question()) == ""
    finding = confirm_dispute.finding_from([], seen=_seen(), error=None, caveat="")
    assert finding["where"] == "紙本與抽取一致"


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


def _ok_record(question, seen, rejected=None):
    _report, changes = confirm_dispute.changes_between(question, seen)
    finding = confirm_dispute.finding_from(changes, seen=seen, error=None)
    return ai_findings.make_record(
        question=question, finding=finding, model="m", endpoint="u", prompt_system="s",
        prompt_user="u", population="dispute", changes=changes, rejected=rejected)

def test_a_refusal_reopens_the_same_reading_with_the_rejected_change():
    import tempfile

    root = tempfile.mkdtemp()
    queue_dir = os.path.join(root, "review-ui")
    os.makedirs(queue_dir, exist_ok=True)
    row = _question(number=4)
    with open(os.path.join(queue_dir, "candidates.jsonl"), "w", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    rejected = {"count": 1, "fields": ["stem"],
                "changes": [{"field": "stem", "from": "錯誤文字", "to": "修正文字"}]}
    store = os.path.join(queue_dir, ai_findings.STREAM)
    ai_findings.append(store, _ok_record(row, _seen()))
    already = confirm_dispute.confirmed_keys(store)

    rows = confirm_dispute.disputed_questions(
        queue_dir, None, None, 0, already=already, rejected_by_key={row["candidate_key"]: rejected})
    assert [question["candidate_key"] for question in rows] == [row["candidate_key"]], \
        "B must reopen an unchanged reading so the next prompt sees the refused edit"

    ai_findings.append(store, _ok_record(row, _seen(), rejected=rejected))
    already = confirm_dispute.confirmed_keys(store)
    rows = confirm_dispute.disputed_questions(
        queue_dir, None, None, 0, already=already, rejected_by_key={row["candidate_key"]: rejected})
    assert rows == [], "the same refusal context must still be idempotently skipped"



def test_human_b_after_machine_edit_reaches_the_next_page_read_prompt(tmp_path):
    queue_dir = tmp_path / "review-ui"
    queue_dir.mkdir()
    row = _question(number=4)
    key = row["candidate_key"]
    (queue_dir / "candidates.jsonl").write_text(
        json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    events = [
        {
            "candidate_key": key,
            "action": "reset_review",
            "reviewer": "repair_dispute_apply",
            "source": "qbr_dispute_apply",
            "applied": "field",
            "changes": [{"field": "stem", "from": "錯誤文字", "to": "修正文字"}],
        },
        {
            "candidate_key": key,
            "action": "block",
            "reviewer": "local",
            "source": "linear_v2",
        },
    ]
    events_path = queue_dir / "question_review_events.jsonl"
    events_path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8")
    inputs = confirm_dispute.load_prompt_inputs(str(queue_dir))
    previous = _ok_record(row, _seen())
    previous["review_context_sha256"] = confirm_dispute.prompt_context_fingerprint(
        row, principles=[], answers=None, notes=None, rejected=None)
    findings_path = queue_dir / ai_findings.STREAM
    ai_findings.append(str(findings_path), previous)
    already = confirm_dispute.confirmed_keys(str(findings_path))

    questions = confirm_dispute.disputed_questions(
        str(queue_dir),
        None,
        None,
        0,
        blocked=confirm_dispute.blocked_keys(str(queue_dir)),
        already=already,
        rejected_by_key=inputs["rejections_by_key"],
        principles=inputs["principles"],
        answers_by_key=inputs["answers_by_key"],
        notes_by_key=inputs["notes_by_key"],
    )
    assert [question["candidate_key"] for question in questions] == [key]
    rejected = inputs["rejections_by_key"][key]
    prompt = confirm_dispute.transcribe_system(
        principles=inputs["principles"],
        answers=inputs["answers_by_key"].get(key),
        notes=inputs["notes_by_key"].get(key),
        rejected=rejected,
    )
    assert "錯誤文字" in prompt and "修正文字" in prompt, \
        "the next model read must be told the exact machine change the human rejected"


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
    assert confirmed[question["candidate_key"]]["reading_sha256"] == \
        ai_findings.reading_fingerprint(question)


def test_the_work_list_is_blocked_and_not_yet_read(tmp_path=None):
    # The resident loop selected nothing for a whole evening because its list was "blocks no detector
    # explains", and after three new dispute kinds landed every block had a detector. The honest list
    # is "a person blocked it" - the kind is not required (2026-09-24: a block with no kind at all is
    # the question only a page read can speak to), so it cannot shrink to zero either because
    # classification succeeded or because it had nothing to say.
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


def test_a_machine_withdrawal_does_not_erase_the_human_block_from_the_work_list(tmp_path):
    queue_dir = tmp_path / "review-ui"
    queue_dir.mkdir()
    question = _question(number=74)
    (queue_dir / "candidates.jsonl").write_text(
        json.dumps(question, ensure_ascii=False) + "\n", encoding="utf-8")
    events = [
        {"candidate_key": question["candidate_key"], "action": "reset_review",
         "reviewer": "repair_dispute_apply", "source": "qbr_dispute_apply",
         "applied": "field", "changes": [{"field": "stem", "from": "原文", "to": "修復"}]},
        {"candidate_key": question["candidate_key"], "action": "block",
         "reviewer": "local", "source": "linear_v2", "created_at": "2026-09-25T02:00:00"},
        {"candidate_key": question["candidate_key"], "action": "reset_review",
         "reviewer": "repair_dispute_apply", "applied": "withdrawn",
         "withdraw": ["stem"], "created_at": "2026-09-25T02:01:00"},
    ]
    (queue_dir / "question_review_events.jsonl").write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8")

    folded = confirm_dispute.repair_loop.fold_review_events(
        str(queue_dir / "question_review_events.jsonl"))[question["candidate_key"]]
    assert folded["action"] == "block", "a machine reset is not a new human decision"
    assert folded["rejections"] == 1
    assert confirm_dispute.blocked_keys(str(queue_dir)) == {question["candidate_key"]}

    rows = confirm_dispute.disputed_questions(
        str(queue_dir), None, None, 0, blocked=confirm_dispute.blocked_keys(str(queue_dir)))
    assert [row["candidate_key"] for row in rows] == [question["candidate_key"]], \
        "the rejected question must return to a page read after its machine repair was withdrawn"


def test_an_empty_block_set_selects_nothing_rather_than_everything(tmp_path=None):
    """**負控制：契約是「只處理人已 block 的」,而空集合必須選出零題。**

    `blocked=None` 與 `blocked=set()` 是兩件不同的事：前者是「呼叫者不要篩」（`--only` 指名時），
    後者是「篩出來真的沒有」。舊版把兩者混成一個，於是 `if blocked and key not in blocked` 在空
    集合時不 continue——**讀不到任何 block 時它會掃全部爭議題**。無人值守的代理人最危險的方向
    就是「動你沒標的題」,而它只在剛好一題 block 都沒有時發生,所以有人看著時看不到。

    這個測試沒有它就會通過在錯的行為上：`test_the_work_list_is_blocked_and_not_yet_read`
    的集合是非空的,所以舊的行為在它底下是對的。
    """
    import tempfile
    root = tempfile.mkdtemp()
    queue_dir = os.path.join(root, "review-ui")
    os.makedirs(queue_dir, exist_ok=True)
    row = _question(number=4)
    with open(os.path.join(queue_dir, "candidates.jsonl"), "w", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    # 沒有任何 block 事件——這就是空集合的來源
    with open(os.path.join(queue_dir, "question_review_events.jsonl"), "w", encoding="utf-8") as handle:
        handle.write("")
    blocked = confirm_dispute.blocked_keys(queue_dir)
    assert blocked == set(), "前提：這個佇列沒有 block"
    rows = confirm_dispute.disputed_questions(queue_dir, None, None, 0, blocked=blocked)
    assert rows == [], "空集合時掃了 %d 題——契約被反過來執行了" % len(rows)
    # 反面：不給 blocked（None）＝呼叫者明確表示「不篩」,那就該選到題。兩個語意必須分開,
    # 否則這個修正會讓 `--only` 變成永遠回空清單。
    rows = confirm_dispute.disputed_questions(queue_dir, None, None, 0, blocked=None)
    assert len(rows) == 1, "blocked=None 是「不篩」,不該被當成空集合"


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


def test_changed_human_answer_reopens_an_unchanged_reading(tmp_path):
    queue_dir = os.path.join(str(tmp_path), "review-ui")
    os.makedirs(queue_dir, exist_ok=True)
    row = _question(number=4)
    with open(os.path.join(queue_dir, "candidates.jsonl"), "w", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    key = row["candidate_key"]
    store = os.path.join(queue_dir, ai_findings.STREAM)
    record = _ok_record(row, _seen())
    record["review_context_sha256"] = confirm_dispute.prompt_context_fingerprint(
        row, principles=[], answers=None, notes=None, rejected=None)
    ai_findings.append(store, record)
    already = confirm_dispute.confirmed_keys(store)

    same_context = set()
    rows = confirm_dispute.disputed_questions(
        queue_dir, None, None, 0, already=already, principles=[], answers_by_key={},
        notes_by_key={}, rejected_by_key={}, completed=same_context)
    assert rows == [] and same_context == {key}, "matching prompt inputs should remain idempotent"

    human_answers = {"k-unused": {"questions": []}, key: {"questions": [
        {"candidate_key": key, "question": "Which figure belongs here?",
         "answer_text": "This crop belongs to q2."}
    ]}}
    rows = confirm_dispute.disputed_questions(
        queue_dir, None, None, 0, already=already, principles=[], answers_by_key=human_answers,
        notes_by_key={}, rejected_by_key={})
    assert [question["candidate_key"] for question in rows] == [key], \
        "a new human answer must override text-only confirmation"


def test_pending_confirmation_with_unchanged_context_is_drained(tmp_path, monkeypatch):
    root = str(tmp_path)
    queue_dir = os.path.join(root, "review-ui")
    os.makedirs(queue_dir, exist_ok=True)
    row = _question(number=4)
    key = row["candidate_key"]
    with open(os.path.join(queue_dir, "candidates.jsonl"), "w", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with open(os.path.join(queue_dir, "question_review_events.jsonl"), "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"candidate_key": key, "action": "block",
                                 "reviewer": "local"}) + "\n")
    scan_state.mark_pending(root, [key])
    inputs = confirm_dispute.load_prompt_inputs(queue_dir)
    record = _ok_record(row, _seen())
    record["review_context_sha256"] = confirm_dispute.prompt_context_fingerprint(
        row, principles=inputs["principles"], answers=inputs["answers_by_key"].get(key),
        notes=inputs["notes_by_key"].get(key), rejected=inputs["rejections_by_key"].get(key))
    ai_findings.append(os.path.join(queue_dir, ai_findings.STREAM), record)

    monkeypatch.setattr(confirm_dispute, "parse_args", lambda: argparse.Namespace(
        queue=root, out=None, report=False, blocked_only=False, skip_confirmed=True,
        pending_only=True, principles=None, only=None, kind=None, limit=0))
    assert confirm_dispute.main() == 0
    assert scan_state.pending_keys(root) == [], \
        "a retry list must not retain work already confirmed with the same prompt inputs"


def _pending_queue(root, keys, blocked):
    """A queue root with `keys` as candidates and `blocked` of them carrying a human `block`."""
    ui = os.path.join(root, "review-ui")
    os.makedirs(ui, exist_ok=True)
    with open(os.path.join(ui, "candidates.jsonl"), "w", encoding="utf-8") as handle:
        for key in keys:
            row = _question(number=7)
            row["candidate_key"] = key
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with open(os.path.join(ui, "question_review_events.jsonl"), "w", encoding="utf-8") as handle:
        for key in blocked:
            handle.write(json.dumps({"candidate_key": key, "action": "block",
                                     "reviewer": "local"}) + "\n")
    return ui


def _run_round(root, *extra):
    import subprocess
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(PKG, "src")
    return subprocess.run(
        [sys.executable, os.path.join(PKG, "scripts", "confirm_dispute.py"),
         "--queue", str(root), "--model", "splash", *extra],
        capture_output=True, text=True, env=env)


def test_pending_is_intersected_with_the_humans_blocks(tmp_path=None):
    """**負控制：pending 是快取，人的 `block` 才是規則。**（owner 2026-09-24 在站上量到的陷阱）

    站上的狀態檔裡有 **488 題**是舊閘門（種類式）排進 pending 的。`--pending-only` 從前是
    `blocked = pending`，理由寫在程式裡：「pending 本來就是掃描用同一組 standing action 算出來的，
    再取交集只是把同一組讀兩次」——那個理由在掃描的閘門換掉的那一刻就不成立了。於是那 488 題
    沒有一題是人 block 的，卻全部會被送去模型，正好是「僅針對block去看」的反面。
    **一個由舊版規則寫下的快取不是規則。**

    這裡釘住兩件事：這一輪只讀 pending ∩ block（沒有紙本的那一題會留下，讀不到不是做完），
    而**不是人 block 的 pending 在同一輪就離開 pending**——掃描已經存過它們的指紋，所以它們不會
    自己回來；但人之後把其中一題標成 block 就是內容變了，那一題下一輪自己會回來。

    舊行為下這條會失敗（三個 key 全部被讀、pending 停在三題）。
    """
    import tempfile
    from qbr import scan_state

    root = tempfile.mkdtemp()
    _pending_queue(root, ["blocked-one", "leftover-1", "leftover-2"], blocked=["blocked-one"])
    scan_state.mark_pending(root, ["blocked-one", "leftover-1", "leftover-2"])
    out = os.path.join(root, "round.jsonl")

    result = _run_round(root, "--pending-only", "--out", out)
    assert result.returncode == 1, "one failed page read must fail the stage, not appear successful"
    assert "讀不到 1" in result.stdout
    read = [record["candidate_key"] for record in ai_findings.load(out)]
    assert read == ["blocked-one"], "這一輪把不是人 block 的題也送出去了：%r" % read
    assert scan_state.pending_keys(root) == ["blocked-one"], \
        "不是人 block 的要離開 pending，人 block 的（讀不到紙本）要留著：%r" % scan_state.pending_keys(root)


def test_pending_that_is_not_the_humans_work_reads_nothing_at_all(tmp_path=None):
    """**負控制：空集合是「什麼都不做」，不是「不篩」。**

    pending 裡一題 block 都沒有時，這一輪**不能**因此變成「不篩」——那會把整份 pending 送去模型，
    而整份 pending 正是舊閘門留下來的那些題。舊行為下它會讀兩題（兩個 no-paper 紀錄），
    這條就會失敗。
    """
    import tempfile
    from qbr import scan_state

    root = tempfile.mkdtemp()
    _pending_queue(root, ["leftover-1", "leftover-2"], blocked=[])
    scan_state.mark_pending(root, ["leftover-1", "leftover-2"])
    out = os.path.join(root, "round.jsonl")

    result = _run_round(root, "--pending-only", "--out", out)
    assert result.returncode == 0, result.stderr
    assert ai_findings.load(out) == [], "pending 裡沒有人 block 的題，卻還是被送去模型了"
    assert scan_state.pending_keys(root) == [], \
        "舊閘門留下來的 pending 要在這一輪清掉：%r" % scan_state.pending_keys(root)


def test_the_resident_loop_is_pinned_to_the_work_list_that_will_not_empty_itself():
    # A shell script is the one part of this system no unit test runs, and the defect it carried was
    # invisible from the outside: the loop started every 30 minutes, printed a log line, and did
    # nothing. The fix is now the scan/repair split plus the flags that pin the repair pass to it; if
    # a future edit drops one, the loop silently returns to "select nothing and report completion".
    # So the flags are asserted against the script text and against the argument parser, which is the
    # only kind of test that can catch a regression in a file that is never imported.
    import subprocess
    daemon = os.path.join(PKG, "scripts", "repair_daemon.sh")
    with open(daemon, encoding="utf-8") as handle:
        text = handle.read()
    assert "--pending-only" in text, \
        "常駐迴圈的工作清單必須來自掃描留下的 pending，否則它會回到「選不到題卻說完成」"
    assert "--skip-confirmed" in text, \
        "一個 30 分鐘迴圈必須跳過已讀過的讀法，否則每輪重拍重問同一批"
    assert "--model" in text and "${LANE}" in text, \
        "lane 宣告了就要真的傳下去，不然 LANE=... 等於裝飾"
    # The work list is `pending`, not the block list, and the difference is the ledger. `pending` is
    # what the scan decided was *new*: since 2026-09-24 the scan's gate is the human's standing
    # `block` (owner: 「我才說後面的agent循環是僅針對block去看」), and `--pending-only` intersects
    # pending with those blocks again, so the loop touches nothing a person did not mark. Going back
    # to `--blocked-only` would drop the ledger and re-select every blocked question on every 30-minute
    # tick - 「反覆吃算力」, the thing the scan/repair split exists to stop.
    assert "--blocked-only" not in text, \
        "常駐迴圈只認 pending，不然掃描的指紋帳本就沒有作用（每 30 分鐘重問同一批 block）"
    assert "scan" in text and "report" in text, \
        "掃描與回報必須是這支腳本自己的模式，不然 owner 只能整條一起跑"
    # And the flags exist: a script mentioning a flag the parser does not define fails at run time,
    # in a loop, where nobody is watching the stderr.
    args = subprocess.run([sys.executable, "-c",
                           "import sys; sys.argv=['x','--queue','/nonexistent']\n"
                           "sys.path[:0]=['%s','%s']\n"
                           "import confirm_dispute as c, json; print(json.dumps(sorted(\n"
                           "    a for a in vars(c.parse_args()) if a.startswith('blocked') or a.startswith('skip') or a.startswith('pending'))))\n"
                           % (os.path.join(PKG, "src"), os.path.join(PKG, "scripts"))],
                          capture_output=True, text=True)
    assert args.returncode == 0, args.stderr
    assert json.loads(args.stdout) == ["blocked_only", "pending_only", "skip_confirmed"]






# --------------------------------------------------- 「讀不出來」不是「改成▢」

def test_a_region_the_model_could_not_read_is_not_a_change_to_write():
    """業主收到的那一則反問逐字是「把 option A 從「」改成「▢」」（2026-09-25）。

    站上量到 127 筆 change 帶 `▢`、其中 96 筆抽取側本來就是空的：`▢` 是模型說「我讀不出來」，
    不是紙本上的字。負控制：拿掉 `readable_changes`（＝修好之前的行為），這一筆會被當成改動
    送進 ③ 的套用流程，而 ③ 只能事後拒絕它（`dispute_apply.page_read.whole_field_refusals`）。
    """
    question = _question(options=[{"key": "A", "text": ""}, {"key": "B", "text": "選項B"},
                                  {"key": "C", "text": "選項C"}, {"key": "D", "text": "選項D"}])
    _report, changes = confirm_dispute.changes_between(
        question, _seen(options={"A": "▢", "B": "選項B", "C": "選項C", "D": "選項D"}))

    assert [change["field"] for change in changes] == ["option A"]
    kept, unreadable = confirm_dispute.readable_changes(changes)
    assert kept == []
    assert [change["field"] for change in unreadable] == ["option A"]


def test_a_real_difference_is_never_mistaken_for_a_region_that_could_not_be_read():
    """反向負控制：紙本真的有字的差異必須留在 `changes` 裡，否則這一版會把所有改動都吃掉。"""
    assert confirm_dispute.unreadable_change({"field": "option A", "page": "長"}) is False
    assert confirm_dispute.unreadable_change({"field": "option A", "page": "使用較長的OID"}) is False
    assert confirm_dispute.unreadable_change({"field": "option A", "page": ""}) is False
    assert confirm_dispute.unreadable_change({"field": "option A", "page": "▢"}) is True


def test_a_question_whose_page_could_not_be_read_is_its_own_class_not_agreement():
    """「讀不出來」既不是「不一致」也不是「一致」：舊行為把它算成 change（於是要改），若只把它從
    change 裡拿掉又會變成 OK（於是判讀說紙本與抽取一致）——兩個都是謊。它自成一類，回報才數得出來。"""
    finding = confirm_dispute.finding_from([], seen=_seen(options={"A": "▢"}), error=None,
                                           unreadable=[{"field": "option A", "page": "▢"}])
    assert finding["what"] == "UNREADABLE"
    assert finding["verdict"] is None
    assert "option A" in finding["where"]
    assert "▢" not in finding.get("fix") or "不要改字" in finding["fix"]
    # 負控制：同一個空 diff 而沒有讀不出來的格子時，仍然是「一致」。
    assert confirm_dispute.finding_from([], seen=_seen(), error=None)["what"] == "NONE"


def test_a_partly_unreadable_reading_still_repairs_what_it_could_read():
    """讀得到的那幾格照修；讀不到的那一格只在 `where` 裡說出來，不進 `fix`（不寫進題庫）。"""
    unreadable = [{"field": "option A", "page": "▢"}]
    changes = [{"field": "option B", "from": "較⻑", "to": "較長", "stored": "較⻑", "page": "較長"}]
    finding = confirm_dispute.finding_from(changes, seen=_seen(), error=None, unreadable=unreadable)
    assert finding["verdict"] == "DEFECT"
    assert "option B" in finding["fix"] and "▢" not in finding["fix"]
    assert "1 格紙本讀不出來" in finding["where"]


def test_the_round_asks_the_person_about_a_region_it_could_not_read_in_those_words():
    """反問的措辭是這一輪唯一的對人介面。舊行為把「▢」從 change 拿掉之後會說「紙本與抽取文字看過
    是一樣的」——那句話在模型根本看不到那一格時是假的。"""
    import tempfile
    from qbr import discuss

    queue_dir = os.path.join(tempfile.mkdtemp(), "review-ui")
    os.makedirs(queue_dir)
    question = _question()
    result = {"unreadable": [{"field": "option A", "page": "▢"}], "changes": []}
    assert confirm_dispute._escalate(queue_dir, question, result, {"name": "m", "url": "u"}) is True

    events = discuss.load_events(os.path.join(queue_dir, discuss.REPAIR_QUESTIONS_STREAM))
    assert len(events) == 1
    ask = events[0]
    assert "讀不出來" in ask["reason"]
    assert "option A" in ask["question"]
    assert "看過是一樣的" not in ask["question"]


# --------------------------------------------------- 截該題看不出來就截上下題

def test_a_read_that_settled_the_question_is_not_sent_to_the_neighbours():
    """業主 2026-09-25：「如果截該題沒辦法找出問題，應該截上下題來參考」。

    已經在這一題自己身上找到東西可修的讀取**不**再花一次呼叫看鄰題：那一題自己的截圖就是證據，
    再看鄰題只會讓結論擴大。負控制：引擎故障（transport／unparsed）也不算「這一題讀不出結論」——
    那時圖不是問題，重送只是多花一次呼叫學同一件事。
    """
    assert confirm_dispute.needs_reference_read(kept=[], error=None) is True       # 一致＝沒找出問題
    assert confirm_dispute.needs_reference_read(kept=[], error=None) is True       # 只有 ▢
    assert confirm_dispute.needs_reference_read(kept=[], error="no-rows") is True  # 這一題沒有列
    assert confirm_dispute.needs_reference_read(kept=[], error="no-crop") is True
    assert confirm_dispute.needs_reference_read(kept=["option B"], error=None) is False
    assert confirm_dispute.needs_reference_read(kept=[], error="request failed") is False
    assert confirm_dispute.needs_reference_read(kept=[], error="unparsed") is False


def test_the_reference_picture_is_the_questions_on_either_side_and_never_this_one():
    """鄰題的列＝前一題的區塊＋後一題的區塊。**不含**被問那一題自己的列，否則模型會把這一題的
    文字當成鄰題的、或把鄰題的當成這一題的。"""
    rows = [
        {"text": "41.前一題", "page": 3, "y0": 10.0, "x0": 20.0, "x1": 100.0, "y1": 24.0},
        {"text": "42.本題", "page": 3, "y0": 30.0, "x0": 20.0, "x1": 100.0, "y1": 44.0},
        {"text": "A.甲", "page": 3, "y0": 50.0, "x0": 20.0, "x1": 100.0, "y1": 64.0},
        {"text": "43.下一題", "page": 3, "y0": 70.0, "x0": 20.0, "x1": 100.0, "y1": 84.0},
    ]
    band = reread.band_rows(rows, 42)
    assert [row["text"] for row in band] == ["42.本題", "A.甲"]
    neighbours = reread.band_rows(rows, 41) + reread.band_rows(rows, 43)
    assert [row["text"] for row in neighbours] == ["41.前一題", "43.下一題"]


def test_the_reference_reading_replaces_the_direct_one_only_when_it_explains_more():
    """判斷規則：鄰題那張圖只有在**多解釋了這一題的欄位**時才取代第一次判讀（業主描述的情形）。
    兩邊都找到東西時留第一次判讀——那一張是被問這一題本身的圖，鄰題只是脈絡。"""
    question = _question(options=[{"key": "A", "text": "x"}, {"key": "B", "text": "較⻑"},
                                  {"key": "C", "text": "y"}, {"key": "D", "text": "z"}])
    agreed = _seen(options={"A": "x", "B": "較⻑", "C": "y", "D": "z"})
    explains_b = _seen(options={"A": "x", "B": "較長", "C": "y", "D": "z"})

    answers = [explains_b, agreed]
    original_crop, original_transcribe = (confirm_dispute.neighbour_crop_for,
                                          confirm_dispute.transcribe)
    confirm_dispute.neighbour_crop_for = lambda *a, **k: (b"PNG", 2, None)
    confirm_dispute.transcribe = lambda *a, **k: (answers.pop(0), "raw", None, {}, 1.0)
    try:
        send = {"endpoint": {"name": "m", "url": "u"}, "max_tokens": 10, "timeout": 10,
                "principles": None, "answers": None, "notes": None, "rejected": None,
                "figures": ""}
        # 第一次判讀什麼都沒解釋 → 鄰題讀到 option B 的差異 → 取代它。
        first = confirm_dispute.reference_read(question, number=42, pdf_path="p.pdf", kept_rows=[],
                                              crops_root="/q/crops", dpi=200, send=send,
                                              first_seen=agreed)
        assert first["used"] is True
        assert first["explained"] == ["option B"] and first["first_explained"] == []
        # 兩邊都解釋了同一格 → 維持第一次判讀。
        second = confirm_dispute.reference_read(question, number=42, pdf_path="p.pdf", kept_rows=[],
                                               crops_root="/q/crops", dpi=200, send=send,
                                               first_seen=explains_b)
        assert second["used"] is False
        assert second["explained"] == [] and second["first_explained"] == ["option B"]
    finally:
        confirm_dispute.neighbour_crop_for, confirm_dispute.transcribe = (original_crop,
                                                                         original_transcribe)


def test_the_prompt_says_which_questions_the_second_picture_is_of():
    """模型無法從圖上知道自己被問的是哪一題。參考讀取的兩輪都要說明白，而且**要禁止**把鄰題的文字
    算進被問那一題（那會變成一個看起來很確定的假修復）。"""
    plain = confirm_dispute.transcribe_system()
    assert "相鄰題目" not in plain
    neighbours = (41, 43)
    system = confirm_dispute.transcribe_system(neighbours=neighbours)
    assert "前一題與後一題" in system and "41" in system and "43" in system
    assert "不要把其他題目的文字算進" in system
    # 裁切是紙本上的一個矩形，所以上下題相夾時**這一題自己也在圖裡**（2026-09-25 實測：q26+q28
    # 那張拼圖被模型讀成 26、27、28）。說「這張圖不是被問的那一題」是對輸入說假話。
    assert "矩形" in system and "也可能一起落在這張圖裡" in system
    instruction = confirm_dispute.neighbour_instruction(42)
    assert "42" in instruction and "上下相鄰" in instruction
    assert "矩形" in instruction
    assert confirm_dispute.neighbour_numbers(1) == (2,)
    assert confirm_dispute.neighbour_numbers(None) == ()
