#!/usr/bin/env python3
"""
Import ChatGPT MCP question audit results into the Review UI AI review log.

Input is a JSONL file produced from export_chatgpt_mcp_audit_batch.py's prompt.
The import appends events only; it does not change human review status.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from serve_question_review_ui import (
    AI_REVIEW_PROMPT_VERSION,
    ReviewState,
    compact_candidate_for_ai,
    latest_path,
)


VALID_STATUSES = {"pass", "needs_review", "blocked"}
VALID_RECOMMENDED_ACTIONS = {"no_action", "human_review", "parser_fix", "manual_image_check"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import ChatGPT MCP audit results.")
    parser.add_argument("result_jsonl", type=Path)
    parser.add_argument("--candidate-jsonl", type=Path, default=None)
    parser.add_argument("--issue-csv", type=Path, default=None)
    parser.add_argument("--review-log", type=Path, default=None)
    parser.add_argument("--model", default="5.4")
    parser.add_argument("--reviewer", default="chatgpt-mcp-audit")
    parser.add_argument("--notes", default="ChatGPT 訂閱版透過 DevSpace MCP 產生的題目格式稽核。")
    return parser.parse_args()


def normalize_finding(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {"message": str(value)}
    severity = str(value.get("severity") or "info")
    if severity not in {"info", "warning", "error"}:
        severity = "info"
    return {
        "code": str(value.get("code") or "chatgpt_mcp_note"),
        "severity": severity,
        "field": str(value.get("field") or "parser"),
        "message": str(value.get("message") or ""),
        "evidence": str(value.get("evidence") or "")[:200],
        "suggestion": str(value.get("suggestion") or ""),
    }


def normalize_result(record: dict[str, Any]) -> dict[str, Any]:
    status = str(record.get("status") or "needs_review")
    if status not in VALID_STATUSES:
        status = "needs_review"
    recommended_action = str(record.get("recommended_action") or "")
    if recommended_action not in VALID_RECOMMENDED_ACTIONS:
        recommended_action = "human_review" if status != "pass" else "no_action"
    try:
        confidence = float(record.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.7 if status == "pass" else 0.5
    confidence = max(0.0, min(confidence, 1.0))
    findings = [normalize_finding(finding) for finding in (record.get("findings") or [])]
    return {
        "provider": "chatgpt_mcp",
        "model": str(record.get("model") or ""),
        "status": status,
        "confidence": confidence,
        "summary": str(record.get("summary") or ""),
        "recommended_action": recommended_action,
        "findings": findings,
    }


def main() -> None:
    args = parse_args()
    if not args.result_jsonl.exists():
        raise SystemExit(f"Result JSONL not found: {args.result_jsonl}")
    candidate_path = args.candidate_jsonl or latest_path("*/question_candidates__*.jsonl")
    issue_path = args.issue_csv or latest_path("*/question_parse_issues__*.csv")
    review_log = args.review_log or candidate_path.parent / "question_review_events.jsonl"
    state = ReviewState(candidate_path, issue_path, review_log)

    imported = 0
    skipped: list[dict[str, str]] = []
    with args.result_jsonl.open(encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                skipped.append({"line": str(line_number), "reason": f"invalid_json:{exc}"})
                continue
            key = str(record.get("candidate_key") or "")
            item = state.candidate_by_key.get(key)
            if not item:
                skipped.append({"line": str(line_number), "candidate_key": key, "reason": "candidate_not_found"})
                continue
            audit = normalize_result(record)
            if not audit["model"]:
                audit["model"] = args.model
            audit_input = compact_candidate_for_ai(state.candidate_payload(item))
            event = {
                "candidate_key": key,
                "reviewer": args.reviewer,
                "action": "ai_audit",
                "prompt_version": f"{AI_REVIEW_PROMPT_VERSION}+chatgpt_mcp",
                "provider": "chatgpt_mcp",
                "model": audit["model"],
                "input_hash": hashlib.sha256(json.dumps(audit_input, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
                "notes": args.notes,
                "source_result_jsonl": str(args.result_jsonl),
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "audit": audit,
            }
            state.append_ai_review(event)
            imported += 1

    print(json.dumps({"imported": imported, "skipped": skipped, "ai_review_log": str(state.ai_review_log)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
