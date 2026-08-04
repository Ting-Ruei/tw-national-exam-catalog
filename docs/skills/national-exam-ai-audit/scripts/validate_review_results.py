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
        "option_structure", "ocr_character", "notation_markup", "semantic_disfluency", "visual_dependency",
        "group_dependency", "semantic_ocr", "scientific_terminology", "formula_integrity",
    },
    "image": {"visual_missing", "visual_wrong_crop", "visual_wrong_placement", "visual_not_required"},
    "group": {"group_missing", "group_range_wrong", "shared_stem_missing", "not_group"},
    "answer": {
        "answer_missing", "answer_source_mismatch", "mod_precedence",
        "multi_answer_representation", "answer_semantic_suspect",
    },
}
ALLOWED_CORRECTION_FIELDS = {"stem", "options", "group_ref", "group_sequence_no"}
RECOMMENDED_ACTIONS = {
    "none", "human_review_text", "human_review_pdf", "fix_parser",
    "add_manual_asset", "review_group", "review_answer",
}
WORK_LANES = {
    "none", "propose_rule", "human_text", "human_pdf", "manual_visual",
    "parser_repair", "answer_review",
}
FINDING_LOCATIONS = {"stem", "options", "group_ref", "group_sequence_no"} | {
    f"option_{key}" for key in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
}
CORRECTION_COVERAGE_VALUES = {"complete", "partial", "none"}
CORRECTION_FIRST_PROMPT_VERSIONS = {
    "llmshare_five_model_text_audit_v2",
    "codex_gpt56_luna_question_audit_v3",
    "national_exam_sparse_audit_v4",
}


def correction_shape_errors(correction: Any) -> list[str]:
    if not isinstance(correction, dict) or not correction:
        return ["suggested_correction_must_be_nonempty_object"]
    errors: list[str] = []
    if set(correction) - ALLOWED_CORRECTION_FIELDS:
        errors.append("correction_contains_unsafe_fields")
    if "stem" in correction and not isinstance(correction["stem"], str):
        errors.append("correction_stem_must_be_string")
    if "options" in correction:
        options = correction["options"]
        if not isinstance(options, list) or not options:
            errors.append("correction_options_must_be_nonempty_list")
        else:
            keys: list[str] = []
            for option in options:
                if not isinstance(option, dict) or set(option) - {"key", "text"}:
                    errors.append("correction_option_must_only_have_key_and_text")
                    continue
                key = str(option.get("key") or "").strip()
                if not key or not isinstance(option.get("text"), str):
                    errors.append("correction_option_requires_key_and_text")
                keys.append(key)
            if len(keys) != len(set(keys)):
                errors.append("correction_option_keys_must_be_unique")
    if "group_ref" in correction and correction["group_ref"] is not None and not isinstance(correction["group_ref"], str):
        errors.append("correction_group_ref_must_be_string_or_null")
    if "group_sequence_no" in correction:
        sequence = correction["group_sequence_no"]
        if sequence is not None and (not isinstance(sequence, int) or isinstance(sequence, bool)):
            errors.append("correction_group_sequence_no_must_be_integer_or_null")
    return errors


def correction_text_values(correction: Any) -> list[str]:
    if not isinstance(correction, dict):
        return []
    values: list[str] = []
    if isinstance(correction.get("stem"), str):
        values.append(correction["stem"])
    if isinstance(correction.get("options"), list):
        values.extend(
            str(option.get("text") or "")
            for option in correction["options"]
            if isinstance(option, dict)
        )
    if isinstance(correction.get("group_ref"), str):
        values.append(correction["group_ref"])
    if correction.get("group_sequence_no") is not None:
        values.append(str(correction["group_sequence_no"]))
    return values


def validate_v2_correction_contract(
    result: dict[str, Any],
    status: str,
    correction: Any,
    *,
    strict_global_ocr: bool = True,
) -> list[str]:
    errors: list[str] = []
    findings = result.get("findings")
    coverage = result.get("correction_coverage")
    uncorrected = result.get("uncorrected_findings")
    if not isinstance(findings, list):
        return ["findings_must_be_list"]
    if len(findings) > 3:
        errors.append("too_many_findings")
    if status == "pass" and findings:
        errors.append("pass_findings_must_be_empty")
    if status != "pass" and not findings:
        errors.append("non_pass_requires_finding")
    if coverage not in CORRECTION_COVERAGE_VALUES:
        errors.append("invalid_correction_coverage")
    if not isinstance(uncorrected, list):
        errors.append("uncorrected_findings_must_be_list")
        uncorrected = []
    if status == "pass":
        if coverage != "none":
            errors.append("pass_correction_coverage_must_be_none")
        if uncorrected:
            errors.append("pass_uncorrected_findings_must_be_empty")
        if correction is not None:
            errors.append("pass_suggested_correction_must_be_null")
    patch_text = "\n".join(correction_text_values(correction))
    uncovered: list[int] = []
    for index, finding in enumerate(findings, start=1):
        if not isinstance(finding, dict):
            errors.append(f"finding_{index}_must_be_object")
            continue
        family = str(finding.get("issue_family") or "")
        location = str(finding.get("location") or "")
        observed = finding.get("observed")
        suggested = finding.get("suggested")
        finding_confidence = finding.get("confidence")
        applicable = finding.get("correction_applicable")
        omission_reason = finding.get("correction_omission_reason")
        if family not in ISSUES_BY_STAGE["question"]:
            errors.append(f"finding_{index}_invalid_issue_family")
        if location not in FINDING_LOCATIONS:
            errors.append(f"finding_{index}_invalid_location")
        if not isinstance(observed, str) or not observed.strip():
            errors.append(f"finding_{index}_observed_required")
        if suggested is not None and not isinstance(suggested, str):
            errors.append(f"finding_{index}_suggested_must_be_string_or_null")
        if (
            not isinstance(finding_confidence, (int, float))
            or isinstance(finding_confidence, bool)
            or not 0 <= finding_confidence <= 1
        ):
            errors.append(f"finding_{index}_invalid_confidence")
        if not isinstance(applicable, bool):
            errors.append(f"finding_{index}_correction_applicable_must_be_boolean")
            continue
        if applicable:
            if not isinstance(suggested, str) or not suggested.strip() or suggested == observed:
                errors.append(f"finding_{index}_applicable_requires_replacement")
            elif suggested not in patch_text:
                uncovered.append(index)
            if (
                strict_global_ocr
                and
                family in {"ocr_character", "semantic_ocr"}
                and isinstance(observed, str)
                and observed.strip()
                and observed != suggested
                and observed in patch_text
            ):
                errors.append(f"finding_{index}_original_ocr_text_remains_in_correction")
        elif not str(omission_reason or "").strip():
            errors.append(f"finding_{index}_omission_reason_required")
    if uncovered:
        errors.append("explicit_findings_without_complete_correction:" + ",".join(map(str, uncovered)))
    if coverage == "complete" and (correction is None or uncovered):
        errors.append("complete_coverage_requires_complete_patch")
    if coverage in {"partial", "none"} and any(
        isinstance(item, dict) and item.get("correction_applicable") is True
        for item in findings
    ) and not uncorrected:
        errors.append("omitted_correction_requires_itemized_reason")
    if coverage == "partial" and correction is None:
        errors.append("partial_coverage_requires_partial_patch")
    if coverage == "none" and correction is not None:
        errors.append("none_coverage_requires_null_patch")
    return errors


def validate_v4_correction_contract(
    result: dict[str, Any],
    correction: Any,
    task: dict[str, Any],
) -> list[str]:
    """Validate sparse-v4 patches at the declared field, not globally.

    A repeated OCR token can legitimately remain in a different option that
    was not reported; global substring rejection made safe one-option patches
    look incomplete.  The v4 contract still requires the observed token to be
    removed from the exact field named by that finding.
    """
    errors: list[str] = []
    if not isinstance(correction, dict):
        return errors
    for index, finding in enumerate(result.get("findings") or [], start=1):
        if not isinstance(finding, dict) or not finding.get("correction_applicable"):
            continue
        family = str(finding.get("issue_family") or "")
        observed = str(finding.get("observed") or "")
        if family not in {"ocr_character", "semantic_ocr"} or not observed:
            continue
        location = str(finding.get("location") or "")
        if location == "stem":
            patched = str(correction.get("stem") or "")
        elif location.startswith("option_"):
            key = location[-1].upper()
            patched = next(
                (
                    str(option.get("text") or "")
                    for option in correction.get("options") or []
                    if isinstance(option, dict) and str(option.get("key") or "").upper() == key
                ),
                "",
            )
        else:
            patched = ""
        if observed in patched:
            errors.append(f"finding_{index}_original_ocr_text_remains_in_declared_field")
    return errors


def validate_luna_v3_correction_contract(
    result: dict[str, Any],
    correction: Any,
    task: dict[str, Any],
) -> list[str]:
    """Enforce UI-safe, operator-actionable corrections for the LUNA v3 prompt."""
    errors: list[str] = []
    suggested_changes = result.get("suggested_changes")
    if correction is not None:
        if (
            not isinstance(suggested_changes, list)
            or not suggested_changes
            or any(not str(value).strip() for value in suggested_changes)
        ):
            errors.append("v3_correction_requires_suggested_changes")
    elif isinstance(suggested_changes, list) and suggested_changes:
        errors.append("v3_suggested_changes_require_correction")

    if not isinstance(correction, dict) or "options" not in correction:
        return errors
    expected_options = (task.get("content") or {}).get("options") or []
    expected_keys = [
        str(option.get("key") or "").strip().upper()
        for option in expected_options
        if isinstance(option, dict) and str(option.get("key") or "").strip()
    ]
    correction_options = correction.get("options") or []
    correction_keys = [
        str(option.get("key") or "").strip().upper()
        for option in correction_options
        if isinstance(option, dict) and str(option.get("key") or "").strip()
    ]
    if expected_keys and correction_keys != expected_keys:
        errors.append("v3_options_correction_must_include_all_original_options_in_order")
    return errors


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
    task_by_key: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    for _line_no, task in read_jsonl(args.tasks):
        if task.get("__error__"):
            errors.append({"source": task["__error__"], "error": "invalid_task_json"})
            continue
        key = str(task.get("candidate_key") or "")
        if key:
            task_stage[key] = str(task.get("stage") or "question")
            task_by_key[key] = task

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
        work_lane = str(result.get("work_lane") or "none")
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
        if work_lane not in WORK_LANES:
            row_errors.append("invalid_work_lane")
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
        if status == "pass" and work_lane != "none":
            row_errors.append("pass_requires_no_work_lane")
        if status != "pass" and (not isinstance(evidence, list) or not evidence):
            row_errors.append("non_pass_requires_evidence")
        correction = result.get("suggested_correction")
        if correction is not None:
            if stage != "question" or not isinstance(correction, dict):
                row_errors.append("correction_only_allowed_for_question_stage")
            else:
                row_errors.extend(correction_shape_errors(correction))
            if not evidence:
                row_errors.append("correction_requires_evidence")
        prompt_version = str(result.get("prompt_version") or "")
        if prompt_version in CORRECTION_FIRST_PROMPT_VERSIONS:
            if stage != "question":
                row_errors.append("correction_first_contract_only_supports_question_stage")
            else:
                row_errors.extend(
                    validate_v2_correction_contract(
                        result,
                        status,
                        correction,
                        strict_global_ocr=prompt_version != "national_exam_sparse_audit_v4",
                    )
                )
                if prompt_version == "codex_gpt56_luna_question_audit_v3":
                    row_errors.extend(
                        validate_luna_v3_correction_contract(
                            result,
                            correction,
                            task_by_key.get(key) or {},
                        )
                    )
                elif prompt_version == "national_exam_sparse_audit_v4":
                    row_errors.extend(
                        validate_v4_correction_contract(
                            result,
                            correction,
                            task_by_key.get(key) or {},
                        )
                    )
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
