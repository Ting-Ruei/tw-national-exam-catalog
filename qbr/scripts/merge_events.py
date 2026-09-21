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


def main() -> int:
    """`merge_events.py <target> <source> <out>` - write the lines to append into `out`."""
    if len(sys.argv) != 4:
        print("usage: merge_events.py <target.jsonl> <source.jsonl> <out.jsonl>", file=sys.stderr)
        return 2
    target, source, out = sys.argv[1:4]
    missing = missing_events(target, source)
    with open(out, "w", encoding="utf-8") as handle:
        handle.writelines(missing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
