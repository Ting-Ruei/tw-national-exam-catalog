#!/usr/bin/env python3
"""Plan resumable agent result segments from ordered sparse run manifests."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from v4_common import read_json, read_jsonl, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        action="extend",
        nargs="+",
        type=Path,
        required=True,
        help="One or more sparse run manifests. Repeat to extend the ordered list.",
    )
    parser.add_argument("--batches-per-segment", type=int, default=20)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def lane_slug(lane: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", lane).strip("._-")
    return slug or "lane"


def load_batches(manifest_path: Path) -> tuple[str, list[dict[str, Any]]]:
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError(f"run manifest must be an object: {manifest_path}")
    lane = manifest.get("lane")
    if not isinstance(lane, str) or not lane:
        raise ValueError(f"run manifest has no non-empty lane: {manifest_path}")
    raw_batches = manifest.get("batches")
    if not isinstance(raw_batches, list):
        raise ValueError(f"run manifest batches must be a list: {manifest_path}")
    batches: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_batch in enumerate(raw_batches, start=1):
        if not isinstance(raw_batch, dict):
            raise ValueError(
                f"run manifest batch {index} must be an object: {manifest_path}"
            )
        batch_id = raw_batch.get("batch_id")
        task_count = raw_batch.get("task_count")
        if not isinstance(batch_id, str) or not batch_id:
            raise ValueError(
                f"run manifest batch {index} has no non-empty batch_id: "
                f"{manifest_path}"
            )
        if batch_id in seen:
            raise ValueError(
                f"duplicate batch_id in run manifest {manifest_path}: {batch_id}"
            )
        if type(task_count) is not int or task_count < 0:
            raise ValueError(
                f"invalid task_count for {batch_id} in {manifest_path}: "
                f"{task_count!r}"
            )
        seen.add(batch_id)
        batches.append({"batch_id": batch_id, "task_count": task_count})
    declared_task_count = manifest.get("task_count")
    actual_task_count = sum(batch["task_count"] for batch in batches)
    if declared_task_count is not None and (
        type(declared_task_count) is not int
        or declared_task_count != actual_task_count
    ):
        raise ValueError(
            f"run manifest task_count mismatch: {manifest_path}: "
            f"declared={declared_task_count!r}, batches={actual_task_count}"
        )
    return lane, batches


def segment_result_status(
    result_path: Path,
    batches: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    if not result_path.exists():
        return "pending", []
    if not result_path.is_file():
        return "invalid", [f"segment result is not a file: {result_path}"]
    expected = {
        str(batch["batch_id"]): int(batch["task_count"])
        for batch in batches
    }
    seen: Counter[str] = Counter()
    errors: list[str] = []
    try:
        rows = list(read_jsonl(result_path))
    except (OSError, ValueError) as exc:
        return "invalid", [f"cannot read segment result: {exc}"]
    for line_no, row in rows:
        batch_id = row.get("batch_id")
        if not isinstance(batch_id, str) or not batch_id:
            errors.append(f"line {line_no}: missing_batch_id")
            continue
        seen[batch_id] += 1
        if batch_id not in expected:
            errors.append(f"line {line_no}: unexpected_batch_id:{batch_id}")
            continue
        if seen[batch_id] > 1:
            errors.append(f"line {line_no}: duplicate_batch_id:{batch_id}")
        checked_count = row.get("checked_count")
        if type(checked_count) is not int or checked_count != expected[batch_id]:
            errors.append(
                f"line {line_no}: checked_count_mismatch:{batch_id}:"
                f"expected={expected[batch_id]}:actual={checked_count!r}"
            )
    missing = [batch_id for batch_id in expected if seen[batch_id] == 0]
    if missing:
        errors.append(f"missing_batch_ids:{','.join(missing)}")
    return ("invalid", errors) if errors else ("completed", [])


def plan_segments(
    manifest_paths: list[Path],
    *,
    batches_per_segment: int,
    output_dir: Path,
) -> dict[str, Any]:
    if batches_per_segment < 1:
        raise ValueError("--batches-per-segment must be at least 1")
    segments: list[dict[str, Any]] = []
    lanes: list[str] = []
    global_segment_index = 0
    for manifest_index, manifest_path in enumerate(manifest_paths, start=1):
        lane, batches = load_batches(manifest_path)
        if lane not in lanes:
            lanes.append(lane)
        for offset in range(0, len(batches), batches_per_segment):
            global_segment_index += 1
            segment_batches = batches[offset : offset + batches_per_segment]
            segment_index = offset // batches_per_segment + 1
            stem = f"segment_{global_segment_index:05d}_{lane_slug(lane)}"
            segment_manifest_path = output_dir / f"{stem}.manifest.json"
            result_path = output_dir / f"{stem}.result.jsonl"
            status, errors = segment_result_status(result_path, segment_batches)
            segment_manifest = {
                "schema_version": "national_exam_agent_segment_manifest_v1",
                "lane": lane,
                "manifest_index": manifest_index,
                "segment_index": segment_index,
                "run_manifest_path": str(manifest_path.resolve()),
                "first_batch_id": segment_batches[0]["batch_id"],
                "last_batch_id": segment_batches[-1]["batch_id"],
                "batch_ids": [batch["batch_id"] for batch in segment_batches],
                "task_count": sum(
                    int(batch["task_count"]) for batch in segment_batches
                ),
                "expected_result_path": str(result_path.resolve()),
                "status": status,
                "validation_errors": errors,
            }
            write_json(segment_manifest_path, segment_manifest)
            segments.append(
                {
                    "lane": lane,
                    "manifest_index": manifest_index,
                    "segment_index": segment_index,
                    "first_batch_id": segment_manifest["first_batch_id"],
                    "last_batch_id": segment_manifest["last_batch_id"],
                    "batch_count": len(segment_batches),
                    "task_count": segment_manifest["task_count"],
                    "status": status,
                    "segment_manifest_path": str(segment_manifest_path.resolve()),
                    "expected_result_path": str(result_path.resolve()),
                }
            )
    counts = Counter(str(segment["status"]) for segment in segments)
    next_pending_by_lane: dict[str, dict[str, Any] | None] = {}
    for lane in lanes:
        pending = next(
            (
                segment
                for segment in segments
                if segment["lane"] == lane and segment["status"] == "pending"
            ),
            None,
        )
        next_pending_by_lane[lane] = dict(pending) if pending else None
    plan = {
        "schema_version": "national_exam_agent_segment_plan_v1",
        "batches_per_segment": batches_per_segment,
        "run_manifest_paths": [
            str(manifest_path.resolve()) for manifest_path in manifest_paths
        ],
        "segment_count": len(segments),
        "pending_count": counts["pending"],
        "completed_count": counts["completed"],
        "invalid_count": counts["invalid"],
        "next_pending_by_lane": next_pending_by_lane,
        "segments": segments,
    }
    write_json(output_dir / "plan.json", plan)
    return plan


def main() -> int:
    args = parse_args()
    try:
        plan = plan_segments(
            args.manifest,
            batches_per_segment=args.batches_per_segment,
            output_dir=args.output_dir,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc
    print(
        json.dumps(
            {
                "ok": True,
                "segment_count": plan["segment_count"],
                "pending_count": plan["pending_count"],
                "completed_count": plan["completed_count"],
                "invalid_count": plan["invalid_count"],
                "next_pending_by_lane": plan["next_pending_by_lane"],
                "plan_path": str((args.output_dir / "plan.json").resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
