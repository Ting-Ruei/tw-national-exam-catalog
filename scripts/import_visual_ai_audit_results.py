#!/usr/bin/env python3
"""
Import visual AI advisory results into question_ai_review_events.

The import is advisory-only. It never writes human question review, answer
review, or visual_review. A reviewer must later accept an AI recommendation in
Review UI or through an explicit batch command.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMPT_VERSION = "visual_asset_semantic_audit_v0.2"
VALID_VISUAL_STATUSES = {"visual_required_likely", "visual_not_required_likely", "visual_uncertain"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import visual AI advisory labels.")
    parser.add_argument("result_jsonl", type=Path, nargs="+", help="Result JSONL files or directories containing visual_ai_audit_results__*.jsonl.")
    parser.add_argument("--reviewer", default="visual-ai-audit")
    parser.add_argument("--provider", default="codex")
    parser.add_argument("--model", default="codex-gpt5")
    parser.add_argument("--notes", default="AI 圖片語意稽核 advisory；不自動改變人工 visual_review。")
    parser.add_argument("--postgres-db", default=os.environ.get("POSTGRES_DB", "tw_national_exam_dev"))
    parser.add_argument("--postgres-user", default=os.environ.get("POSTGRES_USER", "national_exam"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def expand_paths(paths: list[Path]) -> list[Path]:
    expanded: list[Path] = []
    for path in paths:
        if path.is_dir():
            expanded.extend(sorted(path.rglob("visual_ai_audit_results__*.jsonl")))
        else:
            expanded.append(path)
    return sorted(dict.fromkeys(expanded))


def normalize_visual_status(record: dict[str, Any]) -> str:
    raw = str(record.get("visual_status") or record.get("status") or "").strip()
    aliases = {
        "required": "visual_required_likely",
        "needs_visual": "visual_required_likely",
        "visual_required": "visual_required_likely",
        "not_required": "visual_not_required_likely",
        "no_visual": "visual_not_required_likely",
        "no_visual_required": "visual_not_required_likely",
        "uncertain": "visual_uncertain",
        "needs_review": "visual_uncertain",
    }
    normalized = aliases.get(raw, raw)
    if normalized not in VALID_VISUAL_STATUSES:
        normalized = "visual_uncertain"
    return normalized


def confidence_value(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(0.0, min(parsed, 1.0))


def audit_from_record(record: dict[str, Any], provider: str, model: str) -> dict[str, Any]:
    visual_status = normalize_visual_status(record)
    reason = str(record.get("reason") or record.get("summary") or "").strip()
    evidence = str(record.get("evidence") or "").strip()
    confidence = confidence_value(record.get("confidence"), 0.85 if visual_status != "visual_uncertain" else 0.55)
    recommended = str(record.get("recommended_human_action") or "").strip()
    if not recommended:
        recommended = {
            "visual_required_likely": "review_pdf_visual",
            "visual_not_required_likely": "confirm_no_visual_required",
            "visual_uncertain": "keep_visual_candidate",
        }[visual_status]
    label = visual_status
    status = "pass" if visual_status == "visual_not_required_likely" else "needs_review"
    finding = {
        "code": label,
        "severity": "info" if visual_status == "visual_not_required_likely" else "warning",
        "field": "images",
        "message": reason or {
            "visual_required_likely": "AI 判斷此題大概率需要圖片或表格資產。",
            "visual_not_required_likely": "AI 判斷此題只是文字提到圖像/檢查概念，不需要圖片資產。",
            "visual_uncertain": "AI 無法確定是否需要圖片，建議人工確認 PDF。",
        }[visual_status],
        "evidence": evidence,
        "suggestion": recommended,
    }
    findings = [] if visual_status == "visual_not_required_likely" else [finding]
    return {
        "provider": provider,
        "model": str(record.get("model") or model),
        "status": status,
        "confidence": confidence,
        "summary": finding["message"],
        "labels": [label],
        "visual_status": visual_status,
        "recommended_action": "no_action" if visual_status == "visual_not_required_likely" else "manual_image_check",
        "recommended_human_action": recommended,
        "findings": findings,
        "visual_reason": reason,
        "visual_evidence": evidence,
    }


def read_records(paths: list[Path], args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for path in paths:
        if not path.exists():
            skipped.append({"file": str(path), "line": "", "reason": "file_not_found"})
            continue
        with path.open(encoding="utf-8") as f:
            for line_number, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    skipped.append({"file": str(path), "line": str(line_number), "reason": f"invalid_json:{exc}"})
                    continue
                candidate_key = str(record.get("candidate_key") or "").strip()
                if not candidate_key:
                    skipped.append({"file": str(path), "line": str(line_number), "reason": "missing_candidate_key"})
                    continue
                audit = audit_from_record(record, args.provider, args.model)
                input_hash = hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
                event = {
                    "candidate_key": candidate_key,
                    "action": "ai_audit",
                    "reviewer": args.reviewer,
                    "provider": args.provider,
                    "model_name": audit["model"],
                    "prompt_version": PROMPT_VERSION,
                    "input_hash": input_hash,
                    "audit_status": audit["status"],
                    "recommended_action": audit["recommended_action"],
                    "audit_json": audit,
                    "event_json": {
                        "candidate_key": candidate_key,
                        "action": "ai_audit",
                        "reviewer": args.reviewer,
                        "provider": args.provider,
                        "model": audit["model"],
                        "prompt_version": PROMPT_VERSION,
                        "input_hash": input_hash,
                        "notes": args.notes,
                        "source_result_jsonl": str(path),
                        "audit": audit,
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                    },
                    "notes": args.notes,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                }
                rows.append(event)
    return rows, skipped


def csv_payload(rows: list[dict[str, Any]]) -> str:
    fields = [
        "candidate_key",
        "action",
        "reviewer",
        "provider",
        "model_name",
        "prompt_version",
        "input_hash",
        "audit_status",
        "recommended_action",
        "audit_json",
        "event_json",
        "notes",
        "created_at",
    ]
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                key: json.dumps(row[key], ensure_ascii=False, sort_keys=True)
                if key in {"audit_json", "event_json"}
                else row[key]
                for key in fields
            }
        )
    return buffer.getvalue()


def import_rows(rows: list[dict[str, Any]], args: argparse.Namespace) -> int:
    if not rows:
        return 0
    pre_sql = """
CREATE SCHEMA IF NOT EXISTS exam_staging;
CREATE TABLE IF NOT EXISTS exam_staging.visual_ai_review_import (
    candidate_key TEXT,
    action TEXT,
    reviewer TEXT,
    provider TEXT,
    model_name TEXT,
    prompt_version TEXT,
    input_hash TEXT,
    audit_status TEXT,
    recommended_action TEXT,
    audit_json TEXT,
    event_json TEXT,
    notes TEXT,
    created_at TEXT
);
TRUNCATE exam_staging.visual_ai_review_import;
\\copy exam_staging.visual_ai_review_import FROM STDIN WITH (FORMAT csv, HEADER true)
"""
    post_sql = """
INSERT INTO exam.question_ai_review_events (
    candidate_id,
    candidate_key,
    action,
    reviewer,
    provider,
    model_name,
    prompt_version,
    input_hash,
    audit_status,
    recommended_action,
    audit_json,
    event_json,
    notes,
    created_at
)
SELECT
    c.id,
    s.candidate_key,
    s.action,
    s.reviewer,
    s.provider,
    s.model_name,
    s.prompt_version,
    s.input_hash,
    CASE WHEN s.audit_status IN ('block', 'blocked') THEN 'block'
         WHEN s.audit_status = 'needs_review' THEN 'needs_review'
         ELSE 'pass'
    END,
    s.recommended_action,
    NULLIF(s.audit_json, '')::jsonb,
    NULLIF(s.event_json, '')::jsonb,
    s.notes,
    COALESCE(NULLIF(s.created_at, '')::timestamptz, now())
FROM exam_staging.visual_ai_review_import s
JOIN exam.question_candidates c ON c.candidate_key = s.candidate_key;
"""
    cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        args.postgres_user,
        "-d",
        args.postgres_db,
        "-v",
        "ON_ERROR_STOP=1",
    ]
    payload = pre_sql + csv_payload(rows) + "\\.\n" + post_sql
    subprocess.run(cmd, cwd=PROJECT_ROOT, input=payload, text=True, check=True)
    return len(rows)


def main() -> None:
    args = parse_args()
    result_paths = expand_paths(args.result_jsonl)
    if not result_paths:
        raise SystemExit("No result JSONL files found.")
    rows, skipped = read_records(result_paths, args)
    imported = 0 if args.dry_run else import_rows(rows, args)
    print(
        json.dumps(
            {
                "dry_run": args.dry_run,
                "parsed": len(rows),
                "imported": imported,
                "result_files": [str(path) for path in result_paths],
                "skipped": skipped,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
