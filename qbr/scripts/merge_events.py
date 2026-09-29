# -*- coding: utf-8 -*-
"""Merge one review-event log into another, appending only what is missing.

This is the library half of `scripts/push_reviews_to_station.sh`. It exists as a function so the
merge can be tested without a network, and so the shell script and the queue builder compare events
by the *same* rule (`review_queue.record_identity`) instead of two rules that drift.

The direction matters and is enforced by shape, not by convention: the output is a list of lines to
**append** to the target. There is no code path here that returns a whole file to write over the
target, because overwriting the home of the review record is the one outcome that would destroy
work (`qbr/reports/two_review_stores.md`).
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from qbr import review_queue  # noqa: E402


def _lines(path):
    with open(path, encoding="utf-8") as handle:
        return [line for line in handle if line.strip()]


def _identity(line):
    """The record's identity, or the line itself when it will not parse.

    An unparseable line has no content to compare, so it is compared literally and carried as it
    is. Refusing to push it would silently drop a record, and not dropping records is this
    function's entire job.
    """
    try:
        return review_queue.record_identity(json.loads(line))
    except ValueError:
        return line.rstrip("\n")


def missing_events(target_path, source_path):
    """Lines present in `source_path` that are not already in `target_path`, newline-terminated.

    Duplicates within the source are kept as written: if a reviewer recorded the same decision twice
    on purpose, collapsing them here would rewrite their history. The queue builder deduplicates on
    the way *forward* (into a new queue); this function copies what a person wrote.
    """
    known = {_identity(line) for line in _lines(target_path)}
    missing = []
    for line in _lines(source_path):
        if _identity(line) in known:
            continue
        missing.append(line if line.endswith("\n") else line + "\n")
    return missing


def unmatched(target_path, source_path):
    """Lines in `target_path` that `source_path` cannot account for.

    The companion question to `missing_events`, and the two are not the same shape. `missing_events`
    asks *what do I have that you lack* - which is what gets appended. This asks **do you have
    anything I cannot account for**, which is the only thing that should stop a push.

    Why the count comparison this replaces was wrong, measured: the same guard served two streams
    whose producers are different. For the human log the station writes and the laptop copies, so a
    station with fewer lines really is suspicious. For `question_ai_findings.jsonl` the **laptop**
    writes and the station only ever holds what was pushed, so the laptop growing from 71 to 5,295
    lines is the normal state of an unpushed store - and the guard refused exactly the push that
    existed to carry it home. A count cannot tell "I am behind" from "I lost data"; membership can,
    and membership is what actually matters: appending is safe precisely when every event the
    station holds is one the laptop also holds.

    What this does **not** catch: the station losing a tail the laptop never saw. There is no
    external oracle for that, and neither could the count version - if the laptop had ever seen those
    events it would still hold them and they would come back on the next append. The real protection
    there is the pre-push backup and never truncating the station file (`>>`, never `scp`).
    """
    known = {_identity(line) for line in _lines(source_path)}
    return [line for line in _lines(target_path) if _identity(line) not in known]


def main() -> int:
    """Write the lines to move, either direction.

        merge_events.py <target> <source> <out>            # append: source lines missing from target
        merge_events.py --unmatched <target> <source> <out>  # target lines source cannot account for

    Two modes rather than two scripts because they compare events by the *same* rule and the same
    rule is the whole point - two implementations would be two ideas of what "the same event" is,
    which is how a duplicate gets appended or a lost record gets called present.
    """
    args = sys.argv[1:]
    mode = "append"
    if args and args[0] == "--unmatched":
        mode, args = "unmatched", args[1:]
    if len(args) != 3:
        print("usage: merge_events.py [--unmatched] <target.jsonl> <source.jsonl> <out.jsonl>",
              file=sys.stderr)
        return 2
    target, source, out = args
    lines = unmatched(target, source) if mode == "unmatched" else missing_events(target, source)
    with open(out, "w", encoding="utf-8") as handle:
        handle.writelines(lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
