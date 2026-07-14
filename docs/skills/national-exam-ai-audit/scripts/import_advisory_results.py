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
    parser.add_argument("--model", default="glm-5.2")
    parser.add_argument("--prompt-version", default="national_exam_ai_audit_v1")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--apply", action="store_true", help="Without this flag the command is a dry run")
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
    findings = []
    if status != "pass":
        for issue in issue_families:
            findings.append(
                {
                    "code": issue,
                    "severity": "error" if status == "block" else "warning",
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
    unsupported_stages = sorted({str(row.get("stage") or "") for row in rows} - {"question", "image"})
    if unsupported_stages:
        raise SystemExit(
            "advisory SQL import currently supports question/image stages only; "
            f"unsupported: {', '.join(unsupported_stages)}"
        )
    input_hash = hashlib.sha256(args.results.read_bytes()).hexdigest()
    summary = {
        "ok": True,
        "dry_run": not args.apply,
        "result_count": len(rows),
        "provider": args.provider,
        "model": args.model,
        "prompt_version": args.prompt_version,
        "input_hash": input_hash,
    }
    if not args.apply:
        print(json.dumps(summary, ensure_ascii=False))
        return 0

    with psycopg.connect(args.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM exam.model_runs
                WHERE task_type = 'question_format_audit'
                  AND input_hash = %s
                  AND status = 'succeeded'
                LIMIT 1
                """,
                (input_hash,),
            )
            if cur.fetchone():
                raise SystemExit("this result file was already imported")
            cur.execute(
                """
                INSERT INTO exam.model_runs (
                    task_type, provider, model_name, prompt_version, input_hash,
                    status, request_json, response_json, started_at, finished_at
                ) VALUES (
                    'question_format_audit', %s, %s, %s, %s,
                    'succeeded', %s, %s, now(), now()
                ) RETURNING id
                """,
                (
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
                    """
                    INSERT INTO exam.question_ai_review_events (
                        candidate_id, candidate_key, model_run_id, action, reviewer,
                        provider, model_name, prompt_version, input_hash,
                        audit_status, recommended_action, audit_json, event_json, notes
                    )
                    SELECT
                        c.id, c.candidate_key, %s, 'ai_audit', %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    FROM exam.question_candidates c
                    WHERE c.candidate_key = %s
                    """,
                    (
                        model_run_id,
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
