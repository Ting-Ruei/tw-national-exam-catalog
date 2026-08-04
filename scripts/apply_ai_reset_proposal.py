#!/usr/bin/env python3
"""Apply an explicitly approved advisory-AI catch report as reset_review events.

The report is inert by default.  ``--apply`` plus a human ``--approval-ref``
is required, and every candidate is rechecked against its current human action
and visible-content hash before an append-only reset event is written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError:  # pragma: no cover - apply path runs in review-ui
    psycopg = None
    Jsonb = None


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(candidate: dict[str, Any]) -> str:
    visible = {
        "stem": candidate.get("stem", ""),
        "options": candidate.get("options") or [],
        "group_ref": candidate.get("group_ref"),
        "group_sequence_no": candidate.get("group_sequence_no"),
        "image_refs": candidate.get("image_refs") or [],
    }
    return hashlib.sha256(canonical_json(visible).encode("utf-8")).hexdigest()


def json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(value) if isinstance(value, dict) else {}
    return {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal", type=Path, required=True)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--approval-ref", help="Human approval reference; required with --apply")
    parser.add_argument("--reviewer", default="ai-reset")
    parser.add_argument(
        "--reset-notes",
        default="已核准 advisory AI 抓漏清單；退回未審，需人工重新審核。",
    )
    parser.add_argument("--candidate-key", action="append", dest="candidate_keys")
    parser.add_argument("--legacy-review-log", type=Path)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def selected_candidates(proposal: dict[str, Any], keys: list[str] | None) -> list[dict[str, Any]]:
    if not proposal.get("requires_explicit_human_approval_before_reset"):
        raise ValueError("proposal does not declare the required approval gate")
    candidates = [row for row in proposal.get("candidates") or [] if row.get("requires_reset")]
    requested = set(keys or [])
    if requested:
        eligible = {str(row.get("candidate_key") or "") for row in candidates}
        unknown = requested - eligible
        if unknown:
            raise ValueError(f"candidate keys are not eligible in this proposal: {sorted(unknown)[:10]}")
        candidates = [row for row in candidates if str(row.get("candidate_key") or "") in requested]
    return candidates


def current_state(cur: Any, candidate_key: str) -> tuple[dict[str, Any], dict[str, Any]]:
    cur.execute(
        """
        SELECT c.raw_candidate_json,
               e.action, e.corrected_candidate_json, e.notes, e.reviewer, e.created_at
        FROM exam.question_candidates c
        LEFT JOIN LATERAL (
            SELECT action, corrected_candidate_json, notes, reviewer, created_at
            FROM exam.question_review_events
            WHERE candidate_key = c.candidate_key
              AND action NOT IN ('confirm_not_group', 'confirm_group', 'reset_group_review', 'human_review_pdf_visual')
            ORDER BY id DESC
            LIMIT 1
        ) e ON true
        WHERE c.candidate_key = %s
        """,
        (candidate_key,),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError(f"candidate not found: {candidate_key}")
    raw, action, correction, notes, reviewer, created_at = row
    candidate = json_object(raw)
    candidate.update(json_object(correction))
    return candidate, {
        "action": action or "unreviewed",
        "correction": json_object(correction) or None,
        "notes": notes or "",
        "reviewer": reviewer or "",
        "created_at": created_at.isoformat(timespec="seconds") if created_at else None,
    }


def append_legacy(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(canonical_json(event) + "\n")


def main() -> int:
    args = parse_args()
    if args.apply and (not args.approval_ref or len(args.approval_ref.strip()) < 4):
        raise SystemExit("--apply requires a non-empty --approval-ref; AI output is not approval.")
    if args.apply and not args.database_url:
        raise SystemExit("DATABASE_URL is required with --apply")
    if args.apply and (psycopg is None or Jsonb is None):
        raise SystemExit("psycopg is required with --apply; run inside review-ui")

    proposal = json.loads(args.proposal.read_text(encoding="utf-8"))
    candidates = selected_candidates(proposal, args.candidate_keys)
    proposal_hash = hashlib.sha256(args.proposal.read_bytes()).hexdigest()
    summary: dict[str, Any] = {
        "ok": True,
        "dry_run": not args.apply,
        "proposal": str(args.proposal),
        "proposal_hash": proposal_hash,
        "report_type": proposal.get("schema_version") or proposal.get("report_type"),
        "selected_count": len(candidates),
        "approval_ref": args.approval_ref if args.apply else None,
        "events": [],
        "skipped": [],
    }
    if not args.apply:
        summary["eligible_candidate_keys"] = [row.get("candidate_key") for row in candidates]
        print(json.dumps(summary, ensure_ascii=False))
        return 0

    events: list[dict[str, Any]] = []
    with psycopg.connect(args.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('exam.formal_sync_queue')")
            queue_exists = bool(cur.fetchone()[0])
            for item in candidates:
                candidate_key = str(item.get("candidate_key") or "")
                candidate, previous = current_state(cur, candidate_key)
                expected_action = str(item.get("current_human_action") or "")
                if previous["action"] != expected_action:
                    summary["skipped"].append({"candidate_key": candidate_key, "reason": "human_action_changed", "expected": expected_action, "actual": previous["action"]})
                    continue
                expected_hash = str(item.get("effective_content_hash") or "")
                if expected_hash and expected_hash != content_hash(candidate):
                    summary["skipped"].append({"candidate_key": candidate_key, "reason": "candidate_content_changed"})
                    continue
                event = {
                    "candidate_key": candidate_key,
                    "action": "reset_review",
                    "reviewer": args.reviewer,
                    "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "notes": args.reset_notes,
                    "previous_action": previous["action"],
                    "previous_notes": previous["notes"],
                    "previous_reviewer": previous["reviewer"],
                    "previous_reviewed_at": previous["created_at"],
                    "previous_correction": previous["correction"],
                    "approval_ref": args.approval_ref,
                    "ai_catch_report_hash": proposal_hash,
                    "ai_report_type": proposal.get("schema_version") or proposal.get("report_type"),
                    "ai_model": proposal.get("model"),
                    "ai_prompt_version": proposal.get("prompt_version"),
                    "ai_issue_families": item.get("ai_issue_families") or [],
                    "ai_summary": item.get("ai_summary") or "",
                    "ai_evidence": item.get("ai_evidence") or [],
                    "model_agreement": item.get("model_agreement") or {},
                    "advisory_only": True,
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
                    (candidate_key, args.reviewer, Jsonb(event), event["notes"], event["created_at"], candidate_key),
                )
                event_id = cur.fetchone()[0]
                cur.execute("UPDATE exam.question_candidates SET review_status = 'unreviewed', updated_at = now() WHERE candidate_key = %s", (candidate_key,))
                if queue_exists:
                    cur.execute(
                        """
                        INSERT INTO exam.formal_sync_queue (candidate_key, requested_at, processed_at, last_error)
                        VALUES (%s, now(), NULL, NULL)
                        ON CONFLICT (candidate_key) DO UPDATE
                        SET requested_at = now(), processed_at = NULL, last_error = NULL
                        """,
                        (candidate_key,),
                    )
                events.append({**event, "event_id": int(event_id)})
        conn.commit()
    if args.legacy_review_log:
        append_legacy(args.legacy_review_log, events)
    summary["events"] = events
    summary["applied_count"] = len(events)
    summary["formal_sync_queued"] = queue_exists
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
