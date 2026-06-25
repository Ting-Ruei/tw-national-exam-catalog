#!/usr/bin/env python3
"""
Append advisory AI review events for recurring medtech hematology notation issues.

This script does not change candidate JSONL or human review events. It only
writes question_ai_review_events.jsonl so Review UI can show suggested fixes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from build_question_candidates_from_mineru import normalize_blood_group_markup, normalize_science_markup
from serve_question_review_ui import ReviewState, compact_candidate_for_ai, latest_path


DEFAULT_CATEGORY = "醫事檢驗師"
DEFAULT_SUBJECT = "臨床血液學與血庫學"
PROMPT_VERSION = "national_exam_ai_audit_v0.1+hematology_rules"
RESET_OR_PASS_ACTIONS = {"accept", "pass", "unblock"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit medtech hematology notation patterns.")
    parser.add_argument("--candidate-jsonl", type=Path, default=None)
    parser.add_argument("--issue-csv", type=Path, default=None)
    parser.add_argument("--review-log", type=Path, default=None)
    parser.add_argument("--category", default=DEFAULT_CATEGORY)
    parser.add_argument("--subject", default=DEFAULT_SUBJECT)
    parser.add_argument("--include-accepted", action="store_true")
    parser.add_argument("--force", action="store_true", help="Append even if the latest AI event is from this auditor.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reviewer", default="codex-hematology-rule-audit")
    parser.add_argument("--model", default="codex-hematology-rules-v1")
    return parser.parse_args()


def metadata_matches(item: dict[str, Any], args: argparse.Namespace) -> bool:
    metadata = item.get("metadata") or {}
    category = metadata.get("normalized_category_name") or metadata.get("group_name") or ""
    subject = metadata.get("normalized_subject_name") or ""
    return category == args.category and subject == args.subject


def candidate_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    metadata = item.get("metadata") or {}
    try:
        year = int(metadata.get("year") or 0)
    except (TypeError, ValueError):
        year = 0
    try:
        ordinal = int(metadata.get("exam_ordinal") or 0)
    except (TypeError, ValueError):
        ordinal = 0
    try:
        question_number = int(item.get("question_number") or 0)
    except (TypeError, ValueError):
        question_number = 0
    return (-year, ordinal, question_number, str(item.get("candidate_key") or ""))


def option_texts(item: dict[str, Any]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for option in item.get("options") or []:
        if not isinstance(option, dict):
            continue
        rows.append((str(option.get("key") or ""), str(option.get("text") or "")))
    return rows


def normalize_candidate_text(value: str) -> str:
    normalized = normalize_science_markup(value)
    normalized = normalize_blood_group_markup(normalized)
    normalized = re.sub(r"\b([GA])y\b", lambda m: f"{m.group(1)}γ", normalized)
    normalized = re.sub(r"\bFC\s*Y\s+receptor\b", "Fcγ receptor", normalized, flags=re.I)
    normalized = re.sub(r"\bB_\{\.H⁺\}", "B·H⁺", normalized)
    return normalized


def changed_fields(item: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    correction: dict[str, Any] = {}
    changes: list[str] = []

    original_stem = str(item.get("stem") or "")
    normalized_stem = normalize_candidate_text(original_stem)
    if normalized_stem != original_stem:
        correction["stem"] = normalized_stem
        changes.append("題幹：套用血液學上下標與科學符號正規化")

    option_rows: list[dict[str, Any]] = []
    option_changed = False
    for option in item.get("options") or []:
        if not isinstance(option, dict):
            continue
        copied = dict(option)
        original_text = str(copied.get("text") or "")
        normalized_text = normalize_candidate_text(original_text)
        if normalized_text != original_text:
            copied["text"] = normalized_text
            option_changed = True
            changes.append(f"選項 {copied.get('key')}: 套用血液學上下標與科學符號正規化")
        option_rows.append(copied)
    if option_changed:
        correction["options"] = option_rows

    return correction, sorted(set(changes))


def add_finding(findings: list[dict[str, Any]], code: str, severity: str, field: str, message: str, evidence: str, suggestion: str) -> None:
    findings.append(
        {
            "code": code,
            "severity": severity,
            "field": field,
            "message": message,
            "evidence": evidence[:240],
            "suggestion": suggestion,
        }
    )


def audit_candidate(item: dict[str, Any]) -> dict[str, Any] | None:
    stem = str(item.get("stem") or "")
    options = option_texts(item)
    combined = "\n".join([stem, *(text for _key, text in options)])
    findings: list[dict[str, Any]] = []
    labels: set[str] = set()
    recommended_action = "human_review_text"
    try:
        next_question_number = int(item.get("question_number") or 0) + 1
    except (TypeError, ValueError):
        next_question_number = 0

    correction, changes = changed_fields(item)
    if correction:
        labels.add("science_notation_suspect")
        if re.search(r"\b(?:Anti[- ]?)?(?:Fy|Jk|JK|Le|Lu|Di|Mi|Kp|C)\s*[abcw]\b|\bRh\s*null\b|\b(?:A|B)(?:\s+)?(?:el|end|m)\b", combined, flags=re.I):
            labels.add("blood_group_symbol_suspect")
        add_finding(
            findings,
            "hematology_notation_normalized",
            "warning",
            "text",
            "偵測到血液學或血庫常見上下標/希臘字母顯示風險，已提供一鍵套用建議。",
            "; ".join(changes),
            "套用建議後仍需人工比對 PDF。",
        )

    blood_patterns = [
        r"\b(?:Anti[- ]?)?(?:Fy|Jk|JK|Le|Lu|Di|Mi|Kp|C)\s+[abcw]\b",
        r"\b(?:Rh\s*null|Rhnull)\b",
        r"\b(?:A|B)(?:\s+)?(?:el|end|m)\b",
        r"\b(?:BFU|CFU)\s+E\b",
        r"\bweak\s+D\s*\(\s*D\s*u\s*\)",
        r"\banti-PP₁P\s*k\b",
        r"\\zeta|\\gamma|\^\{\\circ\}",
    ]
    for pattern in blood_patterns:
        match = re.search(pattern, combined, flags=re.I)
        if match:
            labels.update({"science_notation_suspect", "blood_group_symbol_suspect"})
            add_finding(
                findings,
                "hematology_symbol_suspect",
                "warning",
                "text",
                "血型或血液學符號可能遺失上下標或希臘字母。",
                match.group(0),
                "比對 PDF；若建議校正正確，套用後再人工通過。",
            )
            break

    option_keys = [key for key, _text in options]
    if len(options) not in {4, 5} or len(option_keys) != len(set(option_keys)):
        labels.update({"option_parse_suspect", "block_likely"})
        add_finding(
            findings,
            "option_structure_suspect",
            "error",
            "options",
            "選項數量或代號異常，可能漏選項、空選項或 parser 切錯。",
            str(option_keys),
            "需要人工比對 PDF，必要時修 parser 或手動校正。",
        )

    if any(not text.strip() for _key, text in options):
        labels.update({"option_parse_suspect", "block_likely"})
        add_finding(
            findings,
            "empty_option_text",
            "error",
            "options",
            "至少一個選項為空，可能是圖片題或 OCR 未綁定資產。",
            "empty option",
            "檢查 MinerU 圖片/layout；必要時新增 manual asset。",
        )

    boundary_match = None
    if next_question_number > 1:
        boundary_match = re.search(rf"\n\s*{next_question_number}[\.．、]\s*\S.{{0,80}}", combined)
    if boundary_match:
        labels.update({"parser_boundary_suspect", "block_likely"})
        add_finding(
            findings,
            "next_question_inside_candidate",
            "error",
            "text",
            "候選題內含另一個題號，疑似吃到下一題。",
            boundary_match.group(0),
            "需要修 parser 邊界或人工校正。",
        )

    has_table = "<table" in combined.lower()
    has_image = bool(item.get("stem_image") or item.get("image_refs"))
    if has_table:
        labels.add("table_or_image_suspect")
        add_finding(
            findings,
            "structured_table_needs_visual_review",
            "warning",
            "stem",
            "血液學題目含表格，建議以 PDF 或 manual asset 複核，避免表格跑版。",
            "<table>",
            "若表格顯示不完整，新增人工截圖資產。",
        )
        recommended_action = "add_manual_asset" if not has_image else "human_review_pdf_visual"

    if not findings:
        return None

    status = "block" if any(finding["severity"] == "error" for finding in findings) else "needs_review"
    if status == "block":
        labels.add("block_likely")
        recommended_action = "fix_parser_rule"
    labels.add("needs_human_review")
    return {
        "status": status,
        "confidence": 0.82 if correction else 0.7,
        "summary": "血液學符號/表格規則稽核發現需人工複核項目。",
        "reason": "偵測到血型、血液學上下標、表格或 parser 邊界的常見風險。",
        "labels": sorted(labels),
        "evidence": [{"field": finding["field"], "value": finding["evidence"]} for finding in findings[:5]],
        "recommended_action": recommended_action,
        "findings": findings,
        "suggested_correction": correction or None,
        "suggested_changes": changes,
        "provider": "codex",
    }


def event_for(item: dict[str, Any], audit: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    audit_input = compact_candidate_for_ai(item)
    audit = dict(audit)
    audit["model"] = args.model
    return {
        "candidate_key": item["candidate_key"],
        "reviewer": args.reviewer,
        "action": "ai_audit",
        "prompt_version": PROMPT_VERSION,
        "provider": "codex",
        "model": args.model,
        "input_hash": hashlib.sha256(json.dumps(audit_input, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
        "notes": "Codex 根據血液學人工註記歸納的符號/表格規則產生 advisory labels；不改變人工審核狀態。",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "audit": audit,
    }


def main() -> None:
    args = parse_args()
    candidate_path = args.candidate_jsonl or latest_path("*/question_candidates__*.jsonl")
    issue_path = args.issue_csv or latest_path("*/question_parse_issues__*.csv")
    review_log = args.review_log or candidate_path.parent / "question_review_events.jsonl"
    state = ReviewState(candidate_path, issue_path, review_log)

    rows = []
    for item in sorted([item for item in state.candidates if metadata_matches(item, args)], key=candidate_sort_key):
        key = str(item.get("candidate_key") or "")
        latest_review = state.latest_reviews.get(key) or {}
        if not args.include_accepted and latest_review.get("action") in RESET_OR_PASS_ACTIONS:
            continue
        latest_ai = state.latest_ai_reviews.get(key) or {}
        if not args.force and latest_ai.get("reviewer") == args.reviewer:
            continue
        audit = audit_candidate(item)
        if not audit:
            continue
        event = event_for(item, audit, args)
        if not args.dry_run:
            state.append_ai_review(event)
        metadata = item.get("metadata") or {}
        rows.append(
            {
                "candidate_key": key,
                "year": metadata.get("year"),
                "ordinal": metadata.get("exam_ordinal"),
                "question_number": item.get("question_number"),
                "status": audit["status"],
                "labels": audit["labels"],
                "suggested_changes": audit.get("suggested_changes") or [],
            }
        )

    status_counts: dict[str, int] = {}
    for row in rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
    print(
        json.dumps(
            {
                "dry_run": args.dry_run,
                "candidate_jsonl": str(candidate_path),
                "ai_review_log": str(state.ai_review_log),
                "audited_with_findings": len(rows),
                "status_counts": status_counts,
                "sample": rows[:20],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
