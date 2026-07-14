#!/usr/bin/env python3
"""Propagate confirmed Traditional-Chinese OCR rules through SQL review data.

The official/raw candidate remains unchanged.  The derived candidate layer is
updated and a same-state append-only review event is added when the visible
stem/options still contain a confirmed OCR error.  Human accept/block states
and notes are preserved; this command never accepts a question.
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

from build_question_candidates_from_mineru import (
    OCR_CHAR_MAP,
    OCR_PHRASE_MAP,
    custom_phrase_map,
    load_text_normalization_rules,
    markup_payload,
    normalize_confirmed_ocr_text,
)
from repair_reviewed_text_normalization import effective_candidate


NON_QUESTION_ACTIONS = {
    "confirm_not_group",
    "confirm_group",
    "reset_group_review",
    "human_review_pdf_visual",
}
REVIEWER = "codex-repair-confirmed-ocr-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--category", action="append", default=[])
    parser.add_argument("--all-categories", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path("tmp/confirmed_ocr_rules"))
    parser.add_argument("--apply", action="store_true", help="Write derived updates and append same-state events.")
    return parser.parse_args()


def all_source_terms(categories: list[str]) -> list[str]:
    terms = set(OCR_PHRASE_MAP)
    terms.update(
        chr(char) if isinstance(char, int) else str(char)
        for char in OCR_CHAR_MAP.keys()
    )
    terms.update(
        str(rule.get("source") or "")
        for rule in load_text_normalization_rules()
        if rule.get("status", "active") == "active" and rule.get("source")
    )
    for category in categories:
        terms.update(custom_phrase_map(category, None))
    return sorted(term for term in terms if term)


def json_contains_regex(terms: list[str]) -> str:
    return "(?:" + "|".join(re.escape(term) for term in terms) + ")"


def fetch_rows(conn: psycopg.Connection[Any], categories: list[str], terms: list[str]) -> list[dict[str, Any]]:
    clauses = ["c.review_status <> 'excluded'"]
    params: list[Any] = []
    if categories:
        clauses.append("COALESCE(c.raw_candidate_json->'metadata'->>'normalized_category_name', c.raw_candidate_json->'metadata'->>'group_name', '') = ANY(%s)")
        params.append(categories)
    pattern = json_contains_regex(terms)
    clauses.append("(c.raw_candidate_json::text ~ %s OR c.normalized_candidate_json::text ~ %s OR COALESCE(latest.corrected_candidate_json, '{}'::jsonb)::text ~ %s)")
    params.extend([pattern, pattern, pattern])
    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH latest AS (
                SELECT DISTINCT ON (e.candidate_key)
                    e.id AS event_id, e.candidate_id, e.candidate_key, e.action,
                    e.reviewer, e.corrected_candidate_json, e.notes
                FROM exam.question_review_events e
                WHERE e.action <> ALL(%s)
                ORDER BY e.candidate_key, e.id DESC
            )
            SELECT c.id, c.candidate_key, c.raw_candidate_json,
                   c.normalized_candidate_json, latest.event_id, latest.action,
                   latest.reviewer, latest.corrected_candidate_json, latest.notes
            FROM exam.question_candidates c
            LEFT JOIN latest ON latest.candidate_key = c.candidate_key
            WHERE {' AND '.join(clauses)}
            ORDER BY c.candidate_key
            """,
            [list(NON_QUESTION_ACTIONS), *params],
        )
        columns = [column.name for column in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def clean_candidate(candidate: dict[str, Any], *, category: str | None, subject: str | None) -> tuple[dict[str, Any], list[str]]:
    cleaned = copy.deepcopy(candidate or {})
    changes: list[str] = []
    stem = cleaned.get("stem")
    if isinstance(stem, str):
        updated = normalize_confirmed_ocr_text(stem, category=category, subject=subject)
        if updated != stem:
            cleaned["stem"] = updated
            cleaned["stem_markup"] = markup_payload(updated)
            changes.append("題幹")
    options = cleaned.get("options")
    if isinstance(options, list):
        for option in options:
            if not isinstance(option, dict) or not isinstance(option.get("text"), str):
                continue
            before = option["text"]
            updated = normalize_confirmed_ocr_text(before, category=category, subject=subject)
            if updated != before:
                option["text"] = updated
                option["markup"] = markup_payload(updated)
                changes.append(f"選項 {option.get('key') or '?'}")
    return cleaned, changes


def make_overlay(raw: dict[str, Any], existing: dict[str, Any] | None, *, category: str | None, subject: str | None) -> tuple[dict[str, Any], list[str]]:
    current = effective_candidate(raw, existing)
    overlay = copy.deepcopy(existing or {})
    cleaned, changes = clean_candidate(current, category=category, subject=subject)
    if "題幹" in changes:
        overlay["stem"] = cleaned.get("stem")
        overlay.pop("stem_markup", None)
    if any(change.startswith("選項 ") for change in changes):
        overlay["options"] = cleaned.get("options") or []
    return overlay, changes


def append_event(conn: psycopg.Connection[Any], row: dict[str, Any], overlay: dict[str, Any], changes: list[str]) -> None:
    action = row.get("action") or "unreviewed"
    note = "\n\n".join(
        part
        for part in (
            str(row.get("notes") or "").strip(),
            "[Codex 確定 OCR 規則] 已套用官方 PDF 核准的簡繁字形規則；保留原人工狀態與註記。",
            "修復欄位：" + "、".join(changes),
        )
        if part
    )
    event_json = {
        "repair_type": "confirmed_ocr_rules",
        "source_event_id": row.get("event_id"),
        "preserved_action": action,
        "changes": changes,
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO exam.question_review_events (
                candidate_id, candidate_key, reviewer, action,
                corrected_candidate_json, event_json, notes, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                row.get("id"), row["candidate_key"], REVIEWER, action,
                Jsonb(overlay), Jsonb(event_json), note, datetime.now(timezone.utc),
            ),
        )


def main() -> int:
    args = parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")
    categories = [] if args.all_categories else args.category
    terms = all_source_terms(categories)
    report: dict[str, Any] = {
        "apply": args.apply,
        "source_term_count": len(terms),
        "scanned": 0,
        "derived_changed": 0,
        "visible_changed": 0,
        "events_appended": 0,
        "rows": [],
    }
    with psycopg.connect(args.database_url) as conn:
        rows = fetch_rows(conn, categories, terms)
        report["scanned"] = len(rows)
        for row in rows:
            raw = row.get("raw_candidate_json") or {}
            metadata = raw.get("metadata") or {}
            category = metadata.get("normalized_category_name") or metadata.get("official_category_name")
            subject = metadata.get("normalized_subject_name") or metadata.get("official_subject_name")
            derived, derived_changes = clean_candidate(row.get("normalized_candidate_json") or {}, category=category, subject=subject)
            overlay, visible_changes = make_overlay(raw, row.get("corrected_candidate_json") or {}, category=category, subject=subject)
            derived_changed = derived != (row.get("normalized_candidate_json") or {})
            visible_changed = bool(visible_changes)
            if not derived_changed and not visible_changed:
                continue
            report["derived_changed"] += int(derived_changed)
            report["visible_changed"] += int(visible_changed)
            report["rows"].append({
                "candidate_key": row["candidate_key"],
                "action": row.get("action") or "unreviewed",
                "derived_changes": derived_changes,
                "visible_changes": visible_changes,
            })
            if args.apply:
                with conn.cursor() as cur:
                    if derived_changed:
                        cur.execute(
                            "UPDATE exam.question_candidates SET normalized_candidate_json=%s, updated_at=now() WHERE id=%s",
                            (Jsonb(derived), row["id"]),
                        )
                if visible_changed:
                    append_event(conn, row, overlay, visible_changes)
                    report["events_appended"] += 1
        if args.apply:
            conn.commit()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    artifact = args.output_dir / f"confirmed_ocr_rules__{datetime.now():%Y%m%d-%H%M%S}.json"
    artifact.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["artifact"] = str(artifact)
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
