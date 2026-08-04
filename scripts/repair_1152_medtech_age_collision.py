#!/usr/bin/env python3
"""Repair the reviewed 115-2 medtech questions split at `26.20 歲`/`28.20 歲`.

The repair is deliberately narrow:

- update existing q025 and q027 from a reviewed parser output;
- insert the missing q026 and q028;
- replace parse issues for only those four candidate keys;
- append reset_review events for q025 and q027 while preserving prior notes
  and corrections in event_json.

Dry-run is the default. The production apply path has strict preconditions so
it aborts if a reviewer changes either affected question after this repair was
prepared.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb


SOURCE_KEY = "moex:115090:308:0103:1:question"
TARGET_KEYS = tuple(f"{SOURCE_KEY}:q{number:03d}" for number in (25, 26, 27, 28))
REVIEWED_KEYS = (TARGET_KEYS[0], TARGET_KEYS[2])
EXPECTED_LATEST_EVENT_IDS = {
    TARGET_KEYS[0]: 82011,
    TARGET_KEYS[2]: 81862,
}
RESET_REASONS = {
    TARGET_KEYS[0]: "依人工阻擋註解修復：第 25 題誤吃第 26 題；已分離第 25 題並補回第 26 題。",
    TARGET_KEYS[2]: "依人工阻擋註解修復：第 27 題誤吃第 28 題；已分離第 27 題並補回第 28 題。",
}
NON_QUESTION_REVIEW_ACTIONS = {
    "confirm_not_group",
    "confirm_group",
    "reset_group_review",
    "human_review_pdf_visual",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-jsonl", required=True, type=Path)
    parser.add_argument("--issue-csv", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--reviewer", default="parser_medtech_age_collision_v0.14")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def json_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def load_candidates(path: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    if len(rows) != 80:
        raise SystemExit(f"expected a single 80-question source, got {len(rows)} candidates")
    if {str(row.get("source_registry_key") or "") for row in rows} != {SOURCE_KEY}:
        raise SystemExit("candidate JSONL contains an unexpected source_registry_key")
    numbers = {int(row["question_number"]) for row in rows}
    if numbers != set(range(1, 81)):
        raise SystemExit("candidate JSONL is not a complete q001-q080 source rebuild")
    selected = {str(row["candidate_key"]): row for row in rows if str(row["candidate_key"]) in TARGET_KEYS}
    if set(selected) != set(TARGET_KEYS):
        raise SystemExit("candidate JSONL does not contain exactly q025-q028")
    return rows, selected


def load_issues(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    selected: list[dict[str, Any]] = []
    for row in rows:
        if row.get("candidate_key") not in TARGET_KEYS:
            continue
        issue_json = json.loads(row.get("issue_json") or "{}")
        selected.append({**row, "issue_json": issue_json})
    return selected


def option_text(candidate: dict[str, Any], key: str) -> str:
    for option in candidate.get("options") or []:
        if str(option.get("key") or "") == key:
            return str(option.get("text") or "")
    return ""


def validate_incoming(candidates: dict[str, dict[str, Any]]) -> None:
    q25, q26, q27, q28 = (candidates[key] for key in TARGET_KEYS)
    checks = [
        (option_text(q25, "D") == "98", "q025 option D is not clean"),
        (str(q26.get("stem") or "").startswith("20 歲男性"), "q026 stem is unexpected"),
        (len(q26.get("options") or []) == 4, "q026 does not have four options"),
        (bool(q26.get("image_refs")), "q026 is missing its lung-volume figure"),
        ("28.20 歲" not in json.dumps(q27, ensure_ascii=False), "q027 still contains q028"),
        (str(q28.get("stem") or "").startswith("20 歲的男性"), "q028 stem is unexpected"),
        (len(q28.get("options") or []) == 4, "q028 does not have four options"),
    ]
    failures = [message for passed, message in checks if not passed]
    if failures:
        raise SystemExit("; ".join(failures))


def normalized_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "stem": candidate.get("stem"),
        "options": candidate.get("options") or [],
        "answer": candidate.get("answer"),
        "answer_payload": candidate.get("answer_payload"),
        "image_refs": candidate.get("image_refs") or [],
        "metadata": candidate.get("metadata") or {},
    }


def fetch_rows(cur: psycopg.Cursor[Any], query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    cur.execute(query, params)
    names = [column.name for column in cur.description or []]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def main() -> int:
    args = parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required; run this script inside the review-ui container")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _all_candidates, incoming = load_candidates(args.candidate_jsonl)
    validate_incoming(incoming)
    incoming_issues = load_issues(args.issue_csv)

    with psycopg.connect(args.database_url) as conn:
        with conn.cursor() as cur:
            before_candidates = fetch_rows(
                cur,
                """
                SELECT *
                FROM exam.question_candidates
                WHERE candidate_key = ANY(%s)
                ORDER BY candidate_key
                FOR UPDATE
                """,
                (list(TARGET_KEYS),),
            )
            existing_keys = {str(row["candidate_key"]) for row in before_candidates}
            if existing_keys != set(REVIEWED_KEYS):
                raise SystemExit(
                    "database precondition failed: expected only q025 and q027 to exist; "
                    f"found {sorted(existing_keys)}"
                )

            old_by_key = {str(row["candidate_key"]): row for row in before_candidates}
            if "26.20 歲" not in json.dumps(old_by_key[TARGET_KEYS[0]]["raw_candidate_json"], ensure_ascii=False):
                raise SystemExit("database precondition failed: q025 no longer contains swallowed q026")
            if "28.20 歲" not in json.dumps(old_by_key[TARGET_KEYS[2]]["raw_candidate_json"], ensure_ascii=False):
                raise SystemExit("database precondition failed: q027 no longer contains swallowed q028")

            before_reviews = fetch_rows(
                cur,
                """
                SELECT *
                FROM exam.question_review_events
                WHERE candidate_key = ANY(%s)
                ORDER BY candidate_key, id
                """,
                (list(REVIEWED_KEYS),),
            )
            latest_reviews = fetch_rows(
                cur,
                """
                SELECT DISTINCT ON (candidate_key)
                    id, candidate_key, action, reviewer, notes, created_at,
                    corrected_candidate_json, event_json
                FROM exam.question_review_events
                WHERE candidate_key = ANY(%s)
                  AND action <> ALL(%s)
                ORDER BY candidate_key, id DESC
                """,
                (list(REVIEWED_KEYS), list(NON_QUESTION_REVIEW_ACTIONS)),
            )
            latest_by_key = {str(row["candidate_key"]): row for row in latest_reviews}
            for key in REVIEWED_KEYS:
                review = latest_by_key.get(key)
                if not review:
                    raise SystemExit(f"database precondition failed: latest review missing for {key}")
                if int(review["id"]) != EXPECTED_LATEST_EVENT_IDS[key] or review["action"] != "block":
                    raise SystemExit(
                        f"database precondition failed: {key} review changed "
                        f"(event={review['id']}, action={review['action']})"
                    )

            before_issues = fetch_rows(
                cur,
                """
                SELECT *
                FROM exam.question_parse_issues
                WHERE candidate_key = ANY(%s)
                ORDER BY candidate_key, id
                """,
                (list(TARGET_KEYS),),
            )

            backup = {
                "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "candidate_jsonl": str(args.candidate_jsonl),
                "candidate_jsonl_sha256": json_hash(args.candidate_jsonl),
                "issue_csv": str(args.issue_csv),
                "issue_csv_sha256": json_hash(args.issue_csv),
                "before_candidates": before_candidates,
                "before_question_parse_issues": before_issues,
                "before_question_review_events": before_reviews,
                "incoming_candidates": [incoming[key] for key in TARGET_KEYS],
                "incoming_issues": incoming_issues,
            }
            write_json(args.output_dir / "repair_snapshot.json", backup)

            summary: dict[str, Any] = {
                "ok": True,
                "dry_run": not args.apply,
                "source_registry_key": SOURCE_KEY,
                "updated_candidate_keys": list(REVIEWED_KEYS),
                "inserted_candidate_keys": [TARGET_KEYS[1], TARGET_KEYS[3]],
                "reset_review_keys": list(REVIEWED_KEYS),
                "incoming_issue_count": len(incoming_issues),
                "latest_review_event_ids": {
                    key: int(latest_by_key[key]["id"]) for key in REVIEWED_KEYS
                },
            }
            if not args.apply:
                write_json(args.output_dir / "dry_run_summary.json", summary)
                conn.rollback()
                print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
                return 0

            for key in TARGET_KEYS:
                candidate = incoming[key]
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
                        review_status = 'unreviewed',
                        issue_count = EXCLUDED.issue_count,
                        updated_at = now()
                    """,
                    (
                        key,
                        SOURCE_KEY,
                        candidate.get("answer_source_registry_key"),
                        str(candidate["question_number"]),
                        candidate.get("question_type"),
                        candidate.get("group_ref") or None,
                        candidate.get("stem"),
                        Jsonb(candidate.get("stem_markup")) if candidate.get("stem_markup") else None,
                        Jsonb(candidate),
                        Jsonb(normalized_candidate(candidate)),
                        metadata.get("parser_version") or "moex_mineru_candidate_v0.14",
                        candidate.get("quality_status") or "needs_review",
                        int(candidate.get("issue_count") or 0),
                        SOURCE_KEY,
                        candidate.get("answer_source_registry_key"),
                    ),
                )

            cur.execute(
                "DELETE FROM exam.question_parse_issues WHERE candidate_key = ANY(%s)",
                (list(TARGET_KEYS),),
            )
            for issue in incoming_issues:
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
                        issue["candidate_key"],
                        SOURCE_KEY,
                        issue["issue_code"],
                        issue["severity"],
                        issue["message"],
                        Jsonb(issue["issue_json"]),
                        issue["candidate_key"],
                    ),
                )

            new_event_ids: dict[str, int] = {}
            for key in REVIEWED_KEYS:
                previous = latest_by_key[key]
                annotations = [
                    str(row["notes"])
                    for row in before_reviews
                    if row["candidate_key"] == key and str(row.get("notes") or "").strip()
                ]
                event = {
                    "candidate_key": key,
                    "action": "reset_review",
                    "reviewer": args.reviewer,
                    "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "notes": RESET_REASONS[key],
                    "reset_notes": RESET_REASONS[key],
                    "previous_action": previous["action"],
                    "previous_notes": previous["notes"],
                    "previous_reviewer": previous["reviewer"],
                    "previous_reviewed_at": (
                        previous["created_at"].isoformat() if previous["created_at"] else None
                    ),
                    "previous_correction": previous["corrected_candidate_json"],
                    "source_annotations": list(dict.fromkeys(annotations)),
                    "parser_version": "moex_mineru_candidate_v0.14",
                    "repair_scope": list(TARGET_KEYS),
                }
                cur.execute(
                    """
                    INSERT INTO exam.question_review_events (
                        candidate_id, candidate_key, reviewer, action,
                        corrected_candidate_json, event_json, notes, created_at
                    )
                    SELECT id, %s, %s, 'reset_review', NULL, %s, %s, %s::timestamptz
                    FROM exam.question_candidates
                    WHERE candidate_key = %s
                    RETURNING id
                    """,
                    (
                        key,
                        args.reviewer,
                        Jsonb(event),
                        event["notes"],
                        event["created_at"],
                        key,
                    ),
                )
                new_event_ids[key] = int(cur.fetchone()[0])

            summary.update(
                {
                    "dry_run": False,
                    "applied": True,
                    "new_reset_event_ids": new_event_ids,
                }
            )
        conn.commit()

    write_json(args.output_dir / "apply_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
