"""The command line: `parse_args` and `main`.

`--help` 的說明是 `parse_args` 裡的 `__doc__`，也就是**composition root**（`scripts/
apply_dispute_repairs.py`）的 docstring：那支腳本載入本模組後把它指定過來（見那裡的再匯出段）。

Extracted verbatim from `scripts/apply_dispute_repairs.py` so one file is no longer 2,321 lines.
`apply_dispute_repairs` re-exports every name here; that indirection is deliberate and is why the
test files that load the script by path keep working unchanged. No behaviour was changed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from qbr import ai_findings, discuss

from qbr.dispute_apply.asking import (
    ASK_LIMIT_DEFAULT,
    _reading_refusal,
    ask_for_refusals,
    questions_to_ask,
)
from qbr.dispute_apply.page_read import (
    SYSTEMATIC_PAIR_MIN,
    page_read_fields,
    page_read_substitutions,
    second_read_verdict,
    systematic_pairs,
)
from qbr.dispute_apply.report import (
    print_asks,
    print_page_read_census,
    print_plan,
    print_refusals,
    print_withdrawals,
)
from qbr.dispute_apply.text import (
    applied_signature,
    build_correction,
    is_normalisation,
    last_repair_signature,
    latest_events,
    load_candidates,
    merge_substitutions,
    queue_relative,
    repair_signature,
    satisfied_sub,
    sha256_file,
    substitutions_for,
    verify,
)
from qbr.dispute_apply.withdrawals import (
    APPLICABLE,
    FIELD_WITHDRAW_TRIGGERS,
    _withdrawal_is_new,
    add_withdrawal,
    build_repair_event,
    build_withdrawal_event,
    MAX_ATTEMPTS,
    closed_questions,
    existing_withdrawals,
    rejection_counts,
    standing_rejections,
    withdrawal_reason,
    withdrawal_state,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queue", required=True,
                        help="queue root; candidates and the review log live under its review-ui/")
    parser.add_argument("--events", help="review-event log; default <queue>/review-ui/"
                                         "question_review_events.jsonl")
    parser.add_argument("--out", help="also write the resulting log here")
    parser.add_argument("--only", nargs="*", default=None,
                        help="question numbers (q004 or 4); default is every applicable question")
    parser.add_argument("--kind", nargs="*", default=None,
                        help="restrict to these dispute kinds (default: %s)" % (APPLICABLE,))
    parser.add_argument("--page-read", action="store_true",
                        help="also apply the repairs confirmed by a **page reading** against the "
                             "question's own disputes and changes (advisory findings, "
                             "population=dispute). Two paths: a character-level repair anchored on a "
                             "detector's own flagged positions (any question, unaffected by every "
                             "clause below), and a whole-field replacement anchored on a standing "
                             "human rejection plus the alignment and length measurements, refused for "
                             "any field whose stored text carries HTML markup (`紙本判讀會拆掉標記`), "
                             "whose reading brings in the unreadable mark (`▢`), whose reading writes a "
                             "Unicode sub/superscript character where the stored text has a plain one, "
                             "or whose introduced `<sub>`/`<sup>` markup changes any other character "
                             "(`判讀動到了上下標以外的字元`), refused **as a whole question** when any "
                             "one of its fields is refused, and unless the second engine's verdict is "
                             "`TRUST` (see PAGE_READ_ALIGN_MIN)")
    parser.add_argument("--withdraw", action="append", default=None, metavar="KEY:FIELD",
                        help="append a withdrawal for one field of one question, for the case where "
                             "the owner bounces a machine whole-field edit by hand (repeatable). "
                             "Refused unless that question's last `qbr_dispute_apply` event is an "
                             "`applied == \"field\"` repair of that field")
    parser.add_argument("--limit", type=int, default=0, help="repair at most this many (0 = all)")
    parser.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS,
                        help="stop repairing a question once this many of its machine repairs have "
                             "been rejected and nobody has accepted since (default %d; 0 = no cap). "
                             "業主 2026-09-25：循環三次之後才送入「AI無法判斷」，而人一按接受或放回"
                             "（accept／unblock）次數歸零、門就再開。" % MAX_ATTEMPTS)
    parser.add_argument("--ask-limit", type=int, default=ASK_LIMIT_DEFAULT,
                        help="ask a person about at most this many readings this run refused "
                             "(0 = ask nobody); only used with --apply")
    parser.add_argument("--reviewer", default="repair_dispute_apply",
                        help="who is recorded as making the repair. Must carry the server's "
                             "`repair_` reviewer prefix (REPAIR_REVIEWER_PREFIXES) or the projection "
                             "would count a machine repair as a human decision")
    parser.add_argument("--apply", action="store_true",
                        help="actually write; without it this is a dry run")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    queue_dir = os.path.join(args.queue, "review-ui")
    candidates_path = Path(os.path.join(queue_dir, "candidates.jsonl"))
    events_path = Path(args.events or os.path.join(queue_dir, "question_review_events.jsonl"))
    if not candidates_path.is_file():
        print("找不到 candidates.jsonl：%s" % candidates_path, file=sys.stderr)
        return 2
    if not events_path.is_file():
        print("找不到 review event log：%s" % events_path, file=sys.stderr)
        return 2

    wanted_kinds = set(args.kind) if args.kind else set(APPLICABLE)
    wanted_numbers = None
    if args.only:
        wanted_numbers = {str(item).lower().lstrip("q").lstrip("0") or "0" for item in args.only}

    # The page readings, when asked for. Read from the same finding stream the reviewer's screen
    # shows, so the repair is applied to exactly the diff a person can open and check.
    page_findings = {}
    if args.page_read:
        store = os.path.join(queue_dir, ai_findings.STREAM)
        for key, record in ai_findings.latest_by_question(store).items():
            if record.get("population") == "dispute" and record.get("changes"):
                page_findings[key] = record

    latest = latest_events(events_path)
    # Who a person has rejected and nobody has accepted since. This is the fence on the whole-field
    # page read (see `standing_rejections`), and it is read before the loop because it is about the
    # *question*, not about the reading.
    rejected = standing_rejections(events_path) if args.page_read else set()
    # 已退滿的題（業主 2026-09-25 的循環上限）。同一個定義也在 `repair_loop.fold_review_events` 的
    # `rejections`（掃描端用那一份），兩者由 `test_the_cap_counts_the_same_events_the_loop_counts`
    # 釘在一起。讀一次，整輪用它。
    attempts = rejection_counts(events_path) if args.max_attempts else {}
    exhausted = 0
    held_back = 0
    # The tool's own past repairs, found by scanning for `qbr_dispute_apply` rather than reading the
    # latest event. A human decision appended after a repair (their clock, or just their turn) must
    # not erase the memory that the repair already happened - otherwise the next run duplicates it
    # and re-resets a question a person just accepted. See `last_repair_signature`.
    prior_repairs = last_repair_signature(events_path)
    # The log read as a sequence, for the withdrawal triggers: who spoke last on the question, and
    # which machine whole-field repair is the one that would have to be undone.
    state = withdrawal_state(events_path)
    # The questions a person has closed to the machine: their newest word is a bounce of a machine
    # whole-field repair. One comparison, two consequences - the repair is withdrawn, and no new one
    # is planned (see `closed_questions`).
    closed = closed_questions(state)
    withdrawn_before = existing_withdrawals(events_path)
    before = sha256_file(events_path)
    created_at = datetime.now().isoformat(timespec="seconds")
    rows = load_candidates(candidates_path)

    def wanted(question: dict) -> bool:
        if wanted_numbers is None:
            return True
        number = str(question.get("question_number")).lstrip("0") or "0"
        return number in wanted_numbers

    # ---- the systematic-pair census, before any planning is finalised ---------------------------
    #
    # The unit is a *character pair* the whole-field replacements of this run change: 癇→癲 on four
    # questions is not four model slips, it is one misreading repeated, and the same character is
    # wrong on every one of them. The census runs over the fields whose two measurements pass
    # (`page_read_fields`) and not over the fields this run ends up applying, because a reading that
    # misreads one character systematically is evidence about the *reading* - it must be visible as
    # such even when a class clause refuses the field anyway (the four 癇→癲 readings tonight also
    # write `GABAₐ`, which clause 1 refuses on its own).
    pair_questions: dict[tuple, set] = {}
    if args.page_read:
        for question in rows:
            if not wanted(question):
                continue
            key = question.get("candidate_key")
            record = page_findings.get(key)
            if not record or key not in rejected:
                continue
            fields, _shape = page_read_fields(question, record.get("changes") or [])
            for candidate in fields:
                for pair in systematic_pairs(candidate["stored"], candidate["page"]):
                    pair_questions.setdefault(pair, set()).add(key)
    systematic = {pair: len(keys) for pair, keys in pair_questions.items()
                  if len(keys) >= SYSTEMATIC_PAIR_MIN}

    planned, refused = [], []
    #: The readings the fences would not let through, with their reasons - the population the person
    #: is asked about below (`questions_to_ask`). A question the reading *did* move is not here.
    ask_reasons: list[dict] = []
    # 逐欄的拒絕紀錄（只有評估過判讀的題目才有）：撤銷的第二、第三個觸發從這裡來。
    refused_fields = {}
    atomicity_dropped = 0
    already_satisfied = 0
    for question in rows:
        if not wanted(question):
            continue
        key = question.get("candidate_key")
        if args.max_attempts and attempts.get(key, 0) >= args.max_attempts:
            # 迴圈上限：這一題的機器修復已經被人退滿 `--max-attempts` 次，而沒有人接受過。機器不再
            # 寫第 N+1 次（也不再為它花一次判讀），這一題在 UI 上是「AI無法判斷」。人接受或放回會把
            # 次數歸零，門自己再開。
            exhausted += 1
            continue
        subs = [s for s in substitutions_for(question) if s["rule"] in wanted_kinds]
        crop = None
        reading_why = []
        reading_records = []
        if args.page_read:
            record = page_findings.get(key)
            if record:
                crop = queue_relative(record.get("crop"), args.queue)
                if key in rejected:
                    subs = subs + page_read_substitutions(
                        question, record.get("changes") or [], human_rejected=True,
                        error=record.get("error"), refused=reading_why,
                        verdict=second_read_verdict(record), pairs=systematic,
                        refused_fields=reading_records)
                else:
                    # Path 2 (the detector-anchored one) runs for every question, exactly as it did
                    # before; path 3 does not even start. The count is reported below rather than per
                    # question: that a reading was held back for lack of a human rejection is a fact
                    # about the question, not about the reading.
                    held_back += 1
                    subs = subs + page_read_substitutions(
                        question, record.get("changes") or [], human_rejected=False)
        if reading_records:
            refused_fields.setdefault(key, []).extend(reading_records)
            if any(record["atomicity"] for record in reading_records):
                atomicity_dropped += 1
        satisfied = [sub for sub in subs if satisfied_sub(question, sub)]
        if satisfied:
            # 這一格已經是紙本那個字（通常是過去某一輪已經改過、而這一列的 `disputes` 沒有重測）。
            # 不是拒絕，是沒有東西要寫——把它從這一輪的計畫裡拿掉，同一題**其他**真的還沒改的欄位
            # 才走得下去（整題一起不動的規則會把整個問題一起擋掉）。
            already_satisfied += len(satisfied)
            subs = [sub for sub in subs if not satisfied_sub(question, sub)]
        subs = merge_substitutions(subs) if subs else []
        if subs is None:
            refused.append({"candidate_key": key,
                            "why": ["同一個位置有兩筆不同的量測結果（dispute 與紙本判讀不一致）"]})
            continue
        if subs and len({bool(s.get("replace")) for s in subs}) > 1:
            # One event carries one form (`applied`), because the producer that writes the extraction
            # file has to know which one it is reading. Rather than write an ambiguous event, the
            # page-read replacement is dropped and the dispute's own repairs are applied - which is
            # exactly what this tool did before the whole-field path existed, so nothing is lost.
            # (measured 2026-09-24: 0 of the mirror's 48 gate-refused questions are in this shape)
            dropped = [s["field"] for s in subs if s.get("replace")]
            subs = [s for s in subs if not s.get("replace")]
            reading_why.append("整欄替換與 dispute 的字元修復同時存在（%s）：事件只能有一種形式，"
                               "只套用 dispute 的" % "、".join(dropped))
        if not subs:
            if reading_why:
                refused.append({"candidate_key": key, "why": list(reading_why)})
                ask_reasons.append(_reading_refusal(key, record, reading_why, reading_records))
            continue
        complaints = verify(question, subs)
        if complaints:
            # A stale reading is a refusal of the reading, not of the dispute, so the reader gets the
            # line that says so when there is one.
            refused.append({"candidate_key": key, "why": complaints + reading_why})
            if reading_why:
                ask_reasons.append(_reading_refusal(key, record, reading_why, reading_records))
            continue
        previous = latest.get(key) or {}
        previous_action = previous.get("action")
        # Already repaired, with exactly these edits. The candidate text is not rewritten (by
        # design), so the same substitution is found again every run; without this the tool would
        # duplicate its own past work. A different edit set - the text moved since - is not skipped.
        if applied_signature(prior_repairs.get(key)) == repair_signature(subs):
            if reading_why:
                refused.append({"candidate_key": key, "why": list(reading_why)})
                ask_reasons.append(_reading_refusal(key, record, reading_why, reading_records))
            continue
        correction = build_correction(question, subs)
        planned.append({"candidate_key": key, "subs": subs,
                        "correction": correction, "previous_action": previous_action,
                        "previous": previous, "crop": crop,
                        "applied": "field" if any(s.get("replace") for s in subs) else "substitution",
                        "normalisation": is_normalisation(subs)})
        if reading_why:
            # Repaired, but part of what the reading asked for was refused; both halves belong in the
            # report or a person cannot tell why a field did not move. It is deliberately *not* an ask:
            # something did move here, so this is a question with a leftover field rather than a
            # question sitting still, and the census line above is where a person reads it.
            refused.append({"candidate_key": key, "why": list(reading_why)})
        if args.limit and len(planned) >= args.limit:
            break

    # ---- what this run would withdraw -----------------------------------------------------------
    #
    # Three triggers, one event shape. (a) is the primary one and needs nothing but the log: the owner
    # blocked every row whose sub/superscripts came out wrong, so the bounce itself is the signal.
    # (b) and (c) are refusals this run measured - a systematic character pair, or a clause that the
    # applied field does not survive - and both can only withdraw a field the machine's last
    # whole-field repair actually wrote (that is the "same field signature": there must be something
    # to put back).
    withdrawals: dict[str, dict] = {}
    for key, item in closed.items():
        add_withdrawal(withdrawals, key, "bounce-back", item["fields"],
                       "人把機器的改動打回了（%s %s）：整欄改寫還原" % (
                           item["human"].get("action"), item["human"].get("created_at") or "?"),
                       note=str(item["human"].get("notes") or ""))
    for key, records in refused_fields.items():
        machine = (state.get(key) or {}).get("machine")
        if not machine:
            continue
        applied = set(machine["fields"])
        for record in records:
            for trigger in record["triggers"]:
                if trigger not in FIELD_WITHDRAW_TRIGGERS or record["field"] not in applied:
                    continue
                add_withdrawal(withdrawals, key, trigger, [record["field"]], record["why"])
    # A question being reverted is not also repaired in the same run: writing both would leave the
    # producer two orders to apply to the same row.
    if withdrawals:
        kept = []
        for item in planned:
            entry = withdrawals.get(item["candidate_key"])
            if entry is None:
                kept.append(item)
                continue
            # 只有**這一輪才第一次寫下去**的那幾個欄位，才需要把同一輪的修復拿掉（否則生產者會拿到
            # 兩個互相矛盾的指令）。撤銷已經在日誌裡的那一輪之後，這一題對機器是**開著**的：判讀重讀
            # 紙本，新的修復照排——主人的原話是「機器改錯就給我重改」，撤銷只是把錯的字還原，還原後
            # 的 `parser_original` 本身還是錯的，停在上面等於這一題永遠沒人修。擋住回彈的不是永久拒絕，
            # 是簽章：同一份改動不會寫第二次（`last_repair_signature`），所以「再想一次又是同一份錯」
            # 會被擋掉，而「想出了不同的、只動上下標的修復」會寫上去。
            if not _withdrawal_is_new(withdrawn_before, item["candidate_key"], entry["fields"]):
                kept.append(item)
                continue
            refused.append({"candidate_key": item["candidate_key"],
                            "why": ["這一輪要撤銷這一題的機器改動（%s）：同一輪不另外寫修復"
                                    % "、".join(sorted(entry["triggers"]))]})
        planned = kept

    # `--withdraw KEY:FIELD`: for the case where the owner bounces a machine edit by hand and the
    # log alone cannot say so (no `block` event landed, or the field is obvious to him and not to a
    # scan). It is refused unless there is exactly the thing to put back: that question's last
    # machine repair is an `applied == "field"` repair **of that field**.
    manual_refusals = []
    for spec in args.withdraw or []:
        text = str(spec)
        key, separator, field = text.rpartition(":")
        if not separator or not key or not field:
            manual_refusals.append("%s：格式要是 KEY:FIELD（整串 candidate_key 加欄位名，例如 "
                                   "moex:113090:305:11:1:question:q053:stem）" % text)
            continue
        machine = (state.get(key) or {}).get("machine")
        if not machine:
            manual_refusals.append("%s：這一題的最後一筆機器修復不是整欄替換"
                                   "（沒有 `applied == \"field\"` 的事件）：沒有東西可以還原" % text)
            continue
        if field not in machine["fields"]:
            manual_refusals.append("%s：那一欄不在最後一筆整欄修復寫過的欄位裡"
                                   "（它寫的是 %s）" % (text, "、".join(machine["fields"]) or "（沒有欄位）"))
            continue
        add_withdrawal(withdrawals, key, "manual", [field],
                       "人退回這筆機器整欄替換（%s）：依契約還原" % text)

    print_plan(args.queue, events_path, before, planned)
    if args.page_read:
        print_page_read_census(held_back, pair_questions, systematic,
                               already_satisfied, atomicity_dropped, exhausted)
        # ---- what a person should look at, and was never told about -----------------------------
        #
        # Measured 2026-09-25 on the station: of the 197 blocked questions whose reading the fences
        # refused, **27** had an open ask. The loop reads the paper, refuses on a documented gate, and
        # says nothing anywhere the person looks - which is why the owner's 阻擋 list does not move
        # (「這些卡在 block 裡面很多題目…你自己明明就有寫應該怎麼改，但是他們還是卡在這邊」). This is
        # the other end of the census above: the census is the machine's record, the ask is the
        # person's copy of it, with the two readings side by side so one glance is enough to decide.
        wanted_asks = questions_to_ask(ask_reasons,
                                       set(closed) | {item["candidate_key"] for item in planned})
        if wanted_asks:
            asks_path = os.path.join(queue_dir, discuss.REPAIR_QUESTIONS_STREAM)
            written = None
            if args.apply and args.ask_limit > 0:
                written = ask_for_refusals(
                    asks_path, {question.get("candidate_key"): question for question in rows},
                    wanted_asks, args.ask_limit)
            print_asks(len(wanted_asks), dry_run=not args.apply,
                       ask_limit=args.ask_limit, written=written)
    print_withdrawals(withdrawals, withdrawn_before)
    print_refusals(manual_refusals, refused)

    if not args.apply:
        print()
        print("dry run；pass --apply to write")
        return 0

    # The re-check that makes a write safe: a person may have decided a question between the
    # measurement above and this line, and reopening it would silently undo their work. This is the
    # same guard `append_reset_review_events.py` uses, and it is re-read from the file rather than
    # trusted from the earlier snapshot.
    now_latest = latest_events(events_path)
    confirmed = []
    for item in planned:
        if (now_latest.get(item["candidate_key"]) or {}).get("action") != item["previous_action"]:
            refused.append({"candidate_key": item["candidate_key"],
                            "why": ["寫入前重驗失敗：判定已變（%s → %s）" % (
                                item["previous_action"],
                                (now_latest.get(item["candidate_key"]) or {}).get("action"))]})
            continue
        confirmed.append(item)

    events = [build_repair_event(item["candidate_key"], item["subs"], item["correction"],
                                 item["previous"], args.reviewer, created_at,
                                 crop=item["crop"], applied=item["applied"],
                                 normalisation=item["normalisation"])
              for item in confirmed]
    # Withdrawals last, and that order is the contract's: the producer reads the *latest*
    # `repair_dispute_apply` event for a key, so a withdrawal has to be the last thing written about
    # that question to be the one it acts on.
    withdrawal_events = [
        build_withdrawal_event(key, sorted(entry["fields"]), withdrawal_reason(entry),
                               args.reviewer, created_at)
        for key, entry in sorted(withdrawals.items())
        if _withdrawal_is_new(withdrawn_before, key, entry["fields"])]

    # A backup first, because this file is the reviewer's history: a wrong append must be a file to
    # put back, not a lost decision.
    backup = events_path.with_name(events_path.name + ".before-" + created_at.replace(":", ""))
    backup.write_bytes(events_path.read_bytes())
    with events_path.open("a", encoding="utf-8") as handle:
        for event in events + withdrawal_events:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    after = sha256_file(events_path)
    print()
    print("backup       : %s" % backup)
    print("appended     : %d reset_review events (carrying the correction)" % len(events))
    print("withdrawn    : %d withdrawal events (applied=withdrawn，還原成 parser_original)"
          % len(withdrawal_events))
    print("sha256 after : %s" % after)
    if args.out:
        with Path(args.out).open("w", encoding="utf-8") as handle:
            handle.write(events_path.read_text(encoding="utf-8"))
        print("copy written : %s" % args.out)
    print()
    print("全部是 append-only 事件；原始抽取文字仍在 candidates.jsonl，由伺服器的 parser_original 保存。")
    print("結果桶位是「修復後待複核」：機器不宣告任何人的判定。")
    return 0
