#!/usr/bin/env python3
"""
Ingest question candidate JSONL and parse issues into PostgreSQL candidate tables.

This script is intentionally limited to the pre-ingestion review layer:
- exam.question_candidates
- exam.question_parse_issues

It does not write exam.questions, exam.question_options, exam.answers, or
exam.question_assets.
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


def latest_path(pattern: str) -> Path:
    paths = sorted(DEFAULT_CANDIDATE_ROOT.glob(pattern))
    if not paths:
        raise SystemExit(f"No candidate output found: {DEFAULT_CANDIDATE_ROOT}/{pattern}")
    return paths[-1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest question candidates into the PostgreSQL review layer.")
    parser.add_argument("--candidate-jsonl", type=Path, default=None)
    parser.add_argument("--issue-csv", type=Path, default=None)
    parser.add_argument("--postgres-db", default=os.environ.get("POSTGRES_DB", "tw_national_exam_dev"))
    parser.add_argument("--postgres-user", default=os.environ.get("POSTGRES_USER", "national_exam"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def resolve_defaults(args: argparse.Namespace) -> tuple[Path, Path]:
    candidate_path = args.candidate_jsonl or latest_path("*/question_candidates__*.jsonl")
    issue_path = args.issue_csv or latest_path("*/question_parse_issues__*.csv")
    return candidate_path, issue_path


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
    return rows


def read_issues(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


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


def candidate_rows(candidates: list[dict[str, Any]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in candidates:
        metadata = item.get("metadata") or {}
        normalized = {
            "stem": item.get("stem"),
            "options": item.get("options") or [],
            "answer": item.get("answer"),
            "answer_payload": item.get("answer_payload"),
            "image_refs": item.get("image_refs") or [],
            "metadata": metadata,
        }
        rows.append(
            {
                "candidate_key": item["candidate_key"],
                "source_registry_key": item["source_registry_key"],
                "answer_source_registry_key": item.get("answer_source_registry_key") or "",
                "question_number": str(item["question_number"]),
                "question_type": item.get("question_type") or "",
                "group_ref": item.get("group_ref") or "",
                "stem_text": item.get("stem") or "",
                "stem_markup_json": json.dumps(item.get("stem_markup"), ensure_ascii=False) if item.get("stem_markup") else "",
                "raw_candidate_json": json.dumps(item, ensure_ascii=False, sort_keys=True),
                "normalized_candidate_json": json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                "parser_version": metadata.get("parser_version") or "unknown",
                "quality_status": item.get("quality_status") or "needs_review",
                "issue_count": int(item.get("issue_count") or 0),
            }
        )
    return rows


def issue_rows(issues: list[dict[str, str]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in issues:
        candidate_key = item.get("candidate_key") or ""
        source_registry_key = item.get("source_registry_key") or ""
        if not source_registry_key:
            continue
        rows.append(
            {
                "candidate_key": candidate_key,
                "source_registry_key": source_registry_key,
                "issue_code": item.get("issue_code") or "unknown_issue",
                "severity": item.get("severity") or "warning",
                "message": item.get("message") or "",
                "issue_json": item.get("issue_json") or "{}",
            }
        )
    return rows


def create_staging(args: argparse.Namespace) -> None:
    psql(
        args,
        """
CREATE SCHEMA IF NOT EXISTS exam_staging;

CREATE TABLE IF NOT EXISTS exam_staging.question_candidates (
    candidate_key TEXT,
    source_registry_key TEXT,
    answer_source_registry_key TEXT,
    question_number TEXT,
    question_type TEXT,
    group_ref TEXT,
    stem_text TEXT,
    stem_markup_json TEXT,
    raw_candidate_json TEXT,
    normalized_candidate_json TEXT,
    parser_version TEXT,
    quality_status TEXT,
    issue_count TEXT
);

CREATE TABLE IF NOT EXISTS exam_staging.question_parse_issues (
    candidate_key TEXT,
    source_registry_key TEXT,
    issue_code TEXT,
    severity TEXT,
    message TEXT,
    issue_json TEXT
);

TRUNCATE exam_staging.question_candidates, exam_staging.question_parse_issues;
""",
    )


def apply_upserts(args: argparse.Namespace) -> None:
    psql(
        args,
        """
INSERT INTO exam.question_candidates (
    candidate_key,
    source_registry_key,
    source_document_id,
    answer_source_registry_key,
    answer_source_document_id,
    question_number,
    question_type,
    group_ref,
    stem_text,
    stem_markup_json,
    raw_candidate_json,
    normalized_candidate_json,
    parser_version,
    quality_status,
    review_status,
    issue_count,
    updated_at
)
SELECT
    s.candidate_key,
    s.source_registry_key,
    source_doc.id,
    NULLIF(s.answer_source_registry_key, ''),
    answer_doc.id,
    s.question_number,
    NULLIF(s.question_type, ''),
    NULLIF(s.group_ref, ''),
    NULLIF(s.stem_text, ''),
    NULLIF(s.stem_markup_json, '')::jsonb,
    s.raw_candidate_json::jsonb,
    NULLIF(s.normalized_candidate_json, '')::jsonb,
    s.parser_version,
    s.quality_status,
    'unreviewed',
    NULLIF(s.issue_count, '')::integer,
    now()
FROM exam_staging.question_candidates s
LEFT JOIN exam.official_documents source_doc ON source_doc.registry_key = s.source_registry_key
LEFT JOIN exam.official_documents answer_doc ON answer_doc.registry_key = NULLIF(s.answer_source_registry_key, '')
ON CONFLICT (candidate_key) DO UPDATE
SET source_registry_key = EXCLUDED.source_registry_key,
    source_document_id = EXCLUDED.source_document_id,
    answer_source_registry_key = EXCLUDED.answer_source_registry_key,
    answer_source_document_id = EXCLUDED.answer_source_document_id,
    question_number = EXCLUDED.question_number,
    question_type = EXCLUDED.question_type,
    group_ref = EXCLUDED.group_ref,
    stem_text = EXCLUDED.stem_text,
    stem_markup_json = EXCLUDED.stem_markup_json,
    raw_candidate_json = EXCLUDED.raw_candidate_json,
    normalized_candidate_json = EXCLUDED.normalized_candidate_json,
    parser_version = EXCLUDED.parser_version,
    quality_status = EXCLUDED.quality_status,
    issue_count = EXCLUDED.issue_count,
    updated_at = now();

DELETE FROM exam.question_parse_issues existing
USING exam_staging.question_parse_issues s
WHERE existing.candidate_key = s.candidate_key
   OR (
        existing.candidate_key IS NULL
        AND existing.source_registry_key = s.source_registry_key
        AND existing.issue_code = s.issue_code
      );

INSERT INTO exam.question_parse_issues (
    candidate_id,
    candidate_key,
    source_registry_key,
    issue_code,
    severity,
    message,
    issue_json
)
SELECT
    c.id,
    NULLIF(s.candidate_key, ''),
    s.source_registry_key,
    s.issue_code,
    s.severity,
    s.message,
    COALESCE(NULLIF(s.issue_json, '')::jsonb, '{}'::jsonb)
FROM exam_staging.question_parse_issues s
LEFT JOIN exam.question_candidates c ON c.candidate_key = NULLIF(s.candidate_key, '');
""",
    )


def print_db_summary(args: argparse.Namespace) -> None:
    result = psql(
        args,
        """
SELECT quality_status, review_status, count(*)
FROM exam.question_candidates
GROUP BY quality_status, review_status
ORDER BY quality_status, review_status;

SELECT severity, issue_code, count(*)
FROM exam.question_parse_issues
GROUP BY severity, issue_code
ORDER BY severity, issue_code;
""",
    )
    print(result.stdout)


def main() -> None:
    args = parse_args()
    candidate_path, issue_path = resolve_defaults(args)
    candidates = read_jsonl(candidate_path)
    issues = read_issues(issue_path)
    c_rows = candidate_rows(candidates)
    i_rows = issue_rows(issues)
    summary = {
        "candidate_jsonl": str(candidate_path),
        "issue_csv": str(issue_path),
        "candidate_rows": len(c_rows),
        "issue_rows": len(i_rows),
        "quality_status_counts": {
            status: sum(1 for item in c_rows if item["quality_status"] == status)
            for status in ("pass", "needs_review", "blocked")
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if args.dry_run:
        return

    create_staging(args)
    copy_table(args, "exam_staging.question_candidates", c_rows, list(c_rows[0].keys()) if c_rows else [])
    copy_table(args, "exam_staging.question_parse_issues", i_rows, list(i_rows[0].keys()) if i_rows else [])
    apply_upserts(args)
    print_db_summary(args)


if __name__ == "__main__":
    main()

