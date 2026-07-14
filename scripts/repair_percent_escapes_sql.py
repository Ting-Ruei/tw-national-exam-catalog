#!/usr/bin/env python3
"""Repair literal LaTeX scientific escapes in SQL-derived review data.

This updates only ``normalized_candidate_json`` (a derived parser layer) and
appends events for affected correction overlays.  ``raw_candidate_json`` and
all existing review events remain unchanged.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from build_question_candidates_from_mineru import markup_payload


ESCAPED_PERCENT_RE = re.compile(r"\\+\s*%")
ESCAPED_CIRC_C_RE = re.compile(
    r"\^\s*\{\s*\\+\s*circ\s*\}\s*(?:\\mathrm\{C\}|C)|\\+\s*circ\s*(?:\\mathrm\{C\}|C)",
    re.I,
)
ESCAPED_DEGREE_RE = re.compile(r"\^\s*\{\s*\\+\s*circ\s*\}|\\+\s*circ\b", re.I)
REPAIR_REVIEWER = "codex-repair-percent-escape-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write SQL changes. Default is dry-run.")
    parser.add_argument("--report", type=Path, help="Optional JSON report path.")
    parser.add_argument("--postgres-db", default=os.environ.get("POSTGRES_DB", "tw_national_exam_dev"))
    parser.add_argument("--postgres-user", default=os.environ.get("POSTGRES_USER", "national_exam"))
    return parser.parse_args()


def db_connect(args: argparse.Namespace) -> psycopg.Connection[Any]:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("REVIEW_DATABASE_URL")
    if dsn:
        return psycopg.connect(dsn)
    return psycopg.connect(
        host=os.environ.get("PGHOST", "postgres"),
        port=os.environ.get("PGPORT", "5432"),
        user=args.postgres_user,
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        dbname=args.postgres_db,
    )


def clean_scientific_escapes(value: Any) -> tuple[Any, bool, list[str]]:
    if not isinstance(value, str):
        return value, False, []
    cleaned = ESCAPED_PERCENT_RE.sub("%", value)
    changes = ["\\% -> %"] if cleaned != value else []
    circ_c_cleaned = ESCAPED_CIRC_C_RE.sub("℃", cleaned)
    if circ_c_cleaned != cleaned:
        changes.append("\\circC -> ℃")
    cleaned = circ_c_cleaned
    degree_cleaned = ESCAPED_DEGREE_RE.sub("°", cleaned)
    if degree_cleaned != cleaned:
        changes.append("\\circ -> °")
    cleaned = degree_cleaned
    return cleaned, cleaned != value, changes


def markup_has_scientific_escape(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    serialized = json.dumps(value, ensure_ascii=False)
    return any(
        pattern.search(serialized)
        for pattern in (ESCAPED_PERCENT_RE, ESCAPED_CIRC_C_RE, ESCAPED_DEGREE_RE)
    )


def clean_candidate(candidate: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    cleaned = copy.deepcopy(candidate)
    changes: list[str] = []

    stem, changed, escape_changes = clean_scientific_escapes(cleaned.get("stem"))
    if changed:
        cleaned["stem"] = stem
        changes.extend(f"題幹 {change}" for change in escape_changes)
        cleaned["stem_markup"] = markup_payload(str(stem or ""))

    options = cleaned.get("options")
    if isinstance(options, list):
        for option in options:
            if not isinstance(option, dict):
                continue
            text, changed, escape_changes = clean_scientific_escapes(option.get("text"))
            if changed:
                option["text"] = text
                option["markup"] = markup_payload(str(text or ""))
                changes.extend(f"選項 {option.get('key') or '?'} {change}" for change in escape_changes)
            elif markup_has_scientific_escape(option.get("markup")):
                option["markup"] = markup_payload(str(option.get("text") or ""))
                changes.append(f"選項 {option.get('key') or '?'} 重建科學符號 markup")

    if markup_has_scientific_escape(cleaned.get("stem_markup")):
        cleaned["stem_markup"] = markup_payload(str(cleaned.get("stem") or ""))
        changes.append("重建題幹科學符號 markup")

    return cleaned, changes


def fetch_candidates(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, candidate_key, normalized_candidate_json
            FROM exam.question_candidates
            WHERE position(chr(92)||'%' in normalized_candidate_json::text) > 0
               OR position(chr(92)||'circ' in normalized_candidate_json::text) > 0
            ORDER BY candidate_key
            """
        )
        columns = [column.name for column in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def fetch_corrections(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH latest AS (
                SELECT DISTINCT ON (e.candidate_key)
                    e.id AS event_id, e.candidate_id, e.candidate_key, e.action,
                    e.reviewer, e.corrected_candidate_json, e.notes
                FROM exam.question_review_events e
                WHERE e.corrected_candidate_json IS NOT NULL
                ORDER BY e.candidate_key, e.created_at DESC, e.id DESC
            )
            SELECT *
            FROM latest
            WHERE position(chr(92)||'%' in corrected_candidate_json::text) > 0
               OR position(chr(92)||'circ' in corrected_candidate_json::text) > 0
            ORDER BY candidate_key
            """
        )
        columns = [column.name for column in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def append_event(conn: psycopg.Connection[Any], row: dict[str, Any], corrected: dict[str, Any], changes: list[str]) -> None:
    notes = "\n\n".join(
        part
        for part in (
            str(row.get("notes") or "").strip(),
            "[Codex 修復] 清理 LaTeX 百分比跳脫字元，保留原人工狀態與註記。",
            "修復內容：" + "；".join(changes),
        )
        if part
    )
    event_json = {
        "repair_type": "percent_escape_overlay",
        "source_event_id": row["event_id"],
        "preserved_action": row["action"],
        "changes": changes,
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO exam.question_review_events (
                candidate_id, candidate_key, reviewer, action,
                corrected_candidate_json, event_json, notes, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                row["candidate_id"],
                row["candidate_key"],
                REPAIR_REVIEWER,
                row["action"],
                Jsonb(corrected),
                Jsonb(event_json),
                notes,
                datetime.now(timezone.utc),
            ),
        )


def main() -> int:
    args = parse_args()
    report: dict[str, Any] = {
        "apply": args.apply,
        "normalized_candidates_scanned": 0,
        "normalized_candidates_changed": 0,
        "correction_overlays_scanned": 0,
        "correction_overlays_changed": 0,
        "rows": [],
    }
    with db_connect(args) as conn:
        candidates = fetch_candidates(conn)
        report["normalized_candidates_scanned"] = len(candidates)
        for row in candidates:
            cleaned, changes = clean_candidate(row["normalized_candidate_json"] or {})
            if not changes:
                continue
            report["normalized_candidates_changed"] += 1
            report["rows"].append({"layer": "normalized_candidate_json", "candidate_key": row["candidate_key"], "changes": changes})
            if args.apply:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE exam.question_candidates SET normalized_candidate_json=%s, updated_at=now() WHERE id=%s",
                        (Jsonb(cleaned), row["id"]),
                    )

        corrections = fetch_corrections(conn)
        report["correction_overlays_scanned"] = len(corrections)
        for row in corrections:
            cleaned, changes = clean_candidate(row["corrected_candidate_json"] or {})
            if not changes:
                continue
            report["correction_overlays_changed"] += 1
            report["rows"].append({"layer": "correction_overlay", "candidate_key": row["candidate_key"], "changes": changes})
            if args.apply:
                append_event(conn, row, cleaned, changes)
        if args.apply:
            conn.commit()

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
