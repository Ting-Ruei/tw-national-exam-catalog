#!/usr/bin/env python3
"""
Validate the formal SQL question-bank layer before package export.

This is a deterministic release gate. It reads review events and formal tables,
then exits non-zero when export-blocking inconsistencies are found.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GROUP_REVIEW_ACTIONS = ("confirm_group", "confirm_not_group", "reset_group_review")
VALID_ASSET_ROLES = {
    "page_image",
    "figure",
    "stem_figure",
    "table",
    "table_structured",
    "table_manual_screenshot",
    "option_image",
    "source_pdf_region",
    "answer_explanation_image",
    "group_shared_asset",
    "other",
}
VALID_ASSET_QUALITY_STATUSES = {"accepted", "needs_review", "rejected", "unreviewed"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate formal SQL question-bank tables before export.")
    parser.add_argument("--category", default="醫事檢驗師", help="Normalized category name.")
    parser.add_argument("--postgres-db", default=os.environ.get("POSTGRES_DB", "tw_national_exam_dev"))
    parser.add_argument("--postgres-user", default=os.environ.get("POSTGRES_USER", "national_exam"))
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", type=Path, help="Optional report output path.")
    parser.add_argument("--max-examples", type=int, default=20)
    return parser.parse_args()


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def psql_json_lines(args: argparse.Namespace, sql: str) -> list[dict[str, Any]]:
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
        "-At",
        "-c",
        sql,
    ]
    proc = subprocess.run(cmd, cwd=PROJECT_ROOT, text=True, check=True, capture_output=True)
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(proc.stdout.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSON from psql line {line_number}: {exc}") from exc
    return rows


def one_json(args: argparse.Namespace, sql: str) -> dict[str, Any]:
    rows = psql_json_lines(args, sql)
    return rows[0] if rows else {}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def med_questions_cte(category: str) -> str:
    return f"""
WITH med_questions AS (
    SELECT
        q.id AS question_id,
        q.question_key,
        q.question_number,
        q.question_text,
        q.question_json,
        q.question_raw_json,
        q.review_status,
        q.question_group_id,
        q.group_sequence_no,
        od.id AS official_document_id,
        od.registry_key,
        od.official_subject_name,
        c.normalized_category_name,
        s.normalized_subject_name,
        s.canonical_subject_name
    FROM exam.questions q
    JOIN exam.official_documents od ON od.id = q.official_document_id
    JOIN exam.categories c ON c.id = od.category_id
    JOIN exam.subjects s ON s.id = od.subject_id
    WHERE c.normalized_category_name = {sql_literal(category)}
      AND q.review_status = 'accepted'
)
"""


def latest_group_cte(category: str) -> str:
    actions = ", ".join(sql_literal(action) for action in GROUP_REVIEW_ACTIONS)
    return (
        med_questions_cte(category)
        + f""",
latest_group AS (
    SELECT DISTINCT ON (candidate_key)
        candidate_key,
        action,
        event_json,
        id AS event_id,
        created_at
    FROM exam.question_review_events
    WHERE action IN ({actions})
    ORDER BY candidate_key, id DESC
),
confirmed AS (
    SELECT
        mq.*,
        lg.event_json,
        NULLIF(lg.event_json->>'group_ref', '') AS group_ref
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
not_confirmed AS (
    SELECT mq.*, lg.action
    FROM med_questions mq
    JOIN latest_group lg ON lg.candidate_key = mq.question_key
    WHERE lg.action IN ('confirm_not_group', 'reset_group_review')
)
"""
    )


def build_issue(severity: str, code: str, message: str, count: int, examples: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "count": int(count or 0),
        "examples": examples or [],
    }


def limited_examples(args: argparse.Namespace, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return rows[: max(0, args.max_examples)]


def collect_summary(args: argparse.Namespace) -> dict[str, Any]:
    return one_json(
        args,
        med_questions_cte(args.category)
        + """
SELECT jsonb_build_object(
    'accepted_questions', count(*),
    'question_options', COALESCE((SELECT count(*) FROM exam.question_options opt JOIN med_questions mq ON mq.question_id = opt.question_id), 0),
    'answers', COALESCE((SELECT count(*) FROM exam.answers ans JOIN med_questions mq ON mq.question_id = ans.question_id), 0),
    'question_assets', COALESCE((SELECT count(*) FROM exam.question_assets qa JOIN med_questions mq ON mq.question_id = qa.question_id), 0),
    'grouped_questions', count(*) FILTER (WHERE question_group_id IS NOT NULL),
    'confirmed_groups', count(DISTINCT question_group_id) FILTER (WHERE question_group_id IS NOT NULL),
    'normalized_subjects', count(DISTINCT normalized_subject_name),
    'canonical_subjects', count(DISTINCT canonical_subject_name)
)
FROM med_questions;
""",
    )


def collect_required_field_issues(args: argparse.Namespace) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    sql = (
        med_questions_cte(args.category)
        + """
, latest_answer AS (
    SELECT DISTINCT ON (question_id) question_id, answer_value, answer_json
    FROM exam.answers
    ORDER BY question_id, id DESC
),
option_counts AS (
    SELECT question_id, count(*) AS option_count
    FROM exam.question_options
    GROUP BY question_id
),
invalid AS (
    SELECT jsonb_build_object(
        'source_question_key', mq.question_key,
        'source_registry_key', mq.registry_key,
        'question_number', mq.question_number,
        'missing_stem', NULLIF(COALESCE(mq.question_text, mq.question_json->>'stem', ''), '') IS NULL,
        'option_count', COALESCE(oc.option_count, 0),
        'missing_answer', NULLIF(COALESCE(la.answer_value, la.answer_json->>'answer', ''), '') IS NULL
    ) AS item
    FROM med_questions mq
    LEFT JOIN option_counts oc ON oc.question_id = mq.question_id
    LEFT JOIN latest_answer la ON la.question_id = mq.question_id
    WHERE NULLIF(COALESCE(mq.question_text, mq.question_json->>'stem', ''), '') IS NULL
       OR COALESCE(oc.option_count, 0) = 0
       OR NULLIF(COALESCE(la.answer_value, la.answer_json->>'answer', ''), '') IS NULL
)
SELECT item FROM invalid ORDER BY item->>'source_question_key';
"""
    )
    rows = psql_json_lines(args, sql)
    if rows:
        issues.append(build_issue("error", "formal_required_fields_missing", "Formal accepted questions are missing stem, options, or answer.", len(rows), limited_examples(args, rows)))

    dup_option_rows = psql_json_lines(
        args,
        med_questions_cte(args.category)
        + """
, option_keys AS (
    SELECT
        mq.question_key,
        upper(NULLIF(opt->>'key', '')) AS option_key
    FROM med_questions mq
    CROSS JOIN LATERAL jsonb_array_elements(COALESCE(mq.question_json->'options', '[]'::jsonb)) AS opt
),
dups AS (
    SELECT question_key, option_key, count(*) AS duplicate_count
    FROM option_keys
    WHERE option_key IS NOT NULL
    GROUP BY question_key, option_key
    HAVING count(*) > 1
)
SELECT jsonb_build_object('source_question_key', question_key, 'option_key', option_key, 'duplicate_count', duplicate_count)
FROM dups
ORDER BY question_key, option_key;
""",
    )
    if dup_option_rows:
        issues.append(build_issue("error", "formal_question_json_duplicate_option_keys", "question_json.options contains duplicate option keys.", len(dup_option_rows), limited_examples(args, dup_option_rows)))
    return issues


def collect_group_issues(args: argparse.Namespace) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    group_summary = one_json(
        args,
        latest_group_cte(args.category)
        + """
SELECT jsonb_build_object(
    'confirmed_questions', (SELECT count(*) FROM expected),
    'confirmed_groups', (SELECT count(DISTINCT expected_group_key) FROM expected),
    'missing_group_records', (
        SELECT count(*)
        FROM expected e
        LEFT JOIN exam.question_groups g ON g.group_key = e.expected_group_key
        WHERE g.id IS NULL
    ),
    'group_link_mismatches', (
        SELECT count(*)
        FROM expected e
        JOIN exam.question_groups g ON g.group_key = e.expected_group_key
        WHERE e.question_group_id IS DISTINCT FROM g.id
           OR e.group_sequence_no IS DISTINCT FROM e.expected_sequence_no
    ),
    'question_json_group_ref_mismatches', (
        SELECT count(*)
        FROM expected e
        WHERE NULLIF(e.question_json->>'group_ref', '') IS DISTINCT FROM e.group_ref
    ),
    'stale_nonconfirmed_links', (
        SELECT count(*)
        FROM not_confirmed
        WHERE question_group_id IS NOT NULL
    )
);
""",
    )
    for key, code, message in [
        ("missing_group_records", "formal_group_record_missing", "Latest confirm_group events refer to missing exam.question_groups records."),
        ("group_link_mismatches", "formal_group_link_mismatch", "Latest confirm_group events do not match formal question_group_id/group_sequence_no."),
        ("question_json_group_ref_mismatches", "formal_question_json_group_ref_mismatch", "question_json.group_ref does not match latest confirmed group_ref."),
        ("stale_nonconfirmed_links", "formal_stale_nonconfirmed_group_link", "Latest confirm_not_group/reset_group_review still has formal group links."),
    ]:
        if int(group_summary.get(key) or 0):
            examples = psql_json_lines(
                args,
                latest_group_cte(args.category)
                + """
SELECT jsonb_build_object(
    'source_question_key', e.question_key,
    'source_registry_key', e.registry_key,
    'question_number', e.question_number,
    'expected_group_key', e.expected_group_key,
    'expected_sequence_no', e.expected_sequence_no,
    'formal_group_id', e.question_group_id,
    'formal_sequence_no', e.group_sequence_no,
    'question_json_group_ref', e.question_json->>'group_ref'
)
FROM expected e
LEFT JOIN exam.question_groups g ON g.group_key = e.expected_group_key
WHERE g.id IS NULL
   OR e.question_group_id IS DISTINCT FROM g.id
   OR e.group_sequence_no IS DISTINCT FROM e.expected_sequence_no
   OR NULLIF(e.question_json->>'group_ref', '') IS DISTINCT FROM e.group_ref
ORDER BY e.registry_key, e.expected_group_key, e.expected_sequence_no
LIMIT 50;
""",
            )
            issues.append(build_issue("error", code, message, int(group_summary[key]), limited_examples(args, examples)))

    seq_rows = psql_json_lines(
        args,
        med_questions_cte(args.category)
        + """
SELECT jsonb_build_object(
    'group_key', g.group_key,
    'member_count', count(*),
    'question_numbers', array_agg(mq.question_number ORDER BY CASE WHEN mq.question_number ~ '^[0-9]+$' THEN mq.question_number::integer ELSE 9999 END),
    'sequence_numbers', array_agg(mq.group_sequence_no ORDER BY mq.group_sequence_no NULLS LAST)
)
FROM med_questions mq
JOIN exam.question_groups g ON g.id = mq.question_group_id
GROUP BY g.group_key
HAVING count(*) < 2
    OR count(*) <> count(DISTINCT mq.group_sequence_no)
    OR min(mq.group_sequence_no) <> 1
    OR max(mq.group_sequence_no) <> count(*)
ORDER BY g.group_key;
""",
    )
    if seq_rows:
        issues.append(build_issue("error", "formal_group_sequence_invalid", "Formal group members must have at least two questions and contiguous sequence numbers.", len(seq_rows), limited_examples(args, seq_rows)))

    registry_rows = psql_json_lines(
        args,
        med_questions_cte(args.category)
        + """
SELECT jsonb_build_object('source_registry_key', mq.registry_key, 'group_key', g.group_key, 'member_count', count(*))
FROM med_questions mq
JOIN exam.question_groups g ON g.id = mq.question_group_id
WHERE g.group_key NOT LIKE mq.registry_key || ':%'
GROUP BY mq.registry_key, g.group_key
ORDER BY mq.registry_key, g.group_key;
""",
    )
    if registry_rows:
        issues.append(build_issue("error", "formal_group_registry_mismatch", "Group keys must remain scoped to the same source_registry_key as their member questions.", len(registry_rows), limited_examples(args, registry_rows)))
    return issues


def collect_subject_issues(args: argparse.Namespace) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    missing_canonical = psql_json_lines(
        args,
        med_questions_cte(args.category)
        + """
SELECT jsonb_build_object(
    'official_subject_name', official_subject_name,
    'normalized_subject_name', normalized_subject_name,
    'question_count', count(*)
)
FROM med_questions
WHERE NULLIF(canonical_subject_name, '') IS NULL
GROUP BY official_subject_name, normalized_subject_name
ORDER BY official_subject_name, normalized_subject_name;
""",
    )
    if missing_canonical:
        issues.append(build_issue("error", "formal_subject_canonical_missing", "Accepted questions must have canonical_subject_name for platform mapping.", len(missing_canonical), limited_examples(args, missing_canonical)))

    missing_mapping_note = psql_json_lines(
        args,
        f"""
WITH rows AS (
    SELECT
        c.official_category_name,
        c.group_name,
        s.official_subject_name,
        s.normalized_subject_name,
        s.canonical_subject_name,
        count(*) AS question_count,
        max(csm.change_note) AS subject_mapping_note
    FROM exam.questions q
    JOIN exam.official_documents od ON od.id = q.official_document_id
    JOIN exam.categories c ON c.id = od.category_id
    JOIN exam.subjects s ON s.id = od.subject_id
    LEFT JOIN exam.canonical_subject_mappings csm
        ON csm.category_group_name = c.group_name
        AND csm.official_category_name = c.official_category_name
        AND csm.official_subject_name = s.official_subject_name
        AND csm.canonical_subject_name = s.canonical_subject_name
    WHERE c.normalized_category_name = {sql_literal(args.category)}
      AND q.review_status = 'accepted'
      AND NULLIF(s.canonical_subject_name, '') IS NOT NULL
    GROUP BY c.official_category_name, c.group_name, s.official_subject_name, s.normalized_subject_name, s.canonical_subject_name
)
SELECT jsonb_build_object(
    'official_subject_name', official_subject_name,
    'normalized_subject_name', normalized_subject_name,
    'canonical_subject_name', canonical_subject_name,
    'question_count', question_count
)
FROM rows
WHERE NULLIF(subject_mapping_note, '') IS NULL
ORDER BY official_subject_name, normalized_subject_name;
""",
    )
    if missing_mapping_note:
        issues.append(build_issue("warning", "formal_subject_mapping_note_missing", "Canonical subject rows should explain historical official-name mapping.", len(missing_mapping_note), limited_examples(args, missing_mapping_note)))
    return issues


def collect_asset_issues(args: argparse.Namespace) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    asset_rows = psql_json_lines(
        args,
        med_questions_cte(args.category)
        + """
SELECT jsonb_build_object(
    'source_question_key', mq.question_key,
    'asset_key', a.asset_key,
    'asset_path', a.asset_path,
    'relative_asset_path', a.relative_asset_path,
    'sha256', a.sha256,
    'role', qa.role,
    'asset_quality_status', qa.asset_quality_status
)
FROM med_questions mq
JOIN exam.question_assets qa ON qa.question_id = mq.question_id
JOIN exam.assets a ON a.id = qa.asset_id
ORDER BY mq.question_key, qa.display_order NULLS LAST, a.asset_key;
""",
    )
    missing_file: list[dict[str, Any]] = []
    bad_sha: list[dict[str, Any]] = []
    bad_metadata: list[dict[str, Any]] = []
    missing_sha: list[dict[str, Any]] = []
    for row in asset_rows:
        path_text = str(row.get("asset_path") or row.get("relative_asset_path") or "").strip()
        role = str(row.get("role") or "")
        status = str(row.get("asset_quality_status") or "")
        if not path_text or role not in VALID_ASSET_ROLES or status not in VALID_ASSET_QUALITY_STATUSES:
            bad_metadata.append(row)
        if not str(row.get("sha256") or "").strip():
            missing_sha.append(row)
        if not path_text:
            continue
        path = Path(path_text)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.exists():
            missing_file.append(row)
            continue
        expected_sha = str(row.get("sha256") or "").strip()
        if expected_sha and sha256_file(path) != expected_sha:
            bad_sha.append(row)
    if bad_metadata:
        issues.append(build_issue("error", "formal_asset_metadata_invalid", "Question assets must have valid path, role, and asset_quality_status.", len(bad_metadata), limited_examples(args, bad_metadata)))
    if missing_file:
        issues.append(build_issue("error", "formal_asset_file_missing", "Formal asset paths must exist before package export.", len(missing_file), limited_examples(args, missing_file)))
    if bad_sha:
        issues.append(build_issue("error", "formal_asset_sha256_mismatch", "Stored asset sha256 does not match the file on disk.", len(bad_sha), limited_examples(args, bad_sha)))
    if missing_sha:
        issues.append(build_issue("warning", "formal_asset_sha256_missing", "Formal asset rows should store sha256; exporter can compute package hashes but lineage is weaker.", len(missing_sha), limited_examples(args, missing_sha)))
    duplicate_refs = psql_json_lines(
        args,
        med_questions_cte(args.category)
        + """
SELECT jsonb_build_object(
    'source_question_key', mq.question_key,
    'normalized_asset_path', COALESCE(NULLIF(a.relative_asset_path, ''), a.asset_path),
    'role', qa.role,
    'duplicate_count', count(*),
    'asset_keys', array_agg(a.asset_key ORDER BY a.asset_key)
)
FROM med_questions mq
JOIN exam.question_assets qa ON qa.question_id = mq.question_id
JOIN exam.assets a ON a.id = qa.asset_id
GROUP BY mq.question_key, COALESCE(NULLIF(a.relative_asset_path, ''), a.asset_path), qa.role
HAVING count(*) > 1
ORDER BY mq.question_key, COALESCE(NULLIF(a.relative_asset_path, ''), a.asset_path), qa.role;
""",
    )
    if duplicate_refs:
        issues.append(build_issue("warning", "formal_duplicate_question_asset_ref", "Formal question_assets contains duplicate same-question/same-path/same-role links; exporter may collapse these, but formal lineage should eventually be deduped.", len(duplicate_refs), limited_examples(args, duplicate_refs)))
    return issues


def collect_duplicate_content_report(args: argparse.Namespace) -> list[dict[str, Any]]:
    rows = psql_json_lines(
        args,
        med_questions_cte(args.category)
        + """
SELECT jsonb_build_object(
    'source_content_hash', mq.question_json->'metadata'->>'source_content_hash',
    'question_count', count(*),
    'source_question_keys', array_agg(mq.question_key ORDER BY mq.question_key)
)
FROM med_questions mq
WHERE NULLIF(mq.question_json->'metadata'->>'source_content_hash', '') IS NOT NULL
GROUP BY mq.question_json->'metadata'->>'source_content_hash'
HAVING count(*) > 1
ORDER BY count(*) DESC, mq.question_json->'metadata'->>'source_content_hash';
""",
    )
    if not rows:
        return []
    return [
        build_issue(
            "warning",
            "formal_duplicate_content_hash",
            "Duplicate content hashes were found; record as reused/duplicate lineage unless human review says otherwise.",
            len(rows),
            limited_examples(args, rows),
        )
    ]


def render_text(report: dict[str, Any]) -> str:
    lines = [
        f"Formal question-bank validation: {report['status']}",
        f"category: {report['category']}",
        "summary:",
    ]
    for key, value in sorted(report.get("summary", {}).items()):
        lines.append(f"  - {key}: {value}")
    if report["issues"]:
        lines.append("issues:")
        for issue in report["issues"]:
            lines.append(f"  - [{issue['severity']}] {issue['code']}: {issue['count']} - {issue['message']}")
            for example in issue.get("examples", [])[:5]:
                lines.append(f"      {json.dumps(example, ensure_ascii=False, sort_keys=True)}")
            if len(issue.get("examples", [])) > 5:
                lines.append("      ...")
    else:
        lines.append("issues: none")
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    issues: list[dict[str, Any]] = []
    summary = collect_summary(args)
    issues.extend(collect_required_field_issues(args))
    issues.extend(collect_group_issues(args))
    issues.extend(collect_subject_issues(args))
    issues.extend(collect_asset_issues(args))
    issues.extend(collect_duplicate_content_report(args))
    error_count = sum(1 for issue in issues if issue["severity"] == "error" and issue["count"] > 0)
    warning_count = sum(1 for issue in issues if issue["severity"] == "warning" and issue["count"] > 0)
    report = {
        "status": "fail" if error_count else "pass",
        "category": args.category,
        "summary": summary,
        "error_count": error_count,
        "warning_count": warning_count,
        "issues": issues,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n" if args.format == "json" else render_text(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if error_count:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
