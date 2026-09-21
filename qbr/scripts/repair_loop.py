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

from qbr import disputes  # noqa: E402

QUESTION_ACTIONS = {"accept", "correct", "needs_review", "block", "exclude", "unblock",
                    "reset_review"}

#: The action a human uses to say "this reading is wrong". `needs_review` is deliberately *not*
#: here: it says "I could not decide", which is a different input to the loop - a detector cannot
#: be built from "I am unsure", only from "this is wrong".
BLOCKING_ACTIONS = {"block"}


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


def read_latest_actions(path: str) -> dict:
    """The most recent question-level action per candidate key.

    Only question-level actions count: a group-level action is about a shared stem and resetting it
    would reopen a batch the reviewer did not judge as a question.
    """
    latest = {}
    if not os.path.exists(path):
        return latest
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                print("  warning: line %d of %s is not JSON; skipped" % (number, path),
                      file=sys.stderr)
                continue
            if event.get("action") not in QUESTION_ACTIONS:
                continue
            key = event.get("candidate_key")
            if key:
                latest[key] = event
    return latest


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


def explain(blocked: dict, rows: dict) -> tuple:
    """Split the blocks into "a detector already says this" and "nothing does"."""
    explained, unexplained = [], []
    for key, event in sorted(blocked.items()):
        question = rows.get(key)
        kinds = detector_names(question) if question else []
        entry = {"candidate_key": key, "notes": (event.get("notes") or "").strip(),
                 "kinds": kinds,
                 "paper": paper_of(key),
                 "subject": ((question or {}).get("metadata") or {}).get("normalized_subject_name")}
        (explained if kinds else unexplained).append(entry)
    return explained, unexplained


def paper_of(candidate_key: str) -> str:
    """The paper prefix of a candidate key: `moex:115090:308:0504:1:question:q041` -> `115090:308`."""
    parts = str(candidate_key).split(":")
    return ":".join(parts[1:3]) if len(parts) >= 3 else str(candidate_key)


def report_blocks(explained: list, unexplained: list, show: int) -> None:
    total = len(explained) + len(unexplained)
    print("== 1. 收集：人類標為 block 的題目 ==")
    print("   共 %d 題" % total)
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
        explained, unexplained = explain(blocked, rows)
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
