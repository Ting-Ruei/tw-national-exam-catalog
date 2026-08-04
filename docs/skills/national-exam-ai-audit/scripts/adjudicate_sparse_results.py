#!/usr/bin/env python3
"""Conservatively adjudicate isolated repeated sparse audits for one segment."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from v4_common import PROMPT_VERSION, read_json, read_jsonl, write_json, write_jsonl


ROUTE_PRIORITY = {
    "human_pdf": 0,
    "parser": 1,
    "group": 2,
    "visual": 3,
    "human_text": 4,
    "propose_rule": 5,
    "deterministic": 6,
    "none": 7,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--full-manifest",
        type=Path,
        help="Full lane manifest when --manifest is a segment batch-id manifest.",
    )
    parser.add_argument("--results", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def issue_identity(issue: dict[str, Any]) -> tuple[Any, ...]:
    return (
        issue.get("candidate_key"),
        issue.get("field"),
        issue.get("before"),
        issue.get("after"),
        issue.get("issue_family"),
        issue.get("source_class"),
        issue.get("route"),
    )


def replacement_identity(issue: dict[str, Any]) -> tuple[Any, ...]:
    return (issue.get("candidate_key"), issue.get("before"), issue.get("after"))


def main() -> int:
    args = parse_args()
    if len(args.results) < 2:
        raise SystemExit("at least two isolated result files are required")
    manifest = read_json(args.manifest)
    batches = manifest.get("batches") or []
    if not batches and args.full_manifest:
        full_manifest = read_json(args.full_manifest)
        wanted = {
            str(value)
            for value in manifest.get("batch_ids") or []
        }
        batches = [
            batch
            for batch in full_manifest.get("batches") or []
            if str(batch.get("batch_id") or "") in wanted
        ]
        if [str(batch.get("batch_id") or "") for batch in batches] != list(
            manifest.get("batch_ids") or []
        ):
            raise SystemExit("segment manifest batch_ids do not match full manifest order")
        manifest = full_manifest
    batch_ids = [str(batch.get("batch_id") or "") for batch in batches]
    if not batch_ids or len(batch_ids) != len(set(batch_ids)):
        raise SystemExit("manifest must contain unique batches")

    source_rows: list[list[dict[str, Any]]] = []
    for path in args.results:
        rows = [row for _, row in read_jsonl(path)]
        if [str(row.get("batch_id") or "") for row in rows] != batch_ids:
            raise SystemExit(f"result batch order does not match manifest: {path}")
        source_rows.append(rows)

    by_candidate: dict[str, dict[tuple[Any, ...], list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for rows in source_rows:
        seen_in_source: set[tuple[Any, ...]] = set()
        for row in rows:
            for issue in row.get("issues") or []:
                if not isinstance(issue, dict):
                    raise SystemExit("source issue must be an object")
                identity = issue_identity(issue)
                if identity in seen_in_source:
                    raise SystemExit("duplicate issue in one source result")
                seen_in_source.add(identity)
                key = str(issue.get("candidate_key") or "")
                by_candidate[key][identity].append(issue)

    # A union is intentionally used for advisory safety: disagreement is not
    # silently converted into a pass. The three-issue cap is the same cap the
    # sparse contract uses; the highest-priority findings survive the cap.
    output_rows: list[dict[str, Any]] = []
    disagreement_candidates = 0
    consensus_issue_count = 0
    union_issue_count = 0
    for row_index, batch_id in enumerate(batch_ids):
        candidate_issues: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for source in source_rows:
            for issue in source[row_index].get("issues") or []:
                candidate_issues[str(issue.get("candidate_key") or "")].append(issue)
        selected: list[dict[str, Any]] = []
        for candidate_key, issues in sorted(candidate_issues.items()):
            by_identity: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
            for issue in issues:
                by_identity[issue_identity(issue)].append(issue)
            if len(by_identity) > 1:
                disagreement_candidates += 1
            union_issue_count += len(by_identity)
            ranked = sorted(
                by_identity.items(),
                key=lambda item: (
                    -len(item[1]),
                    ROUTE_PRIORITY.get(str(item[1][0].get("route") or ""), 99),
                    str(item[0]),
                ),
            )
            selected_replacements: set[tuple[Any, ...]] = set()
            for identity, candidates in ranked[:3]:
                issue = dict(candidates[0])
                replacement = replacement_identity(issue)
                if replacement in selected_replacements:
                    continue
                selected_replacements.add(replacement)
                count = len(candidates)
                if count >= 2:
                    consensus_issue_count += 1
                note = str(issue.get("note") or "")
                suffix = f" adjudicated_sources={count}/{len(source_rows)}."
                issue["note"] = (note[: 160 - len(suffix)] + suffix).strip()
                selected.append(issue)
        output_rows.append(
            {
                "batch_id": batch_id,
                "checked_count": int(batches[row_index].get("task_count") or 0),
                "issues": selected,
                "model": manifest.get("model"),
                "prompt_version": PROMPT_VERSION,
            }
        )

    write_jsonl(args.output, output_rows)
    report = {
        "ok": True,
        "schema_version": "national_exam_sparse_adjudication_report_v1",
        "manifest": str(args.manifest.resolve()),
        "source_results": [
            {"path": str(path.resolve()), "sha256": sha256_file(path)}
            for path in args.results
        ],
        "source_count": len(args.results),
        "batch_count": len(batch_ids),
        "disagreement_candidate_count": disagreement_candidates,
        "consensus_issue_count": consensus_issue_count,
        "union_issue_count_before_cap": union_issue_count,
        "output": str(args.output.resolve()),
        "advisory_only": True,
    }
    write_json(args.report or args.output.with_suffix(".report.json"), report)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
