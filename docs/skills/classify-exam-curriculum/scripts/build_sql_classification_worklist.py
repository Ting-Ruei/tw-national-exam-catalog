#!/usr/bin/env python3
"""Export accepted formal questions as read-only curriculum-classification tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from taxonomy_support import load_taxonomies, subject_taxonomy_map

PROJECT_ROOT = Path(__file__).resolve().parents[4]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category", default="醫事檢驗師")
    parser.add_argument("--subject", action="append", help="Repeat to export more than one subject; defaults to all subjects in the taxonomy.")
    parser.add_argument("--year", type=int)
    parser.add_argument("--limit", type=int, default=0, help="0 means no limit")
    parser.add_argument("--taxonomy", type=Path, action="append", help="Repeat to override the default taxonomy set.")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    return parser.parse_args()


def stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def main() -> None:
    args = parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL or --database-url is required")
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "psycopg is required; run this script in the review-ui/project environment "
            "or install requirements/review-ui.txt"
        ) from exc
    taxonomies = load_taxonomies(args.taxonomy)
    taxonomy_by_subject = subject_taxonomy_map(taxonomies)
    allowed_subjects = sorted(taxonomy_by_subject)
    subjects = args.subject or allowed_subjects
    unknown = sorted(set(subjects) - set(allowed_subjects))
    if unknown:
        raise SystemExit(f"Unknown taxonomy subject(s): {', '.join(unknown)}")
    output_dir = args.output_dir or (
        PROJECT_ROOT / "tmp" / "exam_curriculum_classification" / datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    )
    clauses = [
        "q.review_status = 'accepted'",
        "c.normalized_category_name = %s",
        "(s.normalized_subject_name = ANY(%s) OR s.official_subject_name = ANY(%s))",
    ]
    params: list[Any] = [args.category, subjects, subjects]
    if args.year is not None:
        clauses.append("es.roc_year = %s")
        params.append(args.year)
    limit_sql = ""
    if args.limit:
        limit_sql = " LIMIT %s"
        params.append(args.limit)
    query = f"""
SELECT
    q.question_key,
    q.question_number,
    COALESCE(q.question_text, q.question_json->>'stem', '') AS stem,
    COALESCE(g.shared_stem_text, g.shared_stem_json->>'shared_stem', '') AS group_stem,
    COALESCE(opts.options, '[]'::jsonb) AS options,
    ans.answer_value AS answer,
    c.normalized_category_name AS category,
    s.normalized_subject_name AS subject,
    es.roc_year AS year,
    es.exam_ordinal,
    od.registry_key AS source_registry_key
FROM exam.questions q
JOIN exam.official_documents od ON od.id = q.official_document_id
JOIN exam.categories c ON c.id = od.category_id
JOIN exam.subjects s ON s.id = od.subject_id
JOIN exam.exam_sessions es ON es.id = od.exam_session_id
LEFT JOIN exam.question_groups g ON g.id = q.question_group_id
LEFT JOIN LATERAL (
    SELECT jsonb_agg(
        jsonb_build_object('key', qo.option_label, 'text', COALESCE(qo.option_text, ''))
        ORDER BY qo.option_label
    ) AS options
    FROM exam.question_options qo
    WHERE qo.question_id = q.id
) opts ON true
LEFT JOIN LATERAL (
    SELECT a.answer_value
    FROM exam.answers a
    WHERE a.question_id = q.id
    ORDER BY a.is_correction DESC, a.created_at DESC, a.id DESC
    LIMIT 1
) ans ON true
WHERE {' AND '.join(clauses)}
ORDER BY
    s.normalized_subject_name,
    es.roc_year,
    COALESCE(es.exam_ordinal, 0),
    CASE WHEN q.question_number ~ '^[0-9]+$' THEN q.question_number::integer ELSE 999999 END,
    q.question_key
{limit_sql}
"""
    with psycopg.connect(args.database_url, row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        rows = list(connection.execute(query, params))
    tasks: list[dict[str, Any]] = []
    for row in rows:
        _taxonomy_path, taxonomy, _subject = taxonomy_by_subject[row["subject"]]
        content = {
            "group_stem": row["group_stem"] or "",
            "stem": row["stem"] or "",
            "options": row["options"] or [],
            "answer": row["answer"],
        }
        tasks.append(
            {
                "question_key": row["question_key"],
                "input_hash": stable_hash(content),
                "taxonomy_version": taxonomy["taxonomy_version"],
                "category": row["category"],
                "subject": row["subject"],
                "year": row["year"],
                "exam_ordinal": row["exam_ordinal"],
                "source_registry_key": row["source_registry_key"],
                "question_number": row["question_number"],
                **content,
            }
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    task_path = output_dir / "tasks.jsonl"
    with task_path.open("w", encoding="utf-8") as handle:
        for task in tasks:
            handle.write(json.dumps(task, ensure_ascii=False, default=json_default, separators=(",", ":")) + "\n")
    counts = Counter(task["subject"] for task in tasks)
    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "exam.questions",
        "source_review_status": "accepted",
        "read_only": True,
        "taxonomy_versions": sorted({task["taxonomy_version"] for task in tasks}),
        "category": args.category,
        "subjects": subjects,
        "year": args.year,
        "limit": args.limit,
        "task_count": len(tasks),
        "counts_by_subject": dict(sorted(counts.items())),
        "task_path": str(task_path),
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
