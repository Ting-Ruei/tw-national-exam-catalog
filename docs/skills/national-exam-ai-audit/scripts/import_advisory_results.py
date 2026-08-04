#!/usr/bin/env python3
"""Import validated model output as append-only advisory AI events."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--validation-report", type=Path)
    parser.add_argument("--provider", default="local")
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--prompt-version", default="codex_gpt56_luna_question_audit_v3")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--apply", action="store_true", help="Without this flag the command is a dry run")
    parser.add_argument(
        "--skip-reviewed",
        action="store_true",
        help="When applying, skip candidates already closed by a human and import only the still-unreviewed subset.",
    )
    return parser.parse_args()


def read_results(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"line {line_no} is not an object")
            rows.append(value)
    return rows


def audit_payload(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row["status"])
    issue_families = [str(value) for value in row.get("issue_families") or []]
    findings: list[dict[str, Any]] = []
    if status != "pass":
        for finding in row.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            family = str(finding.get("issue_family") or finding.get("code") or "")
            observed = str(finding.get("observed") or "")
            suggested = finding.get("suggested")
            evidence = finding.get("evidence")
            if not evidence and observed:
                evidence = [
                    {
                        "field": finding.get("location") or "content",
                        "before": observed,
                        **({"after": suggested} if suggested is not None else {}),
                    }
                ]
            findings.append(
                {
                    **finding,
                    "code": family or ",".join(issue_families),
                    "severity": "error" if status == "block" else "warning",
                    "field": finding.get("location") or finding.get("field") or "content",
                    "message": finding.get("message") or row.get("reason") or "",
                    "evidence": evidence or row.get("evidence") or [],
                    "suggestion": (
                        suggested
                        if suggested is not None
                        else finding.get("correction_omission_reason")
                        or row.get("recommended_action")
                        or ""
                    ),
                }
            )
        if not findings:
            for issue in issue_families:
                findings.append(
                    {
                        "code": issue,
                        "severity": "error" if status == "block" else "warning",
                        "field": "content",
                        "message": row.get("reason") or "",
                        "evidence": row.get("evidence") or [],
                        "suggestion": row.get("recommended_action") or "",
                    }
                )
    return {
        **row,
        "labels": issue_families or ["pass_likely"],
        "findings": findings,
        "summary": row.get("reason") or "",
        "recommended_action": row.get("recommended_action") or "none",
    }


def assert_question_candidates_still_unreviewed(
    cur: psycopg.Cursor[Any],
    candidate_keys: list[str],
) -> None:
    """Prevent a newer AI import from reactivating work a human already closed."""
    cur.execute(
        """
        WITH latest_question AS (
            SELECT DISTINCT ON (candidate_key) candidate_key, action
            FROM exam.question_review_events
            WHERE action NOT IN (
                'confirm_not_group', 'confirm_group', 'reset_group_review',
                'human_review_pdf_visual'
            )
            ORDER BY candidate_key, id DESC
        )
        SELECT c.candidate_key, COALESCE(lq.action, '')
        FROM exam.question_candidates c
        LEFT JOIN latest_question lq USING (candidate_key)
        WHERE c.candidate_key = ANY(%s)
          AND COALESCE(lq.action, '') NOT IN ('', 'unreviewed', 'reset_review')
        ORDER BY c.candidate_key
        """,
        (candidate_keys,),
    )
    closed = [(str(key), str(action)) for key, action in cur.fetchall()]
    if closed:
        preview = ", ".join(f"{key}={action}" for key, action in closed[:20])
        raise SystemExit(
            "Refusing to import newer AI events for candidates already reviewed "
            f"by a human ({len(closed)}): {preview}"
        )


def unreviewed_candidate_keys(
    cur: psycopg.Cursor[Any],
    candidate_keys: list[str],
) -> set[str]:
    """Return keys whose latest human question event is still open."""
    if not candidate_keys:
        return set()
    cur.execute(
        """
        WITH latest_question AS (
            SELECT DISTINCT ON (candidate_key) candidate_key, action
            FROM exam.question_review_events
            WHERE action NOT IN (
                'confirm_not_group', 'confirm_group', 'reset_group_review',
                'human_review_pdf_visual', 'mobile_defer', 'mobile_resume'
            )
            ORDER BY candidate_key, id DESC
        )
        SELECT c.candidate_key
        FROM exam.question_candidates c
        LEFT JOIN latest_question lq USING (candidate_key)
        WHERE c.candidate_key = ANY(%s)
          AND COALESCE(lq.action, '') IN ('', 'unreviewed', 'reset_review')
        """,
        (candidate_keys,),
    )
    return {str(row[0]) for row in cur.fetchall()}


def main() -> int:
    args = parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")
    validation_path = args.validation_report or args.results.with_suffix(".validation.json")
    if not validation_path.exists():
        raise SystemExit(f"validation report not found: {validation_path}")
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    if not validation.get("ok"):
        raise SystemExit("validation report is not successful")
    rows = read_results(args.results)
    stages = {str(row.get("stage") or "") for row in rows}
    unsupported_stages = sorted(stages - {"question", "image", "answer"})
    if unsupported_stages:
        raise SystemExit(
            "advisory SQL import currently supports question/image/answer stages only; "
            f"unsupported: {', '.join(unsupported_stages)}"
        )
    if len(stages) != 1:
        raise SystemExit("one result file must contain exactly one stage")
    stage = next(iter(stages)) if stages else "question"
    task_type = "answer_audit" if stage == "answer" else "question_format_audit"
    event_table = "exam.answer_ai_review_events" if stage == "answer" else "exam.question_ai_review_events"
    input_hash = hashlib.sha256(args.results.read_bytes()).hexdigest()
    summary = {
        "ok": True,
        "dry_run": not args.apply,
        "result_count": len(rows),
        "provider": args.provider,
        "model": args.model,
        "prompt_version": args.prompt_version,
        "input_hash": input_hash,
        "stage": stage,
        "event_table": event_table,
        "skip_reviewed": bool(args.skip_reviewed),
    }
    if not args.apply and not args.skip_reviewed:
        print(json.dumps(summary, ensure_ascii=False))
        return 0

    if not args.apply and args.skip_reviewed:
        if stage == "question":
            with psycopg.connect(args.database_url) as conn:
                with conn.cursor() as cur:
                    input_keys = [str(row["candidate_key"]) for row in rows]
                    eligible_keys = unreviewed_candidate_keys(cur, input_keys)
            summary["skipped_human_review_count"] = len(rows) - len(eligible_keys)
            summary["eligible_result_count"] = len(eligible_keys)
            summary["result_count"] = len(eligible_keys)
        print(json.dumps(summary, ensure_ascii=False))
        return 0

    with psycopg.connect(args.database_url) as conn:
        with conn.cursor() as cur:
            if stage == "question":
                input_keys = [str(row["candidate_key"]) for row in rows]
                if args.skip_reviewed:
                    eligible_keys = unreviewed_candidate_keys(cur, input_keys)
                    skipped_human_review_count = len(input_keys) - len(eligible_keys)
                    rows = [row for row in rows if str(row["candidate_key"]) in eligible_keys]
                    summary["skipped_human_review_count"] = skipped_human_review_count
                    summary["eligible_result_count"] = len(rows)
                    summary["result_count"] = len(rows)
                else:
                    assert_question_candidates_still_unreviewed(cur, input_keys)
            cur.execute(
                """
                SELECT id
                FROM exam.model_runs
                WHERE task_type = %s
                  AND input_hash = %s
                  AND status = 'succeeded'
                LIMIT 1
                """,
                (task_type, input_hash),
            )
            if cur.fetchone():
                raise SystemExit("this result file was already imported")
            cur.execute(
                """
                INSERT INTO exam.model_runs (
                    task_type, provider, model_name, prompt_version, input_hash,
                    status, request_json, response_json, started_at, finished_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    'succeeded', %s, %s, now(), now()
                ) RETURNING id
                """,
                (
                    task_type,
                    args.provider,
                    args.model,
                    args.prompt_version,
                    input_hash,
                    Jsonb({"results_path": str(args.results), "advisory_only": True}),
                    Jsonb({"result_count": len(rows)}),
                ),
            )
            model_run_id = int(cur.fetchone()[0])
            for row in rows:
                key = str(row["candidate_key"])
                audit = audit_payload(row)
                cur.execute(
                    f"""
                    INSERT INTO {event_table} (
                        candidate_id, candidate_key, model_run_id, action, reviewer,
                        provider, model_name, prompt_version, input_hash,
                        audit_status, recommended_action, audit_json, event_json, notes
                    )
                    SELECT
                        c.id, c.candidate_key, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    FROM exam.question_candidates c
                    WHERE c.candidate_key = %s
                    """,
                    (
                        model_run_id,
                        "ai_answer_audit" if stage == "answer" else "ai_audit",
                        f"ai:{args.provider}:{args.model}",
                        args.provider,
                        args.model,
                        args.prompt_version,
                        input_hash,
                        row["status"],
                        row.get("recommended_action") or "none",
                        Jsonb(audit),
                        Jsonb({"audit": audit, "advisory_only": True}),
                        row.get("reason") or "",
                        key,
                    ),
                )
                if cur.rowcount != 1:
                    raise ValueError(f"candidate not found: {key}")
        conn.commit()
    print(json.dumps({**summary, "dry_run": False, "model_run_id": model_run_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
