#!/usr/bin/env python3
"""
Synchronize formal question-group links from latest group review events.

This repair is intentionally event-derived: it does not rewrite review events.
It rebuilds exam.questions.question_group_id/group_sequence_no from the latest
confirm_group events, and clears formal links only when the latest group-layer
event is confirm_not_group or reset_group_review.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync formal group links from latest group review events.")
    parser.add_argument("--category", default="醫事檢驗師")
    parser.add_argument("--postgres-db", default=os.environ.get("POSTGRES_DB", "tw_national_exam_dev"))
    parser.add_argument("--postgres-user", default=os.environ.get("POSTGRES_USER", "national_exam"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def psql(args: argparse.Namespace, sql: str) -> subprocess.CompletedProcess[str]:
    docker = os.environ.get("DOCKER_BIN") or shutil.which("docker") or "/usr/local/bin/docker"
    cmd = [
        docker,
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
        "-c",
        sql,
    ]
    return subprocess.run(cmd, cwd=PROJECT_ROOT, text=True, check=True, capture_output=True)


def common_ctes(category: str) -> str:
    category_sql = sql_literal(category)
    return f"""
WITH med_questions AS (
    SELECT
        q.id AS question_id,
        q.question_key,
        q.question_number,
        q.question_group_id,
        q.group_sequence_no,
        q.question_json,
        od.id AS official_document_id,
        od.registry_key,
        od.official_subject_name
    FROM exam.questions q
    JOIN exam.official_documents od ON od.id = q.official_document_id
    JOIN exam.categories c ON c.id = od.category_id
    WHERE c.normalized_category_name = {category_sql}
      AND q.review_status = 'accepted'
),
latest_group AS (
    SELECT DISTINCT ON (candidate_key)
        candidate_key,
        action,
        event_json,
        id AS event_id,
        created_at
    FROM exam.question_review_events
    WHERE action IN ('confirm_group', 'confirm_not_group', 'reset_group_review')
    ORDER BY candidate_key, id DESC
),
confirmed AS (
    SELECT
        mq.*,
        lg.event_json,
        NULLIF(lg.event_json->>'group_ref', '') AS group_ref,
        COALESCE(NULLIF(lg.event_json->>'group_type', ''), 'shared_stem') AS group_type,
        NULLIF(lg.event_json->>'shared_stem', '') AS shared_stem
    FROM med_questions mq
    JOIN latest_group lg ON lg.candidate_key = mq.question_key
    WHERE lg.action = 'confirm_group'
      AND NULLIF(lg.event_json->>'group_ref', '') IS NOT NULL
),
expected AS (
    SELECT
        c.*,
        c.registry_key || ':' || c.group_ref AS expected_group_key,
        row_number() OVER (
            PARTITION BY c.registry_key, c.group_ref
            ORDER BY
                CASE WHEN c.question_number ~ '^[0-9]+$' THEN c.question_number::integer ELSE 9999 END,
                c.question_key
        )::integer AS expected_sequence_no
    FROM confirmed c
),
expected_groups AS (
    SELECT
        official_document_id,
        registry_key,
        group_ref,
        expected_group_key,
        COALESCE(NULLIF(max(group_type), ''), 'shared_stem') AS group_type,
        NULLIF(max(shared_stem), '') AS shared_stem,
        array_agg(question_key ORDER BY expected_sequence_no) AS candidate_keys,
        array_agg(question_number ORDER BY expected_sequence_no) AS question_numbers,
        format(
            'q%s-q%s',
            lpad(min(CASE WHEN question_number ~ '^[0-9]+$' THEN question_number::integer ELSE NULL END)::text, 3, '0'),
            lpad(max(CASE WHEN question_number ~ '^[0-9]+$' THEN question_number::integer ELSE NULL END)::text, 3, '0')
        ) AS group_question_range
    FROM expected
    GROUP BY official_document_id, registry_key, group_ref, expected_group_key
),
not_confirmed AS (
    SELECT mq.*
    FROM med_questions mq
    JOIN latest_group lg ON lg.candidate_key = mq.question_key
    WHERE lg.action IN ('confirm_not_group', 'reset_group_review')
      AND mq.question_group_id IS NOT NULL
)
"""


def dry_run_sql(category: str) -> str:
    return common_ctes(category) + """
SELECT
    (SELECT count(*) FROM expected) AS confirmed_questions,
    (SELECT count(*) FROM expected_groups) AS confirmed_groups,
    (SELECT count(*) FROM expected e LEFT JOIN exam.question_groups g ON g.group_key = e.expected_group_key WHERE g.id IS NULL) AS missing_group_records,
    (
        SELECT count(*)
        FROM expected e
        JOIN exam.question_groups g ON g.group_key = e.expected_group_key
        WHERE e.question_group_id IS DISTINCT FROM g.id
           OR e.group_sequence_no IS DISTINCT FROM e.expected_sequence_no
    ) AS group_link_mismatches,
    (
        SELECT count(*)
        FROM expected e
        WHERE NULLIF(e.question_json->>'group_ref', '') IS DISTINCT FROM e.group_ref
    ) AS question_json_group_ref_mismatches,
    (
        SELECT count(*)
        FROM expected e
        JOIN exam.question_groups g ON g.group_key = e.expected_group_key
        WHERE e.question_group_id IS DISTINCT FROM g.id
           OR e.group_sequence_no IS DISTINCT FROM e.expected_sequence_no
           OR NULLIF(e.question_json->>'group_ref', '') IS DISTINCT FROM e.group_ref
    ) AS question_link_mismatches,
    (SELECT count(*) FROM not_confirmed) AS stale_nonconfirmed_links;
"""


def apply_sql(category: str) -> str:
    return common_ctes(category) + """
, upserted_groups AS (
    INSERT INTO exam.question_groups (
        official_document_id,
        group_key,
        group_type,
        shared_stem_text,
        stem,
        shared_stem_json,
        metadata,
        group_question_range,
        review_status
    )
    SELECT
        official_document_id,
        expected_group_key,
        group_type,
        shared_stem,
        shared_stem,
        CASE WHEN shared_stem IS NULL THEN NULL ELSE jsonb_build_object('text', shared_stem) END,
        jsonb_build_object(
            'source_registry_key', registry_key,
            'group_ref', group_ref,
            'candidate_keys', candidate_keys,
            'question_numbers', question_numbers,
            'sync_source', 'latest_group_review_event'
        ),
        group_question_range,
        'accepted'
    FROM expected_groups
    ON CONFLICT (group_key) DO UPDATE
    SET group_type = EXCLUDED.group_type,
        shared_stem_text = COALESCE(EXCLUDED.shared_stem_text, exam.question_groups.shared_stem_text),
        stem = COALESCE(EXCLUDED.stem, exam.question_groups.stem),
        shared_stem_json = COALESCE(EXCLUDED.shared_stem_json, exam.question_groups.shared_stem_json),
        metadata = EXCLUDED.metadata,
        group_question_range = EXCLUDED.group_question_range,
        review_status = 'accepted'
    RETURNING id, group_key
),
linked AS (
    UPDATE exam.questions q
    SET question_group_id = g.id,
        group_sequence_no = e.expected_sequence_no,
        question_json = jsonb_set(COALESCE(q.question_json, '{}'::jsonb), '{group_ref}', to_jsonb(e.group_ref), true)
    FROM expected e
    JOIN exam.question_groups g ON g.group_key = e.expected_group_key
    WHERE q.id = e.question_id
      AND (
        q.question_group_id IS DISTINCT FROM g.id
        OR q.group_sequence_no IS DISTINCT FROM e.expected_sequence_no
        OR NULLIF(q.question_json->>'group_ref', '') IS DISTINCT FROM e.group_ref
      )
    RETURNING q.question_key
),
cleared AS (
    UPDATE exam.questions q
    SET question_group_id = NULL,
        group_sequence_no = NULL,
        question_json = jsonb_set(COALESCE(q.question_json, '{}'::jsonb), '{group_ref}', 'null'::jsonb, true)
    FROM not_confirmed nc
    WHERE q.id = nc.question_id
    RETURNING q.question_key
)
SELECT
    (SELECT count(*) FROM expected) AS confirmed_questions,
    (SELECT count(*) FROM expected_groups) AS confirmed_groups,
    (SELECT count(*) FROM upserted_groups) AS upserted_groups,
    (SELECT count(*) FROM linked) AS linked_questions,
    (SELECT count(*) FROM cleared) AS cleared_nonconfirmed_links;
"""


def main() -> None:
    args = parse_args()
    sql = dry_run_sql(args.category) if args.dry_run else apply_sql(args.category)
    result = psql(args, sql)
    print(result.stdout.strip())


if __name__ == "__main__":
    main()
