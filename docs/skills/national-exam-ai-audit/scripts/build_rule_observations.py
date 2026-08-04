#!/usr/bin/env python3
"""Cluster recurring sparse model corrections into non-promotable observations."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from v4_common import read_json, read_jsonl, write_json, write_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--minimum-occurrences", type=int, default=2)
    parser.add_argument("--maximum-examples", type=int, default=12)
    parser.add_argument("--proposed-by", default="retrospective:model-observation")
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bounded_scope(values: set[str], maximum: int = 8) -> list[str]:
    cleaned = sorted(value for value in values if value)
    return cleaned if len(cleaned) <= maximum else []


def main() -> int:
    args = parse_args()
    if args.minimum_occurrences < 2:
        raise SystemExit("--minimum-occurrences must be at least 2")
    if args.maximum_examples < 1:
        raise SystemExit("--maximum-examples must be at least 1")

    manifest_path = args.run_dir / "manifest.json"
    results_path = args.run_dir / "results.jsonl"
    validation_path = args.run_dir / "validation.json"
    validation = read_json(validation_path)
    if validation.get("ok") is not True:
        raise SystemExit(f"run validation failed: {validation_path}")

    manifest = read_json(manifest_path)
    task_by_key: dict[str, dict[str, Any]] = {}
    for batch in manifest.get("batches") or []:
        packet = read_json(args.run_dir / str(batch["packet_path"]))
        for task in packet.get("tasks") or []:
            key = str(task.get("candidate_key") or "")
            if not key or key in task_by_key:
                raise SystemExit(f"invalid or duplicate candidate key: {key!r}")
            task_by_key[key] = task

    clusters: dict[
        tuple[str, str, str],
        list[dict[str, Any]],
    ] = defaultdict(list)
    eligible_issue_count = 0
    for _, row in read_jsonl(results_path):
        for issue in row.get("issues") or []:
            before = issue.get("before")
            after = issue.get("after")
            if (
                issue.get("route") != "propose_rule"
                or not isinstance(before, str)
                or not before
                or not isinstance(after, str)
                or not after
                or before == after
            ):
                continue
            key = str(issue.get("candidate_key") or "")
            if key not in task_by_key:
                raise SystemExit(f"issue candidate not found in packets: {key}")
            eligible_issue_count += 1
            clusters[
                (str(issue.get("issue_family") or ""), before, after)
            ].append(issue)

    result_hash = sha256_file(results_path)
    proposals: list[dict[str, Any]] = []
    repeated_issue_count = 0
    for (family, before, after), issues in sorted(
        clusters.items(),
        key=lambda item: (-len(item[1]), item[0]),
    ):
        unique_by_candidate = {
            str(issue["candidate_key"]): issue
            for issue in issues
        }
        if len(unique_by_candidate) < args.minimum_occurrences:
            continue
        repeated_issue_count += len(unique_by_candidate)
        candidate_keys = sorted(unique_by_candidate)
        tasks = [task_by_key[key] for key in candidate_keys]
        categories = {
            str((task.get("exam") or {}).get("category") or "")
            for task in tasks
        }
        subjects = {
            str((task.get("exam") or {}).get("subject") or "")
            for task in tasks
        }
        fields = {
            str(issue.get("field") or "")
            for issue in unique_by_candidate.values()
            if str(issue.get("field") or "")
        }
        pair_hash = hashlib.sha256(
            json.dumps(
                [family, before, after],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        proposals.append(
            {
                "rule_id": f"observed-exact-{pair_hash}",
                "rule_type": "exact_replacement",
                "lane": "ocr_text",
                "status": "observed",
                "source": before,
                "target": after,
                "scope": {
                    "categories": bounded_scope(categories),
                    "subjects": bounded_scope(subjects),
                    "required_context": sorted(
                        {f"field:{field}" for field in fields}
                    ),
                },
                "evidence": [
                    {
                        "kind": "model_observation",
                        "reference": (
                            f"{results_path.resolve()}#sha256={result_hash}"
                            f";occurrences={len(unique_by_candidate)}"
                        ),
                    }
                ],
                "positive_examples": candidate_keys[: args.maximum_examples],
                "negative_examples": [],
                "proposed_by": args.proposed_by,
                "version": 1,
                "note": (
                    f"Recurring {family} suggestion clustered from validated sparse "
                    "output. Model-only observation: source verification and negative "
                    "controls are still required before proposal or activation."
                ),
            }
        )

    write_jsonl(args.output, proposals)
    report = {
        "ok": True,
        "schema_version": "national_exam_rule_observation_report_v1",
        "source_manifest": str(manifest_path.resolve()),
        "source_results": str(results_path.resolve()),
        "source_results_sha256": result_hash,
        "minimum_occurrences": args.minimum_occurrences,
        "eligible_issue_count": eligible_issue_count,
        "unique_exact_pair_count": len(clusters),
        "observation_count": len(proposals),
        "issues_in_observations": repeated_issue_count,
        "rules_promoted": 0,
        "rules_applied": 0,
        "output": str(args.output.resolve()),
    }
    report_path = args.report or args.output.with_suffix(".report.json")
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
