#!/usr/bin/env python3
"""Promote frozen prior-AI pass rows into an independent LUNA recheck lane.

The prior-pass file intentionally contains no question text.  This script
performs an exact, fingerprint-checked join against the immutable compact task
snapshot and writes only the selected full tasks to a new post-MinerU text
audit lane.  It does not call a model or write production state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, TextIO

from v4_common import compact_json, read_jsonl, write_json


SCHEMA_VERSION = "national_exam_prior_pass_recheck_scope_v1"
LEDGER_SCHEMA_VERSION = "national_exam_prior_pass_recheck_coverage_row_v1"
EXPECTED_TERMINAL_SCHEMA = "national_exam_prior_ai_pass_terminal_v1"
TARGET_LANE = "ocr_text"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--prior-pass", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_fingerprint(row: dict[str, Any]) -> str:
    return str(
        row.get("source_fingerprint")
        or row.get("effective_content_hash")
        or ""
    )


def write_jsonl_row(handle: TextIO, row: dict[str, Any]) -> None:
    handle.write(compact_json(row) + "\n")


def load_prior_pass(path: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    rows_by_key: dict[str, dict[str, Any]] = {}
    ordered_keys: list[str] = []
    for line_no, row in read_jsonl(path):
        key = str(row.get("candidate_key") or "")
        if not key:
            raise ValueError(f"{path}:{line_no}: empty candidate key")
        if key in rows_by_key:
            raise ValueError(f"duplicate prior-pass candidate key: {key}")
        if row.get("schema_version") != EXPECTED_TERMINAL_SCHEMA:
            raise ValueError(
                f"{path}:{line_no}: unexpected terminal schema "
                f"{row.get('schema_version')!r}"
            )
        if row.get("primary_route") != "terminal_prior_codex54_pass":
            raise ValueError(
                f"{path}:{line_no}: row is not terminal_prior_codex54_pass"
            )
        if row.get("not_independent_luna_review") is not True:
            raise ValueError(
                f"{path}:{line_no}: row is not marked as lacking independent "
                "LUNA review"
            )
        if row.get("deterministic_proof") is not False:
            raise ValueError(
                f"{path}:{line_no}: deterministic_proof must be false"
            )
        review = row.get("frozen_human_review")
        if not isinstance(review, dict) or review.get("status") != "unreviewed":
            raise ValueError(
                f"{path}:{line_no}: row is not frozen human-unreviewed"
            )
        fingerprint = source_fingerprint(row)
        if not fingerprint:
            raise ValueError(f"{path}:{line_no}: empty source fingerprint")
        rows_by_key[key] = row
        ordered_keys.append(key)
    if not ordered_keys:
        raise ValueError("prior-pass file is empty")
    return rows_by_key, ordered_keys


def build_scope(
    *,
    tasks_path: Path,
    prior_pass_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    if output_dir.exists():
        raise ValueError(f"output directory must not already exist: {output_dir}")
    if not tasks_path.is_file():
        raise ValueError(f"task file does not exist: {tasks_path}")
    if not prior_pass_path.is_file():
        raise ValueError(f"prior-pass file does not exist: {prior_pass_path}")

    initial_tasks_sha256 = sha256_file(tasks_path)
    initial_prior_pass_sha256 = sha256_file(prior_pass_path)
    prior_by_key, prior_order = load_prior_pass(prior_pass_path)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_dir.name}.staging-",
        dir=output_dir.parent,
    ) as temporary:
        staging_dir = Path(temporary)
        lane_path = staging_dir / "lanes" / f"{TARGET_LANE}.jsonl"
        lane_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path = staging_dir / "coverage_ledger.jsonl"

        selected_by_key: dict[str, dict[str, Any]] = {}
        seen_task_keys: set[str] = set()
        with tasks_path.open("r", encoding="utf-8") as tasks_handle:
            for line_no, raw_line in enumerate(tasks_handle, 1):
                if not raw_line.strip():
                    continue
                try:
                    task = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{tasks_path}:{line_no}: {exc}") from exc
                key = str(task.get("candidate_key") or "")
                if not key:
                    raise ValueError(f"{tasks_path}:{line_no}: empty candidate key")
                if key in seen_task_keys:
                    raise ValueError(f"duplicate task candidate key: {key}")
                seen_task_keys.add(key)
                if key not in prior_by_key:
                    continue
                task_fingerprint = source_fingerprint(task)
                prior_fingerprint = source_fingerprint(prior_by_key[key])
                if task_fingerprint != prior_fingerprint:
                    raise ValueError(
                        f"source fingerprint mismatch for {key}: "
                        f"task={task_fingerprint!r}, prior_pass={prior_fingerprint!r}"
                    )
                selected_by_key[key] = task

        missing_keys = [key for key in prior_order if key not in selected_by_key]
        if missing_keys:
            raise ValueError(
                "prior-pass candidate keys missing from tasks: "
                f"{missing_keys[:10]} (count={len(missing_keys)})"
            )

        with lane_path.open("w", encoding="utf-8") as lane_handle, (
            ledger_path.open("w", encoding="utf-8")
        ) as ledger_handle:
            for key in prior_order:
                task = selected_by_key[key]
                prior = prior_by_key[key]
                write_jsonl_row(lane_handle, task)
                write_jsonl_row(
                    ledger_handle,
                    {
                        "schema_version": LEDGER_SCHEMA_VERSION,
                        "candidate_key": key,
                        "source_fingerprint": source_fingerprint(task),
                        "source_route": "terminal_prior_codex54_pass",
                        "target_lane": TARGET_LANE,
                        "recheck_reason": (
                            "prior_ai_pass_is_not_independent_luna_review"
                        ),
                        "frozen_human_review": prior["frozen_human_review"],
                        "prior_ai_evidence": prior.get("carry_forward_evidence") or {},
                        "independent_luna_review_completed": False,
                    },
                )

        if sha256_file(tasks_path) != initial_tasks_sha256:
            raise ValueError("source task file changed during recheck scope build")
        if sha256_file(prior_pass_path) != initial_prior_pass_sha256:
            raise ValueError("prior-pass file changed during recheck scope build")

        selected_keys = set(selected_by_key)
        prior_keys = set(prior_by_key)
        invariants = {
            "task_keys_nonempty_unique": True,
            "prior_pass_keys_nonempty_unique": True,
            "prior_pass_is_subset_of_tasks": prior_keys.issubset(seen_task_keys),
            "exact_prior_pass_to_lane_join": selected_keys == prior_keys,
            "lane_count_matches_prior_pass_count": (
                len(selected_by_key) == len(prior_order)
            ),
            "source_fingerprints_match": True,
            "source_files_stable_during_build": True,
            "no_prior_ai_output_in_model_lane": all(
                "carry_forward_evidence" not in task
                and "latest_ai_evidence" not in task
                and "residual_routing" not in task
                for task in selected_by_key.values()
            ),
        }
        failed = [name for name, passed in invariants.items() if not passed]
        if failed:
            raise AssertionError(f"recheck scope invariants failed: {failed}")

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "advisory_only": True,
            "model_calls_made": False,
            "production_writes": False,
            "mineru_ocr_rerun": False,
            "source_tasks": str(tasks_path.resolve()),
            "source_tasks_sha256": initial_tasks_sha256,
            "source_prior_pass": str(prior_pass_path.resolve()),
            "source_prior_pass_sha256": initial_prior_pass_sha256,
            "source_task_count": len(seen_task_keys),
            "prior_pass_count": len(prior_order),
            "recheck_task_count": len(selected_by_key),
            "target_lane": TARGET_LANE,
            "completion_semantics": {
                "prior_ai_pass_counts_as_independent_luna_review": False,
                "recheck_rows_are_complete_only_after_validated_sparse_results": True,
            },
            "output_files": {
                "lane": {
                    "path": str(lane_path.relative_to(staging_dir)),
                    "row_count": len(selected_by_key),
                    "sha256": sha256_file(lane_path),
                },
                "coverage_ledger": {
                    "path": str(ledger_path.relative_to(staging_dir)),
                    "row_count": len(selected_by_key),
                    "sha256": sha256_file(ledger_path),
                },
            },
            "invariants": invariants,
        }
        write_json(staging_dir / "manifest.json", manifest)
        staging_dir.replace(output_dir)
    return manifest


def main() -> int:
    args = parse_args()
    manifest = build_scope(
        tasks_path=args.tasks,
        prior_pass_path=args.prior_pass,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "recheck_task_count": manifest["recheck_task_count"],
                "target_lane": manifest["target_lane"],
                "output_dir": str(args.output_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
