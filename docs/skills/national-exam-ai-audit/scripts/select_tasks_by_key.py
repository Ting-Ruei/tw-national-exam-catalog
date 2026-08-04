#!/usr/bin/env python3
"""Extract an exact, ordered set of candidate keys from one or more task JSONL files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from v4_common import read_jsonl, sha256_json, write_json, write_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, action="append", required=True)
    parser.add_argument("--candidate-key", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for path in args.tasks:
        for _, row in read_jsonl(path):
            key = str(row.get("candidate_key") or "")
            if key in rows:
                duplicates.add(key)
            rows[key] = row
    if duplicates:
        raise SystemExit(f"duplicate candidate keys across inputs: {sorted(duplicates)}")
    requested = list(args.candidate_key)
    if len(requested) != len(set(requested)):
        raise SystemExit("requested candidate keys must be unique")
    missing = [key for key in requested if key not in rows]
    if missing:
        raise SystemExit(f"candidate keys are missing: {missing}")
    selected = [rows[key] for key in requested]
    write_jsonl(args.output, selected)
    manifest_path = args.manifest or args.output.with_suffix(".manifest.json")
    report = {
        "schema_version": "national_exam_exact_task_selection_v1",
        "candidate_keys": requested,
        "task_count": len(selected),
        "task_hash": sha256_json(selected),
        "source_tasks": [str(path.resolve()) for path in args.tasks],
        "output": str(args.output.resolve()),
    }
    write_json(manifest_path, report)
    print(json.dumps({"ok": True, **report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
