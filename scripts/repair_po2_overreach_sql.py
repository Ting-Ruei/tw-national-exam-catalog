#!/usr/bin/env python3
"""Repair the retired broad PO -> P_O₂ correction in SQL review overlays.

The historical repair rule matched ``P`` + ``O`` anywhere in a string.  It
therefore corrupted ordinary words such as ``hypothyroidism`` and
``Potassium``.  This script appends a repair event only for the known
``P_O₂`` overlay, preserves the current review action and notes, and never
rewrites source candidates or old events.
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

from build_question_candidates_from_mineru import markup_payload, normalize_text


BAD_OVERLAY = "P_O₂"
LEGACY_PO_RE = re.compile(r"P[_ ]?\{?O₂?\}?", re.IGNORECASE)
REPAIR_REVIEWER = "codex-repair-po2-overreach-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Append SQL repair events. Default is dry-run.")
    parser.add_argument("--candidate-key", action="append", default=[], help="Limit repair to a candidate key.")
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


def raw_match_replacements(raw_text: str) -> list[str]:
    """Return what each historical broad match should have remained as."""
    replacements: list[str] = []
    for match in LEGACY_PO_RE.finditer(raw_text or ""):
        token = match.group(0)
        # A source subscript/ASCII 2 is meaningful; otherwise preserve the
        # exact source token, including case (Po in Potassium, PO as a route).
        if "₂" in token or "2" in token:
            replacements.append(normalize_text(token))
        else:
            replacements.append(token)
    return replacements


def repair_text(raw_text: str, current_text: str) -> tuple[str, int, str | None]:
    if BAD_OVERLAY not in (current_text or ""):
        return current_text, 0, None
    replacements = raw_match_replacements(raw_text)
    if not replacements:
        return current_text, 0, "current overlay contains P_O₂ but source has no matching P/O token"

    index = 0

    def replace_one(_: re.Match[str]) -> str:
        nonlocal index
        if index >= len(replacements):
            return BAD_OVERLAY
        replacement = replacements[index]
        index += 1
        return replacement

    repaired = current_text.replace(BAD_OVERLAY, "\x00")
    repaired = re.sub("\x00", replace_one, repaired)
    if BAD_OVERLAY in repaired:
        return current_text, 0, "more corrupt overlay occurrences than source P/O tokens"
    if index == 0 or repaired == current_text:
        return current_text, 0, None
    return repaired, index, None


def repair_overlay(raw: dict[str, Any], correction: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    repaired = copy.deepcopy(correction)
    changes: list[str] = []
    conflicts: list[str] = []

    raw_stem = str(raw.get("stem") or "")
    current_stem = str(correction.get("stem") if correction.get("stem") is not None else raw_stem)
    next_stem, count, conflict = repair_text(raw_stem, current_stem)
    if conflict:
        conflicts.append(f"題幹：{conflict}")
    elif count:
        repaired["stem"] = next_stem
        changes.append(f"題幹修復 {count} 個 P_O₂ overreach")
    if BAD_OVERLAY in json.dumps(repaired.get("stem_markup"), ensure_ascii=False):
        # The old rule also polluted cached markup while the visible stem was
        # unchanged. Rebuild this non-editable cache from the current stem.
        repaired["stem_markup"] = markup_payload(str(repaired.get("stem") or raw_stem))
        changes.append("重建題幹 markup 快取")

    raw_options = raw.get("options") if isinstance(raw.get("options"), list) else []
    current_options = correction.get("options") if isinstance(correction.get("options"), list) else None
    if current_options is not None:
        next_options = copy.deepcopy(current_options)
        for index, option in enumerate(next_options):
            if not isinstance(option, dict):
                continue
            raw_option = raw_options[index] if index < len(raw_options) and isinstance(raw_options[index], dict) else {}
            raw_text = str(raw_option.get("text") or "")
            current_text = str(option.get("text") or "")
            next_text, count, conflict = repair_text(raw_text, current_text)
            label = str(option.get("key") or f"#{index + 1}")
            if conflict:
                conflicts.append(f"選項 {label}：{conflict}")
            elif count:
                option["text"] = next_text
                changes.append(f"選項 {label} 修復 {count} 個 P_O₂ overreach")
            if BAD_OVERLAY in json.dumps(option.get("markup"), ensure_ascii=False):
                option["markup"] = markup_payload(str(option.get("text") or ""))
                changes.append(f"選項 {label} 重建 markup 快取")
        if changes and next_options != current_options:
            repaired["options"] = next_options

    return repaired, changes, conflicts


def fetch_rows(conn: psycopg.Connection[Any], candidate_keys: list[str]) -> list[dict[str, Any]]:
    key_clause = "AND c.candidate_key = ANY(%s)" if candidate_keys else ""
    params: list[Any] = [candidate_keys] if candidate_keys else []
    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH latest AS (
                SELECT DISTINCT ON (e.candidate_key)
                    e.id AS event_id, e.candidate_id, e.candidate_key, e.action,
                    e.reviewer, e.corrected_candidate_json, e.notes, e.created_at
                FROM exam.question_review_events e
                WHERE e.corrected_candidate_json IS NOT NULL
                ORDER BY e.candidate_key, e.created_at DESC, e.id DESC
            )
            SELECT latest.*, c.raw_candidate_json
            FROM latest
            JOIN exam.question_candidates c ON c.candidate_key = latest.candidate_key
            WHERE position(%s in latest.corrected_candidate_json::text) > 0
              {key_clause}
            ORDER BY latest.candidate_key
            """,
            [BAD_OVERLAY, *params],
        )
        columns = [column.name for column in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def append_repair_event(conn: psycopg.Connection[Any], row: dict[str, Any], correction: dict[str, Any], changes: list[str]) -> None:
    notes = "\n\n".join(
        part
        for part in (
            str(row.get("notes") or "").strip(),
            "[Codex 修復] 移除歷史過度寬鬆的 P_O₂ 正規化；僅修正已知錯誤 overlay，保留原人工狀態與原註記。",
            "修復內容：" + "；".join(changes),
        )
        if part
    )
    event_json = {
        "repair_type": "po2_overreach_overlay",
        "source_event_id": row["event_id"],
        "source_reviewer": row.get("reviewer"),
        "preserved_action": row.get("action"),
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
                Jsonb(correction),
                Jsonb(event_json),
                notes,
                datetime.now(timezone.utc),
            ),
        )


def main() -> int:
    args = parse_args()
    report: dict[str, Any] = {"apply": args.apply, "scanned": 0, "repairable": 0, "conflicts": [], "rows": []}
    with db_connect(args) as conn:
        rows = fetch_rows(conn, args.candidate_key)
        report["scanned"] = len(rows)
        for row in rows:
            raw = row["raw_candidate_json"] or {}
            correction = row["corrected_candidate_json"] or {}
            repaired, changes, conflicts = repair_overlay(raw, correction)
            row_report = {"candidate_key": row["candidate_key"], "action": row["action"], "changes": changes, "conflicts": conflicts}
            report["rows"].append(row_report)
            if conflicts:
                report["conflicts"].append(row_report)
            if not changes:
                continue
            report["repairable"] += 1
            if args.apply:
                append_repair_event(conn, row, repaired, changes)
        if args.apply:
            conn.commit()
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
