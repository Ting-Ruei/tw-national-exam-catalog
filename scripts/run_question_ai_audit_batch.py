#!/usr/bin/env python3
"""
Run advisory AI format audits for question candidates in batch.

The batch writes append-only events to question_ai_review_events.jsonl through
the same ReviewState used by the local Review UI. It does not change human
review status and does not write to the formal question tables.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import serve_question_review_ui as review_ui

from serve_question_review_ui import DEFAULT_CANDIDATE_ROOT, ReviewState, latest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch run question AI format audit.")
    parser.add_argument("--candidate-jsonl", type=Path, default=None)
    parser.add_argument("--issue-csv", type=Path, default=None)
    parser.add_argument("--review-log", type=Path, default=None)
    parser.add_argument("--category", default="")
    parser.add_argument("--subject", default="")
    parser.add_argument("--year", default="")
    parser.add_argument("--ordinal", default="")
    parser.add_argument("--limit", type=int, default=0, help="Maximum candidates to audit. 0 means no limit.")
    parser.add_argument("--force", action="store_true", help="Re-run candidates that already have AI audit events.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reviewer", default="batch-ai-audit")
    parser.add_argument("--model", default="", help="Sets OPENAI_REVIEW_MODEL for this run.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_CANDIDATE_ROOT / "ai_audit_runs")
    return parser.parse_args()


def metadata_matches(item: dict[str, Any], args: argparse.Namespace) -> bool:
    metadata = item.get("metadata") or {}
    category = metadata.get("normalized_category_name") or metadata.get("group_name") or ""
    subject = metadata.get("normalized_subject_name") or ""
    if args.category and category != args.category:
        return False
    if args.subject and subject != args.subject:
        return False
    if args.year and str(metadata.get("year") or "") != str(args.year):
        return False
    if args.ordinal and str(metadata.get("exam_ordinal") or "") != str(args.ordinal):
        return False
    return True


def candidate_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    metadata = item.get("metadata") or {}
    try:
        ordinal = int(metadata.get("exam_ordinal") or 0)
    except (TypeError, ValueError):
        ordinal = 0
    try:
        question_number = int(item.get("question_number") or 0)
    except (TypeError, ValueError):
        question_number = 0
    return (ordinal, question_number, str(item.get("candidate_key") or ""))


def main() -> None:
    args = parse_args()
    if args.model:
        os.environ["OPENAI_REVIEW_MODEL"] = args.model
        review_ui.DEFAULT_AI_MODEL = args.model

    candidate_path = args.candidate_jsonl or latest_path("*/question_candidates__*.jsonl")
    issue_path = args.issue_csv or latest_path("*/question_parse_issues__*.csv")
    review_log = args.review_log or candidate_path.parent / "question_review_events.jsonl"
    state = ReviewState(candidate_path, issue_path, review_log)

    candidates = sorted(
        [item for item in state.candidates if metadata_matches(item, args)],
        key=candidate_sort_key,
    )
    if not args.force:
        candidates = [item for item in candidates if item.get("candidate_key") not in state.latest_ai_reviews]
    if args.limit and args.limit > 0:
        candidates = candidates[: args.limit]

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = args.output_dir / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    result_csv = run_dir / f"question_ai_audit_results__{timestamp}.csv"
    summary_json = run_dir / f"question_ai_audit_summary__{timestamp}.json"

    fields = [
        "candidate_key",
        "category",
        "subject",
        "year",
        "ordinal",
        "question_number",
        "status",
        "provider",
        "model",
        "finding_count",
        "recommended_action",
        "error",
    ]
    rows: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    provider_counts: dict[str, int] = {}

    for index, item in enumerate(candidates, start=1):
        key = str(item.get("candidate_key") or "")
        metadata = item.get("metadata") or {}
        row = {
            "candidate_key": key,
            "category": metadata.get("normalized_category_name") or metadata.get("group_name") or "",
            "subject": metadata.get("normalized_subject_name") or "",
            "year": metadata.get("year") or "",
            "ordinal": metadata.get("exam_ordinal") or "",
            "question_number": item.get("question_number") or "",
            "status": "dry_run" if args.dry_run else "",
            "provider": "",
            "model": os.environ.get("OPENAI_REVIEW_MODEL") or os.environ.get("OPENAI_MODEL") or "",
            "finding_count": 0,
            "recommended_action": "",
            "error": "",
        }
        if args.dry_run:
            rows.append(row)
            continue
        try:
            event = state.run_question_ai_audit(
                key,
                reviewer=args.reviewer,
                notes=(
                    "批次 AI 格式稽核："
                    f"{row['category']} / {row['subject']} / {row['year']} 年"
                    f"第 {row['ordinal']} 次；不改變人工審核狀態。"
                ),
            )
            audit = event.get("audit") or {}
            row["status"] = audit.get("status") or ""
            row["provider"] = event.get("provider") or audit.get("provider") or ""
            row["model"] = event.get("model") or audit.get("model") or row["model"]
            row["finding_count"] = len(audit.get("findings") or [])
            row["recommended_action"] = audit.get("recommended_action") or ""
            status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
            provider_counts[row["provider"]] = provider_counts.get(row["provider"], 0) + 1
        except Exception as exc:  # keep the batch moving
            row["status"] = "failed"
            row["error"] = str(exc)
            status_counts["failed"] = status_counts.get("failed", 0) + 1
        rows.append(row)
        print(f"[{index}/{len(candidates)}] {key} -> {row['status']} {row['provider']} {row['model']}")

    with result_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "timestamp": timestamp,
        "candidate_jsonl": str(candidate_path),
        "issue_csv": str(issue_path),
        "review_log": str(review_log),
        "ai_review_log": str(state.ai_review_log),
        "result_csv": str(result_csv),
        "dry_run": args.dry_run,
        "force": args.force,
        "filters": {
            "category": args.category,
            "subject": args.subject,
            "year": args.year,
            "ordinal": args.ordinal,
        },
        "model": os.environ.get("OPENAI_REVIEW_MODEL") or os.environ.get("OPENAI_MODEL") or "",
        "candidate_count": len(candidates),
        "status_counts": status_counts,
        "provider_counts": provider_counts,
    }
    with summary_json.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
