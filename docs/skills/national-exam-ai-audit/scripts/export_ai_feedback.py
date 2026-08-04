#!/usr/bin/env python3
"""Export append-only AI audit feedback for rule/prompt retrospectives."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    import psycopg
except ImportError:  # pragma: no cover - production dependency check
    psycopg = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--rating", choices=("up", "down", "all"), default="down")
    parser.add_argument("--scope", choices=("question", "group", "visual", "answer", "all"), default="all")
    parser.add_argument("--latest-only", action="store_true", help="Keep only the newest rating per candidate/scope/reviewer.")
    parser.add_argument("--output", type=Path, help="Write JSONL here; stdout is used when omitted.")
    return parser.parse_args()


def query(args: argparse.Namespace) -> list[dict[str, Any]]:
    if psycopg is None:
        raise RuntimeError("psycopg is required")
    if not args.database_url:
        raise RuntimeError("DATABASE_URL or --database-url is required")
    filters: list[str] = []
    values: list[str] = []
    if args.rating != "all":
        filters.append("rating = %s")
        values.append(args.rating)
    if args.scope != "all":
        filters.append("audit_scope = %s")
        values.append(args.scope)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    distinct = "DISTINCT ON (candidate_key, audit_scope, COALESCE(reviewer, 'local'))" if args.latest_only else ""
    order = (
        "candidate_key, audit_scope, COALESCE(reviewer, 'local'), id DESC"
        if args.latest_only
        else "id"
    )
    sql = f"""
        SELECT {distinct}
            id, candidate_key, audit_scope, rating, reviewer, reason,
            ai_review_ref, ai_review_event_id, feedback_json, created_at
        FROM exam.question_ai_feedback_events
        {where}
        ORDER BY {order}
    """
    with psycopg.connect(args.database_url) as conn, conn.cursor() as cur:
        cur.execute(sql, values)
        columns = [column.name for column in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def main() -> int:
    args = parse_args()
    try:
        rows = query(args)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    rendered = "".join(
        json.dumps(row, ensure_ascii=False, default=str, sort_keys=True) + "\n"
        for row in rows
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
