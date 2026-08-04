#!/usr/bin/env python3
"""Freeze safe compact-audit tasks from a read-only Review UI API response.

The API response already reflects the latest append-only human corrections.
This script deliberately removes answers, review history, reviewer notes, and
prior AI output before any task can be sent to a model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")


def integer_sort_value(value: Any) -> tuple[int, str]:
    text = str(value or "")
    return (int(text), text) if text.isdigit() else (10**9, text)


def paper_key(candidate: dict[str, Any]) -> str:
    key = str(candidate.get("source_registry_key") or "").strip()
    if key:
        return key
    metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
    return "|".join(
        str(metadata.get(field) or "")
        for field in (
            "normalized_category_name",
            "year",
            "exam_ordinal",
            "subject_code",
        )
    )


def candidate_sort_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
    return (
        integer_sort_value(metadata.get("year")),
        integer_sort_value(metadata.get("exam_ordinal")),
        str(
            metadata.get("normalized_category_name")
            or metadata.get("group_name")
            or ""
        ),
        str(metadata.get("subject_code") or ""),
        str(metadata.get("normalized_subject_name") or ""),
        paper_key(candidate),
        integer_sort_value(candidate.get("question_number")),
        str(candidate.get("candidate_key") or ""),
    )


def normalize_asset_ref(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    ref = {
        key: value.get(key)
        for key in ("raw_ref", "path_relative", "relative_path", "bytes")
        if value.get(key) is not None
    }
    relative = str(
        value.get("path_relative")
        or value.get("relative_path")
        or ""
    ).strip()
    path: Path | None = None
    if relative:
        path = PROJECT_ROOT / relative
    else:
        raw_path = str(value.get("path") or "").strip()
        if raw_path:
            candidate_path = Path(raw_path)
            path = candidate_path if candidate_path.is_absolute() else PROJECT_ROOT / candidate_path
    source_exists = value.get("exists")
    if isinstance(source_exists, bool):
        ref["source_exists"] = source_exists
    if path is not None:
        ref["path"] = str(path)
        local_exists = path.is_file()
        ref["local_exists"] = local_exists
        ref["exists"] = source_exists if isinstance(source_exists, bool) else local_exists
        if local_exists:
            ref["bytes"] = path.stat().st_size
    else:
        ref["local_exists"] = False
        ref["exists"] = source_exists if isinstance(source_exists, bool) else False
    return ref


def safe_options(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for option in candidate.get("options") or []:
        if not isinstance(option, dict):
            continue
        row: dict[str, Any] = {
            "key": str(option.get("key") or "").strip().upper(),
            "text": str(option.get("text") or ""),
        }
        image = normalize_asset_ref(option.get("image"))
        if image is not None:
            row["image"] = image
        if option.get("markup") is not None:
            row["markup"] = option.get("markup")
        options.append(row)
    return options


def parser_issues(candidate: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for issue in candidate.get("question_issues") or candidate.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        code = str(issue.get("issue_code") or issue.get("code") or "")
        if not code:
            continue
        rows.append(
            {
                "code": code,
                "severity": str(issue.get("severity") or ""),
                "message": str(issue.get("message") or "")[:300],
            }
        )
    return rows[:12]


def safe_task(candidate: dict[str, Any]) -> dict[str, Any]:
    metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
    image_refs = [
        normalized
        for ref in candidate.get("image_refs") or []
        if (normalized := normalize_asset_ref(ref)) is not None
    ]
    stem_image = normalize_asset_ref(candidate.get("stem_image"))
    content: dict[str, Any] = {
        "stem": str(candidate.get("stem_with_tables") or candidate.get("stem") or ""),
        "options": safe_options(candidate),
        "image_refs": image_refs,
        "stem_image": stem_image,
        "group_ref": candidate.get("group_ref"),
        "group_sequence_no": candidate.get("group_sequence_no"),
    }
    task = {
        "candidate_key": str(candidate.get("candidate_key") or ""),
        "source_registry_key": paper_key(candidate),
        "exam": {
            "category": str(
                metadata.get("normalized_category_name")
                or metadata.get("group_name")
                or ""
            ),
            "subject": str(metadata.get("normalized_subject_name") or ""),
            "subject_code": str(metadata.get("subject_code") or ""),
            "year": str(metadata.get("year") or ""),
            "ordinal": str(metadata.get("exam_ordinal") or ""),
            "question_number": str(candidate.get("question_number") or ""),
            "occurrence": int(candidate.get("question_number_occurrence") or 1),
        },
        "content": content,
        "signals": {
            "parser_issues": parser_issues(candidate),
            "exam_header_false_question": False,
        },
        "sources": {
            "question_pdf_relative": str(metadata.get("question_pdf_relative") or ""),
            "question_markdown_relative": str(
                metadata.get("question_markdown_relative") or ""
            ),
        },
        "stage": "question",
    }
    task["effective_content_hash"] = stable_hash(content)
    return task


def attach_neighbors(tasks: list[dict[str, Any]]) -> None:
    for index, task in enumerate(tasks):
        previous = tasks[index - 1] if index > 0 else None
        next_item = tasks[index + 1] if index + 1 < len(tasks) else None
        if previous and previous["source_registry_key"] != task["source_registry_key"]:
            previous = None
        if next_item and next_item["source_registry_key"] != task["source_registry_key"]:
            next_item = None

        def neighbor(value: dict[str, Any] | None) -> dict[str, Any] | None:
            if value is None:
                return None
            return {
                "candidate_key": value["candidate_key"],
                "question_number": value["exam"]["question_number"],
                "stem": value["content"]["stem"],
            }

        task["neighbors"] = {
            "previous": neighbor(previous),
            "next": neighbor(next_item),
        }


def paper_manifest(tasks: list[dict[str, Any]], expected_paper_size: int) -> list[dict[str, Any]]:
    papers: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        papers.setdefault(task["source_registry_key"], []).append(task)
    rows: list[dict[str, Any]] = []
    for key, paper_tasks in papers.items():
        numbers = {
            int(task["exam"]["question_number"])
            for task in paper_tasks
            if str(task["exam"]["question_number"]).isdigit()
        }
        expected = (
            set(range(1, expected_paper_size + 1))
            if expected_paper_size > 0
            else numbers
        )
        rows.append(
            {
                "paper_key": key,
                "category": paper_tasks[0]["exam"]["category"],
                "year": paper_tasks[0]["exam"]["year"],
                "ordinal": paper_tasks[0]["exam"]["ordinal"],
                "subject_code": paper_tasks[0]["exam"]["subject_code"],
                "subject": paper_tasks[0]["exam"]["subject"],
                "task_count": len(paper_tasks),
                "missing_question_numbers": (
                    sorted(expected - numbers) if expected_paper_size > 0 else []
                ),
                "unexpected_question_numbers": (
                    sorted(numbers - expected) if expected_paper_size > 0 else []
                ),
            }
        )
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-response",
        type=Path,
        action="append",
        default=[],
        help=(
            "Read-only Review UI API response. Repeat this option for category/year "
            "shards; every shard must be complete and candidate keys must be unique."
        ),
    )
    parser.add_argument(
        "--api-response-dir",
        type=Path,
        action="append",
        default=[],
        help="Directory containing complete `shard_*.json` Review UI responses.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-paper-size", type=int, default=80)
    parser.add_argument(
        "--require-complete-papers",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    response_paths = list(args.api_response)
    for response_dir in args.api_response_dir:
        response_paths.extend(sorted(response_dir.glob("shard_*.json")))
    if not response_paths:
        raise ValueError("provide --api-response or --api-response-dir")
    candidates: list[dict[str, Any]] = []
    filtered_count = 0
    response_metadata: list[dict[str, Any]] = []
    for response_path in response_paths:
        response = json.loads(response_path.read_text(encoding="utf-8"))
        shard_candidates = response.get("candidates")
        if not isinstance(shard_candidates, list):
            raise ValueError(f"{response_path}: API response has no candidates array")
        returned_count = int(response.get("returned_count") or len(shard_candidates))
        shard_filtered_count = int(response.get("filtered_count") or returned_count)
        if (
            returned_count != len(shard_candidates)
            or shard_filtered_count != returned_count
        ):
            raise ValueError(
                f"{response_path}: truncated API response: "
                f"filtered={shard_filtered_count}, returned={returned_count}, "
                f"rows={len(shard_candidates)}"
            )
        candidates.extend(shard_candidates)
        filtered_count += shard_filtered_count
        response_metadata.append(
            {
                "path": str(response_path.resolve()),
                "filtered_count": shard_filtered_count,
                "returned_count": returned_count,
                "candidate_source_jsonl": response.get("candidate_source_jsonl"),
                "issue_csv": response.get("issue_csv"),
            }
        )
    keys = [str(candidate.get("candidate_key") or "") for candidate in candidates]
    duplicates = [key for key, count in Counter(keys).items() if not key or count > 1]
    if duplicates:
        raise ValueError(f"invalid or duplicate candidate keys: {duplicates[:10]}")
    tasks = [safe_task(candidate) for candidate in sorted(candidates, key=candidate_sort_key)]
    attach_neighbors(tasks)
    papers = paper_manifest(tasks, args.expected_paper_size)
    incomplete = (
        [
            paper
            for paper in papers
            if paper["task_count"] != args.expected_paper_size
            or paper["missing_question_numbers"]
            or paper["unexpected_question_numbers"]
        ]
        if args.expected_paper_size > 0
        else []
    )
    if args.require_complete_papers and incomplete:
        raise ValueError(f"incomplete papers: {incomplete}")
    output_tasks = args.output_dir / "compact_audit_tasks.jsonl"
    write_jsonl(output_tasks, tasks)
    manifest = {
        "schema_version": "compact_audit_task_snapshot_v1",
        "source_api_response": (
            response_metadata[0]["path"] if len(response_metadata) == 1 else None
        ),
        "source_api_responses": response_metadata,
        "candidate_source_jsonl": sorted(
            {
                str(item["candidate_source_jsonl"])
                for item in response_metadata
                if item.get("candidate_source_jsonl")
            }
        ),
        "issue_csv": sorted(
            {
                str(item["issue_csv"])
                for item in response_metadata
                if item.get("issue_csv")
            }
        ),
        "filtered_count": filtered_count,
        "task_count": len(tasks),
        "paper_count": len(papers),
        "expected_paper_size": (
            args.expected_paper_size if args.expected_paper_size > 0 else None
        ),
        "complete_papers": (
            not incomplete if args.expected_paper_size > 0 else None
        ),
        "paper_completeness_check_disabled": args.expected_paper_size <= 0,
        "papers": papers,
        "tasks": str(output_tasks),
        "task_hash": stable_hash(tasks),
        "contains_answers": False,
        "contains_human_review_state": False,
    }
    write_json(args.output_dir / "task_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
