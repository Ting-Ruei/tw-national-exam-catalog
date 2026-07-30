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
QUESTION_ANSWER_ALIGNMENT_BLOCKING_ISSUE_CODES = frozenset(
    {
        "answer_number_set_unusable",
        "question_answer_number_set_mismatch",
    }
)


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
    parser.add_argument("--postgres-host", default=os.environ.get("PGHOST", ""), help="Optional remote PostgreSQL host.")
    parser.add_argument("--postgres-port", type=int, default=int(os.environ.get("PGPORT", "5432")))
    parser.add_argument("--category", default="", help="Only ingest candidates whose normalized category/group name matches this value.")
    parser.add_argument(
        "--sync-mode",
        choices=("replace-category", "merge"),
        default="replace-category",
        help=(
            "replace-category keeps SQL staging aligned to a complete category snapshot; "
            "merge only upserts supplied candidates and is required for incremental batches."
        ),
    )
    parser.add_argument(
        "--reviewed-change-report",
        type=Path,
        default=None,
        help=(
            "Write the reviewed-candidate content-diff preflight report here. "
            "The ingest aborts if any changed field is covered by a closed human review."
        ),
    )
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


def question_answer_alignment_failures(
    issues: list[dict[str, str]],
    candidate_keys: set[str] | None = None,
) -> dict[str, int]:
    """Count structural answer/question mismatches within the ingest scope."""
    counts: dict[str, int] = {}
    for item in issues:
        candidate_key = item.get("candidate_key") or ""
        if candidate_keys is not None and candidate_key not in candidate_keys:
            continue
        code = item.get("issue_code") or ""
        if code not in QUESTION_ANSWER_ALIGNMENT_BLOCKING_ISSUE_CODES:
            continue
        counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items()))


def validate_question_answer_alignment(failures: dict[str, int]) -> None:
    """Refuse SQL staging when a candidate document failed number-set alignment."""
    failures = {
        code: int(count or 0)
        for code, count in failures.items()
        if int(count or 0)
    }
    if failures:
        raise SystemExit(
            "Refusing to ingest candidate batch with question/answer number-set "
            "alignment failures: "
            + json.dumps(failures, ensure_ascii=False, sort_keys=True)
        )


def psql(
    args: argparse.Namespace,
    sql: str | None = None,
    stdin: str | None = None,
    *,
    tuples_only: bool = False,
) -> subprocess.CompletedProcess[str]:
    cmd = ["docker", "compose", "exec", "-T"]
    if args.postgres_host and os.environ.get("PGPASSWORD"):
        cmd.extend(["-e", "PGPASSWORD"])
    cmd.append("postgres")
    if args.postgres_host:
        cmd.extend(
            [
                "sh",
                "-lc",
                'if [ -z "${PGPASSWORD:-}" ]; then export PGPASSWORD="$POSTGRES_PASSWORD"; fi; exec psql "$@"',
                "psql",
                "-h",
                args.postgres_host,
                "-p",
                str(args.postgres_port),
            ]
        )
    else:
        cmd.append("psql")
    cmd.extend([
        "-U",
        args.postgres_user,
        "-d",
        args.postgres_db,
        "-v",
        "ON_ERROR_STOP=1",
    ])
    if tuples_only:
        cmd.extend(["-t", "-A"])
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


def candidate_category(item: dict[str, Any]) -> str:
    metadata = item.get("metadata") or {}
    return str(metadata.get("normalized_category_name") or metadata.get("group_name") or "")


def filter_candidates(candidates: list[dict[str, Any]], category: str) -> list[dict[str, Any]]:
    if not category:
        return candidates
    return [item for item in candidates if candidate_category(item) == category]


def issue_rows(issues: list[dict[str, str]], candidate_keys: set[str] | None = None) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in issues:
        candidate_key = item.get("candidate_key") or ""
        if candidate_keys is not None and candidate_key not in candidate_keys:
            continue
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

CREATE TABLE IF NOT EXISTS exam_staging.question_candidate_scope (
    candidate_key TEXT PRIMARY KEY
);

TRUNCATE exam_staging.question_candidates, exam_staging.question_parse_issues, exam_staging.question_candidate_scope;
""",
    )


def reviewed_candidate_changes(args: argparse.Namespace) -> list[dict[str, Any]]:
    """Find parser changes that would invalidate a closed human review."""
    result = psql(
        args,
        r"""
WITH changed AS MATERIALIZED (
    SELECT
        s.candidate_key,
        c.parser_version AS previous_parser_version,
        s.parser_version AS incoming_parser_version,
        array_remove(ARRAY[
            CASE WHEN c.question_number IS DISTINCT FROM s.question_number THEN 'question_number' END,
            CASE WHEN COALESCE(c.question_type, '') IS DISTINCT FROM COALESCE(s.question_type, '') THEN 'question_type' END,
            CASE WHEN COALESCE(c.group_ref, '') IS DISTINCT FROM COALESCE(s.group_ref, '') THEN 'group_ref' END,
            CASE WHEN COALESCE(c.stem_text, '') IS DISTINCT FROM COALESCE(s.stem_text, '') THEN 'stem' END,
            CASE WHEN c.stem_markup_json IS DISTINCT FROM NULLIF(s.stem_markup_json, '')::jsonb THEN 'stem_markup' END,
            CASE WHEN c.raw_candidate_json->'options' IS DISTINCT FROM s.raw_candidate_json::jsonb->'options' THEN 'options' END,
            CASE WHEN c.raw_candidate_json->'image_refs' IS DISTINCT FROM s.raw_candidate_json::jsonb->'image_refs' THEN 'image_refs' END,
            CASE WHEN (
                    COALESCE(c.raw_candidate_json->'metadata', '{}'::jsonb)
                    - ARRAY['parser_version', 'raw_block']
                ) IS DISTINCT FROM (
                    COALESCE(s.raw_candidate_json::jsonb->'metadata', '{}'::jsonb)
                    - ARRAY['parser_version', 'raw_block']
                ) THEN 'metadata' END,
            CASE WHEN c.quality_status IS DISTINCT FROM s.quality_status THEN 'quality_status' END,
            CASE WHEN c.issue_count IS DISTINCT FROM NULLIF(s.issue_count, '')::integer THEN 'issue_count' END
        ], NULL) AS question_changed_fields,
        array_remove(ARRAY[
            CASE WHEN COALESCE(c.answer_source_registry_key, '') IS DISTINCT FROM COALESCE(s.answer_source_registry_key, '') THEN 'answer_source_registry_key' END,
            CASE WHEN c.raw_candidate_json->'answer' IS DISTINCT FROM s.raw_candidate_json::jsonb->'answer' THEN 'answer' END,
            CASE WHEN c.raw_candidate_json->'answer_payload' IS DISTINCT FROM s.raw_candidate_json::jsonb->'answer_payload' THEN 'answer_payload' END
        ], NULL) AS answer_changed_fields,
        array_remove(ARRAY[
            CASE WHEN COALESCE(c.group_ref, '') IS DISTINCT FROM COALESCE(s.group_ref, '') THEN 'group_ref' END,
            CASE WHEN COALESCE(c.stem_text, '') IS DISTINCT FROM COALESCE(s.stem_text, '') THEN 'stem' END,
            CASE WHEN c.raw_candidate_json->'image_refs' IS DISTINCT FROM s.raw_candidate_json::jsonb->'image_refs' THEN 'image_refs' END
        ], NULL) AS group_changed_fields
    FROM exam_staging.question_candidates s
    JOIN exam.question_candidates c USING (candidate_key)
),
latest_question AS (
    SELECT DISTINCT ON (candidate_key) candidate_key, action
    FROM exam.question_review_events
    WHERE action NOT IN (
        'confirm_not_group', 'confirm_group', 'reset_group_review',
        'human_review_pdf_visual'
    )
    ORDER BY candidate_key, id DESC
),
latest_answer AS (
    SELECT DISTINCT ON (candidate_key) candidate_key, action
    FROM exam.answer_review_events
    ORDER BY candidate_key, id DESC
),
latest_group AS (
    SELECT DISTINCT ON (candidate_key) candidate_key, action
    FROM exam.question_review_events
    WHERE action IN ('confirm_not_group', 'confirm_group', 'reset_group_review')
    ORDER BY candidate_key, id DESC
),
blocked AS (
    SELECT
        changed.*,
        question.action AS previous_question_action,
        answer.action AS previous_answer_action,
        group_review.action AS previous_group_action,
        array_remove(ARRAY[
            CASE
                WHEN cardinality(changed.question_changed_fields) > 0
                 AND question.action IS NOT NULL
                 AND question.action NOT IN ('unreviewed', 'reset_review')
                THEN 'question'
            END,
            CASE
                WHEN cardinality(changed.answer_changed_fields) > 0
                 AND answer.action IS NOT NULL
                 AND answer.action NOT IN ('unreviewed', 'reset_review')
                THEN 'answer'
            END,
            CASE
                WHEN cardinality(changed.group_changed_fields) > 0
                 AND group_review.action IS NOT NULL
                 AND group_review.action <> 'reset_group_review'
                THEN 'group'
            END
        ], NULL) AS blocked_review_layers
    FROM changed
    LEFT JOIN latest_question question USING (candidate_key)
    LEFT JOIN latest_answer answer USING (candidate_key)
    LEFT JOIN latest_group group_review USING (candidate_key)
)
SELECT COALESCE(
    jsonb_agg(
        jsonb_build_object(
            'candidate_key', candidate_key,
            'previous_parser_version', previous_parser_version,
            'incoming_parser_version', incoming_parser_version,
            'question_changed_fields', question_changed_fields,
            'answer_changed_fields', answer_changed_fields,
            'group_changed_fields', group_changed_fields,
            'blocked_review_layers', blocked_review_layers,
            'previous_question_action', previous_question_action,
            'previous_answer_action', previous_answer_action,
            'previous_group_action', previous_group_action
        )
        ORDER BY candidate_key
    ),
    '[]'::jsonb
)::text
FROM blocked
WHERE cardinality(blocked_review_layers) > 0;
""",
        tuples_only=True,
    )
    payload = result.stdout.strip()
    parsed = json.loads(payload or "[]")
    if not isinstance(parsed, list):
        raise RuntimeError("Reviewed-candidate change preflight returned invalid JSON")
    return parsed


def write_reviewed_change_report(
    path: Path | None,
    changes: list[dict[str, Any]],
    *,
    candidate_path: Path,
) -> dict[str, Any]:
    report = {
        "candidate_jsonl": str(candidate_path),
        "status": "blocked" if changes else "pass",
        "reviewed_candidate_change_count": len(changes),
        "changes": changes,
    }
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return report


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
USING exam_staging.question_candidate_scope s
WHERE existing.candidate_key = s.candidate_key;

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


def prune_missing_category_candidates(args: argparse.Namespace) -> None:
    """Align affected categories only when the input is an explicit full snapshot."""
    psql(
        args,
        """
DELETE FROM exam.question_candidates c
WHERE COALESCE(
        c.raw_candidate_json->'metadata'->>'normalized_category_name',
        c.raw_candidate_json->'metadata'->>'group_name',
        ''
    ) IN (
        SELECT DISTINCT COALESCE(
            s.raw_candidate_json::jsonb->'metadata'->>'normalized_category_name',
            s.raw_candidate_json::jsonb->'metadata'->>'group_name',
            ''
        )
        FROM exam_staging.question_candidates s
    )
  AND NOT EXISTS (
        SELECT 1
        FROM exam_staging.question_candidate_scope scope
        WHERE scope.candidate_key = c.candidate_key
    );
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
    all_candidates = read_jsonl(candidate_path)
    candidates = filter_candidates(all_candidates, args.category)
    candidate_keys = {str(item.get("candidate_key")) for item in candidates if item.get("candidate_key")}
    issues = read_issues(issue_path)
    alignment_failures = question_answer_alignment_failures(
        issues,
        candidate_keys if args.category else None,
    )
    c_rows = candidate_rows(candidates)
    i_rows = issue_rows(issues, candidate_keys if args.category else None)
    scope_rows = [{"candidate_key": key} for key in sorted(candidate_keys)]
    summary = {
        "candidate_jsonl": str(candidate_path),
        "issue_csv": str(issue_path),
        "category": args.category or None,
        "sync_mode": args.sync_mode,
        "candidate_rows": len(c_rows),
        "issue_rows": len(i_rows),
        "question_answer_alignment_failures": alignment_failures,
        "quality_status_counts": {
            status: sum(1 for item in c_rows if item["quality_status"] == status)
            for status in ("pass", "needs_review", "blocked")
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    validate_question_answer_alignment(alignment_failures)
    if args.dry_run:
        return

    create_staging(args)
    copy_table(args, "exam_staging.question_candidates", c_rows, list(c_rows[0].keys()) if c_rows else [])
    copy_table(args, "exam_staging.question_candidate_scope", scope_rows, ["candidate_key"])
    copy_table(args, "exam_staging.question_parse_issues", i_rows, list(i_rows[0].keys()) if i_rows else [])
    changes = reviewed_candidate_changes(args)
    change_report = write_reviewed_change_report(
        args.reviewed_change_report,
        changes,
        candidate_path=candidate_path,
    )
    print(json.dumps(change_report, ensure_ascii=False, indent=2, sort_keys=True))
    if changes:
        raise SystemExit(
            "Refusing to overwrite "
            f"{len(changes)} reviewed candidate(s); inspect the reviewed-change report "
            "and append per-question reset events before retrying."
        )
    apply_upserts(args)
    if args.sync_mode == "replace-category":
        prune_missing_category_candidates(args)
    print_db_summary(args)


if __name__ == "__main__":
    main()
