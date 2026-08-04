#!/usr/bin/env python3
"""Freeze fully validated segment results into an immutable partial run."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from v4_common import read_json, read_jsonl, write_json, write_jsonl


SEGMENT_RESULT_RE = re.compile(
    r"^segment_(?P<index>\d{5})_(?P<lane>[a-z_]+)"
    r"(?:\.(?P<suffix>[a-z0-9_]+))?\.result\.jsonl$"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--segment-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--exclude-segment",
        type=int,
        action="append",
        default=[],
        help="Segment index to leave pending despite a valid result.",
    )
    parser.add_argument(
        "--include-result",
        type=Path,
        action="append",
        default=[],
        help="Explicit validated result to include even when its segment is excluded.",
    )
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rebase_batch_paths(batch: dict[str, Any], source_run_dir: Path) -> dict[str, Any]:
    """Keep packet/request references valid after moving the partial manifest."""
    rebased = dict(batch)
    for field in ("packet_path", "request_path"):
        raw_path = rebased.get(field)
        if not isinstance(raw_path, str) or not raw_path:
            continue
        path = Path(raw_path)
        if not path.is_absolute():
            path = source_run_dir / path
        rebased[field] = str(path.resolve())
    return rebased


def validation_reports(
    segment_dir: Path,
    *,
    segment_index: int,
    lane: str,
) -> list[tuple[Path, dict[str, Any]]]:
    prefix = f"segment_{segment_index:05d}_{lane}"
    reports: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(segment_dir.glob(f"{prefix}*.json")):
        if path.name.endswith(".manifest.json"):
            continue
        try:
            payload = read_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if (
            isinstance(payload, dict)
            and payload.get("ok") is True
            and int(payload.get("missing_batch_count") or 0) == 0
            and not payload.get("errors")
        ):
            reports.append((path, payload))
    return reports


def build_partial_run(
    *,
    manifest_path: Path,
    segment_dir: Path,
    output_dir: Path,
    excluded_segments: set[int],
    included_results: list[Path] | None = None,
) -> dict[str, Any]:
    if output_dir.exists():
        raise ValueError(f"output directory must not already exist: {output_dir}")
    full_manifest_sha256 = sha256_file(manifest_path)
    full_manifest = read_json(manifest_path)
    full_batches = full_manifest.get("batches") or []
    batch_by_id = {
        str(batch["batch_id"]): batch
        for batch in full_batches
    }
    if len(batch_by_id) != len(full_batches):
        raise ValueError("full manifest contains duplicate batch ids")

    accepted_rows: dict[str, dict[str, Any]] = {}
    accepted_segments: list[dict[str, Any]] = []
    rejected_segments: list[dict[str, Any]] = []
    explicit_results = {
        path.resolve()
        for path in (included_results or [])
    }
    result_paths = {
        path.resolve()
        for path in segment_dir.glob("segment_*_*.result.jsonl")
    }
    result_paths.update(explicit_results)
    for result_path in sorted(result_paths):
        match = SEGMENT_RESULT_RE.match(result_path.name)
        if not match:
            continue
        segment_index = int(match.group("index"))
        lane = match.group("lane")
        if segment_index in excluded_segments and result_path.resolve() not in explicit_results:
            rejected_segments.append(
                {
                    "segment_index": segment_index,
                    "reason": "explicitly_excluded",
                    "result_path": str(result_path.resolve()),
                }
            )
            continue
        segment_manifest_path = (
            segment_dir / f"segment_{segment_index:05d}_{lane}.manifest.json"
        )
        if not segment_manifest_path.is_file():
            rejected_segments.append(
                {
                    "segment_index": segment_index,
                    "reason": "missing_segment_manifest",
                    "result_path": str(result_path.resolve()),
                }
            )
            continue
        segment_manifest = read_json(segment_manifest_path)
        expected_ids = [str(value) for value in segment_manifest.get("batch_ids") or []]
        rows = [row for _, row in read_jsonl(result_path)]
        result_ids = [str(row.get("batch_id") or "") for row in rows]
        reports = validation_reports(
            segment_dir,
            segment_index=segment_index,
            lane=lane,
        )
        matching_reports = [
            (path, report)
            for path, report in reports
            if int(report.get("expected_batch_count", -1)) == len(expected_ids)
            and int(report.get("result_batch_count", -1)) == len(rows)
            and int(report.get("issue_count", -1))
            == sum(len(row.get("issues") or []) for row in rows)
        ]
        reasons: list[str] = []
        if not expected_ids or len(expected_ids) != len(set(expected_ids)):
            reasons.append("invalid_expected_batch_ids")
        if result_ids != expected_ids:
            reasons.append("result_batch_ids_do_not_match_segment_order")
        if any(batch_id not in batch_by_id for batch_id in result_ids):
            reasons.append("unexpected_batch_id")
        if any(
            int(row.get("checked_count") or -1)
            != int(batch_by_id[str(row.get("batch_id"))]["task_count"])
            for row in rows
            if str(row.get("batch_id") or "") in batch_by_id
        ):
            reasons.append("checked_count_mismatch")
        if not matching_reports:
            reasons.append("no_matching_successful_validation_report")
        if any(batch_id in accepted_rows for batch_id in result_ids):
            reasons.append("duplicate_batch_across_segments")
        if reasons:
            rejected_segments.append(
                {
                    "segment_index": segment_index,
                    "reason": ",".join(reasons),
                    "result_path": str(result_path.resolve()),
                }
            )
            continue
        for row in rows:
            accepted_rows[str(row["batch_id"])] = row
        accepted_segments.append(
            {
                "segment_index": segment_index,
                "lane": lane,
                "batch_count": len(rows),
                "checked_count": sum(int(row["checked_count"]) for row in rows),
                "issue_count": sum(len(row.get("issues") or []) for row in rows),
                "result_path": str(result_path.resolve()),
                "result_sha256": sha256_file(result_path),
                "validation_path": str(matching_reports[0][0].resolve()),
                "validation_sha256": sha256_file(matching_reports[0][0]),
            }
        )

    selected_batches = [
        rebase_batch_paths(batch, manifest_path.parent)
        for batch in full_batches
        if str(batch["batch_id"]) in accepted_rows
    ]
    selected_rows = [
        accepted_rows[str(batch["batch_id"])]
        for batch in selected_batches
    ]
    if not selected_rows:
        raise ValueError("no fully validated segments were found")
    selected_batch_ids = {str(batch["batch_id"]) for batch in selected_batches}
    missing_batch_ids = [
        str(batch["batch_id"])
        for batch in full_batches
        if str(batch["batch_id"]) not in selected_batch_ids
    ]

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_dir.name}.staging-",
        dir=output_dir.parent,
    ) as temporary:
        staging_dir = Path(temporary)
        partial_manifest = {
            **full_manifest,
            "schema_version": "national_exam_sparse_partial_run_manifest_v1",
            "partial": True,
            "partial_of_manifest": str(manifest_path.resolve()),
            "partial_of_manifest_sha256": full_manifest_sha256,
            "batches": selected_batches,
            "task_count": sum(int(batch["task_count"]) for batch in selected_batches),
            "partial_completion": {
                "full_batch_count": len(full_batches),
                "completed_batch_count": len(selected_batches),
                "missing_batch_count": len(missing_batch_ids),
                "full_task_count": int(full_manifest.get("task_count") or 0),
                "completed_task_count": sum(
                    int(batch["task_count"]) for batch in selected_batches
                ),
                "accepted_segment_count": len(accepted_segments),
                "explicitly_excluded_segments": sorted(excluded_segments),
            },
        }
        write_json(staging_dir / "manifest.json", partial_manifest)
        write_jsonl(staging_dir / "results.jsonl", selected_rows)
        report = {
            "ok": True,
            "full_manifest": str(manifest_path.resolve()),
            "full_manifest_sha256": full_manifest_sha256,
            "accepted_segment_count": len(accepted_segments),
            "accepted_segments": accepted_segments,
            "rejected_segment_count": len(rejected_segments),
            "rejected_segments": rejected_segments,
            "completed_batch_count": len(selected_batches),
            "completed_task_count": partial_manifest["task_count"],
            "issue_count": sum(len(row.get("issues") or []) for row in selected_rows),
            "missing_batch_count": len(missing_batch_ids),
            "missing_batch_ids_preview": missing_batch_ids[:20],
            "results_sha256": sha256_file(staging_dir / "results.jsonl"),
        }
        write_json(staging_dir / "partial_run.report.json", report)
        staging_dir.replace(output_dir)
    return report


def main() -> int:
    args = parse_args()
    report = build_partial_run(
        manifest_path=args.manifest,
        segment_dir=args.segment_dir,
        output_dir=args.output_dir,
        excluded_segments=set(args.exclude_segment),
        included_results=args.include_result,
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
