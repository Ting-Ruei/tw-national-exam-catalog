#!/usr/bin/env python3
"""Recover papers swallowed into ROC-year exam-header candidates."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

import build_question_candidates_from_mineru as builder


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "tmp" / "exam_header_repair"
TERMINAL_ACTIONS = {"accept", "unblock", "block", "needs_review", "exclude", "reviewed", "correct"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--pair-index", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-reviewed-reset", action="store_true")
    return parser.parse_args()


def strict_header_rows(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT candidate_key, source_registry_key, raw_candidate_json
            FROM exam.question_candidates
            WHERE question_number ~ '^[0-9]+$'
              AND raw_candidate_json->'metadata'->>'year' = question_number
              AND length(COALESCE(raw_candidate_json->>'stem', '')) > 80
              AND COALESCE(raw_candidate_json->>'stem', '') ~ '(考試時間|類科名稱|科目名稱|座號|本試題)'
              AND CASE
                    WHEN jsonb_typeof(raw_candidate_json->'options') = 'array'
                        THEN jsonb_array_length(raw_candidate_json->'options')
                    ELSE 0
                  END > 8
            ORDER BY source_registry_key, candidate_key
            """
        )
        return [
            {"candidate_key": key, "source_registry_key": source, "raw_candidate_json": raw}
            for key, source, raw in cur.fetchall()
        ]


def latest_human_reviews(conn: psycopg.Connection[Any], keys: list[str]) -> dict[str, dict[str, Any]]:
    if not keys:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (candidate_key)
                candidate_key, action, notes, reviewer, created_at, corrected_candidate_json
            FROM exam.question_review_events
            WHERE candidate_key = ANY(%s)
              AND action NOT IN ('confirm_not_group', 'confirm_group', 'reset_group_review', 'human_review_pdf_visual')
            ORDER BY candidate_key, id DESC
            """,
            (keys,),
        )
        return {
            key: {
                "action": action,
                "notes": notes,
                "reviewer": reviewer,
                "created_at": created_at.isoformat() if created_at else None,
                "correction": correction,
            }
            for key, action, notes, reviewer, created_at, correction in cur.fetchall()
        }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def normalized_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "stem": candidate.get("stem"),
        "options": candidate.get("options") or [],
        "answer": candidate.get("answer"),
        "answer_payload": candidate.get("answer_payload"),
        "image_refs": candidate.get("image_refs") or [],
        "metadata": candidate.get("metadata") or {},
    }


def main() -> int:
    args = parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required; run inside review-ui or pass --database-url")
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = (args.output_dir or DEFAULT_OUTPUT_ROOT / run_id).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    pair_index = args.pair_index or builder.latest_path(builder.PAIR_INDEX_DIR, "question_answer_pairs_detail__*.csv")
    pairs = {row.get("question_registry_key") or "": row for row in builder.read_csv(pair_index)}

    with psycopg.connect(args.database_url) as conn:
        old_headers = strict_header_rows(conn)
        source_keys = sorted({row["source_registry_key"] for row in old_headers})
        missing_pairs = [key for key in source_keys if key not in pairs]
        if missing_pairs:
            raise SystemExit(f"pair-index rows missing for {len(missing_pairs)} sources")

        candidates: list[dict[str, Any]] = []
        issues: list[builder.Issue] = []
        document_reports: list[dict[str, Any]] = []
        for source_key in source_keys:
            built, built_issues, meta = builder.build_candidates_for_pair(pairs[source_key])
            candidates.extend(built)
            issues.extend(built_issues)
            numbers = [int(item["question_number"]) for item in built if str(item.get("question_number") or "").isdigit()]
            document_reports.append(
                {
                    "source_registry_key": source_key,
                    "candidate_count": len(built),
                    "first_question": min(numbers) if numbers else None,
                    "last_question": max(numbers) if numbers else None,
                    "duplicate_count": len(numbers) - len(set(numbers)),
                    "non_four_option_count": sum(len(item.get("options") or []) != 4 for item in built),
                    "issue_count": len(built_issues),
                    "status": meta.get("status"),
                }
            )

        new_keys = [str(item["candidate_key"]) for item in candidates]
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT candidate_key, source_registry_key, raw_candidate_json
                FROM exam.question_candidates
                WHERE candidate_key = ANY(%s)
                """,
                (new_keys,),
            )
            existing_rows = [
                {"candidate_key": key, "source_registry_key": source, "raw_candidate_json": raw}
                for key, source, raw in cur.fetchall()
            ]
        existing_reviews = latest_human_reviews(conn, [row["candidate_key"] for row in existing_rows])
        reviewed_existing = {
            key: review
            for key, review in existing_reviews.items()
            if review.get("action") in TERMINAL_ACTIONS
        }

        write_jsonl(output_dir / "before_header_candidates.jsonl", old_headers)
        write_jsonl(output_dir / "before_existing_recovered_keys.jsonl", existing_rows)
        write_jsonl(output_dir / "recovered_candidates.jsonl", candidates)
        write_jsonl(output_dir / "document_reports.jsonl", document_reports)
        write_jsonl(
            output_dir / "recovered_issues.jsonl",
            [
                {
                    "candidate_key": issue.candidate_key,
                    "source_registry_key": issue.source_registry_key,
                    "question_number": issue.question_number,
                    "issue_code": issue.issue_code,
                    "severity": issue.severity,
                    "message": issue.message,
                    "issue_json": issue.issue_json,
                }
                for issue in issues
            ],
        )

        summary = {
            "ok": True,
            "dry_run": not args.apply,
            "pair_index": str(pair_index),
            "output_dir": str(output_dir),
            "header_candidate_count": len(old_headers),
            "source_document_count": len(source_keys),
            "recovered_candidate_count": len(candidates),
            "candidate_count_distribution": dict(sorted(Counter(row["candidate_count"] for row in document_reports).items())),
            "recovered_issue_count": len(issues),
            "documents_with_remaining_structure_risk": sum(
                row["duplicate_count"] > 0 or row["non_four_option_count"] > 0 or row["candidate_count"] < 40
                for row in document_reports
            ),
            "existing_recovered_key_count": len(existing_rows),
            "reviewed_existing_key_count": len(reviewed_existing),
        }
        (output_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if not args.apply:
            print(json.dumps(summary, ensure_ascii=False))
            return 0
        if reviewed_existing and not args.allow_reviewed_reset:
            raise SystemExit(
                f"refusing to change {len(reviewed_existing)} reviewed candidates; rerun with --allow-reviewed-reset after inspecting backup"
            )

        issues_by_key: dict[str, list[builder.Issue]] = {}
        for issue in issues:
            if issue.candidate_key:
                issues_by_key.setdefault(issue.candidate_key, []).append(issue)
        with conn.cursor() as cur:
            for candidate in candidates:
                metadata = candidate.get("metadata") or {}
                cur.execute(
                    """
                    INSERT INTO exam.question_candidates (
                        candidate_key, source_registry_key, source_document_id,
                        answer_source_registry_key, answer_source_document_id,
                        question_number, question_type, group_ref, stem_text,
                        stem_markup_json, raw_candidate_json, normalized_candidate_json,
                        parser_version, quality_status, review_status, issue_count, updated_at
                    )
                    SELECT
                        %s, %s, source_doc.id,
                        %s, answer_doc.id,
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, 'unreviewed', %s, now()
                    FROM (SELECT 1) seed
                    LEFT JOIN exam.official_documents source_doc ON source_doc.registry_key = %s
                    LEFT JOIN exam.official_documents answer_doc ON answer_doc.registry_key = %s
                    ON CONFLICT (candidate_key) DO UPDATE SET
                        source_registry_key = EXCLUDED.source_registry_key,
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
                        updated_at = now()
                    """,
                    (
                        candidate["candidate_key"],
                        candidate["source_registry_key"],
                        candidate.get("answer_source_registry_key"),
                        str(candidate["question_number"]),
                        candidate.get("question_type"),
                        candidate.get("group_ref"),
                        candidate.get("stem"),
                        Jsonb(candidate.get("stem_markup")) if candidate.get("stem_markup") else None,
                        Jsonb(candidate),
                        Jsonb(normalized_candidate(candidate)),
                        metadata.get("parser_version") or builder.PARSER_VERSION,
                        candidate.get("quality_status") or "needs_review",
                        int(candidate.get("issue_count") or 0),
                        candidate["source_registry_key"],
                        candidate.get("answer_source_registry_key"),
                    ),
                )

            cur.execute("DELETE FROM exam.question_parse_issues WHERE source_registry_key = ANY(%s)", (source_keys,))
            for issue in issues:
                cur.execute(
                    """
                    INSERT INTO exam.question_parse_issues (
                        candidate_id, candidate_key, source_registry_key,
                        issue_code, severity, message, issue_json
                    )
                    SELECT id, %s, %s, %s, %s, %s, %s
                    FROM exam.question_candidates
                    WHERE candidate_key = %s
                    """,
                    (
                        issue.candidate_key or None,
                        issue.source_registry_key,
                        issue.issue_code,
                        issue.severity,
                        issue.message,
                        Jsonb(issue.issue_json),
                        issue.candidate_key,
                    ),
                )

            for key, review in reviewed_existing.items():
                cur.execute(
                    """
                    INSERT INTO exam.question_review_events (
                        candidate_id, candidate_key, reviewer, action, event_json, notes
                    )
                    SELECT id, candidate_key, %s, 'reset_review', %s, %s
                    FROM exam.question_candidates
                    WHERE candidate_key = %s
                    """,
                    (
                        "parser_exam_header_recovery_v0.9",
                        Jsonb(
                            {
                                "candidate_key": key,
                                "action": "reset_review",
                                "reviewer": "parser_exam_header_recovery_v0.9",
                                "previous_action": review.get("action"),
                                "previous_notes": review.get("notes"),
                                "previous_correction": review.get("correction"),
                                "reset_notes": "表頭吞卷修復改變此題 parser 內容，保留原人工紀錄並退回複核。",
                            }
                        ),
                        "表頭吞卷修復改變 parser 內容；原人工註記保留於 event_json。",
                        key,
                    ),
                )

            old_keys = [row["candidate_key"] for row in old_headers]
            cur.execute("DELETE FROM exam.question_candidates WHERE candidate_key = ANY(%s)", (old_keys,))
        conn.commit()

    summary.update({"dry_run": False, "applied": True})
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
