#!/usr/bin/env python3
"""Validate advisory model review JSONL before any SQL import."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


STATUS_VALUES = {"pass", "needs_review", "block"}
ISSUES_BY_STAGE = {
    "question": {
        "non_question_header", "boundary_merge", "boundary_missing", "empty_stem",
        "option_structure", "ocr_character", "notation_markup", "visual_dependency",
        "group_dependency",
    },
    "image": {"visual_missing", "visual_wrong_crop", "visual_wrong_placement", "visual_not_required"},
    "group": {"group_missing", "group_range_wrong", "shared_stem_missing", "not_group"},
    "answer": {"answer_missing", "answer_source_mismatch", "mod_precedence", "multi_answer_representation"},
}
ALLOWED_CORRECTION_FIELDS = {"stem", "options", "group_ref", "group_sequence_no"}
RECOMMENDED_ACTIONS = {
    "none", "human_review_text", "human_review_pdf", "fix_parser",
    "add_manual_asset", "review_group", "review_answer",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True, help="Task JSONL file or task directory")
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def jsonl_paths(path: Path) -> list[Path]:
    return sorted(path.glob("*.jsonl")) if path.is_dir() else [path]


def read_jsonl(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    for file_path in jsonl_paths(path):
        with file_path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    yield line_no, {"__error__": f"{file_path}:{line_no}: {exc}"}
                    continue
                value["__source__"] = f"{file_path}:{line_no}"
                yield line_no, value


def main() -> int:
    args = parse_args()
    task_stage: dict[str, str] = {}
    errors: list[dict[str, Any]] = []
    for _line_no, task in read_jsonl(args.tasks):
        if task.get("__error__"):
            errors.append({"source": task["__error__"], "error": "invalid_task_json"})
            continue
        key = str(task.get("candidate_key") or "")
        if key:
            task_stage[key] = str(task.get("stage") or "question")

    seen: set[str] = set()
    valid_count = 0
    for _line_no, result in read_jsonl(args.results):
        source = result.pop("__source__", str(args.results))
        row_errors: list[str] = []
        if result.get("__error__"):
            errors.append({"source": source, "error": result["__error__"]})
            continue
        key = str(result.get("candidate_key") or "")
        stage = str(result.get("stage") or task_stage.get(key) or "")
        status = str(result.get("status") or "")
        issues = result.get("issue_families")
        evidence = result.get("evidence")
        confidence = result.get("confidence")
        recommended_action = str(result.get("recommended_action") or "")
        if not key or key not in task_stage:
            row_errors.append("candidate_key_not_in_tasks")
        if key in seen:
            row_errors.append("duplicate_candidate_key")
        seen.add(key)
        if stage != task_stage.get(key):
            row_errors.append("stage_mismatch")
        if status not in STATUS_VALUES:
            row_errors.append("invalid_status")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
            row_errors.append("invalid_confidence")
        if not str(result.get("reason") or "").strip():
            row_errors.append("reason_required")
        if recommended_action not in RECOMMENDED_ACTIONS:
            row_errors.append("invalid_recommended_action")
        if not isinstance(issues, list):
            row_errors.append("issue_families_must_be_list")
            issues = []
        if len(issues) > 3:
            row_errors.append("too_many_issue_families")
        invalid_issues = set(issues) - ISSUES_BY_STAGE.get(stage, set())
        if invalid_issues:
            row_errors.append(f"invalid_issue_families:{','.join(sorted(invalid_issues))}")
        if status == "pass" and issues:
            row_errors.append("pass_must_not_have_issues")
        if status == "pass" and recommended_action != "none":
            row_errors.append("pass_requires_no_action")
        if status != "pass" and (not isinstance(evidence, list) or not evidence):
            row_errors.append("non_pass_requires_evidence")
        correction = result.get("suggested_correction")
        if correction is not None:
            if stage != "question" or not isinstance(correction, dict):
                row_errors.append("correction_only_allowed_for_question_stage")
            elif set(correction) - ALLOWED_CORRECTION_FIELDS:
                row_errors.append("correction_contains_unsafe_fields")
            if not evidence:
                row_errors.append("correction_requires_evidence")
        if row_errors:
            errors.append({"source": source, "candidate_key": key, "errors": row_errors})
        else:
            valid_count += 1

    missing_keys = sorted(set(task_stage) - seen)
    if missing_keys:
        errors.append({"error": "missing_results", "count": len(missing_keys), "candidate_keys": missing_keys[:100]})
    report = {
        "ok": not errors,
        "task_count": len(task_stage),
        "result_count": len(seen),
        "valid_count": valid_count,
        "missing_result_count": len(missing_keys),
        "errors": errors[:500],
    }
    report_path = args.report or args.results.with_suffix(".validation.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
