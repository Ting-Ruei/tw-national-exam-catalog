#!/usr/bin/env python3
"""Audit candidate question numbers against official answer-table numbers.

The audit compares number *sets* per source document, not only row counts.
It is read-only by default.  ``--apply`` only reconciles advisory parser
issues on candidates whose effective human question gate is still unreviewed;
it never writes review events or accepts/blocks a human review decision.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

import build_question_candidates_from_mineru as builder


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "tmp" / "question_answer_alignment"
ALIGNMENT_ISSUE_CODES = {
    "answer_number_set_unusable",
    "question_answer_number_set_mismatch",
}
UNREVIEWED_GATE_STATUSES = {"unreviewed"}
AUDIT_VERSION = "question_answer_alignment_v1"


@dataclass
class CandidateRow:
    candidate_id: int
    candidate_key: str
    source_registry_key: str
    question_number: int
    category: str
    subject: str
    year: str
    exam_ordinal: str
    question_gate_status: str
    metadata: dict[str, Any]


@dataclass
class DocumentAudit:
    source_registry_key: str
    category: str
    subject: str
    year: str
    exam_ordinal: str
    answer_role: str
    answer_markdown: str
    candidate_count: int
    candidate_distinct_count: int
    answer_number_count: int
    authoritative_answer_set: bool
    authority_reason: str
    missing_candidate_numbers: list[int]
    candidate_numbers_absent_from_answer_key: list[int]
    duplicate_candidate_numbers: list[int]
    eligible_unreviewed_count: int
    status: str
    issue_anchor_candidate_key: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="PostgreSQL URL. Defaults to DATABASE_URL.",
    )
    parser.add_argument(
        "--category-like",
        help="SQL LIKE filter for normalized category, for example %%藥%%.",
    )
    parser.add_argument(
        "--review-scope",
        choices=("all", "unreviewed"),
        default="unreviewed",
        help="Audit all matching documents or only documents with unreviewed candidates.",
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Reconcile active alignment issues on unreviewed candidates.",
    )
    return parser.parse_args()


def fetch_candidate_rows(
    conn: psycopg.Connection[Any],
    category_like: str | None,
) -> list[CandidateRow]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                c.id,
                c.candidate_key,
                c.source_registry_key,
                c.question_number,
                s.category,
                s.subject,
                s.year,
                s.exam_ordinal,
                s.question_gate_status,
                COALESCE(c.raw_candidate_json->'metadata', '{}'::jsonb)
            FROM exam.question_candidates c
            JOIN exam.review_candidate_state s ON s.candidate_id = c.id
            WHERE c.question_number ~ '^[0-9]+$'
              AND (%s::text IS NULL OR s.category LIKE %s)
            ORDER BY c.source_registry_key, c.question_number::integer, c.id
            """,
            (category_like, category_like),
        )
        return [
            CandidateRow(
                candidate_id=int(row[0]),
                candidate_key=str(row[1]),
                source_registry_key=str(row[2]),
                question_number=int(row[3]),
                category=str(row[4] or ""),
                subject=str(row[5] or ""),
                year=str(row[6] or ""),
                exam_ordinal=str(row[7] or ""),
                question_gate_status=str(row[8] or "unreviewed"),
                metadata=dict(row[9] or {}),
            )
            for row in cur.fetchall()
        ]


def resolve_project_path(value: str | None) -> Path | None:
    if not value:
        return None
    raw = Path(value).expanduser()
    if raw.exists():
        return raw
    parts = raw.parts
    for marker in ("國考題資料夾", "國考題資料夾_其他類型"):
        if marker not in parts:
            continue
        index = parts.index(marker)
        rebased = PROJECT_ROOT.joinpath(*parts[index:])
        if rebased.exists():
            return rebased
    rebased = PROJECT_ROOT / value
    return rebased if rebased.exists() else None


def answer_markdown_for_row(row: CandidateRow) -> Path | None:
    relative = row.metadata.get("answer_markdown_relative")
    resolved = resolve_project_path(str(relative)) if relative else None
    if resolved:
        return resolved
    absolute = row.metadata.get("answer_markdown")
    return resolve_project_path(str(absolute)) if absolute else None


def choose_anchor(
    rows: list[CandidateRow],
    eligible: list[CandidateRow],
    missing_numbers: list[int],
    extra_numbers: list[int],
    duplicate_numbers: list[int],
) -> str:
    if not eligible:
        return ""
    preferred_numbers = extra_numbers + duplicate_numbers
    for number in preferred_numbers:
        for row in eligible:
            if row.question_number == number:
                return row.candidate_key
    if missing_numbers:
        missing = missing_numbers[0]
        preceding = [row for row in eligible if row.question_number < missing]
        if preceding:
            return preceding[-1].candidate_key
        following = [row for row in eligible if row.question_number > missing]
        if following:
            return following[0].candidate_key
    return eligible[0].candidate_key


def audit_documents(
    rows: list[CandidateRow],
    review_scope: str,
) -> tuple[list[DocumentAudit], dict[str, CandidateRow]]:
    by_source: dict[str, list[CandidateRow]] = defaultdict(list)
    by_key: dict[str, CandidateRow] = {}
    for row in rows:
        by_source[row.source_registry_key].append(row)
        by_key[row.candidate_key] = row

    audits: list[DocumentAudit] = []
    for source, source_rows in sorted(by_source.items()):
        eligible = [
            row
            for row in source_rows
            if row.question_gate_status in UNREVIEWED_GATE_STATUSES
        ]
        if review_scope == "unreviewed" and not eligible:
            continue
        first = source_rows[0]
        markdown_path = answer_markdown_for_row(first)
        answer_numbers: set[int] = set()
        if markdown_path:
            answer_numbers = builder.parse_answer_question_numbers(
                markdown_path.read_text(encoding="utf-8", errors="replace")
            )
        occurrences = Counter(row.question_number for row in source_rows)
        candidate_numbers = set(occurrences)
        authoritative, reason = builder.answer_number_set_is_authoritative(
            answer_numbers,
            candidate_numbers,
        )
        missing = sorted(answer_numbers - candidate_numbers) if authoritative else []
        extras = sorted(candidate_numbers - answer_numbers) if authoritative else []
        duplicates = sorted(number for number, count in occurrences.items() if count > 1)
        if not markdown_path:
            authoritative = False
            reason = "answer_markdown_not_found"
        if not authoritative:
            status = "unusable_answer_set"
        elif missing or extras or duplicates:
            status = "mismatch"
        else:
            status = "aligned"
        anchor = choose_anchor(
            source_rows,
            eligible,
            missing,
            extras,
            duplicates,
        )
        audits.append(
            DocumentAudit(
                source_registry_key=source,
                category=first.category,
                subject=first.subject,
                year=first.year,
                exam_ordinal=first.exam_ordinal,
                answer_role=str(first.metadata.get("answer_role_primary") or ""),
                answer_markdown=str(markdown_path or ""),
                candidate_count=len(source_rows),
                candidate_distinct_count=len(candidate_numbers),
                answer_number_count=len(answer_numbers),
                authoritative_answer_set=authoritative,
                authority_reason=reason,
                missing_candidate_numbers=missing,
                candidate_numbers_absent_from_answer_key=extras,
                duplicate_candidate_numbers=duplicates,
                eligible_unreviewed_count=len(eligible),
                status=status,
                issue_anchor_candidate_key=anchor,
            )
        )
    return audits, by_key


def issue_payload(audit: DocumentAudit) -> tuple[str, str, str, dict[str, Any]] | None:
    common = {
        "audit_version": AUDIT_VERSION,
        "answer_markdown": audit.answer_markdown,
        "answer_role": audit.answer_role,
        "candidate_count": audit.candidate_count,
        "candidate_distinct_count": audit.candidate_distinct_count,
        "answer_number_count": audit.answer_number_count,
    }
    if audit.status == "unusable_answer_set":
        return (
            "answer_number_set_unusable",
            "blocked",
            "官方答案 Markdown 的題號集合不完整或不可信，無法自動確認題本是否缺題／多題。",
            {**common, "reason": audit.authority_reason},
        )
    if audit.status == "mismatch":
        return (
            "question_answer_number_set_mismatch",
            "blocked",
            "同一份題本的候選題號與官方答案題號不一致，可能有吞題、重題或誤把頁首切成題目。",
            {
                **common,
                "missing_candidate_numbers": audit.missing_candidate_numbers,
                "candidate_numbers_absent_from_answer_key": (
                    audit.candidate_numbers_absent_from_answer_key
                ),
                "duplicate_candidate_numbers": audit.duplicate_candidate_numbers,
            },
        )
    return None


def reconcile_issues(
    conn: psycopg.Connection[Any],
    audits: list[DocumentAudit],
    by_key: dict[str, CandidateRow],
) -> dict[str, int]:
    desired: dict[tuple[str, str], tuple[DocumentAudit, tuple[str, str, str, dict[str, Any]]]] = {}
    eligible_keys = {
        row.candidate_key
        for row in by_key.values()
        if row.question_gate_status in UNREVIEWED_GATE_STATUSES
    }
    scoped_sources = {audit.source_registry_key for audit in audits}
    for audit in audits:
        payload = issue_payload(audit)
        if not payload or audit.issue_anchor_candidate_key not in eligible_keys:
            continue
        desired[(audit.source_registry_key, payload[0])] = (audit, payload)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, candidate_key, source_registry_key, issue_code,
                   severity, message, COALESCE(issue_json, '{}'::jsonb)
            FROM exam.question_parse_issues
            WHERE resolved_at IS NULL
              AND issue_code = ANY(%s)
              AND source_registry_key = ANY(%s)
            ORDER BY id
            """,
            (sorted(ALIGNMENT_ISSUE_CODES), sorted(scoped_sources)),
        )
        active = cur.fetchall()

    active_by_key: dict[tuple[str, str], list[tuple[Any, ...]]] = defaultdict(list)
    for row in active:
        if str(row[1] or "") in eligible_keys:
            active_by_key[(str(row[2]), str(row[3]))].append(row)

    resolved = 0
    inserted = 0
    unchanged = 0
    with conn.cursor() as cur:
        for key in sorted(set(active_by_key) | set(desired)):
            wanted = desired.get(key)
            current = active_by_key.get(key, [])
            exact_id: int | None = None
            if wanted:
                audit, (code, severity, message, payload_json) = wanted
                for row in current:
                    if (
                        str(row[1] or "") == audit.issue_anchor_candidate_key
                        and str(row[4]) == severity
                        and str(row[5]) == message
                        and dict(row[6] or {}) == payload_json
                    ):
                        exact_id = int(row[0])
                        break
            for row in current:
                if exact_id is not None and int(row[0]) == exact_id:
                    continue
                cur.execute(
                    "UPDATE exam.question_parse_issues SET resolved_at = now() WHERE id = %s",
                    (int(row[0]),),
                )
                resolved += 1
            if exact_id is not None:
                unchanged += 1
                continue
            if not wanted:
                continue
            audit, (code, severity, message, payload_json) = wanted
            anchor = by_key[audit.issue_anchor_candidate_key]
            cur.execute(
                """
                INSERT INTO exam.question_parse_issues (
                    candidate_id, candidate_key, source_registry_key,
                    issue_code, severity, message, issue_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    anchor.candidate_id,
                    anchor.candidate_key,
                    audit.source_registry_key,
                    code,
                    severity,
                    message,
                    Jsonb(payload_json),
                ),
            )
            inserted += 1
    return {"inserted": inserted, "resolved": resolved, "unchanged": unchanged}


def write_report(output_dir: Path, audits: list[DocumentAudit], summary: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    fields = list(asdict(audits[0]).keys()) if audits else list(DocumentAudit.__annotations__)
    with (output_dir / "document_alignment.csv").open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for audit in audits:
            row = asdict(audit)
            for key, value in row.items():
                if isinstance(value, list):
                    row[key] = json.dumps(value, ensure_ascii=False)
            writer.writerow(row)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required; run inside review-ui or pass --database-url")
    if args.apply and args.review_scope != "unreviewed":
        raise SystemExit("--apply requires --review-scope unreviewed")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = (args.output_dir or DEFAULT_OUTPUT_ROOT / stamp).resolve()

    with psycopg.connect(args.database_url) as conn:
        rows = fetch_candidate_rows(conn, args.category_like)
        audits, by_key = audit_documents(rows, args.review_scope)
        status_counts = Counter(audit.status for audit in audits)
        summary: dict[str, Any] = {
            "audit_version": AUDIT_VERSION,
            "category_like": args.category_like,
            "review_scope": args.review_scope,
            "document_count": len(audits),
            "candidate_row_count": len(rows),
            "status_counts": dict(sorted(status_counts.items())),
            "mismatch_sources": [
                audit.source_registry_key
                for audit in audits
                if audit.status == "mismatch"
            ],
            "unusable_answer_sources": [
                audit.source_registry_key
                for audit in audits
                if audit.status == "unusable_answer_set"
            ],
            "applied": False,
        }
        if args.apply:
            summary["issue_reconciliation"] = reconcile_issues(conn, audits, by_key)
            conn.commit()
            summary["applied"] = True
        write_report(output_dir, audits, summary)
        summary["output_dir"] = str(output_dir)
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
