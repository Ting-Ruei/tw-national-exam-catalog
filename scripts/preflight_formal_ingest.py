#!/usr/bin/env python3
"""
Preflight candidate records before promoting them into formal question tables.

This script is deliberately read-only. It inspects question candidates, parse
issues, question review events, and answer review events, then reports whether a
candidate is safe to promote into exam.questions / exam.question_groups /
exam.question_assets. It does not write PostgreSQL tables or review logs.
"""

from __future__ import annotations

import argparse
import csv
import copy
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATE_ROOT = PROJECT_ROOT / "國考題資料夾" / "30_normalized_items" / "question_candidates"
RESET_ACTIONS = {"unreviewed", "reset_review"}
QUESTION_READY_ACTIONS = {"accept", "unblock"}
ANSWER_READY_ACTIONS = {"accept", "unblock"}
BLOCKING_REVIEW_ACTIONS = {"block", "exclude", "needs_review", "comment", "reviewed"}
BLOCKING_ISSUE_SEVERITIES = {"error", "blocked"}

GROUP_HINT_RE = re.compile(
    r"(下列資料|下圖|依圖|依下圖|此病人|此案例|前述|承上題|上題|題組|共同題幹|根據下列|依據下列)"
)
VISUAL_HINT_RE = re.compile(
    r"(下圖|附圖|圖中|圖示|圖形|表中|下表|附表|心電圖|X\s*光|影像|切片|照片|顯微鏡|曲線|電泳圖|染色圖|光譜|chromatogram|gel|figure|table)",
    re.IGNORECASE,
)
MARKUP_HINT_RE = re.compile(r"(<table|</table>|<td|</td>|<tr|</tr>|<sup>|</sup>|<sub>|</sub>|\\[A-Za-z]+|_\{|\\\(|\\\))")


def latest_path(pattern: str) -> Path:
    paths = sorted(DEFAULT_CANDIDATE_ROOT.glob(pattern))
    if not paths:
        raise SystemExit(f"No candidate output found: {DEFAULT_CANDIDATE_ROOT}/{pattern}")
    return paths[-1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preflight reviewed candidates before formal SQL ingestion.")
    parser.add_argument("--candidate-jsonl", type=Path)
    parser.add_argument("--issue-csv", type=Path)
    parser.add_argument("--question-review-log", type=Path)
    parser.add_argument("--answer-review-log", type=Path)
    parser.add_argument("--format", choices=["summary", "csv", "jsonl"], default="summary")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--include-ready", action="store_true", help="Include ready rows in csv/jsonl output.")
    parser.add_argument("--limit", type=int, help="Limit candidates after reading, useful for quick checks.")
    parser.add_argument("--category", help="Filter by normalized category name.")
    parser.add_argument("--subject", help="Filter by normalized subject name.")
    parser.add_argument("--year", help="Filter by ROC year.")
    parser.add_argument("--exam-ordinal", help="Filter by exam ordinal.")
    return parser.parse_args()


def resolve_inputs(args: argparse.Namespace) -> tuple[Path, Path, Path, Path]:
    candidate_path = args.candidate_jsonl or latest_path("*/question_candidates__*.jsonl")
    issue_path = args.issue_csv or candidate_path.with_name(candidate_path.name.replace("question_candidates__", "question_parse_issues__").replace(".jsonl", ".csv"))
    if not issue_path.exists():
        issue_path = args.issue_csv or latest_path("*/question_parse_issues__*.csv")
    review_log = args.question_review_log or candidate_path.parent / "question_review_events.jsonl"
    answer_review_log = args.answer_review_log or candidate_path.parent / "answer_review_events.jsonl"
    return candidate_path, issue_path, review_log, answer_review_log


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
    return rows


def load_latest_events(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    latest: dict[str, dict[str, Any]] = {}
    latest_reset: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return latest, latest_reset
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = event.get("candidate_key")
            if not key:
                continue
            if event.get("action") in RESET_ACTIONS:
                latest.pop(key, None)
                latest_reset[key] = event
                continue
            if "correction" not in event and key in latest and latest[key].get("correction"):
                event["correction"] = latest[key]["correction"]
            latest[key] = event
            latest_reset.pop(key, None)
    return latest, latest_reset


def load_issues(path: Path) -> dict[str, list[dict[str, str]]]:
    by_key: dict[str, list[dict[str, str]]] = {}
    if not path.exists():
        return by_key
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = row.get("candidate_key") or ""
            if key:
                by_key.setdefault(key, []).append(row)
    return by_key


def text_for_candidate(candidate: dict[str, Any]) -> str:
    parts = [candidate.get("stem") or ""]
    for option in candidate.get("options") or []:
        if isinstance(option, dict):
            parts.append(str(option.get("text") or ""))
    return "\n".join(parts)


def normalized_option_keys(candidate: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for option in candidate.get("options") or []:
        if isinstance(option, dict):
            key = str(option.get("key") or option.get("label") or "").strip().upper()
            if key:
                keys.add(key)
    return keys


def has_four_choice_options(candidate: dict[str, Any]) -> bool:
    return {"A", "B", "C", "D"}.issubset(normalized_option_keys(candidate))


def apply_review_correction(candidate: dict[str, Any], correction: dict[str, Any] | None) -> dict[str, Any]:
    effective = copy.deepcopy(candidate)
    if not isinstance(correction, dict):
        return effective
    for field in (
        "stem",
        "stem_markup",
        "stem_image",
        "options",
        "answer",
        "answer_payload",
        "group_ref",
        "question_type",
    ):
        if field in correction:
            effective[field] = correction[field]
    if correction.get("image_refs"):
        existing = effective.get("image_refs") or []
        effective["image_refs"] = [*existing, *correction["image_refs"]]
    return effective


def issue_resolved_by_effective_candidate(issue: dict[str, str], effective: dict[str, Any]) -> bool:
    code = issue.get("issue_code") or ""
    if code == "too_few_options":
        return has_four_choice_options(effective)
    if code == "image_hint_without_asset":
        return has_usable_asset(effective, None)
    if code == "missing_group_ref":
        return bool(effective.get("group_ref"))
    return False


def image_refs(candidate: dict[str, Any], correction: dict[str, Any] | None) -> list[Any]:
    refs: list[Any] = []
    refs.extend(candidate.get("image_refs") or [])
    stem_image = candidate.get("stem_image")
    if stem_image:
        refs.append(stem_image)
    for option in candidate.get("options") or []:
        if isinstance(option, dict) and option.get("image"):
            refs.append(option["image"])
    correction = correction or {}
    if correction.get("stem_image"):
        refs.append(correction["stem_image"])
    refs.extend(correction.get("image_refs") or [])
    for option in correction.get("options") or []:
        if isinstance(option, dict) and option.get("image"):
            refs.append(option["image"])
    return refs


def has_usable_asset(candidate: dict[str, Any], correction: dict[str, Any] | None) -> bool:
    for ref in image_refs(candidate, correction):
        if isinstance(ref, str) and ref.strip():
            return True
        if isinstance(ref, dict):
            if ref.get("exists") is False:
                continue
            if ref.get("path") or ref.get("relative_path") or ref.get("raw_ref"):
                return True
    return False


def latest_action(events: dict[str, dict[str, Any]], key: str) -> str:
    return str((events.get(key) or {}).get("action") or "")


def evaluate_candidate(
    candidate: dict[str, Any],
    issues: dict[str, list[dict[str, str]]],
    question_reviews: dict[str, dict[str, Any]],
    question_resets: dict[str, dict[str, Any]],
    answer_reviews: dict[str, dict[str, Any]],
    answer_resets: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    key = candidate.get("candidate_key") or ""
    reasons: list[str] = []
    warnings: list[str] = []
    q_event = question_reviews.get(key) or {}
    a_event = answer_reviews.get(key) or {}
    q_action = latest_action(question_reviews, key)
    a_action = latest_action(answer_reviews, key)
    correction = q_event.get("correction") or q_event.get("corrected_candidate_json") or {}
    effective = apply_review_correction(candidate, correction if isinstance(correction, dict) else None)
    question_ready = q_action in QUESTION_READY_ACTIONS

    if candidate.get("quality_status") != "pass":
        if question_ready:
            warnings.append(f"candidate_quality_resolved_by_human_review={candidate.get('quality_status') or 'missing'}")
        else:
            reasons.append(f"candidate_quality_status={candidate.get('quality_status') or 'missing'}")

    if key in question_resets and key not in question_reviews:
        reasons.append("question_review_reset_pending")
    elif q_action not in QUESTION_READY_ACTIONS:
        if q_action in BLOCKING_REVIEW_ACTIONS:
            reasons.append(f"question_review_not_ready={q_action}")
        else:
            reasons.append("question_review_missing_accept")

    if key in answer_resets and key not in answer_reviews:
        reasons.append("answer_review_reset_pending")
    elif a_action not in ANSWER_READY_ACTIONS:
        if a_action in BLOCKING_REVIEW_ACTIONS:
            reasons.append(f"answer_review_not_ready={a_action}")
        else:
            reasons.append("answer_review_missing_accept")

    unresolved_blocking_issues = [
        item for item in issues.get(key, [])
        if (item.get("severity") or "").lower() in BLOCKING_ISSUE_SEVERITIES
        and not (question_ready and issue_resolved_by_effective_candidate(item, effective))
    ]
    if unresolved_blocking_issues:
        issue_codes = sorted({item.get("issue_code") or "unknown_issue" for item in unresolved_blocking_issues})
        reasons.append("blocking_parse_issues=" + ",".join(issue_codes))
    resolved_blocking_issues = [
        item for item in issues.get(key, [])
        if (item.get("severity") or "").lower() in BLOCKING_ISSUE_SEVERITIES
        and question_ready
        and issue_resolved_by_effective_candidate(item, effective)
    ]
    if resolved_blocking_issues:
        issue_codes = sorted({item.get("issue_code") or "unknown_issue" for item in resolved_blocking_issues})
        warnings.append("blocking_parse_issues_resolved_by_human_correction=" + ",".join(issue_codes))

    text = text_for_candidate(effective)
    group_ref = effective.get("group_ref")
    if GROUP_HINT_RE.search(text) and not group_ref:
        if question_ready:
            warnings.append("group_hint_without_group_ref_confirmed_by_human_review")
        else:
            reasons.append("group_hint_without_group_ref")

    if VISUAL_HINT_RE.search(text) and not has_usable_asset(effective, None):
        if question_ready:
            warnings.append("visual_hint_without_asset_confirmed_by_human_review")
        else:
            reasons.append("visual_hint_without_asset")

    if MARKUP_HINT_RE.search(text) and not (effective.get("stem_markup") or any((opt or {}).get("markup") for opt in effective.get("options") or [] if isinstance(opt, dict))):
        warnings.append("markup_or_table_fragment_without_markup_json")

    metadata = candidate.get("metadata") or {}
    return {
        "candidate_key": key,
        "source_registry_key": candidate.get("source_registry_key") or "",
        "answer_source_registry_key": candidate.get("answer_source_registry_key") or "",
        "category": metadata.get("normalized_category_name") or metadata.get("group_name") or "",
        "subject": metadata.get("normalized_subject_name") or metadata.get("subject_name") or "",
        "year": metadata.get("year") or "",
        "exam_ordinal": metadata.get("exam_ordinal") or "",
        "question_number": candidate.get("question_number") or "",
        "quality_status": candidate.get("quality_status") or "",
        "question_review_action": q_action or ("reset_review" if key in question_resets else ""),
        "answer_review_action": a_action or ("reset_review" if key in answer_resets else ""),
        "group_ref": group_ref or "",
        "asset_count": len(image_refs(effective, None)),
        "issue_count": len(issues.get(key, [])),
        "status": "ready" if not reasons else "blocked",
        "reasons": ";".join(reasons),
        "warnings": ";".join(warnings),
    }


def write_rows(path: Path | None, text: str) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def rows_to_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    fields = list(rows[0].keys())
    from io import StringIO

    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def candidate_matches_filters(candidate: dict[str, Any], args: argparse.Namespace) -> bool:
    metadata = candidate.get("metadata") or {}
    if args.category and metadata.get("normalized_category_name") != args.category:
        return False
    if args.subject and metadata.get("normalized_subject_name") != args.subject:
        return False
    if args.year and str(metadata.get("year") or "") != str(args.year):
        return False
    if args.exam_ordinal and str(metadata.get("exam_ordinal") or "") != str(args.exam_ordinal):
        return False
    return True


def main() -> None:
    args = parse_args()
    candidate_path, issue_path, question_review_log, answer_review_log = resolve_inputs(args)
    candidates = read_jsonl(candidate_path)
    candidates = [candidate for candidate in candidates if candidate_matches_filters(candidate, args)]
    if args.limit:
        candidates = candidates[: args.limit]
    issues = load_issues(issue_path)
    question_reviews, question_resets = load_latest_events(question_review_log)
    answer_reviews, answer_resets = load_latest_events(answer_review_log)

    rows = [
        evaluate_candidate(candidate, issues, question_reviews, question_resets, answer_reviews, answer_resets)
        for candidate in candidates
    ]
    output_rows = rows if args.include_ready else [row for row in rows if row["status"] != "ready" or row["warnings"]]
    counts = Counter(row["status"] for row in rows)
    reason_counts = Counter(reason for row in rows for reason in str(row["reasons"]).split(";") if reason)
    warning_counts = Counter(warning for row in rows for warning in str(row["warnings"]).split(";") if warning)

    if args.format == "csv":
        write_rows(args.output, rows_to_csv(output_rows))
        return
    if args.format == "jsonl":
        payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in output_rows)
        write_rows(args.output, payload)
        return

    summary = {
        "candidate_jsonl": str(candidate_path),
        "issue_csv": str(issue_path),
        "question_review_log": str(question_review_log),
        "answer_review_log": str(answer_review_log),
        "candidate_count": len(rows),
        "ready_count": counts.get("ready", 0),
        "blocked_count": counts.get("blocked", 0),
        "top_block_reasons": dict(reason_counts.most_common(20)),
        "warnings": dict(warning_counts.most_common(20)),
    }
    json.dump(summary, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    print()


if __name__ == "__main__":
    main()
