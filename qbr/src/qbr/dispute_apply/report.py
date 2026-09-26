"""The run report: the plan, the page-read census, the ask line, the withdrawals and the refusals.

Every line here printed from `main()` before `scripts/apply_dispute_repairs.py` was split; the strings
are byte-for-byte the ones `main` printed, in the same order, and `cli.main` only decides what to
render. The file is a composition root now: the implementation lives in `qbr/src/qbr/dispute_apply/`.

`apply_dispute_repairs` re-exports every name here; that indirection is deliberate and is why the
test files that load the script by path keep working unchanged. No behaviour was changed.
"""

from __future__ import annotations

from qbr.dispute_apply.withdrawals import (
    WITHDRAW_PRIORITY,
    _withdrawal_is_new,
    withdrawal_reason,
)


def print_plan(queue, events_path, before: str, planned: list[dict]) -> None:
    """The header and the plan table: one line per repair this run would write."""
    print("queue        : %s" % queue)
    print("events file  : %s" % events_path)
    print("sha256 before: %s" % before)
    print("to repair    : %d" % len(planned))
    for item in planned:
        edits = "；".join(
            ("%s 整欄 %r…→%r…" % (s["field"], s["before"][:12], s["after"][:12]))
            if s.get("replace") else
            ("%s[%d] %r→%r" % (s["field"], s["position"], s["before"], s["after"]))
            for s in item["subs"])
        print("  %-44s %-14s %-12s %s" % (
            item["candidate_key"].replace("moex:", ""), item["previous_action"] or "-",
            item["applied"] + ("/norm" if item["normalisation"] else ""), edits[:96]))


def print_page_read_census(held_back: int, pair_questions: dict, systematic: dict,
                           already_satisfied: int, atomicity_dropped: int,
                           exhausted: int = 0) -> None:
    """The page-read census: the readings held back, the CJK→CJK pair census, and the two counters."""
    print("held back    : %d page readings on questions nobody rejected "
          "(the whole-field path needs a standing `block`/`needs_review`)" % held_back)
    print("pair census  : %d CJK→CJK pairs changed by this run's whole-field readings"
          % len(pair_questions))
    for pair, keys in sorted(pair_questions.items(), key=lambda item: (-len(item[1]), item[0])):
        flag = "  ←系統性" if pair in systematic else ""
        print("  %s→%s  %d 題%s" % (pair[0], pair[1], len(keys), flag))
    if already_satisfied:
        print("已符合       : %d fields 已經是紙本那個字，沒有東西要寫（列上的 disputes 沒重測）"
              % already_satisfied)
    if atomicity_dropped:
        print("atomicity    : %d questions dropped whole (一整題一起不動，不然同一題會出現兩種寫法)"
              % atomicity_dropped)
    if exhausted:
        print("已退滿       : %d 題的機器修復已被退滿上限，不再修（人接受或放回會重新開門）"
              % exhausted)


def print_asks(wanted: int, *, dry_run: bool, ask_limit: int, written=None) -> None:
    """The ask line: how many readings were refused with nothing to write, and what was asked.

    `written` is `ask_for_refusals`' `(asked, already, waiting)`; it is `None` whenever nothing was
    written, which is what a dry run and `--ask-limit 0` are.
    """
    if dry_run:
        print("反問人       : %d 題判讀被閘門擋住、沒有東西可寫"
              "（乾跑沒有寫反問，--apply 才會問）" % wanted)
    elif ask_limit <= 0:
        print("反問人       : %d 題判讀被閘門擋住、沒有東西可寫"
              "（--ask-limit 0：這一輪不問人）" % wanted)
    else:
        asked, waiting, already = written
        print("反問人       : 新問 %d 題（判讀被閘門擋住、沒有東西可寫）；"
              "已經問過 %d 題；還有 %d 題等下一輪" % (asked, already, waiting))


def print_withdrawals(withdrawals: dict, withdrawn_before: dict) -> None:
    """The withdrawal table: the trigger counts, then one line per question and field."""
    if withdrawals:
        counts = {trigger: [0, 0] for trigger in WITHDRAW_PRIORITY}
        for entry in withdrawals.values():
            for trigger, fields in entry["triggers"].items():
                counts.setdefault(trigger, [0, 0])
                counts[trigger][0] += 1
                counts[trigger][1] += len(fields)
        summary = "；".join("%s %d題/%d欄" % (trigger, numbers[0], numbers[1])
                           for trigger, numbers in counts.items() if numbers[0])
        print("withdraw     : %d questions / %d fields  (%s)"
              % (len(withdrawals), sum(len(e["fields"]) for e in withdrawals.values()), summary))
        for key, entry in sorted(withdrawals.items()):
            for field in sorted(entry["fields"]):
                triggers = "、".join(sorted(t for t, fields in entry["triggers"].items()
                                            if field in fields))
                why = entry["whys"].get(triggers.split("、")[0]) or withdrawal_reason(entry)
                note = entry["notes"].get("bounce-back")
                print("  %s  %s  [%s]  %s%s" % (key, field, triggers, why,
                                                "（人的話：%s）" % note if note else ""))
        stale = [key for key, entry in withdrawals.items()
                 if not _withdrawal_is_new(withdrawn_before, key, entry["fields"])]
        if stale:
            print("already      : %d questions already carry this withdrawal "
                  "(不再寫第二筆：%s)" % (len(stale), "、".join(sorted(stale))))


def print_refusals(manual_refusals: list, refused: list) -> None:
    """The `--withdraw` refusals and the REFUSED list: what was measured and deliberately not written."""
    for line in manual_refusals:
        print("REFUSED --withdraw: %s" % line)
    if refused:
        print()
        print("REFUSED (%d entries) - not touched:" % len(refused))
        for item in refused:
            print("  %-44s %s" % (item["candidate_key"].replace("moex:", ""),
                                   "；".join(item["why"])[:140]))
