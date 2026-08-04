#!/usr/bin/env python3
"""Assemble resumable sparse result segments in manifest order."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from v4_common import read_json, read_jsonl, write_json, write_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--segment-dir", type=Path, required=True)
    parser.add_argument(
        "--glob",
        action="append",
        default=[],
        help="Segment filename pattern. Repeat for compatibility names.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--allow-partial", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = read_json(args.manifest)
    batches = manifest.get("batches") or []
    expected = {
        str(batch["batch_id"]): int(batch["task_count"])
        for batch in batches
    }
    rows_by_id: dict[str, dict[str, Any]] = {}
    sources: dict[str, str] = {}
    errors: list[str] = []
    patterns = args.glob or ["*.jsonl"]
    paths = sorted(
        {
            path
            for pattern in patterns
            for path in args.segment_dir.glob(pattern)
        }
    )
    for path in paths:
        for line_no, row in read_jsonl(path):
            batch_id = str(row.get("batch_id") or "")
            if batch_id not in expected:
                errors.append(f"{path}:{line_no}: unexpected_batch_id:{batch_id}")
                continue
            if batch_id in rows_by_id:
                errors.append(
                    f"{path}:{line_no}: duplicate_batch_id:{batch_id}:"
                    f"first={sources[batch_id]}"
                )
                continue
            if int(row.get("checked_count") or -1) != expected[batch_id]:
                errors.append(
                    f"{path}:{line_no}: checked_count_mismatch:{batch_id}:"
                    f"expected={expected[batch_id]}:actual={row.get('checked_count')}"
                )
                continue
            rows_by_id[batch_id] = row
            sources[batch_id] = f"{path}:{line_no}"
    missing = [
        str(batch["batch_id"])
        for batch in batches
        if str(batch["batch_id"]) not in rows_by_id
    ]
    if missing and not args.allow_partial:
        errors.append(f"missing_batches:{len(missing)}")
    ordered = [
        rows_by_id[str(batch["batch_id"])]
        for batch in batches
        if str(batch["batch_id"]) in rows_by_id
    ]
    if not errors:
        write_jsonl(args.output, ordered)
    report = {
        "ok": not errors,
        "partial": bool(missing),
        "expected_batch_count": len(batches),
        "assembled_batch_count": len(ordered),
        "checked_count": sum(int(row["checked_count"]) for row in ordered),
        "issue_count": sum(len(row.get("issues") or []) for row in ordered),
        "missing_batch_count": len(missing),
        "missing_batch_ids_preview": missing[:20],
        "missing_batch_first": missing[0] if missing else None,
        "missing_batch_last": missing[-1] if missing else None,
        "segment_files": [str(path.resolve()) for path in paths],
        "errors": errors,
        "output": str(args.output.resolve()),
    }
    write_json(args.report or args.output.with_suffix(".assembly.json"), report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
