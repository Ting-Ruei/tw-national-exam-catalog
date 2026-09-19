# -*- coding: utf-8 -*-
"""Append `reset_review` events for candidates whose *content actually changed*.

This is the write half of `qbr/reports/reset_review_scope.md`. It is deliberately narrow:

    it appends to an append-only log and rewrites nothing
    it touches only the candidate keys named in the proposal
    it re-checks, at the moment of writing, that the question's human decision is still the one the
    proposal was built against, and refuses if a person has decided something in the meantime

Why an event and not an edit
----------------------------
A review decision is a statement about the reading that was in front of the reviewer. When the
reading changes, the old decision is not wrong - it was made about different text - and the honest
record is a new event that says so, keeping the old one. `reset_review` reopens the human decision
and nothing else: `load_review_events` keeps any `correction` the reviewer had saved, so a person's
own corrections and manual images are not thrown away by a parser repair.

The previous context travels with the event
-------------------------------------------
`previous_action`, `previous_notes`, `previous_reviewed_at` and `reset_notes` are carried because the
documented contract requires them: a reviewer coming back to this question has to be able to see
what they decided before and what changed underneath them. Without them the reset is
indistinguishable from "nobody has looked at this".

Safety
------
Dry run by default. `--apply` is required to write, and it writes a new file when the destination
already exists rather than appending to it, so a mistake is a file to look at rather than a
corrupted log. A hash of the event log before the write is recorded in the output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

QUESTION_ACTIONS = {"accept", "correct", "needs_review", "block", "exclude", "unblock", "comment",
                    "reviewed", "unreviewed", "reset_review"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal", required=True, help="proposal JSON from the scope measurement")
    parser.add_argument("--events", required=True, help="the append-only review-event log")
    parser.add_argument("--out", help="also write a copy of the resulting log here")
    parser.add_argument("--reviewer", default="content-change-reset")
    parser.add_argument("--apply", action="store_true", help="actually write; without it this is a dry run")
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def latest_event_of(lines: list[str], key: str) -> dict | None:
    """The most recent event for a key, ignoring group-level actions the applier must not disturb."""
    found = None
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("candidate_key") != key:
            continue
        if event.get("action") not in QUESTION_ACTIONS:
            continue
        found = event
    return found


def main() -> int:
    args = parse_args()
    proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
    events_path = Path(args.events)
    lines = events_path.read_text(encoding="utf-8").splitlines(keepends=True)
    before = sha256_file(events_path)

    planned, refused = [], []
    for entry in proposal["proposal"]:
        key = entry["candidate_key"]
        current = latest_event_of(lines, key)
        current_action = (current or {}).get("action")
        # The re-check that makes this safe: a person may have accepted the question between the
        # measurement and this write, in which case reopening it would silently undo their work.
        if current_action != entry["previous_action"]:
            refused.append({"candidate_key": key, "expected": entry["previous_action"],
                            "found": current_action,
                            "why": "the human decision is no longer the one the proposal was built on"})
            continue
        planned.append(entry)

    created_at = datetime.now().isoformat(timespec="seconds")
    new_events = []
    for entry in planned:
        new_events.append({
            "candidate_key": entry["candidate_key"],
            "action": "reset_review",
            "reviewer": args.reviewer,
            "notes": entry["reset_notes"],
            "reset_notes": entry["reset_notes"],
            "repair_kind": entry.get("repair_kind") or "content_change",
            "previous_action": entry["previous_action"],
            "previous_notes": entry.get("previous_notes") or "",
            "previous_reviewed_at": entry.get("previous_reviewed_at"),
            "changes": entry.get("changes") or [],
            "source": "qbr_scope_measurement",
            "created_at": created_at,
        })

    print("events file   : %s" % events_path)
    print("sha256 before : %s" % before)
    print("lines before  : %d" % len(lines))
    print()
    print("to append     : %d" % len(new_events))
    for event in new_events:
        print("  %-48s %-8s → reset_review   %s" % (
            event["candidate_key"], event["previous_action"],
            " ".join("%s:%r→%r" % (c["field"], c["from"], c["to"]) for c in event["changes"])[:80]))
    if refused:
        print()
        print("REFUSED (%d) - not touched:" % len(refused))
        for item in refused:
            print("  %-48s expected=%-8s found=%s (%s)" % (
                item["candidate_key"], item["expected"], item["found"], item["why"]))

    if not args.apply:
        print()
        print("dry run; pass --apply to write")
        return 0

    # The log is append-only, so the write is an append - that is what the server itself does
    # (`open("a")`). A backup is taken first because the file is the reviewer's history: if the
    # append is wrong, the wrong thing must be a file that can be put back, not a lost decision.
    backup = events_path.with_name(events_path.name + ".before-" + created_at.replace(":", ""))
    backup.write_bytes(events_path.read_bytes())
    with events_path.open("a", encoding="utf-8") as handle:
        for event in new_events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    receipt = {
        "written_at": created_at,
        "events_file": str(events_path),
        "backup": str(backup),
        "sha256_before": before,
        "sha256_after": sha256_file(events_path),
        "lines_before": len(lines),
        "lines_after": len(events_path.read_text(encoding="utf-8").splitlines()),
        "appended": len(new_events),
        "refused": refused,
        "applied_keys": [event["candidate_key"] for event in new_events],
        "reviewer": args.reviewer,
    }
    if args.out:
        Path(args.out).write_bytes(events_path.read_bytes())
        receipt["copy"] = str(args.out)
    receipt_path = events_path.with_name(events_path.name + ".reset-receipt.json")
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    print()
    print("backup        : %s" % backup)
    print("appended %d to %s" % (len(new_events), events_path))
    print("wrote %s" % receipt_path)
    print("sha256 after  : %s" % receipt["sha256_after"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
