#!/usr/bin/env python3
"""Prove that frozen audit lanes cover the complete candidate scope exactly once.

The proof is intentionally set-based.  It does not infer correctness from an
AI result; it only verifies that each frozen candidate key is represented by
exactly one of the completed independent LUNA run, the earlier LUNA run, or
the parser queue that remains for human/parser handling.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from v4_common import read_json, read_jsonl, write_json


SCHEMA_VERSION = "national_exam_coverage_proof_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", type=Path, required=True, help="Frozen compact task JSONL")
    parser.add_argument("--new-run-dir", type=Path, required=True, help="Validated LUNA run")
    parser.add_argument("--prior-advisories", type=Path, required=True)
    parser.add_argument("--parser-queue", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def jsonl_keys(path: Path) -> tuple[list[str], set[str]]:
    rows: list[str] = []
    for line_no, row in read_jsonl(path):
        key = str(row.get("candidate_key") or "")
        if not key:
            raise ValueError(f"{path}:{line_no}: missing candidate_key")
        rows.append(key)
    return rows, set(rows)


def checkpoint_keys(run_dir: Path) -> tuple[list[str], set[str], dict[str, Any]]:
    manifest_path = run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    rows: list[str] = []
    for batch in manifest.get("batches") or []:
        if not isinstance(batch, dict):
            raise ValueError(f"{manifest_path}: batch must be an object")
        keys = batch.get("candidate_keys") or []
        if not isinstance(keys, list):
            raise ValueError(f"{manifest_path}: candidate_keys must be a list")
        rows.extend(str(key) for key in keys)
    if any(not key for key in rows):
        raise ValueError(f"{manifest_path}: empty candidate key")
    return rows, set(rows), manifest


def source_summary(path: Path, rows: list[str], unique: set[str]) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "row_count": len(rows),
        "unique_candidate_count": len(unique),
        "duplicate_row_count": len(rows) - len(unique),
    }


def main() -> int:
    args = parse_args()
    scope_rows, scope = jsonl_keys(args.scope)
    new_rows, new_keys, new_manifest = checkpoint_keys(args.new_run_dir)
    prior_rows, prior = jsonl_keys(args.prior_advisories)
    parser_rows, parser = jsonl_keys(args.parser_queue)

    lanes = {
        "new_independent_luna": new_keys,
        "prior_luna": prior,
        "parser_queue": parser,
    }
    overlap_counts = {
        f"{left}__{right}": len(lanes[left] & lanes[right])
        for left, right in (
            ("new_independent_luna", "prior_luna"),
            ("new_independent_luna", "parser_queue"),
            ("prior_luna", "parser_queue"),
        )
    }
    union = set().union(*lanes.values())
    missing = sorted(scope - union)
    extra = sorted(union - scope)
    completion = new_manifest.get("partial_completion") or {}
    report = {
        "schema_version": SCHEMA_VERSION,
        "advisory_only": True,
        "ok": (
            len(scope) == len(scope_rows)
            and not any(summary["duplicate_row_count"] for summary in (
                source_summary(args.scope, scope_rows, scope),
                source_summary(args.prior_advisories, prior_rows, prior),
                source_summary(args.parser_queue, parser_rows, parser),
            ))
            and len(new_keys) == len(new_rows)
            and not missing
            and not extra
            and not any(overlap_counts.values())
            and len(union) == len(scope)
        ),
        "scope": source_summary(args.scope, scope_rows, scope),
        "lanes": {
            "new_independent_luna": {
                **source_summary(args.new_run_dir / "manifest.json", new_rows, new_keys),
                "run_dir": str(args.new_run_dir.resolve()),
                "validated": read_json(args.new_run_dir / "validation.json"),
            },
            "prior_luna": source_summary(args.prior_advisories, prior_rows, prior),
            "parser_queue": source_summary(args.parser_queue, parser_rows, parser),
        },
        "expected_counts": {
            "scope": len(scope),
            "new_independent_luna": len(new_keys),
            "prior_luna": len(prior),
            "parser_queue": len(parser),
            "union": len(union),
        },
        "manifest_counts": {
            "new_run_task_count": int(
                completion.get("completed_task_count") or new_manifest.get("task_count") or 0
            ),
            "new_run_batch_count": int(completion.get("completed_batch_count") or 0),
            "new_run_missing_batch_count": int(completion.get("missing_batch_count") or 0),
        },
        "overlap_counts": overlap_counts,
        "missing_candidate_count": len(missing),
        "missing_candidate_keys_sample": missing[:20],
        "extra_candidate_count": len(extra),
        "extra_candidate_keys_sample": extra[:20],
    }
    write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
