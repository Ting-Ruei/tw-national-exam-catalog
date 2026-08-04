#!/usr/bin/env python3
"""Reconcile structurally misaligned SQL candidates from a verified rebuild.

Only sources listed as mismatches in an alignment report are considered.  The
rebuilt candidate set must exactly match its official answer-table number set
before any write is allowed.  Existing rows are replaced only when their raw
question boundary changed; unrelated normalization/manual-review content is
left untouched.  Human review history remains append-only.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

import build_question_candidates_from_mineru as builder


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NON_QUESTION_ACTIONS = {
    "confirm_not_group",
    "confirm_group",
    "reset_group_review",
    "human_review_pdf_visual",
}
PRESERVE_REVIEW_ACTIONS = {"block", "needs_review"}
UNREVIEWED_ACTIONS = {None, "", "unreviewed", "reset_review"}
STRUCTURAL_ISSUE_CODES = {
    "duplicate_question_number",
    "question_number_gap",
    "question_answer_number_set_mismatch",
    "answer_number_set_unusable",
    "fixed_exam_question_count_missing",
    "fixed_exam_question_count_out_of_range",
}
REVIEWER = "parser-question-answer-alignment-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--alignment-report", type=Path, required=True)
    parser.add_argument("--candidate-jsonl", type=Path, required=True)
    parser.add_argument("--issue-csv", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "tmp" / "candidate_alignment_reconciliation",
    )
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def mismatch_sources(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [
            row["source_registry_key"]
            for row in csv.DictReader(handle)
            if row.get("status") == "mismatch"
        ]


def read_rebuilt_candidates(
    path: Path,
    targets: set[str],
) -> dict[str, list[dict[str, Any]]]:
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            candidate = json.loads(line)
            source = str(candidate.get("source_registry_key") or "")
            if source in targets:
                by_source[source].append(candidate)
    return by_source


def read_rebuilt_issues(
    path: Path,
    targets: set[str],
) -> dict[str, list[builder.Issue]]:
    by_key: dict[str, list[builder.Issue]] = defaultdict(list)
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("source_registry_key") not in targets:
                continue
            candidate_key = str(row.get("candidate_key") or "")
            if not candidate_key:
                continue
            by_key[candidate_key].append(
                builder.Issue(
                    candidate_key=candidate_key,
                    source_registry_key=str(row["source_registry_key"]),
                    question_number=str(row.get("question_number") or ""),
                    issue_code=str(row["issue_code"]),
                    severity=str(row["severity"]),
                    message=str(row["message"]),
                    issue_json=json.loads(row.get("issue_json") or "{}"),
                )
            )
    return by_key


def resolve_project_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if path.exists():
        return path
    for marker in ("國考題資料夾", "國考題資料夾_其他類型"):
        if marker not in path.parts:
            continue
        rebased = PROJECT_ROOT.joinpath(*path.parts[path.parts.index(marker) :])
        if rebased.exists():
            return rebased
    return None


def validate_rebuild(
    source: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    if not candidates:
        raise RuntimeError(f"{source}: verified rebuild produced no candidates")
    numbers = [int(candidate["question_number"]) for candidate in candidates]
    counts = Counter(numbers)
    duplicates = sorted(number for number, count in counts.items() if count > 1)
    metadata = candidates[0].get("metadata") or {}
    answer_path = resolve_project_path(
        str(
            metadata.get("answer_markdown_relative")
            or metadata.get("answer_markdown")
            or ""
        )
    )
    if not answer_path:
        raise RuntimeError(f"{source}: rebuilt answer markdown is unavailable")
    answer_numbers = builder.parse_answer_question_numbers(
        answer_path.read_text(encoding="utf-8", errors="replace")
    )
    authoritative, reason = builder.answer_number_set_is_authoritative(
        answer_numbers,
        set(numbers),
    )
    if not authoritative:
        raise RuntimeError(f"{source}: answer set is not authoritative: {reason}")
    if set(numbers) != answer_numbers or duplicates:
        raise RuntimeError(
            f"{source}: rebuild is not aligned: missing="
            f"{sorted(answer_numbers - set(numbers))} extra="
            f"{sorted(set(numbers) - answer_numbers)} duplicates={duplicates}"
        )
    return {
        "candidate_count": len(candidates),
        "answer_number_count": len(answer_numbers),
        "number_range": [min(answer_numbers), max(answer_numbers)],
    }


def latest_reviews(
    conn: psycopg.Connection[Any],
    keys: list[str],
) -> dict[str, dict[str, Any]]:
    if not keys:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (candidate_key)
                candidate_key, action, reviewer, notes,
                corrected_candidate_json, event_json, id
            FROM exam.question_review_events
            WHERE candidate_key = ANY(%s)
              AND action <> ALL(%s)
            ORDER BY candidate_key, id DESC
            """,
            (keys, list(NON_QUESTION_ACTIONS)),
        )
        return {
            str(row[0]): {
                "action": row[1],
                "reviewer": row[2],
                "notes": row[3],
                "correction": row[4],
                "event_json": row[5],
                "event_id": row[6],
            }
            for row in cur.fetchall()
        }


def raw_block(candidate: dict[str, Any]) -> str:
    return str((candidate.get("metadata") or {}).get("raw_block") or "")


def normalized_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "stem": candidate.get("stem"),
        "options": candidate.get("options") or [],
        "answer": candidate.get("answer"),
        "answer_payload": candidate.get("answer_payload"),
        "image_refs": candidate.get("image_refs") or [],
        "metadata": candidate.get("metadata") or {},
    }


def upsert_candidate(cur: psycopg.Cursor[Any], candidate: dict[str, Any]) -> None:
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
            %s, %s, source_doc.id, %s, answer_doc.id, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, 'unreviewed', %s, now()
        FROM (SELECT 1) seed
        LEFT JOIN exam.official_documents source_doc
          ON source_doc.registry_key = %s
        LEFT JOIN exam.official_documents answer_doc
          ON answer_doc.registry_key = %s
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
            Jsonb(candidate.get("stem_markup"))
            if candidate.get("stem_markup")
            else None,
            Jsonb(candidate),
            Jsonb(normalized_candidate(candidate)),
            metadata.get("parser_version") or builder.PARSER_VERSION,
            candidate.get("quality_status") or "needs_review",
            int(candidate.get("issue_count") or 0),
            candidate["source_registry_key"],
            candidate.get("answer_source_registry_key"),
        ),
    )


def append_review_event(
    cur: psycopg.Cursor[Any],
    key: str,
    previous: dict[str, Any],
    *,
    deleting: bool = False,
) -> None:
    previous_action = previous.get("action")
    if deleting:
        action = "exclude"
        reason = "此候選為 parser 誤切出的頁首、圖號、題組子序號或重複列，已從候選層移除。"
    elif previous_action in PRESERVE_REVIEW_ACTIONS:
        action = previous_action
        reason = "已依官方答案題號與重建邊界修復，保留原阻擋／待複核狀態與註記。"
    elif previous_action in UNREVIEWED_ACTIONS:
        return
    else:
        action = "reset_review"
        reason = "parser 題目邊界已改變；保留原審核資料並退回人工複核。"
    old_notes = str(previous.get("notes") or "").strip()
    notes = "\n\n".join(part for part in (old_notes, f"[題號對齊修復] {reason}") if part)
    event = {
        "candidate_key": key,
        "action": action,
        "reviewer": REVIEWER,
        "repair_kind": "question_answer_number_alignment",
        "previous_action": previous_action,
        "previous_reviewer": previous.get("reviewer"),
        "previous_notes": old_notes,
        "previous_correction": previous.get("correction"),
        "source_event_id": previous.get("event_id"),
        "candidate_removed": deleting,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    cur.execute(
        """
        INSERT INTO exam.question_review_events (
            candidate_id, candidate_key, reviewer, action,
            corrected_candidate_json, event_json, notes
        )
        SELECT id, candidate_key, %s, %s, NULL, %s, %s
        FROM exam.question_candidates
        WHERE candidate_key = %s
        """,
        (REVIEWER, action, Jsonb(event), notes, key),
    )


def reconcile_parse_issues(
    cur: psycopg.Cursor[Any],
    affected_keys: list[str],
    affected_sources: list[str],
    rebuilt_issues: dict[str, list[builder.Issue]],
) -> None:
    if affected_keys:
        cur.execute(
            """
            UPDATE exam.question_parse_issues
            SET resolved_at = now()
            WHERE resolved_at IS NULL
              AND candidate_key = ANY(%s)
            """,
            (affected_keys,),
        )
    if affected_sources:
        cur.execute(
            """
            UPDATE exam.question_parse_issues
            SET resolved_at = now()
            WHERE resolved_at IS NULL
              AND source_registry_key = ANY(%s)
              AND issue_code = ANY(%s)
            """,
            (affected_sources, sorted(STRUCTURAL_ISSUE_CODES)),
        )
    for key in affected_keys:
        for issue in rebuilt_issues.get(key, []):
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
                    key,
                    issue.source_registry_key,
                    issue.issue_code,
                    issue.severity,
                    issue.message,
                    Jsonb(issue.issue_json),
                    key,
                ),
            )


def main() -> int:
    args = parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")
    targets = mismatch_sources(args.alignment_report)
    if not targets:
        raise SystemExit("alignment report has no mismatch sources")
    target_set = set(targets)
    rebuilt_by_source = read_rebuilt_candidates(args.candidate_jsonl, target_set)
    rebuilt_issues = read_rebuilt_issues(args.issue_csv, target_set)
    missing_rebuild_sources = sorted(target_set - set(rebuilt_by_source))
    if missing_rebuild_sources:
        raise SystemExit(f"verified rebuild is missing sources: {missing_rebuild_sources}")
    validation = {
        source: validate_rebuild(source, rebuilt_by_source[source])
        for source in targets
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    artifact = args.output_dir / f"candidate_alignment_reconciliation__{stamp}.json"
    with psycopg.connect(args.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    candidate_key,
                    source_registry_key,
                    raw_candidate_json,
                    to_jsonb(c)
                FROM exam.question_candidates
                AS c
                WHERE source_registry_key = ANY(%s)
                ORDER BY source_registry_key, id
                """,
                (targets,),
            )
            current_rows = cur.fetchall()
        current = {
            str(key): {
                "source_registry_key": str(source),
                "raw_candidate_json": raw,
                "row": full_row,
            }
            for key, source, raw, full_row in current_rows
        }
        rebuilt = {
            candidate["candidate_key"]: candidate
            for source in targets
            for candidate in rebuilt_by_source[source]
        }
        current_keys = set(current)
        rebuilt_keys = set(rebuilt)
        insert_keys = sorted(rebuilt_keys - current_keys)
        stale_keys = sorted(current_keys - rebuilt_keys)
        changed_boundary_keys = sorted(
            key
            for key in current_keys & rebuilt_keys
            if raw_block(current[key]["raw_candidate_json"])
            != raw_block(rebuilt[key])
        )
        changed_keys = insert_keys + changed_boundary_keys
        reviews = latest_reviews(
            conn,
            sorted(set(stale_keys) | set(changed_boundary_keys)),
        )
        backup_keys = sorted(set(stale_keys) | set(changed_boundary_keys))
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT to_jsonb(e)
                FROM exam.question_review_events e
                WHERE candidate_key = ANY(%s)
                ORDER BY id
                """,
                (backup_keys,),
            )
            backup_review_events = [row[0] for row in cur.fetchall()]
            cur.execute(
                """
                SELECT to_jsonb(e)
                FROM exam.question_parse_issues e
                WHERE candidate_key = ANY(%s)
                ORDER BY id
                """,
                (backup_keys,),
            )
            backup_parse_issues = [row[0] for row in cur.fetchall()]
        report: dict[str, Any] = {
            "dry_run": not args.apply,
            "alignment_report": str(args.alignment_report),
            "candidate_jsonl": str(args.candidate_jsonl),
            "issue_csv": str(args.issue_csv),
            "target_source_count": len(targets),
            "validation": validation,
            "insert_candidate_keys": insert_keys,
            "replace_boundary_candidate_keys": changed_boundary_keys,
            "remove_stale_candidate_keys": stale_keys,
            "preserved_or_reset_review_states": {
                key: {
                    "action": reviews.get(key, {}).get("action"),
                    "reviewer": reviews.get(key, {}).get("reviewer"),
                    "notes": reviews.get(key, {}).get("notes"),
                }
                for key in sorted(set(stale_keys) | set(changed_boundary_keys))
                if reviews.get(key)
            },
            "backup_current_rows": {
                key: current[key]["row"]
                for key in sorted(set(stale_keys) | set(changed_boundary_keys))
            },
            "backup_review_events": backup_review_events,
            "backup_parse_issues": backup_parse_issues,
            "artifact": str(artifact),
        }
        artifact.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        if not args.apply:
            printable = dict(report)
            printable.pop("backup_current_rows", None)
            printable.pop("backup_review_events", None)
            printable.pop("backup_parse_issues", None)
            print(json.dumps(printable, ensure_ascii=False, indent=2, default=str))
            return 0

        with conn.cursor() as cur:
            for key in changed_keys:
                if key in changed_boundary_keys:
                    append_review_event(cur, key, reviews.get(key, {}))
                upsert_candidate(cur, rebuilt[key])
            reconcile_parse_issues(
                cur,
                changed_keys,
                targets,
                rebuilt_issues,
            )
            for key in stale_keys:
                append_review_event(cur, key, reviews.get(key, {}), deleting=True)
            if stale_keys:
                cur.execute(
                    "DELETE FROM exam.question_candidates WHERE candidate_key = ANY(%s)",
                    (stale_keys,),
                )
        conn.commit()

    report["dry_run"] = False
    report["applied"] = True
    artifact.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    printable = dict(report)
    printable.pop("backup_current_rows", None)
    printable.pop("backup_review_events", None)
    printable.pop("backup_parse_issues", None)
    print(json.dumps(printable, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
