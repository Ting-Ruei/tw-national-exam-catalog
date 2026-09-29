# -*- coding: utf-8 -*-
"""The repair loop: a human's `block` becomes a detector, and the detector reopens what it finds.

The loop this implements, in one direction and never the other:

    1. collect    read the human decisions; every question whose latest action is `block` is the
                  input. A block with no note is still input - it says "this is wrong" - it just
                  does not say what.
    2. explain    ask which *existing* detector already explains the block. A block the pipeline
                  already reports is not a gap; it is a detector that was not visible to the
                  reviewer, and the fix for that is elsewhere.
    3. strengthen a block **no** detector explains is the signal to build one. This script does not
                  build it (a rule that reads meaning is a prompt, not a script - charter §2); it
                  prints the unexplained blocks grouped by paper so a person can see whether they
                  are one cause or twenty.
    4. rescan     with the strengthened detector in place, re-run detection over the whole queue and
                  report what newly fires.
    5. reopen     every question the new detector fires on that a person had already judged gets a
                  `reset_review` event, so the accepted ones are put back in front of a human. The
                  event is appended and the old decision kept - never overwritten.

Why `reset_review` and not a silent edit
---------------------------------------
A review decision is a statement about the reading that was in front of the reviewer. When a new
rule says that reading was wrong, the old decision is not false - it was made about different text -
and the honest record is a new event that says so, keeping the old one. This is the same contract
`append_reset_review_events.py` implements for *content* changes; here the trigger is a *detector*
change, which is why the `reset_notes` name the new rule.

Why it cannot run the other way
-------------------------------
A question a person **accepted** is only reopened if a detector fires on it. "No detector fires" is
not evidence of correctness and is never upgraded to one, so the loop cannot turn a human's silence
into approval, nor their approval into rejection without a machine-checkable reason. The output is
advisory (`GOV-05`): it writes a proposal, and a separate deliberate `--apply` writes events.

Usage
-----
    # 1+2+3: what do the human blocks reveal that no detector explains?
    python scripts/repair_loop.py --queue <queue-root>

    # 4: re-run detection over the queue with the current code, and diff against what is stored
    python scripts/repair_loop.py --queue <queue-root> --rescan

    # 5: propose the `reset_review` events for questions a new rule now fires on
    python scripts/repair_loop.py --queue <queue-root> --rescan --propose out.json
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from qbr import discuss, disputes  # noqa: E402
from qbr.dispute_apply.withdrawals import ATTEMPT_REJECTING_ACTIONS  # noqa: E402

QUESTION_ACTIONS = {"accept", "correct", "needs_review", "block", "exclude", "unblock",
                    "reset_review"}

#: Actions that are not a decision about one question: a group action is about a shared stem
#: (`confirm_group`, `group_block`, …), a visual action about the paper's picture, a mobile action
#: about a device. Kept in step with `serve_question_review_ui`'s sets **by name** rather than by
#: importing the server module, because this script must stay loadable without the server's
#: dependencies - and they live here, next to the one fold that uses them, instead of in each script
#: that reads the stream (that was `confirm_dispute.GROUP_ACTIONS`, a second copy of the same list).
NON_QUESTION_ACTIONS = {"group_accept", "group_block", "group_needs_review", "group_reviewed",
                        "group_unreviewed", "confirm_not_group", "confirm_group",
                        "reset_group_review", "human_review_pdf_visual", "mobile_defer",
                        "mobile_resume"}

#: The action a human uses to say "this reading is wrong". `needs_review` is deliberately *not*
#: here: it says "I could not decide", which is a different input to the loop - a detector cannot
#: be built from "I am unsure", only from "this is wrong". The set itself is defined next to the
#: rejection semantics that use it (`dispute_apply.withdrawals.ATTEMPT_REJECTING_ACTIONS`) so the
#: cycle cap and this fold count the same events; a second literal here would be a second answer.
BLOCKING_ACTIONS = frozenset(ATTEMPT_REJECTING_ACTIONS)

#: `applied` with this value means the machine **took its own repair back** (`apply_withdrawal_event`
#: in `apply_dispute_repairs.py`: `applied == "withdrawn"`, no `changes`, a `withdraw` list), not that
#: it made a new one. Truthy, so it must be named: reading it as a repair would erase the fact that a
#: repair was tried and refused (see `fold_review_events`).
WITHDRAWN = "withdrawn"

#: Who writes an event. Measured on the station's stream (8,307 events, 2026-09-25): the human
#: surfaces sign `source: linear_v2` (題目區) and `linear_v2_discuss` (錯題討論區) with
#: `reviewer: local`, and the server's own default for an event posted without one is `review_ui`;
#: the machine signs `source: qbr_dispute_apply` / `reviewer: repair_dispute_apply` (its withdrawals
#: carry no `source` at all) and the scope measurements sign `reviewer: content-change-reset`.
#:
#: A whitelist rather than a blacklist, because the question this answers is "is this a person's
#: words", and the expensive mistake is answering *yes* wrongly: 311 of the 464 questions with a
#: standing note have the machine's own 「依 dispute 的機械證據修復：…」 as that note, and every
#: reader of `notes_by_key` renders them as 「審題者…寫下的原話（未經改寫）」.
HUMAN_SOURCES = frozenset({"linear_v2", "linear_v2_discuss", "review_ui"})
HUMAN_REVIEWERS = frozenset({"local"})


def is_human_event(event: dict) -> bool:
    """True when a **person** wrote this event, false for the machine and for measuring scripts.

    Two markers, either one enough, because both are set by the writing end and neither is derived:
    the surface (`source`) and the signer (`reviewer`). A human event that carried neither would be
    invisible to the note channel - the defect this file's `notes_by_key` exists to fix - so the pair
    is deliberate rather than a single field. See `HUMAN_SOURCES` for the measured values.
    """
    source = str(event.get("source") or "").strip()
    reviewer = str(event.get("reviewer") or "").strip()
    return source in HUMAN_SOURCES or reviewer in HUMAN_REVIEWERS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queue", required=True,
                        help="queue root; the human decisions live in its review-ui/ subdirectory")
    parser.add_argument("--rescan", action="store_true",
                        help="re-run detection over every question and report what changed")
    parser.add_argument("--propose",
                        help="write a reset_review proposal JSON here (consumed by "
                             "append_reset_review_events.py)")
    parser.add_argument("--reviewer", default="detector-change-reset")
    parser.add_argument("--show", type=int, default=12,
                        help="how many unexplained blocks to print")
    return parser.parse_args()


def review_ui_dir(queue: str) -> str:
    """The `review-ui/` subdirectory of a queue root.

    Tolerates being handed the subdirectory itself, because the flag is `--queue` and both spellings
    are natural to type - and a script that silently reads the wrong directory would report zero
    decisions, which looks exactly like "nobody has reviewed anything".
    """
    if os.path.exists(os.path.join(queue, "candidates.jsonl")):
        return queue
    nested = os.path.join(queue, "review-ui")
    if os.path.exists(os.path.join(nested, "candidates.jsonl")):
        return nested
    return queue


def iter_events(path: str):
    """Every parseable event of the append-only human review stream.

    A corrupt line is printed and skipped rather than treated as an absence of a decision: "the log is
    damaged" and "nobody decided" must not look the same, and the line number is the only way anyone
    can go and look at it.
    """
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                print("  warning: line %d of %s is not JSON; skipped" % (number, path),
                      file=sys.stderr)


def fold_review_events(path: str) -> dict:
    """One pass over the human review stream: per key, the decision that stands and the note that stands.

    **One fold, because two folds that read one file disagree.** The repair pass, the scan and the
    confirmation loop all ask the same question of the same stream - "what did the person decide about
    this question, and what did they write?" - and every hand-written copy of that traversal is a
    place where one of them can answer differently. `confirm_dispute.blocked_keys` was such a copy,
    and the note is what it lost.

    `{key: row}`, where a row is:

      * `action` — the most recent **decision** (`QUESTION_ACTIONS`). A note is not a decision: a
        `comment` must not un-block a question, and a group/visual/mobile action is about something
        other than this question.
      * `note` — the reviewer's own sentence that still stands (a **person's**, see
        `is_human_event`): the most recent non-empty note, carried forward across later events that
        carry none. This is the reader's half of the rule the writer now applies
        (`legacy_assets._reaffirm_standing_action`), so history written before the
        writer had it still reads correctly. The need is measured, not hypothetical:
        `moex:105100:305:33:1:question:q046` is comment「檢查上下標」→ `block` with `notes: ""`, and a
        latest-event-wins reader sees an empty note - which is how the reviewer's only sentence about
        that question never reached the model.
      * `note_rows` — every **distinct** note a **person** wrote (`is_human_event`), oldest first
        (`[{candidate_key, notes, created_at}]`), because the *number* of sentences is information: a
        question the person came back to twice has two, and a new one is new work
        (`scan_state.question_fingerprint`). Distinct, not "one row per event": the stream carries
        copies, not just sentences - the real log has 「檢查上下標」 twice on `q046`, and from now on
        the writer also carries an existing note into a later bare decision, so a `block` written with
        the box closed repeats what the question already stood on. Counting copies would quote the same
        sentence to the model two or three times and, worse, make every click of a decision button look
        like a new note to the scan (a re-ask per click). The rule is the one the writer's half states:
        the note is a property of the question, and the same property written twice is one property.
      * `reviewed_at` and `event` — the time of the most recent decision and its own record, so a
        caller that needs the rest of the fields (a correction, a reviewer) still has them.
      * `rejections` — how many times the machine has been refused on this question: a `block` on a
        question that **had a machine repair earlier in its history** counts one, an `accept`/`unblock`
        clears the count. A `block` on a question the machine never touched counts **zero**, because
        there was no attempt to refuse - that block is a new problem. This is the loop's own memory of
        "we already tried and it was refused", and it exists because a **second** refusal used to leave
        no trace a scan could see: the action is `block` again, the text is the parser's again (the
        repair was withdrawn), and usually no note is written (`scan_state.question_fingerprint`).
        Owner's direction, 2026-09-25: 「被**重複拒絕**的題目要讓它**再進入掃描的迴圈**」.

        Measured on the station's log (2026-09-25, 8,307 events): under this rule 68 questions were
        refused, **64 of them twice** and one three times; under the narrower rule I first wrote
        ("the block must follow a repair that is still standing") the count was 64 questions **all at
        one**, because the machine withdraws its repair after the first refusal (`applied:
        "withdrawn"`) - the second refusal was therefore invisible, which is exactly the defect. So the
        repair's later withdrawal must not erase the fact that it was tried and refused.
      * `refused_changes` — the change the person refused, field by field (`[{field, from, to}]`: the
        last machine repair on the question **at the time of the block**). The repair's own text is the
        point: "we tried and it was refused" is only actionable if the next pass can see *what* was
        tried, otherwise it re-invents the same edit (`ai_findings.rejected_note` renders this). Empty
        when the machine never wrote one, or when the count is zero. Measured: 64 of the 64
        twice-refused questions carry it.
      * `machine_applied_kind` — the `applied` form of that repair (`field` / `substitution`), so a
        reader rebuilding the before/after knows whether `from`/`to` is a whole field or a character
        pair (`propose_principles.snapshots_for`).
      * `confirmations` and `confirmed_changes` — the mirror image: an `accept` while a machine repair
        was **still standing** (`repair_standing`) is the person saying "this change is right", and
        `confirmed_changes` is that change. It is the learning signal of the owner's 2026-09-25
        direction 「被**同意**的題目則成為**可以學習的原則**」, and it is the *only* place it exists:
        the server's correction-feedback path requires a `correction` on the event
        (`review_state._event_is_human_correction`), so an `accept` used to write nothing at all.
        `unblock` deliberately does not count - releasing a question says nothing about its text.
    """
    folded = {}
    for event in iter_events(path):
        key = event.get("candidate_key")
        if not key:
            continue
        action = str(event.get("action") or "")
        if action in NON_QUESTION_ACTIONS:
            continue
        row = folded.setdefault(key, {"action": "", "note": "", "note_rows": [],
                                      "reviewed_at": None, "event": {},
                                      "rejections": 0, "repairs_seen": 0,
                                      "machine_applied": [], "refused_changes": [],
                                      "repair_standing": False,
                                      "confirmations": 0, "confirmed_changes": [],
                                      "confirmed_at": None, "machine_applied_kind": ""})
        # **只有人寫的句子是註解。** 機器的修復事件也帶 `notes`（「依 dispute 的機械證據修復：
        # (⻑→長)」），而這個頻道整條路都在說「審題者寫下的原話」——`NOTES` 的標題、`BLOCKED_WITH_NOTE`
        # 的「他在標記時寫下了註解」、`notes_note` 的逐字引號。把機器的摘要放進去，提示詞就會拿機器的
        # 話冒充人的方向。測到的規模（站上 2026-09-25）：464 題有「還站著的註解」，其中 **311 題
        # 的那一句是機器寫的**——機器修復 141 筆、其中 137 筆帶 summary。
        # 機器做了什麼由 `refused_changes` 逐欄攜帶（可驗證的 from/to），不需要它的散文。
        if is_human_event(event):
            note = str(event.get("notes") or "").strip()
            if note:
                row["note"] = note
                if note not in [known["notes"] for known in row["note_rows"]]:
                    row["note_rows"].append({"candidate_key": key, "notes": note,
                                             "created_at": event.get("created_at")})
        applied = str(event.get("applied") or "").strip().lower()
        if applied == WITHDRAWN:
            # 撤回＝那一筆修復**不再站在題目上**了。它不抹掉「試過」的歷史（`repairs_seen` 不動），
            # 但它讓「人接下來如果接受，接受的是這筆修復」變成假的——所以只把站著與否設回 False。
            row["repair_standing"] = False
        elif applied:
            row["repairs_seen"] += 1
            row["repair_standing"] = True
            row["machine_applied_kind"] = applied
            row["machine_applied"] = [_machine_change(change)
                                      for change in (event.get("changes") or [])
                                      if isinstance(change, dict)]
        if action in BLOCKING_ACTIONS and row["repairs_seen"]:
            # 打回的是**那一筆改動**：`machine_applied` 會被下一次修復蓋掉，所以先留一份。撤銷
            # （`applied: "withdrawn"`）不動 `repairs_seen`：那筆修復試過了，這一點不會因為它被
            # 還原而變成沒發生過。
            row["refused_changes"] = list(row["machine_applied"])
            row["rejections"] += 1
        elif action == "accept":
            # **同意一筆站著的機器修復**＝這一筆改動被人確認了（業主 2026-09-25：「被同意的題目
            # 則成為可以學習的原則」）。它與「人自己改」是不同的訊號，而且它以前**完全不寫任何東西**
            # （`review_state._event_is_human_correction` 只認 `correct`，accept 沒有 correction
            # 就落 `NoVisibleChange` 而不寫）。這裡是那個訊號唯一的來源，`propose_principles.py`
            # 是它唯一的消費者：≥2 筆同類才提案，而提案永遠不等於核准。
            if row["repair_standing"] and row["machine_applied"]:
                row["confirmations"] += 1
                row["confirmed_changes"] = list(row["machine_applied"])
                # 確認的時間就是那一筆 accept 的時間：它是審核事件流裡的一筆事件，也就是這個訊號
                # 唯一的出處（`propose_principles.py` 把它當成例子的出處寫進封包裡）。
                row["confirmed_at"] = event.get("created_at")
            row["rejections"] = 0
        elif action == "unblock":
            # 放回可用不是「同意這一筆改動」：它只結束拒絕的計數。
            row["rejections"] = 0
        # A machine `reset_review` changes repair state, not the person's decision. Keeping the
        # latest human action is what lets a rejected question return for another page read.
        if action in QUESTION_ACTIONS and is_human_event(event):
            row["action"] = action
            row["reviewed_at"] = event.get("created_at")
            row["event"] = event
    return folded


def _machine_change(change: dict) -> dict:
    """One `changes[]` entry of a machine `reset_review`, kept as it was written.

    Kept verbatim (`from`/`to` included) rather than reduced to the field name, because the refused
    repair's **own text** is what a second attempt must not repeat: "we already changed `stem`" tells
    the next pass nothing about which change it must not make again.
    """
    return {"field": str(change.get("field") or ""),
            "from": change.get("from"),
            "to": change.get("to")}


def read_latest_actions(path: str) -> dict:
    """The most recent question-level action per candidate key, carrying the note that stands on it.

    Only question-level actions count: a group-level action is about a shared stem and resetting it
    would reopen a batch the reviewer did not judge as a question. The returned event's `notes` is the
    reviewer's **standing** note, not merely the latest event's field, so a reader that displays or
    forwards it cannot show an empty note for a question the person wrote about.
    """
    latest = {}
    for key, row in fold_review_events(path).items():
        if row["action"] not in QUESTION_ACTIONS:
            continue
        event = dict(row["event"])
        if row["note"]:
            event["notes"] = row["note"]
        latest[key] = event
    return latest


def notes_by_key(path: str) -> dict:
    """`{candidate_key: [{"notes", "created_at"}, …]}` — the reviewer's 註解, oldest first.

    **Only what a person wrote** (`is_human_event`): the machine's repair events also carry `notes`
    (「依 dispute 的機械證據修復：(⻑→長)」), and this stream is rendered into the prompt as
    「審題者…寫下的原話（未經改寫）」. Measured on the station 2026-09-25: 311 of the 464 questions with
    a standing note had the machine's own sentence as that note, so the prompt was quoting the machine
    back to the model as the person's direction (and `BLOCKED_WITH_NOTE` introduced it as 「他在標記時
    寫下了註解」). What the machine did is carried by `rejections_by_key` instead - field by field, with
    the text either side, which is what a second attempt can act on.

    Rows, not one string, because the count is information (see `fold_review_events`). This is what the
    confirmation pass feeds to the prompt (`ai_findings.notes_note` prints them newest first) and what
    the scan feeds to the fingerprint (`scan_state.notes_text` joins them in the order they were
    written), so both read the reviewer's words through the same traversal.
    """
    return {key: row["note_rows"] for key, row in fold_review_events(path).items()
            if row["note_rows"]}


def human_answers_by_key(path: str) -> dict:
    """Human answers to the repair agent's questions, grouped by their candidate.

    `discuss.repair_questions_projection` owns the ask/answer fold. This layer only filters its
    projection to human answers and narrows the prompt context to the candidate that was answered;
    machine-authored responses and another candidate's answer are not reviewer evidence here.
    """
    events = discuss.load_events(path)
    rows = discuss.repair_questions_projection(events)["questions"]
    out = {}
    for row in rows:
        key = row.get("candidate_key")
        answer = str(row.get("answer_text") or "").strip()
        if not key or not answer or not is_human_event(row.get("answer") or {}):
            continue
        out.setdefault(key, []).append({
            "candidate_key": key,
            "question": row.get("question"),
            "answer_text": answer,
        })
    return {key: {"questions": rows} for key, rows in out.items()}


def rejections_by_key(path: str) -> dict:
    """`{candidate_key: {"count", "fields", "changes"}}` — for the questions machine-tried and refused.

    Only the questions with at least one refusal appear: a question nobody repaired and nobody refused
    has nothing to remember, and a row of zeros would be a second way of saying "no" that every reader
    then has to interpret. `fields`/`changes` describe the repair that was **refused** (the last one,
    when there were several).

    Three readers, one traversal (`fold_review_events`): the scan puts `count` into the fingerprint
    (a repeat refusal is new work), the confirmation pass renders `count`+`changes` into the prompt
    (`ai_findings.rejected_note`) so the model is told what was tried and refused instead of being asked
    to invent the same repair a third time, and `explain` carries `count` into the human's report.
    """
    out = {}
    for key, row in fold_review_events(path).items():
        if not row["rejections"]:
            continue
        changes = list(row["refused_changes"])
        out[key] = {"count": row["rejections"],
                    "fields": [change["field"] for change in changes if change.get("field")],
                    "changes": changes}
    return out


def confirmations_by_key(path: str) -> dict:
    """`{candidate_key: {"count", "fields", "changes"}}` — machine repairs a person **accepted**.

    The mirror of `rejections_by_key` and read from the same fold, on purpose: "what we tried and he
    said yes" and "what we tried and he said no" are two readings of one stream, and a second parser
    for the second reading is exactly how the two would come to disagree.

    Only repairs that were **still standing** when the `accept` arrived count (see
    `fold_review_events`): a repair the machine withdrew is not what the person accepted, even though
    it stays in `repairs_seen` as history.

    The consumer is `propose_principles.py`, which turns same-class confirmations into a **proposed**
    principle - a proposal, never an approval. Nothing else may read this as permission to change a
    question: a confirmation is a fact about one change on one question, not a rule.
    """
    out = {}
    for key, row in fold_review_events(path).items():
        if not row["confirmations"]:
            continue
        changes = list(row["confirmed_changes"])
        out[key] = {"count": row["confirmations"],
                    "fields": [change["field"] for change in changes if change.get("field")],
                    "changes": changes,
                    # 那一筆修復是哪一種形式（`field`＝整欄替換、`substitution`＝單字替換）。
                    # `propose_principles` 用它決定怎麼重建 before/after：單字替換的 `from`/`to`
                    # 是一個字，直接賦值會把整個題幹換成一個字。
                    "applied": row["machine_applied_kind"],
                    "confirmed_at": row["confirmed_at"]}
    return out


def load_candidates(path: str):
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def detector_names(question) -> list:
    """The dispute kinds already stored on a queue row.

    Reads the stored field rather than recomputing, because the question here is "did the reviewer
    have this information in front of them" - and the stored field is exactly what they were served.
    """
    found = question.get("disputes") or []
    return sorted({d.get("kind") for d in found if d.get("kind")})


def collect_blocks(queue_dir: str):
    """Every question whose latest human action is a block, with the reading they blocked."""
    events_path = os.path.join(queue_dir, "question_review_events.jsonl")
    latest = read_latest_actions(events_path)
    blocked = {key: event for key, event in latest.items()
               if event.get("action") in BLOCKING_ACTIONS}
    rows = {}
    for question in load_candidates(os.path.join(queue_dir, "candidates.jsonl")):
        key = question.get("candidate_key")
        if key in blocked:
            rows[key] = question
    return blocked, rows


def explain(blocked: dict, rows: dict, rejections: dict | None = None) -> tuple:
    """Split the blocks into "a detector already says this" and "nothing does"."""
    explained, unexplained = [], []
    rejections = rejections or {}
    for key, event in sorted(blocked.items()):
        question = rows.get(key)
        kinds = detector_names(question) if question else []
        entry = {"candidate_key": key, "notes": (event.get("notes") or "").strip(),
                 "kinds": kinds,
                 "paper": paper_of(key),
                 "subject": ((question or {}).get("metadata") or {}).get("normalized_subject_name")}
        # 這一題機器試過、被人打回的次數與那筆被退的改動（`fold_review_events` 的同一份折疊）。
        # 帶著它走，因為「已經被退兩次之後又被退一次」與「第一次被退」要的是不同的下一個動作，
        # 而報告（`report_blocks`）與提示詞（`ask_about_blocks.ask_one` → `rejected_note`）都必須
        # 看得到差別。`None` 是「沒被退過」，不是「沒查」。
        entry["rejected"] = rejections.get(key) or None
        (explained if kinds else unexplained).append(entry)
    return explained, unexplained


def paper_of(candidate_key: str) -> str:
    """The paper prefix of a candidate key: `moex:115090:308:0504:1:question:q041` -> `115090:308`."""
    parts = str(candidate_key).split(":")
    return ":".join(parts[1:3]) if len(parts) >= 3 else str(candidate_key)


def report_blocks(explained: list, unexplained: list, show: int) -> None:
    total = len(explained) + len(unexplained)
    repeated = [entry for entry in explained + unexplained
                if int(((entry.get("rejected") or {}).get("count")) or 0) > 1]
    print("== 1. 收集：人類標為 block 的題目 ==")
    print("   共 %d 題" % total)
    # 「機器試過、他再退一次」不是新題目，是**同一題再一次**——它需要的不是同一條路再走一遍。
    # 這一行以前印不出來，因為沒有任何地方在數它（業主 2026-09-25 的方向：重複被拒要再進迴圈）。
    print("   其中機器改過、被人打回 2 次以上的：%d 題" % len(repeated))
    for entry in repeated[:show]:
        refused = entry.get("rejected") or {}
        print("     %-44s 打回 %d 次  改的是 %s"
              % (entry["candidate_key"].replace("moex:", ""), refused.get("count"),
                 "、".join(refused.get("fields") or []) or "（未記錄）"))
    print()
    print("== 2. 解釋：既有偵測器已經說過的 ==")
    print("   %d 題（管線早就報了，只是審核畫面上沒看到）" % len(explained))
    for entry in explained:
        print("    %-44s %-28s %s" % (entry["candidate_key"].replace("moex:", ""),
                                      "、".join(entry["kinds"]), entry["notes"][:30]))
    print()
    print("== 3. 強化：沒有任何偵測器解釋的（這是要建規則的地方）==")
    print("   %d 題" % len(unexplained))
    # Grouped by paper: one paper with many unexplained blocks is one cause, and finding that out
    # before writing a rule is the whole point of this step.
    by_paper = collections.Counter(entry["paper"] for entry in unexplained)
    clustered = [(paper, count) for paper, count in by_paper.most_common() if count > 1]
    if clustered:
        print("   集中在：")
        for paper, count in clustered:
            subjects = {entry["subject"] for entry in unexplained if entry["paper"] == paper}
            print("     %-14s %2d 題   %s" % (paper, count, "、".join(sorted(filter(None, subjects)))))
    print("   逐題：")
    for entry in unexplained[:show]:
        print("     %-44s %s" % (entry["candidate_key"].replace("moex:", ""),
                                 entry["notes"][:36] or "（沒有備註）"))
    if len(unexplained) > show:
        print("     …（其餘 %d 題；用 --show 調整）" % (len(unexplained) - show))


def rescan(queue_dir: str, propose_path: str | None, reviewer: str) -> int:
    """Re-run detection with the code as it stands and diff against what the queue stored.

    This is the step that answers "did the new rule change anything". It recomputes
    `disputes.of_question` per row rather than trusting the stored field, because the stored field
    is by definition the *old* code's answer - comparing those two is the measurement.
    """
    path = os.path.join(queue_dir, "candidates.jsonl")
    latest = read_latest_actions(os.path.join(queue_dir, "question_review_events.jsonl"))
    scanned = 0
    newly_firing = collections.Counter()
    reopened = []
    for question in load_candidates(path):
        scanned += 1
        key = question.get("candidate_key")
        images = {str(ref.get("option_key") or "").upper()
                  for ref in (question.get("image_refs") or [])
                  if ref.get("asset_role") == "option-image" and ref.get("option_key")}
        found = disputes.of_question(question, option_images=images)
        now = {d.get("kind") for d in found if d.get("kind")}
        was = set(detector_names(question))
        for kind in sorted(now - was):
            newly_firing[kind] += 1
        # A question a person judged, where a kind fires now that did not fire when they judged it.
        event = latest.get(key)
        delta = now - was
        if event and delta:
            reopened.append({"candidate_key": key, "action": event.get("action"),
                             "new_kinds": sorted(delta),
                             "previous_action": event.get("action"),
                             "previous_notes": event.get("notes") or "",
                             "previous_reviewed_at": event.get("created_at")})

    print("== 4. 重掃：以現在的程式碼重跑偵測，與佇列裡存著的比對 ==")
    print("   掃描 %d 題" % scanned)
    if newly_firing:
        print("   新規則開始報的類別：")
        for kind, count in newly_firing.most_common():
            print("     %-24s %5d 題" % (kind, count))
    else:
        print("   沒有新的類別開始報（佇列已是最新程式碼的結果）")
    print()
    print("   其中**已經有人判斷過**的：%d 題" % len(reopened))
    by_action = collections.Counter(item["action"] for item in reopened)
    for action, count in sorted(by_action.items()):
        print("     %-14s %d" % (action, count))

    if propose_path:
        proposal = {
            "generated_by": "repair_loop.py --rescan --propose",
            "reviewer": reviewer,
            "proposal": [
                {"candidate_key": item["candidate_key"],
                 "previous_action": item["previous_action"],
                 "previous_notes": item["previous_notes"],
                 "previous_reviewed_at": item["previous_reviewed_at"],
                 "reset_notes": "新的偵測規則（%s）在這一題上有發現，請重看" % "、".join(item["new_kinds"]),
                 "repair_kind": "detector_change",
                 "changes": [{"field": "dispute_kinds", "from": [], "to": item["new_kinds"]}]}
                for item in reopened],
        }
        with open(propose_path, "w", encoding="utf-8") as handle:
            json.dump(proposal, handle, ensure_ascii=False, indent=1)
        print()
        print("   已寫出提案：%s（%d 筆；用 append_reset_review_events.py --apply 才會寫入）"
              % (propose_path, len(reopened)))
    elif reopened:
        print()
        print("   要寫成 reset_review 事件請加 --propose <file>（本腳本不會自己寫）")
    return 0


def main() -> int:
    args = parse_args()
    queue_dir = review_ui_dir(args.queue)
    if not os.path.exists(os.path.join(queue_dir, "candidates.jsonl")):
        print("找不到 candidates.jsonl：%s" % queue_dir, file=sys.stderr)
        return 2
    print("佇列：%s" % queue_dir)
    print()

    if not args.rescan or args.propose:
        blocked, rows = collect_blocks(queue_dir)
        rejections = rejections_by_key(os.path.join(queue_dir, "question_review_events.jsonl"))
        explained, unexplained = explain(blocked, rows, rejections)
        report_blocks(explained, unexplained, args.show)
        print()

    if args.rescan:
        return rescan(queue_dir, args.propose, args.reviewer)

    if not args.rescan:
        print("下一步：")
        if unexplained:
            print("  * 上面那 %d 題沒被解釋的，先看它們是不是同一個原因（同一卷很多題＝一個原因）。"
                  % len(unexplained))
            print("    要建規則的地方在 `qbr/src/qbr/disputes.py`；建好之後跑 --rescan 看它報了什麼。")
        print("  * `python scripts/repair_loop.py --queue %s --rescan --propose /tmp/reset.json`"
              % args.queue)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
