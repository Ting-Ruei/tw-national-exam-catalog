#!/usr/bin/env python3
"""Validate sparse v4 batch results against the frozen coverage ledger."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from v4_common import (
    ISSUE_FAMILIES,
    PROMPT_VERSION,
    ROUTES,
    SOURCE_CLASSES,
    compact_json,
    get_field_text,
    read_json,
    read_jsonl,
    sha256_json,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--segment-manifest",
        type=Path,
        help="Filter the full manifest to the ordered batch_ids in a segment manifest.",
    )
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def issue_errors(issue: Any, task: dict[str, Any]) -> list[str]:
    if not isinstance(issue, dict):
        return ["issue_must_be_object"]
    errors: list[str] = []
    field = str(issue.get("field") or "")
    before = issue.get("before")
    after = issue.get("after")
    family = str(issue.get("issue_family") or "")
    source_class = str(issue.get("source_class") or "")
    route = str(issue.get("route") or "")
    confidence = issue.get("confidence")
    note = issue.get("note")
    if field not in {"stem", "options", "group_ref", "image_refs"} and not (
        field.startswith("option_") and len(field) == 8 and field[-1].isalpha()
    ):
        errors.append("invalid_field")
    if before is not None and not isinstance(before, str):
        errors.append("before_must_be_string_or_null")
    if after is not None and not isinstance(after, str):
        errors.append("after_must_be_string_or_null")
    if family not in ISSUE_FAMILIES:
        errors.append("invalid_issue_family")
    if source_class not in SOURCE_CLASSES:
        errors.append("invalid_source_class")
    if route not in ROUTES:
        errors.append("invalid_route")
    if (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not 0 <= confidence <= 1
    ):
        errors.append("invalid_confidence")
    if not isinstance(note, str) or not note.strip():
        errors.append("note_required_with_observable_reason")
    elif len(note) > 160:
        errors.append("note_too_long")
    if isinstance(before, str) and (
        field == "stem" or field.startswith("option_")
    ):
        current = get_field_text(task, field)
        if current is None:
            errors.append("text_field_not_found")
        elif before not in current:
            errors.append("before_not_found_in_declared_field")

    if route == "deterministic":
        rule_id = str(issue.get("rule_id") or "")
        rules = {
            str(rule.get("id")): rule
            for rule in task.get("active_rules") or []
            if isinstance(rule, dict)
        }
        rule = rules.get(rule_id)
        if not rule:
            errors.append("deterministic_route_requires_matching_active_rule")
        elif before != rule.get("source") or after != rule.get("target"):
            errors.append("deterministic_issue_must_match_active_rule_source_target")
        if source_class not in {"candidate_mismatch", "mineru_ocr_mismatch"}:
            errors.append("deterministic_route_rejects_source_class")
        current = get_field_text(task, field)
        if current is None:
            errors.append("deterministic_field_not_materializable")
        elif not isinstance(before, str) or not before or current.count(before) != 1:
            errors.append("deterministic_before_must_match_exactly_once")

    for control in task.get("negative_controls") or []:
        if (
            route == "deterministic"
            and before == control.get("observed")
            and after == control.get("rejected_target")
        ):
            errors.append(f"negative_control_violation:{control.get('id')}")
    return errors


def load_and_validate_packets(
    manifest: dict[str, Any],
    run_dir: Path,
) -> tuple[
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, dict[str, Any]]],
    list[dict[str, Any]],
]:
    batch_rows = manifest.get("batches") or []
    if not isinstance(batch_rows, list):
        return [], {}, {}, [{"error": "manifest_batches_must_be_list"}]

    integrity_errors: list[dict[str, Any]] = []
    batch_id_counts = Counter(
        str(row.get("batch_id") or "")
        for row in batch_rows
        if isinstance(row, dict)
    )
    batches: dict[str, dict[str, Any]] = {}
    packet_tasks: dict[str, dict[str, dict[str, Any]]] = {}
    candidate_owners: dict[str, tuple[str, int]] = {}
    total_packet_tasks = 0
    max_packet_bytes = manifest.get("max_packet_bytes")
    if (
        max_packet_bytes is not None
        and (
            not isinstance(max_packet_bytes, int)
            or isinstance(max_packet_bytes, bool)
            or max_packet_bytes < 1
        )
    ):
        integrity_errors.append({"error": "invalid_manifest_max_packet_bytes"})
        max_packet_bytes = None

    for index, batch in enumerate(batch_rows, start=1):
        if not isinstance(batch, dict):
            integrity_errors.append(
                {"batch_index": index, "errors": ["manifest_batch_must_be_object"]}
            )
            continue
        batch_id = str(batch.get("batch_id") or "")
        batch_errors: list[str] = []
        if not batch_id:
            batch_errors.append("empty_manifest_batch_id")
        if batch_id_counts[batch_id] > 1:
            batch_errors.append("duplicate_manifest_batch_id")
        if batch_id and batch_id not in batches:
            batches[batch_id] = batch

        packet_path = str(batch.get("packet_path") or "")
        if not packet_path:
            batch_errors.append("missing_packet_path")
            packet = None
        else:
            try:
                packet = read_json(run_dir / packet_path)
            except FileNotFoundError:
                batch_errors.append("packet_file_not_found")
                packet = None
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                batch_errors.append("packet_file_unreadable_or_invalid_json")
                packet = None

        if not isinstance(packet, dict):
            if packet is not None:
                batch_errors.append("packet_must_be_object")
            if batch_errors:
                integrity_errors.append(
                    {"batch_id": batch_id, "errors": sorted(set(batch_errors))}
                )
            continue

        expected_sha256 = str(batch.get("packet_sha256") or "")
        actual_sha256 = sha256_json(packet)
        if not expected_sha256:
            batch_errors.append("missing_packet_sha256")
        elif actual_sha256 != expected_sha256:
            batch_errors.append("packet_sha256_mismatch")
        if str(packet.get("batch_id") or "") != batch_id:
            batch_errors.append("packet_batch_id_mismatch")

        tasks = packet.get("tasks")
        if not isinstance(tasks, list):
            batch_errors.append("packet_tasks_must_be_list")
            tasks = []
        packet_keys: list[str] = []
        task_map: dict[str, dict[str, Any]] = {}
        for task_index, task in enumerate(tasks, start=1):
            if not isinstance(task, dict):
                batch_errors.append(f"packet_task_{task_index}_must_be_object")
                packet_keys.append("")
                continue
            key = str(task.get("candidate_key") or "")
            packet_keys.append(key)
            if not key:
                batch_errors.append("empty_candidate_key_in_packet")
            elif key in task_map:
                batch_errors.append("duplicate_candidate_key_in_packet")
            else:
                task_map[key] = task
            if key:
                owner = candidate_owners.get(key)
                if owner is not None:
                    batch_errors.append("candidate_key_overlaps_batches")
                else:
                    candidate_owners[key] = (batch_id, index)

        manifest_keys = batch.get("candidate_keys")
        if not isinstance(manifest_keys, list):
            batch_errors.append("manifest_candidate_keys_must_be_list")
        elif [str(key) for key in manifest_keys] != packet_keys:
            batch_errors.append("manifest_candidate_keys_mismatch_packet_tasks")
        if packet.get("task_count") != len(tasks):
            batch_errors.append("packet_task_count_mismatch")
        if batch.get("task_count") != len(tasks):
            batch_errors.append("manifest_batch_task_count_mismatch")
        total_packet_tasks += len(tasks)
        if batch_id and batch_id not in packet_tasks:
            packet_tasks[batch_id] = task_map

        actual_packet_bytes = len(compact_json(packet).encode("utf-8"))
        declared_packet_bytes = batch.get("packet_bytes")
        if declared_packet_bytes is not None and declared_packet_bytes != actual_packet_bytes:
            batch_errors.append("packet_bytes_mismatch")
        batch_oversized = bool(batch.get("oversized_singleton"))
        packet_oversized = bool(packet.get("oversized_singleton"))
        if batch_oversized != packet_oversized:
            batch_errors.append("oversized_singleton_marker_mismatch")
        if max_packet_bytes is not None:
            if actual_packet_bytes > max_packet_bytes:
                if not batch_oversized:
                    batch_errors.append(
                        "packet_exceeds_max_packet_bytes_without_oversized_singleton"
                    )
                if len(tasks) != 1:
                    batch_errors.append("oversized_packet_must_be_singleton")
            elif batch_oversized:
                batch_errors.append("oversized_singleton_not_over_limit")

        if batch_errors:
            integrity_errors.append(
                {"batch_id": batch_id, "errors": sorted(set(batch_errors))}
            )

    manifest_task_count = manifest.get("task_count")
    if manifest_task_count is not None and manifest_task_count != total_packet_tasks:
        integrity_errors.append(
            {
                "error": "manifest_task_count_mismatch",
                "manifest_task_count": manifest_task_count,
                "packet_task_count": total_packet_tasks,
            }
        )
    return batch_rows, batches, packet_tasks, integrity_errors


def main() -> int:
    args = parse_args()
    manifest = read_json(args.manifest)
    if args.segment_manifest:
        segment = read_json(args.segment_manifest)
        wanted = [str(value) for value in segment.get("batch_ids") or []]
        by_id = {
            str(batch.get("batch_id") or ""): batch
            for batch in manifest.get("batches") or []
        }
        if any(batch_id not in by_id for batch_id in wanted):
            raise SystemExit("segment manifest contains unknown batch id")
        manifest = {
            **manifest,
            "batches": [by_id[batch_id] for batch_id in wanted],
            "task_count": sum(by_id[batch_id].get("task_count", 0) for batch_id in wanted),
        }
    run_dir = args.manifest.parent
    batch_rows, batches, packet_tasks, errors = load_and_validate_packets(
        manifest,
        run_dir,
    )

    rows = [row for _, row in read_jsonl(args.results)]
    counts = Counter(str(row.get("batch_id") or "") for row in rows)
    issue_count = 0
    seen_replacements: set[tuple[str, str, str | None]] = set()
    for row in rows:
        batch_id = str(row.get("batch_id") or "")
        row_errors: list[str] = []
        batch = batches.get(batch_id)
        if not batch:
            row_errors.append("unknown_batch_id")
        if counts[batch_id] > 1:
            row_errors.append("duplicate_batch_result")
        if row.get("prompt_version") != PROMPT_VERSION:
            row_errors.append("prompt_version_mismatch")
        if row.get("model") != manifest.get("model"):
            row_errors.append("model_mismatch")
        issues = row.get("issues")
        if not isinstance(issues, list):
            row_errors.append("issues_must_be_list")
            issues = []
        if batch and row.get("checked_count") != batch.get("task_count"):
            row_errors.append("checked_count_mismatch")
        per_candidate = Counter()
        for index, issue in enumerate(issues, start=1):
            issue_count += 1
            key = str(issue.get("candidate_key") or "") if isinstance(issue, dict) else ""
            task = packet_tasks.get(batch_id, {}).get(key)
            if not task:
                row_errors.append(f"issue_{index}_candidate_not_in_batch")
                continue
            per_candidate[key] += 1
            for error in issue_errors(issue, task):
                row_errors.append(f"issue_{index}_{error}")
            before = issue.get("before") if isinstance(issue, dict) else None
            after = issue.get("after") if isinstance(issue, dict) else None
            if isinstance(before, str) and (
                isinstance(after, str) or after is None
            ):
                replacement = (key, before, after)
                if replacement in seen_replacements:
                    row_errors.append(
                        "duplicate_exact_before_after_for_candidate"
                    )
                else:
                    seen_replacements.add(replacement)
        if any(count > 3 for count in per_candidate.values()):
            row_errors.append("more_than_three_issues_for_candidate")
        if row_errors:
            errors.append({"batch_id": batch_id, "errors": sorted(set(row_errors))})

    missing = sorted(set(batches) - set(counts))
    if missing:
        errors.append({"error": "missing_batch_results", "batch_ids": missing})
    report = {
        "ok": not errors,
        "expected_batch_count": len(batch_rows),
        "result_batch_count": len(rows),
        "issue_count": issue_count,
        "missing_batch_count": len(missing),
        "errors": errors,
    }
    report_path = args.report or args.results.with_suffix(".validation.json")
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
