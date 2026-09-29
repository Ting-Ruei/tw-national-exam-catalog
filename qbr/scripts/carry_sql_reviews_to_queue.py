#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 Postgres 的人工審核紀錄，換算成 jsonl 佇列可以帶入的提案。

背景：本專案有**兩個互不相通的審核儲存**。
  - `--review-backend sql`（容器 8765）：Postgres `exam.question_review_events`，25,843 筆人工事件。
  - `--review-backend jsonl`（本機 8774）：`question_review_events.jsonl`，只有 192 筆。
`build_review_queue.py --carry-from` 只掃**檔案系統的兄弟佇列**，看不見 Postgres，
所以 SQL 裡的人工決定永遠不會出現在 jsonl 佇列，這不是 `/tmp` 的問題。

本腳本的分類規則（**不是**「都帶過去」）：
  1. 題目不在現行佇列 -> `orphan`，保留但標記，不寫入。
  2. stem 與四個選項在 NFKC/去空白後**完全相同** -> `carry`，人工決定仍成立。
  3. 只要有一處**實質不同** -> `reset`，因為那個決定是對**舊文字**做的，
     新管線改了紙本上的東西，必須重新確認（沿用 `append_reset_review_events.py` 的 reset 模式）。

預設只輸出提案，不寫任何檔案。
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import unicodedata
from pathlib import Path

CARRIED_PROVENANCE = "sql-question-review-events"


def canon(text: str) -> str:
    """NFKC + 移除所有空白。只做比較，不改動儲存的字（沙盒 §3）。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


def option_texts(options) -> list[str]:
    out = []
    for opt in options or []:
        if isinstance(opt, dict):
            out.append(opt.get("text") or "")
        elif isinstance(opt, str):
            out.append(opt)
    return out


def load_db_events(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_queue(path: Path) -> dict[str, dict]:
    queue = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            key = row.get("candidate_key") or row.get("source_question_key")
            if key:
                queue[key] = row
    return queue


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-events", type=Path, required=True,
                        help="json array exported from exam.question_review_events")
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--out", type=Path,
                        help="where to write the proposal (default: stdout summary only)")
    args = parser.parse_args()

    db_events = load_db_events(args.db_events)
    queue = load_queue(args.candidates)

    # 每題只留最後一次人工決定：審核者可以改變決定，較晚的才是現行狀態。
    latest: dict[str, dict] = {}
    for event in db_events:
        key = event["key"]
        if key not in latest or (event.get("created_at") or "") >= (latest[key].get("created_at") or ""):
            latest[key] = event

    buckets = collections.Counter()
    by_category = collections.defaultdict(collections.Counter)
    proposal = []

    for key, event in latest.items():
        row = queue.get(key)
        category = ((row or {}).get("metadata") or {}).get("official_category_name") or "(未知)"
        if row is None:
            buckets["orphan"] += 1
            by_category[category]["orphan"] += 1
            continue

        same = canon(event.get("stem")) == canon(row.get("stem")) and \
            [canon(t) for t in option_texts(event.get("options"))] == \
            [canon(t) for t in option_texts(row.get("options"))]

        if same:
            buckets["carry"] += 1
            by_category[category]["carry"] += 1
            proposal.append({
                "kind": "carry",
                "candidate_key": key,
                "action": event.get("action"),
                "created_at": event.get("created_at"),
                "reviewer": event.get("reviewer"),
                "notes": event.get("notes"),
                "_carried_from": CARRIED_PROVENANCE,
            })
        else:
            buckets["reset"] += 1
            by_category[category]["reset"] += 1
            proposal.append({
                "kind": "reset",
                "candidate_key": key,
                "previous_action": event.get("action"),
                "previous_reviewed_at": event.get("created_at"),
                "previous_notes": event.get("notes"),
                "reset_notes": "內容在本管線中已改變，原決定係對舊文字所做，需重新確認。",
                "_carried_from": CARRIED_PROVENANCE,
            })

    print(f"DB 人工事件 {len(db_events):,} 筆 -> 去重後 {len(latest):,} 題")
    print(f"現行佇列 {len(queue):,} 題")
    print()
    print(f"  carry（內容相同，決定仍成立）: {buckets['carry']:6,}")
    print(f"  reset（內容已變，需重審）    : {buckets['reset']:6,}")
    print(f"  orphan（題目不在佇列）        : {buckets['orphan']:6,}")
    print()
    print(f"{'類別':12} {'carry':>8} {'reset':>8} {'orphan':>8}")
    for category in sorted(by_category):
        row = by_category[category]
        print(f"  {category:12} {row['carry']:8,} {row['reset']:8,} {row['orphan']:8,}")

    if args.out:
        args.out.write_text(json.dumps(proposal, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n提案寫入 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
