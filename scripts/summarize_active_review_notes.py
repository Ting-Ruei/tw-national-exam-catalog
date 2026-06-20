#!/usr/bin/env python3
"""
Summarize only currently active human review notes.

Review logs are append-only. A candidate can have old block/needs_review notes
that are later superseded by an accept action. This helper intentionally reports
only the latest event per candidate unless --include-resolved is used.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATE_ROOT = PROJECT_ROOT / "國考題資料夾" / "30_normalized_items" / "question_candidates"
RESOLVED_ACTIONS = {"accept", "correct", "reviewed", "unblock"}
ACTIVE_ACTIONS = {"block", "needs_review", "comment"}


def latest_path(pattern: str) -> Path:
    paths = sorted(DEFAULT_CANDIDATE_ROOT.glob(pattern))
    if not paths:
        raise SystemExit(f"No review log found: {DEFAULT_CANDIDATE_ROOT}/{pattern}")
    return paths[-1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize latest active Review UI notes.")
    parser.add_argument("--review-log", type=Path, default=None)
    parser.add_argument("--include-resolved", action="store_true")
    parser.add_argument("--format", choices=["text", "jsonl", "csv"], default="text")
    return parser.parse_args()


def load_latest_events(path: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = event.get("candidate_key")
            if key:
                latest[key] = event
    return latest


def is_active(event: dict[str, Any], include_resolved: bool) -> bool:
    action = event.get("action")
    notes = (event.get("notes") or "").strip()
    if include_resolved:
        return bool(notes)
    return action in ACTIVE_ACTIONS and bool(notes) and action not in RESOLVED_ACTIONS


def main() -> None:
    args = parse_args()
    review_log = args.review_log or latest_path("*/question_review_events.jsonl")
    latest = load_latest_events(review_log)
    active = [event for event in latest.values() if is_active(event, args.include_resolved)]
    active.sort(key=lambda item: (item.get("created_at") or "", item.get("candidate_key") or ""))

    if args.format == "jsonl":
        for event in active:
            print(json.dumps(event, ensure_ascii=False, sort_keys=True))
        return

    fields = ["created_at", "candidate_key", "action", "notes", "reviewer"]
    if args.format == "csv":
        writer = csv.DictWriter(__import__("sys").stdout, fieldnames=fields)
        writer.writeheader()
        for event in active:
            writer.writerow({field: event.get(field, "") for field in fields})
        return

    print(f"Review log: {review_log}")
    print(f"Latest candidates with active notes: {len(active)}")
    for event in active:
        print()
        print(f"- {event.get('candidate_key')} [{event.get('action')}] {event.get('created_at')}")
        print((event.get("notes") or "").strip())


if __name__ == "__main__":
    main()
