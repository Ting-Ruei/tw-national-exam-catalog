#!/usr/bin/env python3
"""
Remove false question candidates created from chart-axis numeric tick labels.

The affected medical technologist molecular/microscopy subject has legacy-style
PDF text where MinerU emits many isolated numeric ticks such as 000, 010, 020.
The legacy parser previously treated these as repeated question 1 candidates.
This repair removes only unreviewed numeric-only candidates and recomputes the
issue CSV.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import build_question_candidates_from_mineru as builder


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = PROJECT_ROOT / "國考題資料夾" / "30_normalized_items" / "question_candidates" / "20260620-213413"
DEFAULT_CANDIDATE_PATH = RUN_DIR / "question_candidates__20260620-213413.jsonl"
DEFAULT_ISSUE_PATH = RUN_DIR / "question_parse_issues__20260620-213413.csv"
DEFAULT_REVIEW_LOG = RUN_DIR / "question_review_events.jsonl"
REPORT_DIR = RUN_DIR / "repair_reports"

CATEGORY = "醫事檢驗師"
SUBJECT = "醫學分子檢驗學與臨床鏡檢學(包括寄生蟲學)"
SCRIPT_NAME = "repair_medtech_molecular_numeric_tick_candidates"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Remove numeric tick false candidates from medtech molecular/microscopy subject.")
    parser.add_argument("--candidate-path", type=Path, default=DEFAULT_CANDIDATE_PATH)
    parser.add_argument("--issue-path", type=Path, default=DEFAULT_ISSUE_PATH)
    parser.add_argument("--review-log", type=Path, default=DEFAULT_REVIEW_LOG)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def latest_review_actions(review_log: Path) -> dict[str, str]:
    latest: dict[str, str] = {}
    if not review_log.exists():
        return latest
    with review_log.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = str(event.get("candidate_key") or "")
            if not key:
                continue
            action = str(event.get("action") or "")
            if action in {"reset_review", "unreviewed"}:
                latest.pop(key, None)
            else:
                latest[key] = action
    return latest


def is_target_subject(candidate: dict[str, Any]) -> bool:
    metadata = candidate.get("metadata") or {}
    return metadata.get("normalized_category_name") == CATEGORY and metadata.get("normalized_subject_name") == SUBJECT


def is_false_tick_or_markup_candidate(candidate: dict[str, Any]) -> bool:
    stem = str(candidate.get("stem") or "").strip()
    options = candidate.get("options") or []
    if stem.lower() in {"</details>", "<details>", "</summary>", "text_image"}:
        return True
    if options or not re.fullmatch(r"\d{1,4}", stem):
        return False
    value = int(stem)
    return value == 0 or value % 10 == 0 or stem in {"1", "2", "3", "4", "5"}


def recompute_issues(candidates: list[dict[str, Any]]) -> list[builder.Issue]:
    issues: list[builder.Issue] = []
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        own_issues = builder.candidate_issues(candidate)
        candidate["quality_status"] = builder.quality_status(own_issues)
        candidate["issue_count"] = len(own_issues)
        issues.extend(own_issues)
        by_source[candidate["source_registry_key"]].append(candidate)
    doc_issues: list[builder.Issue] = []
    for source, source_candidates in by_source.items():
        doc_issues.extend(builder.document_issues(source_candidates, source))
    issues.extend(doc_issues)
    by_key: dict[str, list[builder.Issue]] = defaultdict(list)
    for issue in issues:
        if issue.candidate_key:
            by_key[issue.candidate_key].append(issue)
    for candidate in candidates:
        related = by_key.get(candidate["candidate_key"], [])
        candidate["quality_status"] = builder.quality_status(related)
        candidate["issue_count"] = len(related)
    return issues


def write_manifest(path: Path, removed: list[dict[str, Any]], protected: list[dict[str, Any]], dry_run: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "script": SCRIPT_NAME,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "removed_count": len(removed),
        "protected_count": len(protected),
        "removed_by_exam": defaultdict(int),
        "removed_keys": [item["candidate_key"] for item in removed],
        "protected": protected,
        "sample_removed": [
            {
                "candidate_key": item["candidate_key"],
                "stem": item.get("stem"),
                "year": (item.get("metadata") or {}).get("year"),
                "exam_ordinal": (item.get("metadata") or {}).get("exam_ordinal"),
            }
            for item in removed[:30]
        ],
    }
    for item in removed:
        metadata = item.get("metadata") or {}
        summary["removed_by_exam"][f"{metadata.get('year')}-{metadata.get('exam_ordinal')}"] += 1
    summary["removed_by_exam"] = dict(summary["removed_by_exam"])
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    candidates = load_jsonl(args.candidate_path)
    latest_actions = latest_review_actions(args.review_log)
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    protected: list[dict[str, Any]] = []

    for candidate in candidates:
        if is_target_subject(candidate) and is_false_tick_or_markup_candidate(candidate):
            key = candidate["candidate_key"]
            latest_action = latest_actions.get(key)
            if latest_action:
                protected.append({"candidate_key": key, "latest_action": latest_action, "stem": candidate.get("stem")})
                kept.append(candidate)
            else:
                removed.append(candidate)
            continue
        kept.append(candidate)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    manifest = REPORT_DIR / f"medtech_molecular_numeric_tick_repair__{stamp}.json"
    write_manifest(manifest, removed, protected, args.dry_run)

    issues = recompute_issues(kept)
    result = {
        "candidate_path": str(args.candidate_path),
        "issue_path": str(args.issue_path),
        "manifest": str(manifest),
        "original_count": len(candidates),
        "new_count": len(kept),
        "removed_count": len(removed),
        "protected_count": len(protected),
        "dry_run": args.dry_run,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if args.dry_run:
        return

    write_jsonl(args.candidate_path, kept)
    builder.write_issues_csv(args.issue_path, issues)


if __name__ == "__main__":
    main()
