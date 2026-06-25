#!/usr/bin/env python3
"""
Sync JSONL review events into the PostgreSQL review staging layer.

The JSONL files remain the append-only source of record for human review.
This script rebuilds the SQL event rows for a selected candidate scope so the
Review UI can query current review state without rescanning large JSONL files.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from io import StringIO
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = PROJECT_ROOT / "國考題資料夾"
DEFAULT_CANDIDATE_ROOT = ASSET_ROOT / "30_normalized_items" / "question_candidates"
QUESTION_ACTIONS = {"accept", "correct", "needs_review", "block", "exclude", "unblock", "comment", "reviewed", "unreviewed", "reset_review"}
ANSWER_ACTIONS = {"accept", "correct", "needs_review", "block", "unblock", "comment", "reviewed", "unreviewed", "reset_review"}


def latest_path(pattern: str) -> Path:
    paths = sorted(DEFAULT_CANDIDATE_ROOT.glob(pattern))
    if not paths:
        raise SystemExit(f"No candidate output found: {DEFAULT_CANDIDATE_ROOT}/{pattern}")
    return paths[-1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync JSONL review events into PostgreSQL staging tables.")
    parser.add_argument("--candidate-jsonl", type=Path, default=None)
    parser.add_argument("--review-log", type=Path, default=None)
    parser.add_argument("--answer-review-log", type=Path, default=None)
    parser.add_argument("--ai-review-log", type=Path, default=None)
    parser.add_argument("--category", default="", help="Only sync events for candidates in this normalized category/group.")
    parser.add_argument("--postgres-db", default=os.environ.get("POSTGRES_DB", "tw_national_exam_dev"))
    parser.add_argument("--postgres-user", default=os.environ.get("POSTGRES_USER", "national_exam"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
    return rows


def candidate_category(item: dict[str, Any]) -> str:
    metadata = item.get("metadata") or {}
    return str(metadata.get("normalized_category_name") or metadata.get("group_name") or "")


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path, Path, Path]:
    candidate_path = args.candidate_jsonl or latest_path("*/question_candidates__*.jsonl")
    review_log = args.review_log or candidate_path.parent / "question_review_events.jsonl"
    answer_review_log = args.answer_review_log or candidate_path.parent / "answer_review_events.jsonl"
    ai_review_log = args.ai_review_log or candidate_path.parent / "question_ai_review_events.jsonl"
    return candidate_path, review_log, answer_review_log, ai_review_log


def json_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def psql(args: argparse.Namespace, sql: str | None = None, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        args.postgres_user,
        "-d",
        args.postgres_db,
        "-v",
        "ON_ERROR_STOP=1",
    ]
    if sql is not None:
        cmd.extend(["-c", sql])
    try:
        return subprocess.run(cmd, cwd=PROJECT_ROOT, input=stdin, text=True, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        print(exc.stdout)
        print(exc.stderr)
        raise


def csv_text(rows: list[dict[str, object]], fields: list[str]) -> str:
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def copy_table(args: argparse.Namespace, table: str, rows: list[dict[str, object]], fields: list[str]) -> None:
    if not rows:
        return
    payload = f"\\copy {table} ({', '.join(fields)}) FROM STDIN WITH (FORMAT csv, HEADER true)\n"
    payload += csv_text(rows, fields)
    psql(args, stdin=payload)


def create_staging(args: argparse.Namespace) -> None:
    psql(
        args,
        """
CREATE SCHEMA IF NOT EXISTS exam_staging;

CREATE TABLE IF NOT EXISTS exam_staging.review_event_scope (
    candidate_key TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS exam_staging.question_review_events (
    candidate_key TEXT,
    reviewer TEXT,
    action TEXT,
    corrected_candidate_json TEXT,
    event_json TEXT,
    notes TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS exam_staging.answer_review_events (
    candidate_key TEXT,
    answer_source_registry_key TEXT,
    reviewer TEXT,
    action TEXT,
    reviewed_answer_json TEXT,
    corrected_answer_json TEXT,
    event_json TEXT,
    notes TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS exam_staging.question_ai_review_events (
    candidate_key TEXT,
    action TEXT,
    reviewer TEXT,
    provider TEXT,
    model_name TEXT,
    prompt_version TEXT,
    input_hash TEXT,
    audit_status TEXT,
    recommended_action TEXT,
    audit_json TEXT,
    event_json TEXT,
    notes TEXT,
    created_at TEXT
);

TRUNCATE exam_staging.review_event_scope,
    exam_staging.question_review_events,
    exam_staging.answer_review_events,
    exam_staging.question_ai_review_events;
""",
    )


def normalize_ai_status(value: Any) -> str:
    status = str(value or "pass").strip()
    if status in {"blocked", "block"}:
        return "block"
    if status == "needs_review":
        return "needs_review"
    return "pass"


def question_rows(events: list[dict[str, Any]], scope: set[str]) -> list[dict[str, object]]:
    rows = []
    for event in events:
        key = str(event.get("candidate_key") or "")
        action = str(event.get("action") or "")
        if key not in scope or action not in QUESTION_ACTIONS:
            continue
        rows.append(
            {
                "candidate_key": key,
                "reviewer": event.get("reviewer") or "",
                "action": action,
                "corrected_candidate_json": json_text(event.get("correction")),
                "event_json": json_text(event),
                "notes": event.get("notes") or "",
                "created_at": event.get("created_at") or "",
            }
        )
    return rows


def answer_rows(events: list[dict[str, Any]], scope: set[str]) -> list[dict[str, object]]:
    rows = []
    for event in events:
        key = str(event.get("candidate_key") or "")
        action = str(event.get("action") or "")
        if key not in scope or action not in ANSWER_ACTIONS:
            continue
        rows.append(
            {
                "candidate_key": key,
                "answer_source_registry_key": event.get("answer_source_registry_key") or "",
                "reviewer": event.get("reviewer") or "",
                "action": action,
                "reviewed_answer_json": json_text(event.get("reviewed_answer")),
                "corrected_answer_json": json_text(event.get("corrected_answer")),
                "event_json": json_text(event),
                "notes": event.get("notes") or "",
                "created_at": event.get("created_at") or "",
            }
        )
    return rows


def ai_rows(events: list[dict[str, Any]], scope: set[str]) -> list[dict[str, object]]:
    rows = []
    for event in events:
        key = str(event.get("candidate_key") or "")
        if key not in scope:
            continue
        audit = event.get("audit") if isinstance(event.get("audit"), dict) else {}
        rows.append(
            {
                "candidate_key": key,
                "action": event.get("action") or "ai_audit",
                "reviewer": event.get("reviewer") or "",
                "provider": event.get("provider") or audit.get("provider") or "local",
                "model_name": event.get("model") or audit.get("model") or "",
                "prompt_version": event.get("prompt_version") or "",
                "input_hash": event.get("input_hash") or "",
                "audit_status": normalize_ai_status(audit.get("status")),
                "recommended_action": audit.get("recommended_action") or "",
                "audit_json": json_text(audit or {"status": "pass"}),
                "event_json": json_text(event),
                "notes": event.get("notes") or "",
                "created_at": event.get("created_at") or "",
            }
        )
    return rows


def apply_sync(args: argparse.Namespace) -> None:
    psql(
        args,
        """
DELETE FROM exam.question_review_events e
USING exam_staging.review_event_scope s
WHERE e.candidate_key = s.candidate_key;

DELETE FROM exam.answer_review_events e
USING exam_staging.review_event_scope s
WHERE e.candidate_key = s.candidate_key;

DELETE FROM exam.question_ai_review_events e
USING exam_staging.review_event_scope s
WHERE e.candidate_key = s.candidate_key;

INSERT INTO exam.question_review_events (
    candidate_id,
    candidate_key,
    reviewer,
    action,
    corrected_candidate_json,
    event_json,
    notes,
    created_at
)
SELECT
    c.id,
    s.candidate_key,
    NULLIF(s.reviewer, ''),
    s.action,
    COALESCE(NULLIF(s.corrected_candidate_json, '')::jsonb, NULL),
    COALESCE(NULLIF(s.event_json, '')::jsonb, NULL),
    s.notes,
    COALESCE(NULLIF(s.created_at, '')::timestamptz, now())
FROM exam_staging.question_review_events s
JOIN exam.question_candidates c ON c.candidate_key = s.candidate_key;

INSERT INTO exam.answer_review_events (
    candidate_id,
    candidate_key,
    answer_source_registry_key,
    reviewer,
    action,
    reviewed_answer_json,
    corrected_answer_json,
    event_json,
    notes,
    created_at
)
SELECT
    c.id,
    s.candidate_key,
    NULLIF(s.answer_source_registry_key, ''),
    NULLIF(s.reviewer, ''),
    s.action,
    COALESCE(NULLIF(s.reviewed_answer_json, '')::jsonb, NULL),
    COALESCE(NULLIF(s.corrected_answer_json, '')::jsonb, NULL),
    COALESCE(NULLIF(s.event_json, '')::jsonb, NULL),
    s.notes,
    COALESCE(NULLIF(s.created_at, '')::timestamptz, now())
FROM exam_staging.answer_review_events s
JOIN exam.question_candidates c ON c.candidate_key = s.candidate_key;

INSERT INTO exam.question_ai_review_events (
    candidate_id,
    candidate_key,
    action,
    reviewer,
    provider,
    model_name,
    prompt_version,
    input_hash,
    audit_status,
    recommended_action,
    audit_json,
    event_json,
    notes,
    created_at
)
SELECT
    c.id,
    s.candidate_key,
    COALESCE(NULLIF(s.action, ''), 'ai_audit'),
    NULLIF(s.reviewer, ''),
    COALESCE(NULLIF(s.provider, ''), 'local'),
    NULLIF(s.model_name, ''),
    NULLIF(s.prompt_version, ''),
    NULLIF(s.input_hash, ''),
    s.audit_status,
    NULLIF(s.recommended_action, ''),
    COALESCE(NULLIF(s.audit_json, '')::jsonb, '{"status":"pass"}'::jsonb),
    COALESCE(NULLIF(s.event_json, '')::jsonb, NULL),
    s.notes,
    COALESCE(NULLIF(s.created_at, '')::timestamptz, now())
FROM exam_staging.question_ai_review_events s
JOIN exam.question_candidates c ON c.candidate_key = s.candidate_key;
""",
    )


def print_summary(args: argparse.Namespace) -> None:
    result = psql(
        args,
        """
SELECT 'question_review_events' AS table_name, count(*) FROM exam.question_review_events
UNION ALL
SELECT 'answer_review_events', count(*) FROM exam.answer_review_events
UNION ALL
SELECT 'question_ai_review_events', count(*) FROM exam.question_ai_review_events
ORDER BY table_name;
""",
    )
    print(result.stdout)


def main() -> None:
    args = parse_args()
    candidate_path, review_log, answer_review_log, ai_review_log = resolve_paths(args)
    candidates = read_jsonl(candidate_path)
    if args.category:
        candidates = [item for item in candidates if candidate_category(item) == args.category]
    scope = {str(item.get("candidate_key")) for item in candidates if item.get("candidate_key")}
    q_rows = question_rows(read_jsonl(review_log), scope)
    a_rows = answer_rows(read_jsonl(answer_review_log), scope)
    ai_event_rows = ai_rows(read_jsonl(ai_review_log), scope)
    scope_rows = [{"candidate_key": key} for key in sorted(scope)]
    summary = {
        "candidate_jsonl": str(candidate_path),
        "category": args.category or None,
        "scope_candidates": len(scope),
        "question_review_events": len(q_rows),
        "answer_review_events": len(a_rows),
        "question_ai_review_events": len(ai_event_rows),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if args.dry_run:
        return
    create_staging(args)
    copy_table(args, "exam_staging.review_event_scope", scope_rows, ["candidate_key"])
    copy_table(args, "exam_staging.question_review_events", q_rows, list(q_rows[0].keys()) if q_rows else [])
    copy_table(args, "exam_staging.answer_review_events", a_rows, list(a_rows[0].keys()) if a_rows else [])
    copy_table(args, "exam_staging.question_ai_review_events", ai_event_rows, list(ai_event_rows[0].keys()) if ai_event_rows else [])
    apply_sync(args)
    print_summary(args)


if __name__ == "__main__":
    main()
