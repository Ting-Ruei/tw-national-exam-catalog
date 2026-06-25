#!/usr/bin/env python3
"""
Repair legacy option merges in question candidate JSONL.

The script rebuilds only affected source documents from the original MinerU
markdown, then replaces candidates that have not been accepted by a human.
Accepted/unblocked/excluded candidates are left unchanged.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_DIR = PROJECT_ROOT / "國考題資料夾" / "30_normalized_items" / "question_candidates" / "20260620-213413"
PROTECTED_ACTIONS = {"accept", "unblock", "exclude"}


def load_builder():
    path = PROJECT_ROOT / "scripts" / "build_question_candidates_from_mineru.py"
    spec = importlib.util.spec_from_file_location("question_candidate_builder", path)
    if not spec or not spec.loader:
        raise SystemExit(f"Cannot load parser: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["question_candidate_builder"] = module
    spec.loader.exec_module(module)
    return module


builder = load_builder()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair legacy A-D option merges in candidate JSONL.")
    parser.add_argument("--candidate-jsonl", type=Path, default=DEFAULT_RUN_DIR / "question_candidates__20260620-213413.jsonl")
    parser.add_argument("--issue-csv", type=Path, default=DEFAULT_RUN_DIR / "question_parse_issues__20260620-213413.csv")
    parser.add_argument("--review-log", type=Path, default=DEFAULT_RUN_DIR / "question_review_events.jsonl")
    parser.add_argument("--pair-index", type=Path, default=None)
    parser.add_argument("--category", action="append", default=[], help="Also rebuild non-accepted candidates under this category.")
    parser.add_argument("--subject", action="append", default=[], help="Also rebuild non-accepted candidates under this subject.")
    parser.add_argument("--all-unreviewed-option-merges", action="store_true", help="Repair option merges for unreviewed/reset_review candidates across all categories.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
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


def latest_review_actions(path: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return latest
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            event = json.loads(line)
            key = event.get("candidate_key")
            if key:
                latest[key] = event
    return latest


def read_pair_rows(path: Path) -> dict[str, dict[str, str]]:
    rows = builder.read_csv(path)
    return {row["question_registry_key"]: row for row in rows if row.get("pair_status") in {"paired_ans_only", "paired_mod_primary"}}


def option_merge_suspect(candidate: dict[str, Any]) -> bool:
    options = candidate.get("options") or []
    if not options or len(options) >= 4:
        return False
    for option in options:
        text = str((option or {}).get("text") or "")
        if re.search(r"(?:^|\s)[（(]?[B-D][)）\.、．]\s*\\?[^\\s]?", text):
            return True
    return False


def non_question_duplicate_artifact(candidate: dict[str, Any]) -> bool:
    if int(candidate.get("question_number_occurrence") or 1) <= 1:
        return False
    text = "\n".join(
        [
            str(candidate.get("stem") or ""),
            str((candidate.get("metadata") or {}).get("raw_block") or ""),
        ]
    )
    has_visual_noise = bool(re.search(r"\b(JPEG|DICOM|MHz|cm/s|HGen|WF High|Med|hours|PR\s*\d+HZ)\b", text, re.I))
    has_question_language = bool(re.search(r"(何者|下列|何種|多少|錯誤|正確|為何|？|\?)", text))
    return has_visual_noise and not has_question_language


def should_rebuild_source(candidate: dict[str, Any], event: dict[str, Any] | None, args: argparse.Namespace) -> bool:
    action = (event or {}).get("action") or "unreviewed"
    if action in PROTECTED_ACTIONS:
        return False
    metadata = candidate.get("metadata") or {}
    scoped = False
    if args.category or args.subject:
        category_match = not args.category or metadata.get("normalized_category_name") in set(args.category)
        subject_match = not args.subject or metadata.get("normalized_subject_name") in set(args.subject)
        scoped = bool(category_match and subject_match)
    if scoped:
        return True
    if args.all_unreviewed_option_merges and action in {"unreviewed", "reset_review"} and option_merge_suspect(candidate):
        return True
    if non_question_duplicate_artifact(candidate):
        return True
    return False


def candidate_signature(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "stem": candidate.get("stem"),
        "options": [
            {"key": option.get("key"), "text": option.get("text"), "image": option.get("image")}
            for option in (candidate.get("options") or [])
            if isinstance(option, dict)
        ],
        "image_refs": candidate.get("image_refs") or [],
        "group_ref": candidate.get("group_ref"),
        "question_type": candidate.get("question_type"),
        "answer": candidate.get("answer"),
    }


def recompute_issues(candidates: list[dict[str, Any]]) -> list[Any]:
    issues: list[Any] = []
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        own = builder.candidate_issues(candidate)
        candidate["quality_status"] = builder.quality_status(own)
        candidate["issue_count"] = len(own)
        issues.extend(own)
        by_source[candidate["source_registry_key"]].append(candidate)

    doc_issues: list[Any] = []
    for source, source_candidates in by_source.items():
        doc_issues.extend(builder.document_issues(source_candidates, source))
    issues.extend(doc_issues)

    issues_by_key: dict[str, list[Any]] = defaultdict(list)
    for issue in issues:
        if issue.candidate_key:
            issues_by_key[issue.candidate_key].append(issue)
    for candidate in candidates:
        related = issues_by_key.get(candidate["candidate_key"], [])
        candidate["quality_status"] = builder.quality_status(related)
        candidate["issue_count"] = len(related)
    return issues


def main() -> None:
    args = parse_args()
    if not args.all_unreviewed_option_merges and not args.category and not args.subject:
        raise SystemExit("Select a repair scope, e.g. --category 醫事檢驗師 or --all-unreviewed-option-merges.")

    pair_index = args.pair_index or builder.latest_path(builder.PAIR_INDEX_DIR, "question_answer_pairs_detail__*.csv")
    pair_rows = read_pair_rows(pair_index)
    candidates = read_jsonl(args.candidate_jsonl)
    latest_events = latest_review_actions(args.review_log)

    sources_to_rebuild: set[str] = set()
    for candidate in candidates:
        if should_rebuild_source(candidate, latest_events.get(candidate["candidate_key"]), args):
            source = candidate.get("source_registry_key")
            if source in pair_rows:
                sources_to_rebuild.add(source)

    rebuilt_by_source: dict[str, dict[str, dict[str, Any]]] = {}
    rebuild_errors: dict[str, str] = {}
    for source in sorted(sources_to_rebuild):
        try:
            rebuilt, _issues, _meta = builder.build_candidates_for_pair(pair_rows[source])
            rebuilt_by_source[source] = {candidate["candidate_key"]: candidate for candidate in rebuilt}
        except Exception as exc:  # pragma: no cover - repair should report and keep old rows
            rebuild_errors[source] = repr(exc)

    changed = 0
    removed = 0
    kept_protected = 0
    kept_missing_rebuild = 0
    added = 0
    change_reasons: Counter[str] = Counter()
    seen_keys: set[str] = set()
    output: list[dict[str, Any]] = []

    for old in candidates:
        key = old["candidate_key"]
        source = old.get("source_registry_key")
        event = latest_events.get(key)
        action = (event or {}).get("action") or "unreviewed"
        new = rebuilt_by_source.get(source, {}).get(key)
        seen_keys.add(key)

        if source not in rebuilt_by_source:
            output.append(old)
            continue
        if action in PROTECTED_ACTIONS:
            kept_protected += 1
            output.append(old)
            continue
        if new is None:
            if non_question_duplicate_artifact(old):
                removed += 1
                change_reasons["removed_non_question_duplicate_artifact"] += 1
                continue
            kept_missing_rebuild += 1
            output.append(old)
            continue

        if candidate_signature(old) != candidate_signature(new) or old.get("quality_status") != new.get("quality_status"):
            changed += 1
            if option_merge_suspect(old):
                change_reasons["split_merged_options"] += 1
            if non_question_duplicate_artifact(old):
                change_reasons["non_question_artifact_reparsed"] += 1
            output.append(new)
        else:
            output.append(old)

    current_keys = {candidate["candidate_key"] for candidate in candidates}
    for source, rebuilt in rebuilt_by_source.items():
        for key, new in rebuilt.items():
            if key not in current_keys:
                output.append(new)
                added += 1
                change_reasons["added_candidate_after_reparse"] += 1

    issues = recompute_issues(output)

    summary = {
        "candidate_jsonl": str(args.candidate_jsonl),
        "issue_csv": str(args.issue_csv),
        "pair_index": str(pair_index),
        "sources_selected": len(sources_to_rebuild),
        "sources_rebuilt": len(rebuilt_by_source),
        "rebuild_errors": rebuild_errors,
        "changed_candidates": changed,
        "removed_candidates": removed,
        "added_candidates": added,
        "kept_protected_candidates": kept_protected,
        "kept_missing_rebuild_candidates": kept_missing_rebuild,
        "change_reasons": dict(change_reasons),
        "candidate_count_before": len(candidates),
        "candidate_count_after": len(output),
        "quality_status_counts_after": dict(Counter(candidate.get("quality_status") for candidate in output)),
        "dry_run": args.dry_run,
    }

    if not args.dry_run:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir = args.candidate_jsonl.parent / "_repair_backups" / f"{stamp}-option-merge-repair"
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.candidate_jsonl, backup_dir / args.candidate_jsonl.name)
        if args.issue_csv.exists():
            shutil.copy2(args.issue_csv, backup_dir / args.issue_csv.name)
        write_jsonl(args.candidate_jsonl, output)
        builder.write_issues_csv(args.issue_csv, issues)
        summary["backup_dir"] = str(backup_dir)

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
