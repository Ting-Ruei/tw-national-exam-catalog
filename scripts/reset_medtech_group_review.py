#!/usr/bin/env python3
"""
Reset medtech group-tag review for explicit group ranges.

This is an append-only repair helper. It does not rewrite old review events and
does not change question/answer review acceptance. It appends
`reset_group_review` events, inserts the same events into SQL, and clears formal
question_group_id/group_sequence_no so the Review UI can re-confirm grouping.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REVIEW_LOG = (
    PROJECT_ROOT
    / "國考題資料夾"
    / "30_normalized_items"
    / "question_candidates"
    / "20260620-213413"
    / "question_review_events.jsonl"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append reset_group_review events for medtech group-tag re-review.")
    parser.add_argument("--category", default="醫事檢驗師")
    parser.add_argument("--review-log", type=Path, default=DEFAULT_REVIEW_LOG)
    parser.add_argument("--postgres-db", default=os.environ.get("POSTGRES_DB", "tw_national_exam_dev"))
    parser.add_argument("--postgres-user", default=os.environ.get("POSTGRES_USER", "national_exam"))
    parser.add_argument("--reviewer", default="codex")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def psql(args: argparse.Namespace, sql: str) -> subprocess.CompletedProcess[str]:
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
        "-P",
        "pager=off",
        "-At",
        "-c",
        sql,
    ]
    return subprocess.run(cmd, cwd=PROJECT_ROOT, text=True, check=True, capture_output=True)


def candidate_sql(category: str) -> str:
    return f"""
WITH anchors AS (
    SELECT
        q.question_key,
        q.question_number,
        q.question_json->>'group_ref' AS group_ref,
        od.registry_key,
        es.roc_year,
        es.exam_ordinal,
        s.normalized_subject_name
    FROM exam.questions q
    JOIN exam.official_documents od ON od.id = q.official_document_id
    JOIN exam.exam_sessions es ON es.id = od.exam_session_id
    JOIN exam.subjects s ON s.id = od.subject_id
    JOIN exam.categories c ON c.id = od.category_id
    WHERE c.normalized_category_name = {sql_literal(category)}
      AND NULLIF(q.question_json->>'group_ref', '') IS NOT NULL
      AND q.review_status = 'accepted'
),
parsed AS (
    SELECT *, regexp_match(group_ref, '^q([0-9]+)-q([0-9]+)$') AS m
    FROM anchors
),
expected AS (
    SELECT
        p.group_ref,
        p.registry_key,
        generate_series((p.m[1])::integer, (p.m[2])::integer) AS expected_qnum
    FROM parsed p
    WHERE p.m IS NOT NULL
),
expected_questions AS (
    SELECT
        q.question_key,
        q.question_number,
        e.group_ref,
        e.registry_key,
        es.roc_year,
        es.exam_ordinal,
        s.normalized_subject_name
    FROM expected e
    JOIN exam.official_documents od ON od.registry_key = e.registry_key
    JOIN exam.exam_sessions es ON es.id = od.exam_session_id
    JOIN exam.subjects s ON s.id = od.subject_id
    JOIN exam.questions q ON q.official_document_id = od.id
        AND q.question_number = e.expected_qnum::text
),
combined AS (
    SELECT question_key, question_number, group_ref, registry_key, roc_year, exam_ordinal, normalized_subject_name, 'anchor_with_group_ref' AS reset_scope
    FROM anchors
    UNION
    SELECT question_key, question_number, group_ref, registry_key, roc_year, exam_ordinal, normalized_subject_name, 'expected_range_member' AS reset_scope
    FROM expected_questions
)
SELECT jsonb_build_object(
    'candidate_key', question_key,
    'question_number', question_number,
    'group_ref', group_ref,
    'source_registry_key', registry_key,
    'roc_year', roc_year,
    'exam_ordinal', exam_ordinal,
    'normalized_subject_name', normalized_subject_name,
    'reset_scope', reset_scope
)
FROM combined
ORDER BY roc_year, exam_ordinal, normalized_subject_name,
    CASE WHEN question_number ~ '^[0-9]+$' THEN question_number::integer ELSE 9999 END,
    question_key
"""


def load_candidates(args: argparse.Namespace) -> list[dict[str, Any]]:
    result = psql(args, candidate_sql(args.category))
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = str(row["candidate_key"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows


def build_events(rows: list[dict[str, Any]], reviewer: str) -> list[dict[str, Any]]:
    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    events = []
    for row in rows:
        events.append(
            {
                "action": "reset_group_review",
                "candidate_key": row["candidate_key"],
                "created_at": created_at,
                "group_ref": row.get("group_ref"),
                "group_sheet_key": row.get("group_ref") or "",
                "notes": "題組標記重看：明示 range 內成員標記不完整，退回題組層重新確認；不改變題目與答案審核狀態。",
                "reset_reason": "medtech_group_ref_range_member_incomplete",
                "reset_scope": row.get("reset_scope"),
                "review_layer": "group",
                "reviewer": reviewer,
                "source_registry_key": row.get("source_registry_key"),
            }
        )
    return events


def append_review_log(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for event in events:
            f.write(json_dump(event))
            f.write("\n")


def apply_sql(args: argparse.Namespace, events: list[dict[str, Any]]) -> None:
    values = []
    for event in events:
        values.append(
            "("
            + ", ".join(
                [
                    sql_literal(str(event["candidate_key"])),
                    sql_literal(str(event["reviewer"])),
                    sql_literal(str(event["action"])),
                    sql_literal(json_dump(event)) + "::jsonb",
                    sql_literal(str(event["notes"])),
                    sql_literal(str(event["created_at"])) + "::timestamptz",
                ]
            )
            + ")"
        )
    values_sql = ",\n    ".join(values)
    sql = f"""
WITH payload(candidate_key, reviewer, action, event_json, notes, created_at) AS (
    VALUES
    {values_sql}
),
inserted AS (
    INSERT INTO exam.question_review_events (
        candidate_id,
        candidate_key,
        reviewer,
        action,
        event_json,
        notes,
        created_at
    )
    SELECT
        qc.id,
        p.candidate_key,
        p.reviewer,
        p.action,
        p.event_json,
        p.notes,
        p.created_at
    FROM payload p
    JOIN exam.question_candidates qc ON qc.candidate_key = p.candidate_key
    RETURNING candidate_key
),
framed_candidates AS (
    UPDATE exam.question_candidates qc
    SET group_ref = p.event_json->>'group_ref',
        raw_candidate_json = jsonb_set(COALESCE(qc.raw_candidate_json, '{{}}'::jsonb), '{{group_ref}}', to_jsonb(p.event_json->>'group_ref'), true),
        normalized_candidate_json = CASE
            WHEN qc.normalized_candidate_json IS NULL THEN NULL
            ELSE jsonb_set(qc.normalized_candidate_json, '{{group_ref}}', to_jsonb(p.event_json->>'group_ref'), true)
        END,
        updated_at = now()
    FROM payload p
    WHERE qc.candidate_key = p.candidate_key
    RETURNING qc.candidate_key
),
cleared AS (
    UPDATE exam.questions q
    SET question_group_id = NULL,
        group_sequence_no = NULL,
        question_json = jsonb_set(COALESCE(q.question_json, '{{}}'::jsonb), '{{group_ref}}', 'null'::jsonb, true)
    FROM payload p
    WHERE q.question_key = p.candidate_key
    RETURNING q.question_key
)
SELECT
    (SELECT count(*) FROM inserted) AS inserted_events,
    (SELECT count(*) FROM framed_candidates) AS framed_candidates,
    (SELECT count(*) FROM cleared) AS cleared_formal_groups;
"""
    print(psql(args, sql).stdout.strip())


def main() -> None:
    args = parse_args()
    rows = load_candidates(args)
    events = build_events(rows, args.reviewer)
    summary = {
        "review_log": str(args.review_log),
        "candidate_count": len(rows),
        "action": "reset_group_review",
        "dry_run": args.dry_run,
    }
    print(json_dump(summary))
    if args.dry_run:
        for row in rows[:10]:
            print(json_dump(row))
        if len(rows) > 10:
            print(f"... {len(rows) - 10} more")
        return
    append_review_log(args.review_log, events)
    apply_sql(args, events)


if __name__ == "__main__":
    main()
