#!/usr/bin/env python3
"""Reset question decisions that were accidentally created by old visual-label clicks.

Older Review UI versions saved a visual decision as a normal question review
event. When a reviewer used the visual page before the question page, that
click could set ``question_candidates.review_status`` to ``accepted`` even
though no independent question decision existed.

This repair is append-only. It preserves every visual event, appends one
``reset_review`` event for each affected candidate, marks the candidate
unreviewed, and queues a formal-layer refresh. The default mode is dry-run.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reset accidental question acceptance caused by old visual-label clicks."
    )
    parser.add_argument("--category", default="醫事檢驗師")
    parser.add_argument("--year", default="115")
    parser.add_argument("--exam-ordinal", default="2")
    parser.add_argument("--reviewer", default="repair_visual_label_state_v1")
    parser.add_argument("--postgres-db", default=os.environ.get("POSTGRES_DB", "tw_national_exam_dev"))
    parser.add_argument("--postgres-user", default=os.environ.get("POSTGRES_USER", "national_exam"))
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def psql(args: argparse.Namespace, sql: str) -> subprocess.CompletedProcess[str]:
    docker = shutil.which("docker") or "/usr/local/bin/docker"
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


def affected_ctes(args: argparse.Namespace) -> str:
    category = sql_literal(args.category)
    year = sql_literal(args.year)
    ordinal = sql_literal(args.exam_ordinal)
    return f"""
WITH scoped_candidates AS MATERIALIZED (
    SELECT c.*
    FROM exam.question_candidates c
    WHERE COALESCE(
              c.raw_candidate_json->'metadata'->>'normalized_category_name',
              c.raw_candidate_json->'metadata'->>'group_name',
              ''
          ) = {category}
      AND COALESCE(c.raw_candidate_json->'metadata'->>'year', '') = {year}
      AND COALESCE(c.raw_candidate_json->'metadata'->>'exam_ordinal', '') = {ordinal}
),
events AS MATERIALIZED (
    SELECT
        e.*,
        (
            e.corrected_candidate_json ? 'visual_review'
            AND COALESCE(e.notes, '') LIKE '圖片審核：%'
        ) AS is_visual_click
    FROM exam.question_review_events e
    JOIN scoped_candidates c USING (candidate_key)
),
affected AS MATERIALIZED (
    SELECT DISTINCT ON (v.candidate_key)
        v.candidate_id,
        v.candidate_key,
        v.id AS visual_event_id,
        v.action AS visual_event_action,
        v.corrected_candidate_json->>'visual_review' AS visual_review_status
    FROM events v
    JOIN scoped_candidates c USING (candidate_key)
    WHERE v.is_visual_click
      AND c.review_status = 'accepted'
      AND NOT EXISTS (
          SELECT 1
          FROM events genuine
          WHERE genuine.candidate_key = v.candidate_key
            AND genuine.action NOT IN (
                'confirm_group',
                'confirm_not_group',
                'reset_group_review',
                'human_review_pdf_visual'
            )
            AND NOT genuine.is_visual_click
      )
    ORDER BY v.candidate_key, v.id DESC
)
"""


def dry_run_sql(args: argparse.Namespace) -> str:
    return (
        affected_ctes(args)
        + """
SELECT
    a.candidate_key,
    c.question_number,
    c.raw_candidate_json->'metadata'->>'normalized_subject_name' AS subject,
    a.visual_event_id,
    a.visual_event_action,
    a.visual_review_status,
    c.review_status
FROM affected a
JOIN scoped_candidates c USING (candidate_key)
ORDER BY subject,
         CASE WHEN c.question_number ~ '^[0-9]+$' THEN c.question_number::int ELSE 9999 END,
         a.candidate_key;
"""
    )


def apply_sql(args: argparse.Namespace) -> str:
    reviewer = sql_literal(args.reviewer)
    return (
        affected_ctes(args)
        + f"""
, inserted_events AS (
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
        a.candidate_id,
        a.candidate_key,
        {reviewer},
        'reset_review',
        NULL,
        jsonb_build_object(
            'candidate_key', a.candidate_key,
            'reviewer', {reviewer},
            'action', 'reset_review',
            'repair_reason', 'visual_label_was_misclassified_as_question_review',
            'preserved_visual_event_id', a.visual_event_id,
            'preserved_visual_review_status', a.visual_review_status,
            'was_reviewed_before_reset', true
        ),
        '流程修復：舊版圖片頁點擊曾誤成題目通過；保留圖片判讀，題目退回待審。',
        now()
    FROM affected a
    RETURNING candidate_key
),
reset_candidates AS (
    UPDATE exam.question_candidates c
    SET review_status = 'unreviewed',
        updated_at = now()
    FROM inserted_events e
    WHERE c.candidate_key = e.candidate_key
    RETURNING c.candidate_key
),
queued AS (
    INSERT INTO exam.formal_sync_queue (
        candidate_key,
        requested_at,
        attempt_count,
        last_attempt_at,
        last_error,
        processed_at
    )
    SELECT candidate_key, now(), 0, NULL, NULL, NULL
    FROM inserted_events
    ON CONFLICT (candidate_key) DO UPDATE
    SET requested_at = now(),
        attempt_count = 0,
        last_attempt_at = NULL,
        last_error = NULL,
        processed_at = NULL
    RETURNING candidate_key
)
SELECT
    (SELECT count(*) FROM affected) AS affected,
    (SELECT count(*) FROM inserted_events) AS reset_events_appended,
    (SELECT count(*) FROM reset_candidates) AS candidates_reset,
    (SELECT count(*) FROM queued) AS formal_refresh_queued;
"""
    )


def main() -> None:
    args = parse_args()
    sql = apply_sql(args) if args.apply else dry_run_sql(args)
    result = psql(args, sql)
    print(result.stdout.strip())
    if not args.apply:
        print("Dry-run only. Re-run with --apply after taking a database backup.")


if __name__ == "__main__":
    main()
