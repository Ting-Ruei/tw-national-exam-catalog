#!/usr/bin/env python3
"""Select a reproducible multi-paper shadow pilot and assign one lane per task."""

from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

from v4_common import (
    LANE_DISPLAY_NAMES,
    SKILL_ROOT,
    read_json,
    read_jsonl,
    sha256_json,
    task_text,
    write_json,
    write_jsonl,
)


GROUP_RE = re.compile(r"承上題|呈上題|回答(?:下列|以下).{0,8}題|請依序回答下列")
VISUAL_RE = re.compile(r"如下圖|如圖所示|根據所附影像|依下表|附圖|圖中|圖示")
PARSER_CONTROL_RE = re.compile(
    r"boundary|option|non_question|missing_question|header|merge|split",
    re.IGNORECASE,
)
SEMANTIC_ISSUE_RE = re.compile(
    r"translation|semantic|terminology|amino_acid",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, action="append", required=True)
    parser.add_argument(
        "--exclude-keys-from",
        type=Path,
        action="append",
        default=[],
        help="JSONL task/result files whose candidate_key values must not be routed again.",
    )
    parser.add_argument("--required-key", action="append", default=[])
    parser.add_argument("--per-paper", type=int, default=10)
    parser.add_argument(
        "--all-tasks",
        action="store_true",
        help="Route every input task instead of selecting a per-paper pilot sample.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def parser_issue_codes(task: dict[str, Any]) -> list[str]:
    return [
        str(issue.get("code") or issue.get("issue_code") or "")
        for issue in ((task.get("signals") or {}).get("parser_issues") or [])
        if isinstance(issue, dict)
    ]


def has_visual_asset(task: dict[str, Any]) -> bool:
    content = task.get("content") or {}
    if content.get("image_refs") or content.get("stem_image"):
        return True
    return any(
        isinstance(option, dict) and option.get("image")
        for option in content.get("options") or []
    )


def has_group_signal(task: dict[str, Any]) -> bool:
    content = task.get("content") or {}
    return bool(content.get("group_ref") or GROUP_RE.search(task_text(task)))


def has_visual_signal(task: dict[str, Any]) -> bool:
    return has_visual_asset(task) or bool(VISUAL_RE.search(task_text(task)))


def load_semantic_terms() -> set[str]:
    payload = read_json(SKILL_ROOT / "rules" / "semantic-anchors.json")
    terms: set[str] = set()
    for row in payload.get("anchors") or []:
        if not isinstance(row, dict):
            continue
        terms.add(str(row.get("anchor") or ""))
        terms.update(str(value) for value in row.get("suspect_terms") or [])
    return {term for term in terms if term and term != "chemical_formula"}


def load_negative_keys() -> set[str]:
    payload = read_json(SKILL_ROOT / "rules" / "negative-controls.json")
    return {
        str(row.get("candidate_key") or "")
        for row in payload.get("controls") or []
        if isinstance(row, dict) and row.get("candidate_key")
    }


def classify_task(
    task: dict[str, Any],
    *,
    semantic_terms: set[str],
    negative_keys: set[str],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    codes = parser_issue_codes(task)
    text = task_text(task)
    key = str(task.get("candidate_key") or "")
    if has_group_signal(task):
        return "group", ["group_signal"]
    if has_visual_signal(task):
        return "visual", ["visual_signal"]
    if any(PARSER_CONTROL_RE.search(code) for code in codes):
        return "semantic_transcription", ["parser_route_control"]
    if key in negative_keys:
        reasons.append("negative_control")
    if any(SEMANTIC_ISSUE_RE.search(code) for code in codes):
        reasons.append("semantic_parser_signal")
    if any(term.casefold() in text.casefold() for term in semantic_terms):
        reasons.append("semantic_anchor")
    if reasons:
        return "semantic_transcription", reasons
    return "ocr_text", ["clean_or_residual_text_control"]


def question_sort_key(task: dict[str, Any]) -> tuple[int, str]:
    value = str((task.get("exam") or {}).get("question_number") or "")
    return (int(value), value) if value.isdigit() else (10**9, value)


def evenly_spaced(rows: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if count <= 0 or not rows:
        return []
    if count >= len(rows):
        return list(rows)
    positions = [
        min(len(rows) - 1, round((index + 1) * (len(rows) - 1) / (count + 1)))
        for index in range(count)
    ]
    selected: list[dict[str, Any]] = []
    used: set[int] = set()
    for position in positions:
        if position in used:
            for candidate in range(len(rows)):
                if candidate not in used:
                    position = candidate
                    break
        used.add(position)
        selected.append(rows[position])
    return selected


def first_matching(
    rows: list[dict[str, Any]],
    predicate: Callable[[dict[str, Any]], bool],
    selected_keys: set[str],
) -> dict[str, Any] | None:
    return next(
        (
            row
            for row in rows
            if str(row.get("candidate_key") or "") not in selected_keys and predicate(row)
        ),
        None,
    )


def main() -> int:
    args = parse_args()
    if args.per_paper < 1:
        raise SystemExit("--per-paper must be positive")

    all_rows: list[dict[str, Any]] = []
    source_hashes: dict[str, str] = {}
    for path in args.tasks:
        rows = [row for _, row in read_jsonl(path)]
        all_rows.extend(rows)
        source_hashes[str(path.resolve())] = sha256_json(rows)
    excluded_keys: set[str] = set()
    exclude_hashes: dict[str, str] = {}
    for path in args.exclude_keys_from:
        rows = [row for _, row in read_jsonl(path)]
        keys = {
            str(row.get("candidate_key") or "")
            for row in rows
            if str(row.get("candidate_key") or "")
        }
        excluded_keys.update(keys)
        exclude_hashes[str(path.resolve())] = sha256_json(sorted(keys))
    keys = [str(row.get("candidate_key") or "") for row in all_rows]
    if not all(keys) or len(keys) != len(set(keys)):
        raise SystemExit("task candidate keys must be non-empty and unique")
    missing_excluded = sorted(excluded_keys - set(keys))
    if missing_excluded:
        raise SystemExit(f"excluded keys are missing from input tasks: {missing_excluded[:10]}")
    all_rows = [
        row
        for row in all_rows
        if str(row.get("candidate_key") or "") not in excluded_keys
    ]

    required = set(args.required_key)
    missing_required = sorted(required - set(keys))
    if missing_required:
        raise SystemExit(f"required keys are missing: {missing_required}")

    papers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in all_rows:
        paper = str(row.get("source_registry_key") or "")
        if not paper:
            raise SystemExit(f"task has no source_registry_key: {row.get('candidate_key')}")
        papers[paper].append(row)
    if not args.all_tasks and len(papers) < 3:
        raise SystemExit("shadow pilot requires at least three source papers")

    semantic_terms = load_semantic_terms()
    negative_keys = load_negative_keys()
    selections: list[dict[str, Any]] = []
    selection_meta: dict[str, dict[str, Any]] = {}

    if args.all_tasks:
        for paper, paper_rows in sorted(papers.items()):
            for row in sorted(paper_rows, key=question_sort_key):
                key = str(row["candidate_key"])
                lane, lane_reasons = classify_task(
                    row,
                    semantic_terms=semantic_terms,
                    negative_keys=negative_keys,
                )
                selections.append(row)
                selection_meta[key] = {
                    "paper": paper,
                    "lane": lane,
                    "selection_reasons": ["full_backlog"],
                    "lane_reasons": lane_reasons,
                }
    else:
        for paper, paper_rows in sorted(papers.items()):
            rows = sorted(paper_rows, key=question_sort_key)
            if len(rows) < args.per_paper:
                raise SystemExit(
                    f"paper {paper} has {len(rows)} tasks, fewer than --per-paper {args.per_paper}"
                )
            selected: list[dict[str, Any]] = []
            selected_keys: set[str] = set()

            def add(row: dict[str, Any] | None, reason: str) -> None:
                if row is None:
                    return
                key = str(row["candidate_key"])
                if key in selected_keys:
                    selection_meta[key]["selection_reasons"].append(reason)
                    return
                selected.append(row)
                selected_keys.add(key)
                lane, lane_reasons = classify_task(
                    row,
                    semantic_terms=semantic_terms,
                    negative_keys=negative_keys,
                )
                selection_meta[key] = {
                    "paper": paper,
                    "lane": lane,
                    "selection_reasons": [reason],
                    "lane_reasons": lane_reasons,
                }

            for row in rows:
                if str(row["candidate_key"]) in required:
                    add(row, "required_control")

            priority_buckets: list[tuple[str, Callable[[dict[str, Any]], bool]]] = [
                ("parser_signal", lambda row: bool(parser_issue_codes(row))),
                ("group_signal", has_group_signal),
                ("visual_signal", has_visual_signal),
                (
                    "semantic_signal",
                    lambda row: classify_task(
                        row,
                        semantic_terms=semantic_terms,
                        negative_keys=negative_keys,
                    )[0]
                    == "semantic_transcription",
                ),
            ]
            for reason, predicate in priority_buckets:
                if len(selected) >= args.per_paper:
                    break
                add(first_matching(rows, predicate, selected_keys), reason)

            remaining = [
                row for row in rows if str(row["candidate_key"]) not in selected_keys
            ]
            for row in evenly_spaced(remaining, args.per_paper - len(selected)):
                add(row, "stratified_spread")
            if len(selected) != args.per_paper:
                raise SystemExit(f"failed to select {args.per_paper} tasks from {paper}")
            selections.extend(sorted(selected, key=question_sort_key))

    for task in selections:
        task["source_fingerprint"] = str(
            task.get("source_fingerprint")
            or task.get("effective_content_hash")
            or sha256_json(task.get("content") or {})
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output_dir / "pilot_tasks.jsonl", selections)
    lane_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in selections:
        lane_rows[selection_meta[str(task["candidate_key"])]["lane"]].append(task)
    lane_files: dict[str, str] = {}
    for lane, rows in sorted(lane_rows.items()):
        path = args.output_dir / "lanes" / f"{lane}.jsonl"
        write_jsonl(path, rows)
        lane_files[lane] = str(path.relative_to(args.output_dir))

    lane_counts = Counter(
        selection_meta[str(task["candidate_key"])]["lane"] for task in selections
    )
    paper_rows = []
    for paper in sorted(papers):
        selected_for_paper = [
            task for task in selections if str(task["source_registry_key"]) == paper
        ]
        first = selected_for_paper[0]
        paper_rows.append(
            {
                "paper_key": paper,
                "category": (first.get("exam") or {}).get("category"),
                "subject": (first.get("exam") or {}).get("subject"),
                "year": (first.get("exam") or {}).get("year"),
                "ordinal": (first.get("exam") or {}).get("ordinal"),
                "available_count": len(papers[paper]),
                "selected_count": len(selected_for_paper),
                "candidate_keys": [str(task["candidate_key"]) for task in selected_for_paper],
            }
        )
    manifest = {
        "schema_version": "national_exam_shadow_pilot_scope_v1",
        "advisory_only": True,
        "production_writes": False,
        "paper_count": len(papers),
        "task_count": len(selections),
        "selection_mode": "all_tasks" if args.all_tasks else "per_paper_pilot",
        "per_paper": None if args.all_tasks else args.per_paper,
        "required_keys": sorted(required),
        "excluded_key_count": len(excluded_keys),
        "exclude_key_hashes": exclude_hashes,
        "source_task_hashes": source_hashes,
        "pilot_task_hash": sha256_json(selections),
        "lane_counts": dict(sorted(lane_counts.items())),
        "lane_display_names": LANE_DISPLAY_NAMES,
        "lane_files": lane_files,
        "papers": paper_rows,
        "selection": {
            str(task["candidate_key"]): selection_meta[str(task["candidate_key"])]
            for task in selections
        },
    }
    write_json(args.output_dir / "selection_manifest.json", manifest)
    print_json = {
        "ok": True,
        "paper_count": manifest["paper_count"],
        "task_count": manifest["task_count"],
        "lane_counts": manifest["lane_counts"],
        "output_dir": str(args.output_dir),
    }
    import json

    print(json.dumps(print_json, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
