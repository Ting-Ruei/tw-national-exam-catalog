#!/usr/bin/env python3
"""Append safe text-normalization overlays for reviewed, non-accepted candidates.

This repair never rewrites review history and never accepts a question.  It
rebuilds only stem/options from the parser's current safe normalization rules,
preserves manual assets and group fields, and appends an event that retains the
reviewer's current terminal state and note.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import psycopg
from psycopg.types.json import Jsonb

from build_question_candidates_from_mineru import (
    markup_payload,
    normalize_text,
    normalize_science_markup,
    normalize_trademark_superscript_spacing,
)


DEFAULT_CATEGORIES = ["藥師", "藥師(一)", "藥師(二)"]
REPAIRABLE_ACTIONS = {"block", "needs_review", "correct"}
NON_QUESTION_ACTIONS = {
    "confirm_not_group",
    "confirm_group",
    "reset_group_review",
    "human_review_pdf_visual",
}
NOTATION_SOURCE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"K(?:M|_M|_\{M\})|V(?:max|_max|_\{max\}|D|_D|p|_p|ss|_ss|exp|_exp)|"
    r"C(?:max|_max|p|_p|cr|_cr|ss|SS|_ss|_SS)|t(?:max|_max|_\{max\})|"
    r"Clcr|CLcr|fe|fu|D0|D_0|DL|D_L|Rin|R_in|MW\s+dextrose|"
    r"Ksp|K_sp|Du(?:∞)?|D_u(?:∞)?|D_\{u(?:\(0-t\))?\}|"
    r"Co|C0|Eo|E0|[~～]\s*(?:h⁻¹|hr|L)|\\+\s*e"
    r")(?![A-Za-z0-9])"
)
REVIEWED_ACTIONS = {"accept", "unblock", "reviewed", "comment"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--category", action="append", default=[])
    parser.add_argument(
        "--all-categories",
        action="store_true",
        help="Scan every non-excluded category. Without this flag the historical pharmacy default is used.",
    )
    parser.add_argument("--candidate-key", action="append", default=[])
    parser.add_argument("--output-dir", type=Path, default=Path("tmp/reviewed_text_normalization"))
    parser.add_argument(
        "--audit-all",
        action="store_true",
        help="Inspect every non-excluded candidate in scope without writing repair events.",
    )
    parser.add_argument(
        "--include-unreviewed",
        action="store_true",
        help="Include candidates with no question review event; they remain unreviewed after a repair.",
    )
    parser.add_argument(
        "--include-reviewed",
        action="store_true",
        help="Include accepted/reviewed candidates for safe text-only repairs while retaining their action.",
    )
    parser.add_argument(
        "--match-text",
        default="",
        help="Restrict the scan to candidates whose raw JSON contains this exact text.",
    )
    parser.add_argument(
        "--notation-only",
        action="store_true",
        help="Restrict repair to confirmed scientific/biomedical notation tokens; do not propagate unrelated OCR rules.",
    )
    parser.add_argument(
        "--reset-reviewed-repairs",
        action="store_true",
        help="After a repair, append reset_review for previously reviewed repair events so human review is required again.",
    )
    parser.add_argument("--apply", action="store_true", help="Append events. Without this flag the command is a dry run.")
    return parser.parse_args()


def effective_candidate(raw: dict[str, Any], correction: dict[str, Any] | None) -> dict[str, Any]:
    value = deepcopy(raw)
    for key, replacement in (correction or {}).items():
        if key == "options" and isinstance(replacement, list):
            by_key = {str(row.get("key") or "").upper(): row for row in value.get("options") or []}
            for row in replacement:
                option_key = str(row.get("key") or "").upper()
                if option_key:
                    by_key[option_key] = {**by_key.get(option_key, {}), **row, "key": option_key}
            value["options"] = [by_key[key] for key in sorted(by_key)]
        else:
            value[key] = replacement
    return value


def normalization_equivalent(
    left: str,
    right: str,
    *,
    category: str | None,
    subject: str | None,
    normalizer: Callable[..., str] = normalize_text,
) -> bool:
    """Treat whitespace-only presentation differences as safe merge candidates."""
    compact = lambda value: "".join(normalizer(value, category=category, subject=subject).split())
    return compact(left) == compact(right)


def normalized_overlay(
    raw: dict[str, Any],
    existing: dict[str, Any] | None,
    *,
    category: str | None = None,
    subject: str | None = None,
    normalizer: Callable[..., str] = normalize_text,
    source_pattern: re.Pattern[str] | None = None,
) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    """Plan a safe parser-normalization overlay without replacing manual edits.

    The newest correction JSON replaces the previous correction JSON in the UI.
    Therefore the overlay begins with the current correction and only changes a
    field when its current value still exactly equals parser source text.  Any
    other difference is treated as an existing human/AI edit and reported as a
    conflict for human review instead of being overwritten.
    """
    current = effective_candidate(raw, existing)
    overlay = deepcopy(existing or {})
    rule_changes: list[str] = []
    conflicts: list[str] = []
    pending_change = False

    raw_stem = str(raw.get("stem") or "")
    normalized_stem = (
        normalizer(raw_stem, category=category, subject=subject)
        if source_pattern is None or source_pattern.search(raw_stem)
        else raw_stem
    )
    current_stem = str(current.get("stem") or "")
    if normalized_stem != raw_stem:
        rule_changes.append("題幹")
    # A previous repair or human correction may already contain the same
    # parser result in a different but equivalent presentation (for example
    # visible <sub> markup). Treat it as effective so repeated scans stay
    # idempotent and do not append duplicate repair events.
    # Do not treat the raw source as already repaired merely because running
    # the parser on it would produce the desired result.  The review UI reads
    # the stored candidate/overlay, so a raw `KM` still needs a correction
    # overlay containing `K<sub>M</sub>`.  Only an existing non-raw overlay
    # (or an exact normalized value) counts as effective.
    current_stem_is_effective = (
        current_stem == normalized_stem
        or (
            current_stem != raw_stem
            and (
                normalizer(current_stem, category=category, subject=subject) == normalized_stem
                or normalization_equivalent(
                    current_stem,
                    raw_stem,
                    category=category,
                    subject=subject,
                    normalizer=normalizer,
                )
            )
        )
    )
    if normalized_stem == raw_stem or current_stem == normalized_stem:
        pass
    elif normalized_stem != raw_stem and current_stem != raw_stem and current_stem_is_effective:
        overlay["stem"] = normalized_stem
        overlay.pop("stem_markup", None)
        pending_change = True
    elif normalized_stem != raw_stem and current_stem == raw_stem:
        overlay["stem"] = normalized_stem
        # `stem_markup` is not an editable UI field; avoid carrying stale markup.
        overlay.pop("stem_markup", None)
        pending_change = True
    elif normalized_stem != raw_stem and current_stem not in {raw_stem, normalized_stem}:
        if normalization_equivalent(
            current_stem,
            raw_stem,
            category=category,
            subject=subject,
            normalizer=normalizer,
        ):
            overlay["stem"] = normalizer(current_stem, category=category, subject=subject)
            overlay.pop("stem_markup", None)
            pending_change = True
        elif normalize_trademark_superscript_spacing(current_stem) == normalized_stem:
            overlay["stem"] = normalized_stem
            overlay.pop("stem_markup", None)
            pending_change = True
        else:
            conflicts.append("題幹已有不同於 parser 原文的校正，未自動覆蓋。")

    raw_options = raw.get("options") if isinstance(raw.get("options"), list) else []
    current_options = {str(row.get("key") or "").upper(): row for row in current.get("options") or []}
    option_keys = [str(row.get("key") or "").upper() for row in raw_options]
    replacement_by_key: dict[str, str] = {}
    if len(option_keys) != len(set(option_keys)):
        # Duplicate labels are a parser issue.  Never use an index keyed by label
        # to write a text overlay for such a candidate.
        if any(
            normalize_text(str(row.get("text") or ""), category=category, subject=subject)
            != str(row.get("text") or "")
            for row in raw_options
        ):
            conflicts.append("選項標籤重複，未自動套用文字正規化。")
    for raw_option in raw_options:
        key = str(raw_option.get("key") or "").upper()
        if len(option_keys) != len(set(option_keys)):
            continue
        current_option = current_options.get(key, {})
        raw_text = str(raw_option.get("text") or "")
        normalized_text = (
            normalizer(raw_text, category=category, subject=subject)
            if source_pattern is None or source_pattern.search(raw_text)
            else raw_text
        )
        current_text = str(current_option.get("text") or raw_text)
        if normalized_text == raw_text:
            continue
        rule_changes.append(f"選項 {key}")
        current_text_is_effective = (
            current_text == normalized_text
            or (
                current_text != raw_text
                and (
                    normalizer(current_text, category=category, subject=subject) == normalized_text
                    or normalization_equivalent(
                        current_text,
                        raw_text,
                        category=category,
                        subject=subject,
                        normalizer=normalizer,
                    )
                )
            )
        )
        if current_text == normalized_text:
            continue
        if current_text != raw_text and current_text_is_effective:
            replacement_by_key[key] = normalized_text
            continue
        if current_text == raw_text:
            replacement_by_key[key] = normalized_text
        elif current_text not in {raw_text, normalized_text}:
            if normalization_equivalent(
                current_text,
                raw_text,
                category=category,
                subject=subject,
                normalizer=normalizer,
            ):
                replacement_by_key[key] = normalizer(current_text, category=category, subject=subject)
            elif normalize_trademark_superscript_spacing(current_text) == normalized_text:
                replacement_by_key[key] = normalized_text
            else:
                conflicts.append(f"選項 {key} 已有不同於 parser 原文的校正，未自動覆蓋。")

    if replacement_by_key:
        # Options are replaced as a complete list by the Review UI.  Start from
        # the effective current list so manually corrected option text/assets stay.
        normalized_options = deepcopy(current.get("options") or raw_options)
        for option in normalized_options:
            key = str(option.get("key") or "").upper()
            if key not in replacement_by_key:
                continue
            option["text"] = replacement_by_key[key]
            option["markup"] = markup_payload(replacement_by_key[key])
        overlay["options"] = normalized_options
        pending_change = True

    if conflicts:
        return None, rule_changes, conflicts
    return (overlay if pending_change else None), rule_changes, conflicts


def query_rows(
    conn: psycopg.Connection[Any],
    categories: list[str],
    candidate_keys: list[str],
    *,
    audit_all: bool,
) -> list[dict[str, Any]]:
    clauses = ["c.review_status <> 'excluded'"]
    params: list[Any] = []
    if not audit_all:
        clauses.append("latest.action = ANY(%s)")
        params.append(list(REPAIRABLE_ACTIONS))
    if candidate_keys:
        clauses.append("c.candidate_key = ANY(%s)")
        params.append(candidate_keys)
    elif categories:
        clauses.append("COALESCE(c.raw_candidate_json->'metadata'->>'normalized_category_name', c.raw_candidate_json->'metadata'->>'group_name', '') = ANY(%s)")
        params.append(categories)
    where = " AND ".join(clauses)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH latest AS (
                SELECT DISTINCT ON (e.candidate_key)
                    e.candidate_key, e.action, e.reviewer, e.notes,
                    e.corrected_candidate_json, e.event_json, e.created_at
                FROM exam.question_review_events e
                WHERE e.action <> ALL(%s)
                ORDER BY e.candidate_key, e.created_at DESC, e.id DESC
            )
            SELECT c.candidate_key, c.review_status, c.raw_candidate_json,
                   latest.action, latest.reviewer, latest.notes,
                   latest.corrected_candidate_json, latest.event_json
            FROM exam.question_candidates c
            {"LEFT JOIN" if audit_all else "JOIN"} latest ON latest.candidate_key = c.candidate_key
            WHERE {where}
            ORDER BY c.candidate_key
            """,
            [list(NON_QUESTION_ACTIONS), *params],
        )
        columns = [column.name for column in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def repair_event(
    row: dict[str, Any],
    correction: dict[str, Any],
    *,
    repair_kind: str = "safe_text_normalization",
    repair_note: str | None = None,
) -> dict[str, Any]:
    original_note = str(row.get("notes") or "").strip()
    repair_note = repair_note or "Codex 文字正規化修復：依官方 PDF 與現行 parser 規則更新題幹/選項；保留原審核狀態，請人工複核後再決定是否通過。"
    note_parts = [original_note] if original_note else []
    if repair_note and repair_note not in original_note:
        note_parts.append(repair_note)
    notes = "\n\n".join(note_parts)
    event = {
        "candidate_key": row["candidate_key"],
        "reviewer": "codex-text-normalization-repair",
        "action": "reset_review" if row.get("action") in REVIEWED_ACTIONS else (row.get("action") or "unreviewed"),
        "notes": notes,
        "correction": correction,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repair_kind": repair_kind,
        "previous_action": row.get("action"),
        "previous_notes": original_note,
        "source_event": {
            "reviewer": row.get("reviewer"),
            "action": row.get("action"),
        },
    }
    return event


def main() -> int:
    args = parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")
    if args.audit_all and args.apply:
        raise SystemExit("--audit-all is read-only; remove --apply to append only safe non-accepted repairs.")
    categories = [] if args.all_categories else (args.category or DEFAULT_CATEGORIES)
    audit_all = args.audit_all or args.include_unreviewed or args.include_reviewed or args.reset_reviewed_repairs
    normalizer: Callable[..., str] = normalize_text
    source_pattern: re.Pattern[str] | None = None
    repair_kind = "safe_text_normalization"
    repair_note = None
    if args.notation_only:
        normalizer = lambda value, **_kwargs: normalize_science_markup(value)
        source_pattern = NOTATION_SOURCE_RE
        repair_kind = "safe_scientific_notation_normalization"
        repair_note = "Codex 上下標/科學符號修復：依人工註記與現行 parser token 規則更新題幹/選項；保留原審核狀態與註記，請人工複核後再決定是否通過。"
    with psycopg.connect(args.database_url) as conn:
        rows = query_rows(conn, categories, args.candidate_key, audit_all=audit_all)
        events: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        rule_matched = 0
        already_effective = 0
        manual_conflicts = 0
        safe_nonaccepted = 0
        for row in rows:
            raw = row["raw_candidate_json"] or {}
            if args.match_text and args.match_text not in json.dumps(raw, ensure_ascii=False):
                continue
            existing = row["corrected_candidate_json"] or {}
            metadata = raw.get("metadata") or {}
            category = metadata.get("normalized_category_name") or metadata.get("official_category_name")
            subject = metadata.get("normalized_subject_name") or metadata.get("official_subject_name")
            overlay, rule_changes, conflicts = normalized_overlay(
                raw,
                existing,
                category=category,
                subject=subject,
                normalizer=normalizer,
                source_pattern=source_pattern,
            )
            if rule_changes:
                rule_matched += 1
            if rule_changes and not overlay and not conflicts:
                already_effective += 1
            if conflicts:
                manual_conflicts += 1
            current_action = str(row.get("action") or "")
            can_apply = bool(overlay) and (
                current_action in REPAIRABLE_ACTIONS
                or (
                    args.include_unreviewed
                    and current_action in {"", "unreviewed", "reset_review"}
                )
                or (args.include_reviewed and current_action in {"accept", "unblock", "reviewed", "comment"})
            )
            if can_apply:
                safe_nonaccepted += 1
            if rule_changes or conflicts:
                findings.append(
                    {
                        "candidate_key": row["candidate_key"],
                        "action": row.get("action"),
                        "reviewer": row.get("reviewer"),
                        "rule_changes": rule_changes,
                        "conflicts": conflicts,
                        "already_effective": bool(rule_changes and not overlay and not conflicts),
                        "safe_to_apply_for_nonaccepted": can_apply,
                    }
                )
            if not args.audit_all and can_apply and overlay:
                events.append(
                    repair_event(
                        row,
                        overlay,
                        repair_kind=repair_kind,
                        repair_note=repair_note,
                    )
                )
            if (
                not args.audit_all
                and args.reset_reviewed_repairs
                and current_action in REVIEWED_ACTIONS
                and row.get("reviewer") == "codex-text-normalization-repair"
                and isinstance(existing, dict)
                and existing
                and overlay
                and isinstance(row.get("event_json"), dict)
                and row["event_json"].get("repair_kind") == repair_kind
            ):
                events.append(
                    repair_event(
                        row,
                        existing,
                        repair_kind=repair_kind,
                        repair_note=repair_note,
                    )
                )

        args.output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        artifact = args.output_dir / f"text_normalization_repair__{stamp}.json"
        artifact.write_text(
            json.dumps(
                {
                    "dry_run": not args.apply,
                    "audit_all": args.audit_all,
                    "events": events,
                    "findings": findings,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        summary = {
            "dry_run": not args.apply,
            "audit_all": args.audit_all,
            "scanned": len(rows),
            "rule_matched": rule_matched,
            "already_effective": already_effective,
            "manual_conflicts": manual_conflicts,
            "safe_nonaccepted": safe_nonaccepted,
            "repairable": len(events),
            "artifact": str(artifact),
        }
        if not args.apply or args.audit_all:
            print(json.dumps(summary, ensure_ascii=False))
            return 0

        with conn.cursor() as cur:
            for event in events:
                cur.execute(
                    """
                    INSERT INTO exam.question_review_events (
                        candidate_id, candidate_key, reviewer, action,
                        corrected_candidate_json, event_json, notes, created_at
                    )
                    SELECT id, %s, %s, %s, %s, %s, %s, %s
                    FROM exam.question_candidates
                    WHERE candidate_key = %s
                    """,
                    (
                        event["candidate_key"], event["reviewer"], event["action"],
                        Jsonb(event["correction"]), Jsonb(event), event["notes"],
                        event["created_at"], event["candidate_key"],
                    ),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(f"candidate not found: {event['candidate_key']}")
                cur.execute(
                    "UPDATE exam.question_candidates SET updated_at = now() WHERE candidate_key = %s",
                    (event["candidate_key"],),
                )
        conn.commit()
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
