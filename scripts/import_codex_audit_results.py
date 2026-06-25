#!/usr/bin/env python3
"""
Import Codex skill-based advisory labels into the Review UI AI review log.

This appends to question_ai_review_events.jsonl only. It never changes human
question review or answer review events.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from serve_question_review_ui import ReviewState, compact_candidate_for_ai, latest_path, normalized_correction


PROMPT_VERSION = "national_exam_ai_audit_v0.1+codex"
VALID_STATUSES = {"pass", "needs_review", "block"}
VALID_LABELS = {
    "pass_likely",
    "ocr_char_suspect",
    "amino_acid_translation_suspect",
    "science_notation_suspect",
    "blood_group_symbol_suspect",
    "option_parse_suspect",
    "table_or_image_suspect",
    "group_question_suspect",
    "answer_pair_suspect",
    "parser_boundary_suspect",
    "needs_human_review",
    "block_likely",
}
ACTION_MAP = {
    "human_can_quick_accept": "no_action",
    "human_review_text": "human_review",
    "human_review_pdf_visual": "manual_image_check",
    "fix_parser_rule": "parser_fix",
    "add_manual_asset": "manual_image_check",
    "defer_to_answer_audit": "human_review",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Codex advisory audit labels.")
    parser.add_argument("result_jsonl", type=Path, nargs="+", help="One or more result JSONL files, or directories containing result JSONL files.")
    parser.add_argument("--candidate-jsonl", type=Path, default=None)
    parser.add_argument("--issue-csv", type=Path, default=None)
    parser.add_argument("--review-log", type=Path, default=None)
    parser.add_argument("--reviewer", default="codex-skill-audit")
    parser.add_argument("--model", default="codex-gpt5")
    parser.add_argument("--notes", default="Codex 依 national-exam-ai-audit skill 產生的 advisory labels。")
    return parser.parse_args()


def expand_result_paths(paths: list[Path]) -> list[Path]:
    expanded: list[Path] = []
    for path in paths:
        if path.is_dir():
            expanded.extend(sorted(path.rglob("codex_question_audit_results__*.jsonl")))
        else:
            expanded.append(path)
    return sorted(dict.fromkeys(expanded))


def normalize_evidence(value: Any) -> str:
    if isinstance(value, list):
        parts = []
        for item in value[:3]:
            if isinstance(item, dict):
                field = item.get("field") or "field"
                evidence = item.get("value") or item.get("evidence") or ""
                parts.append(f"{field}: {evidence}")
            else:
                parts.append(str(item))
        return " | ".join(parts)[:300]
    return str(value or "")[:300]


def normalize_record(record: dict[str, Any], fallback_model: str) -> dict[str, Any]:
    status = str(record.get("status") or "needs_review")
    if status == "blocked":
        status = "block"
    if status not in VALID_STATUSES:
        status = "needs_review"

    labels = [str(label) for label in record.get("labels") or []]
    labels = [label for label in labels if label in VALID_LABELS]
    if not labels:
        labels = ["pass_likely"] if status == "pass" else ["needs_human_review"]

    try:
        confidence = float(record.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.75 if status == "pass" else 0.6
    confidence = max(0.0, min(confidence, 1.0))

    action = str(record.get("recommended_action") or "")
    mapped_action = ACTION_MAP.get(action, "no_action" if status == "pass" else "human_review")
    reason = str(record.get("reason") or "")
    evidence = normalize_evidence(record.get("evidence"))
    severity = "info" if status == "pass" else "error" if status == "block" else "warning"
    findings = [] if status == "pass" else [
        {
            "code": ",".join(labels),
            "severity": severity,
            "field": "parser",
            "message": reason,
            "evidence": evidence,
            "suggestion": action,
        }
    ]
    audit = {
        "provider": "codex",
        "model": str(record.get("model") or fallback_model),
        "status": status,
        "confidence": confidence,
        "summary": reason,
        "labels": labels,
        "reason": reason,
        "evidence": record.get("evidence") or [],
        "recommended_action": mapped_action,
        "skill_recommended_action": action,
        "findings": findings,
    }
    suggested_correction = normalized_correction(record.get("suggested_correction"))
    if suggested_correction:
        audit["suggested_correction"] = suggested_correction
    suggested_changes = record.get("suggested_changes")
    if isinstance(suggested_changes, list):
        audit["suggested_changes"] = [str(item) for item in suggested_changes if str(item).strip()]
    return audit


def main() -> None:
    args = parse_args()
    result_paths = expand_result_paths(args.result_jsonl)
    missing = [str(path) for path in result_paths if not path.exists()]
    if missing:
        raise SystemExit(f"Result JSONL not found: {missing[0]}")
    if not result_paths:
        raise SystemExit("No result JSONL files found.")
    candidate_path = args.candidate_jsonl or latest_path("*/question_candidates__*.jsonl")
    issue_path = args.issue_csv or latest_path("*/question_parse_issues__*.csv")
    review_log = args.review_log or candidate_path.parent / "question_review_events.jsonl"
    state = ReviewState(candidate_path, issue_path, review_log)

    imported = 0
    skipped: list[dict[str, str]] = []
    for result_path in result_paths:
        with result_path.open(encoding="utf-8") as f:
            for line_number, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    skipped.append({"file": str(result_path), "line": str(line_number), "reason": f"invalid_json:{exc}"})
                    continue
                key = str(record.get("candidate_key") or "")
                item = state.candidate_by_key.get(key)
                if not item:
                    skipped.append({"file": str(result_path), "line": str(line_number), "candidate_key": key, "reason": "candidate_not_found"})
                    continue
                audit = normalize_record(record, args.model)
                audit_input = compact_candidate_for_ai(state.candidate_payload(item))
                event = {
                    "candidate_key": key,
                    "reviewer": args.reviewer,
                    "action": "ai_audit",
                    "prompt_version": PROMPT_VERSION,
                    "provider": "codex",
                    "model": audit["model"],
                    "input_hash": hashlib.sha256(json.dumps(audit_input, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
                    "notes": args.notes,
                    "source_result_jsonl": str(result_path),
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "audit": audit,
                }
                state.append_ai_review(event)
                imported += 1

    print(json.dumps({"imported": imported, "result_files": [str(path) for path in result_paths], "skipped": skipped, "ai_review_log": str(state.ai_review_log)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
