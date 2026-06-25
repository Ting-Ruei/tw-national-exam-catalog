#!/usr/bin/env python3
"""
Promote reviewed question candidates into formal PostgreSQL question tables.

This script only promotes rows that pass preflight. It merges human question
corrections and answer-review corrections before writing formal tables.
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

import preflight_formal_ingest as preflight


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATE_ROOT = PROJECT_ROOT / "國考題資料夾" / "30_normalized_items" / "question_candidates"
QUESTION_READY_ACTIONS = {"accept", "unblock"}
ANSWER_READY_ACTIONS = {"accept", "unblock"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Promote ready reviewed candidates into formal PostgreSQL tables.")
    parser.add_argument("--candidate-jsonl", type=Path)
    parser.add_argument("--issue-csv", type=Path)
    parser.add_argument("--question-review-log", type=Path)
    parser.add_argument("--answer-review-log", type=Path)
    parser.add_argument("--category", help="Filter by normalized category name.")
    parser.add_argument("--subject", help="Filter by normalized subject name.")
    parser.add_argument("--year", help="Filter by ROC year.")
    parser.add_argument("--exam-ordinal", help="Filter by exam ordinal.")
    parser.add_argument("--postgres-db", default=os.environ.get("POSTGRES_DB", "tw_national_exam_dev"))
    parser.add_argument("--postgres-user", default=os.environ.get("POSTGRES_USER", "national_exam"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


def latest_path(pattern: str) -> Path:
    paths = sorted(DEFAULT_CANDIDATE_ROOT.glob(pattern))
    if not paths:
        raise SystemExit(f"No candidate output found: {DEFAULT_CANDIDATE_ROOT}/{pattern}")
    return paths[-1]


def resolve_inputs(args: argparse.Namespace) -> tuple[Path, Path, Path, Path]:
    candidate_path = args.candidate_jsonl or latest_path("*/question_candidates__*.jsonl")
    issue_path = args.issue_csv or candidate_path.with_name(
        candidate_path.name.replace("question_candidates__", "question_parse_issues__").replace(".jsonl", ".csv")
    )
    if not issue_path.exists():
        issue_path = args.issue_csv or latest_path("*/question_parse_issues__*.csv")
    question_review_log = args.question_review_log or candidate_path.parent / "question_review_events.jsonl"
    answer_review_log = args.answer_review_log or candidate_path.parent / "answer_review_events.jsonl"
    return candidate_path, issue_path, question_review_log, answer_review_log


def read_jsonl(path: Path) -> list[dict[str, Any]]:
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
        if exc.stdout:
            print(exc.stdout)
        if exc.stderr:
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


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if value is not None else ""


def answer_values_from_text(value: str) -> list[str]:
    value = value.strip()
    if not value:
        return []
    if "|" in value:
        return [part.strip() for part in value.split("|") if part.strip()]
    if "+" in value:
        return [value]
    return [value]


def reviewed_answer(candidate: dict[str, Any], answer_event: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    corrected = str(answer_event.get("corrected_answer") or "").strip()
    reviewed_payload = answer_event.get("reviewed_answer") if isinstance(answer_event.get("reviewed_answer"), dict) else {}
    if corrected:
        value = corrected
    else:
        value = str(reviewed_payload.get("answer") or candidate.get("answer") or "").strip()
    payload = dict(candidate.get("answer_payload") or {})
    payload.update(reviewed_payload)
    if value:
        payload["answer"] = value
        payload["accepted_values"] = answer_values_from_text(value)
    payload["answer_review_event"] = {
        "action": answer_event.get("action"),
        "created_at": answer_event.get("created_at"),
        "notes": answer_event.get("notes"),
        "sheet_action": answer_event.get("sheet_action"),
    }
    return value, payload


def official_answer_registry_key(candidate: dict[str, Any], answer_event: dict[str, Any]) -> str:
    event_key = str(answer_event.get("answer_source_registry_key") or "")
    if "|" in event_key:
        event_key = event_key.split("|", 1)[0]
    return event_key or str(candidate.get("answer_source_registry_key") or "")


def asset_type_for_ref(ref: dict[str, Any]) -> str:
    role = str(ref.get("asset_role") or ref.get("role") or "").lower()
    mime = str(ref.get("mime_type") or "").lower()
    if "table" in role:
        return "table_image"
    if mime.startswith("image/") or "image" in role or "figure" in role or "screenshot" in role:
        return "question_image"
    return "other"


def question_asset_role(ref: dict[str, Any]) -> str:
    role = str(ref.get("asset_role") or ref.get("role") or "").lower()
    if "table_manual" in role or ("table" in role and "manual" in role):
        return "table_manual_screenshot"
    if "table" in role:
        return "table"
    if "option" in role:
        return "option_image"
    if "stem" in role or "formula" in role or "figure" in role:
        return "stem_figure"
    return "figure"


def iter_asset_refs(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for ref in candidate.get("image_refs") or []:
        if isinstance(ref, str):
            refs.append({"path": ref, "raw_ref": ref})
        elif isinstance(ref, dict):
            refs.append(ref)
    stem_image = candidate.get("stem_image")
    if isinstance(stem_image, str):
        refs.append({"path": stem_image, "asset_role": "stem_figure", "raw_ref": stem_image})
    elif isinstance(stem_image, dict):
        refs.append(stem_image)
    for option in candidate.get("options") or []:
        if isinstance(option, dict) and option.get("image"):
            image = option["image"]
            if isinstance(image, str):
                refs.append({"path": image, "asset_role": "option_image", "raw_ref": image, "option_label": option.get("key")})
            elif isinstance(image, dict):
                image = dict(image)
                image.setdefault("asset_role", "option_image")
                image.setdefault("option_label", option.get("key"))
                refs.append(image)
    return refs


def build_rows(
    candidates: list[dict[str, Any]],
    issues: dict[str, list[dict[str, str]]],
    question_reviews: dict[str, dict[str, Any]],
    question_resets: dict[str, dict[str, Any]],
    answer_reviews: dict[str, dict[str, Any]],
    answer_resets: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], list[dict[str, Any]]]:
    question_rows: list[dict[str, object]] = []
    option_rows: list[dict[str, object]] = []
    answer_rows: list[dict[str, object]] = []
    asset_rows: list[dict[str, object]] = []
    skipped: list[dict[str, Any]] = []

    for candidate in candidates:
        key = candidate.get("candidate_key") or ""
        preflight_row = preflight.evaluate_candidate(candidate, issues, question_reviews, question_resets, answer_reviews, answer_resets)
        if preflight_row["status"] != "ready":
            skipped.append(preflight_row)
            continue
        question_event = question_reviews.get(key) or {}
        answer_event = answer_reviews.get(key) or {}
        if question_event.get("action") not in QUESTION_READY_ACTIONS or answer_event.get("action") not in ANSWER_READY_ACTIONS:
            skipped.append({**preflight_row, "status": "blocked", "reasons": "latest_review_not_ready"})
            continue
        correction = question_event.get("correction") or question_event.get("corrected_candidate_json") or {}
        effective = preflight.apply_review_correction(candidate, correction if isinstance(correction, dict) else None)
        metadata = candidate.get("metadata") or {}
        question_json = {
            "candidate_key": key,
            "canonical_question_key": candidate.get("canonical_question_key"),
            "metadata": metadata,
            "stem": effective.get("stem"),
            "stem_markup": effective.get("stem_markup"),
            "options": effective.get("options") or [],
            "group_ref": effective.get("group_ref"),
            "image_refs": effective.get("image_refs") or [],
            "preflight_warnings": preflight_row.get("warnings"),
        }
        question_rows.append(
            {
                "source_registry_key": candidate.get("source_registry_key") or "",
                "question_key": key,
                "question_number": str(candidate.get("question_number") or ""),
                "question_text": effective.get("stem") or "",
                "normalized_text": effective.get("stem") or "",
                "display_text": effective.get("stem") or "",
                "question_markup_json": json_dump(effective.get("stem_markup")) if effective.get("stem_markup") else "",
                "question_raw_json": json_dump(candidate),
                "human_corrected_json": json_dump(correction) if correction else "",
                "question_json": json_dump(question_json),
                "parser_version": metadata.get("parser_version") or "unknown",
                "review_status": "accepted",
                "group_key": effective.get("group_ref") or "",
            }
        )
        for option in effective.get("options") or []:
            if not isinstance(option, dict):
                continue
            label = str(option.get("key") or option.get("label") or "").strip().upper()
            if not label:
                continue
            option_rows.append(
                {
                    "question_key": key,
                    "option_label": label,
                    "option_text": option.get("text") or "",
                    "normalized_text": option.get("text") or "",
                    "display_text": option.get("text") or "",
                    "option_markup_json": json_dump(option.get("markup")) if option.get("markup") else "",
                    "option_raw_json": json_dump(option),
                    "human_corrected_json": json_dump(option) if correction.get("options") else "",
                    "option_json": json_dump(option),
                }
            )
        answer_value, answer_json = reviewed_answer(candidate, answer_event)
        answer_source_key = official_answer_registry_key(candidate, answer_event)
        answer_rows.append(
            {
                "question_key": key,
                "answer_source_registry_key": answer_source_key,
                "answer_value": answer_value,
                "answer_json": json_dump(answer_json),
                "is_correction": "true" if metadata.get("answer_role_primary") == "correction" else "false",
            }
        )
        for index, ref in enumerate(iter_asset_refs(effective), start=1):
            path = str(ref.get("path") or ref.get("path_relative") or ref.get("relative_path") or ref.get("raw_ref") or "").strip()
            if not path:
                continue
            asset_key = str(ref.get("asset_key") or f"{key}:asset:{index:03d}")
            asset_rows.append(
                {
                    "question_key": key,
                    "asset_key": asset_key,
                    "asset_type": asset_type_for_ref(ref),
                    "asset_path": path,
                    "relative_asset_path": ref.get("path_relative") or ref.get("relative_path") or path,
                    "mime_type": ref.get("mime_type") or "",
                    "role": question_asset_role(ref),
                    "display_order": index,
                    "asset_json": json_dump(ref),
                }
            )
    return question_rows, option_rows, answer_rows, asset_rows, skipped


def create_staging(args: argparse.Namespace) -> None:
    psql(
        args,
        """
CREATE SCHEMA IF NOT EXISTS exam_staging;

ALTER TABLE exam.questions
    ADD COLUMN IF NOT EXISTS normalized_text TEXT,
    ADD COLUMN IF NOT EXISTS display_text TEXT,
    ADD COLUMN IF NOT EXISTS question_markup_json JSONB,
    ADD COLUMN IF NOT EXISTS question_raw_json JSONB,
    ADD COLUMN IF NOT EXISTS human_corrected_json JSONB,
    ADD COLUMN IF NOT EXISTS source_page_start INTEGER,
    ADD COLUMN IF NOT EXISTS source_page_end INTEGER,
    ADD COLUMN IF NOT EXISTS source_bbox JSONB,
    ADD COLUMN IF NOT EXISTS parse_confidence NUMERIC(5,4);

ALTER TABLE exam.question_options
    ADD COLUMN IF NOT EXISTS normalized_text TEXT,
    ADD COLUMN IF NOT EXISTS display_text TEXT,
    ADD COLUMN IF NOT EXISTS option_markup_json JSONB,
    ADD COLUMN IF NOT EXISTS option_raw_json JSONB,
    ADD COLUMN IF NOT EXISTS human_corrected_json JSONB;

ALTER TABLE exam.question_groups
    ADD COLUMN IF NOT EXISTS display_markup_json JSONB,
    ADD COLUMN IF NOT EXISTS asset_policy_json JSONB,
    ADD COLUMN IF NOT EXISTS source_page_start INTEGER,
    ADD COLUMN IF NOT EXISTS source_page_end INTEGER,
    ADD COLUMN IF NOT EXISTS source_bbox JSONB,
    ADD COLUMN IF NOT EXISTS group_question_range TEXT;

ALTER TABLE exam.question_assets
    ADD COLUMN IF NOT EXISTS source_mineru_block_id TEXT,
    ADD COLUMN IF NOT EXISTS display_order INTEGER,
    ADD COLUMN IF NOT EXISTS asset_quality_status TEXT NOT NULL DEFAULT 'unreviewed';

ALTER TABLE exam.question_assets
    DROP CONSTRAINT IF EXISTS question_assets_role_check;

ALTER TABLE exam.question_assets
    ADD CONSTRAINT question_assets_role_check
    CHECK (role IN (
        'page_image',
        'figure',
        'stem_figure',
        'table',
        'table_structured',
        'table_manual_screenshot',
        'option_image',
        'source_pdf_region',
        'answer_explanation_image',
        'group_shared_asset',
        'other'
    ));

CREATE TABLE IF NOT EXISTS exam_staging.formal_questions (
    source_registry_key TEXT,
    question_key TEXT,
    question_number TEXT,
    question_text TEXT,
    normalized_text TEXT,
    display_text TEXT,
    question_markup_json TEXT,
    question_raw_json TEXT,
    human_corrected_json TEXT,
    question_json TEXT,
    parser_version TEXT,
    review_status TEXT,
    group_key TEXT
);

CREATE TABLE IF NOT EXISTS exam_staging.formal_question_options (
    question_key TEXT,
    option_label TEXT,
    option_text TEXT,
    normalized_text TEXT,
    display_text TEXT,
    option_markup_json TEXT,
    option_raw_json TEXT,
    human_corrected_json TEXT,
    option_json TEXT
);

CREATE TABLE IF NOT EXISTS exam_staging.formal_answers (
    question_key TEXT,
    answer_source_registry_key TEXT,
    answer_value TEXT,
    answer_json TEXT,
    is_correction TEXT
);

CREATE TABLE IF NOT EXISTS exam_staging.formal_question_assets (
    question_key TEXT,
    asset_key TEXT,
    asset_type TEXT,
    asset_path TEXT,
    relative_asset_path TEXT,
    mime_type TEXT,
    role TEXT,
    display_order TEXT,
    asset_json TEXT
);

TRUNCATE
    exam_staging.formal_questions,
    exam_staging.formal_question_options,
    exam_staging.formal_answers,
    exam_staging.formal_question_assets;
""",
    )


def apply_upserts(args: argparse.Namespace) -> None:
    psql(
        args,
        """
INSERT INTO exam.question_groups (
    official_document_id,
    group_key,
    shared_stem_json,
    review_status
)
SELECT DISTINCT
    od.id,
    s.group_key,
    jsonb_build_object('group_ref', s.group_key),
    'accepted'
FROM exam_staging.formal_questions s
JOIN exam.official_documents od ON od.registry_key = s.source_registry_key
WHERE NULLIF(s.group_key, '') IS NOT NULL
ON CONFLICT (group_key) DO UPDATE
SET shared_stem_json = EXCLUDED.shared_stem_json,
    review_status = EXCLUDED.review_status;

INSERT INTO exam.questions (
    official_document_id,
    question_group_id,
    question_key,
    question_number,
    question_text,
    normalized_text,
    display_text,
    question_markup_json,
    question_raw_json,
    human_corrected_json,
    question_json,
    parser_version,
    review_status
)
SELECT
    od.id,
    g.id,
    s.question_key,
    s.question_number,
    NULLIF(s.question_text, ''),
    NULLIF(s.normalized_text, ''),
    NULLIF(s.display_text, ''),
    NULLIF(s.question_markup_json, '')::jsonb,
    s.question_raw_json::jsonb,
    NULLIF(s.human_corrected_json, '')::jsonb,
    s.question_json::jsonb,
    s.parser_version,
    s.review_status
FROM exam_staging.formal_questions s
JOIN exam.official_documents od ON od.registry_key = s.source_registry_key
LEFT JOIN exam.question_groups g ON g.group_key = NULLIF(s.group_key, '')
ON CONFLICT (question_key) DO UPDATE
SET official_document_id = EXCLUDED.official_document_id,
    question_group_id = EXCLUDED.question_group_id,
    question_number = EXCLUDED.question_number,
    question_text = EXCLUDED.question_text,
    normalized_text = EXCLUDED.normalized_text,
    display_text = EXCLUDED.display_text,
    question_markup_json = EXCLUDED.question_markup_json,
    question_raw_json = EXCLUDED.question_raw_json,
    human_corrected_json = EXCLUDED.human_corrected_json,
    question_json = EXCLUDED.question_json,
    parser_version = EXCLUDED.parser_version,
    review_status = EXCLUDED.review_status;

INSERT INTO exam.question_options (
    question_id,
    option_label,
    option_text,
    normalized_text,
    display_text,
    option_markup_json,
    option_raw_json,
    human_corrected_json,
    option_json
)
SELECT
    q.id,
    s.option_label,
    NULLIF(s.option_text, ''),
    NULLIF(s.normalized_text, ''),
    NULLIF(s.display_text, ''),
    NULLIF(s.option_markup_json, '')::jsonb,
    s.option_raw_json::jsonb,
    NULLIF(s.human_corrected_json, '')::jsonb,
    s.option_json::jsonb
FROM exam_staging.formal_question_options s
JOIN exam.questions q ON q.question_key = s.question_key
ON CONFLICT (question_id, option_label) DO UPDATE
SET option_text = EXCLUDED.option_text,
    normalized_text = EXCLUDED.normalized_text,
    display_text = EXCLUDED.display_text,
    option_markup_json = EXCLUDED.option_markup_json,
    option_raw_json = EXCLUDED.option_raw_json,
    human_corrected_json = EXCLUDED.human_corrected_json,
    option_json = EXCLUDED.option_json;

DELETE FROM exam.answers a
USING exam.questions q
JOIN exam_staging.formal_answers s ON s.question_key = q.question_key
WHERE a.question_id = q.id;

INSERT INTO exam.answers (
    question_id,
    answer_source_document_id,
    answer_value,
    answer_json,
    is_correction
)
SELECT
    q.id,
    answer_doc.id,
    NULLIF(s.answer_value, ''),
    s.answer_json::jsonb,
    lower(s.is_correction) = 'true'
FROM exam_staging.formal_answers s
JOIN exam.questions q ON q.question_key = s.question_key
LEFT JOIN exam.official_documents answer_doc ON answer_doc.registry_key = NULLIF(s.answer_source_registry_key, '');

INSERT INTO exam.assets (
    asset_key,
    asset_type,
    asset_path,
    relative_asset_path,
    mime_type
)
SELECT DISTINCT
    s.asset_key,
    s.asset_type,
    s.asset_path,
    NULLIF(s.relative_asset_path, ''),
    NULLIF(s.mime_type, '')
FROM exam_staging.formal_question_assets s
WHERE NULLIF(s.asset_key, '') IS NOT NULL
ON CONFLICT (asset_key) DO UPDATE
SET asset_type = EXCLUDED.asset_type,
    asset_path = EXCLUDED.asset_path,
    relative_asset_path = EXCLUDED.relative_asset_path,
    mime_type = EXCLUDED.mime_type;

INSERT INTO exam.question_assets (
    question_id,
    asset_id,
    role,
    display_order,
    asset_quality_status
)
SELECT
    q.id,
    a.id,
    s.role,
    NULLIF(s.display_order, '')::integer,
    'accepted'
FROM exam_staging.formal_question_assets s
JOIN exam.questions q ON q.question_key = s.question_key
JOIN exam.assets a ON a.asset_key = s.asset_key
ON CONFLICT (question_id, asset_id, role) DO UPDATE
SET display_order = EXCLUDED.display_order,
    asset_quality_status = EXCLUDED.asset_quality_status;
""",
    )


def print_db_summary(args: argparse.Namespace) -> None:
    result = psql(
        args,
        """
SELECT count(*) AS promoted_questions
FROM exam.questions q
JOIN exam_staging.formal_questions s ON s.question_key = q.question_key;

SELECT count(*) AS promoted_options
FROM exam.question_options o
JOIN exam.questions q ON q.id = o.question_id
JOIN exam_staging.formal_questions s ON s.question_key = q.question_key;

SELECT count(*) AS promoted_answers
FROM exam.answers a
JOIN exam.questions q ON q.id = a.question_id
JOIN exam_staging.formal_questions s ON s.question_key = q.question_key;

SELECT count(*) AS promoted_question_assets
FROM exam.question_assets qa
JOIN exam.questions q ON q.id = qa.question_id
JOIN exam_staging.formal_questions s ON s.question_key = q.question_key;
""",
    )
    print(result.stdout)


def main() -> None:
    args = parse_args()
    candidate_path, issue_path, question_review_log, answer_review_log = resolve_inputs(args)
    candidates = [
        candidate
        for candidate in read_jsonl(candidate_path)
        if preflight.candidate_matches_filters(candidate, args)
    ]
    if args.limit:
        candidates = candidates[: args.limit]
    issues = preflight.load_issues(issue_path)
    question_reviews, question_resets = preflight.load_latest_events(question_review_log)
    answer_reviews, answer_resets = preflight.load_latest_events(answer_review_log)
    question_rows, option_rows, answer_rows, asset_rows, skipped = build_rows(
        candidates,
        issues,
        question_reviews,
        question_resets,
        answer_reviews,
        answer_resets,
    )
    summary = {
        "candidate_jsonl": str(candidate_path),
        "issue_csv": str(issue_path),
        "question_review_log": str(question_review_log),
        "answer_review_log": str(answer_review_log),
        "candidate_count": len(candidates),
        "promotable_questions": len(question_rows),
        "promotable_options": len(option_rows),
        "promotable_answers": len(answer_rows),
        "promotable_assets": len(asset_rows),
        "skipped_count": len(skipped),
        "dry_run": args.dry_run,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if skipped:
        print("Skipped sample:", json.dumps(skipped[:5], ensure_ascii=False, indent=2, sort_keys=True))
    if args.dry_run:
        return

    create_staging(args)
    copy_table(args, "exam_staging.formal_questions", question_rows, list(question_rows[0].keys()) if question_rows else [])
    copy_table(args, "exam_staging.formal_question_options", option_rows, list(option_rows[0].keys()) if option_rows else [])
    copy_table(args, "exam_staging.formal_answers", answer_rows, list(answer_rows[0].keys()) if answer_rows else [])
    copy_table(args, "exam_staging.formal_question_assets", asset_rows, list(asset_rows[0].keys()) if asset_rows else [])
    apply_upserts(args)
    print_db_summary(args)


if __name__ == "__main__":
    main()
