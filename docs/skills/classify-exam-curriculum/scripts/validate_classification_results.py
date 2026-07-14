#!/usr/bin/env python3
"""Validate advisory curriculum-classification JSONL against tasks and taxonomy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from taxonomy_support import load_taxonomies

FIELDS = {
    "question_key",
    "input_hash",
    "taxonomy_version",
    "category",
    "subject",
    "domain_code",
    "primary_chapter_code",
    "secondary_chapter_codes",
    "confidence",
    "review_status",
    "evidence_terms",
    "reason",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--tasks", type=Path)
    parser.add_argument("--taxonomy", type=Path, action="append", help="Repeat to override the default taxonomy set.")
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Invalid JSON at {path}:{line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise SystemExit(f"Expected object at {path}:{line_number}")
            row["_line_number"] = line_number
            rows.append(row)
    return rows


def taxonomy_indexes(
    taxonomies: list[tuple[Path, dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    by_version: dict[str, dict[str, Any]] = {}
    for _path, taxonomy in taxonomies:
        subjects: dict[str, dict[str, Any]] = {}
        for subject in taxonomy.get("subjects", []):
            domain_chapters: dict[str, set[str]] = {}
            all_chapters: set[str] = set()
            for domain in subject.get("domains", []):
                chapters = {chapter["code"] for chapter in domain.get("chapters", [])}
                domain_chapters[domain["code"]] = chapters
                all_chapters.update(chapters)
            indexed = {"domains": domain_chapters, "all_chapters": all_chapters}
            for name in subject.get("names", []):
                subjects[name] = indexed
        by_version[str(taxonomy["taxonomy_version"])] = {
            "category": taxonomy["category"],
            "subjects": subjects,
        }
    return by_version


def validate_row(
    row: dict[str, Any],
    taxonomy_by_version: dict[str, dict[str, Any]],
    task: dict[str, Any] | None,
) -> list[str]:
    errors: list[str] = []
    actual_fields = set(row) - {"_line_number"}
    if actual_fields != FIELDS:
        missing = sorted(FIELDS - actual_fields)
        unknown = sorted(actual_fields - FIELDS)
        if missing:
            errors.append(f"missing_fields={','.join(missing)}")
        if unknown:
            errors.append(f"unknown_fields={','.join(unknown)}")
    for field in ("question_key", "input_hash", "taxonomy_version", "category", "subject", "domain_code", "primary_chapter_code", "reason"):
        if not isinstance(row.get(field), str) or not row.get(field, "").strip():
            errors.append(f"invalid_{field}")
    taxonomy = taxonomy_by_version.get(row.get("taxonomy_version"))
    if not taxonomy:
        errors.append("unknown_taxonomy_version")
        subject = None
    else:
        if row.get("category") != taxonomy.get("category"):
            errors.append("category_mismatch")
        subject = taxonomy["subjects"].get(row.get("subject"))
        if not subject:
            errors.append("unknown_subject")
    if subject:
        domain = row.get("domain_code")
        primary = row.get("primary_chapter_code")
        if domain not in subject["domains"]:
            errors.append("unknown_domain_code")
        elif primary not in subject["domains"][domain]:
            errors.append("primary_chapter_not_under_domain")
        secondary = row.get("secondary_chapter_codes")
        if not isinstance(secondary, list) or len(secondary) > 2 or len(secondary) != len(set(secondary or [])):
            errors.append("invalid_secondary_chapter_codes")
        elif primary in secondary or any(code not in subject["all_chapters"] for code in secondary):
            errors.append("unknown_or_repeated_secondary_chapter")
    if row.get("confidence") not in {"high", "medium", "low"}:
        errors.append("invalid_confidence")
    if row.get("review_status") not in {"ai_suggested", "needs_human_review"}:
        errors.append("invalid_review_status")
    if row.get("confidence") == "low" and row.get("review_status") != "needs_human_review":
        errors.append("low_confidence_requires_human_review")
    evidence = row.get("evidence_terms")
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 5 or any(not isinstance(item, str) or not item.strip() for item in evidence):
        errors.append("invalid_evidence_terms")
    if task is not None:
        for field in ("question_key", "input_hash", "taxonomy_version", "category", "subject"):
            if row.get(field) != task.get(field):
                errors.append(f"task_{field}_mismatch")
    return errors


def main() -> None:
    args = parse_args()
    taxonomies = load_taxonomies(args.taxonomy)
    taxonomy_by_version = taxonomy_indexes(taxonomies)
    task_rows = read_jsonl(args.tasks) if args.tasks else []
    tasks = {row.get("question_key"): row for row in task_rows}
    if len(tasks) != len(task_rows):
        raise SystemExit("Duplicate or missing question_key in tasks")
    results = read_jsonl(args.results)
    seen: set[str] = set()
    error_rows: list[dict[str, Any]] = []
    for row in results:
        key = row.get("question_key")
        errors: list[str] = []
        if key in seen:
            errors.append("duplicate_question_key")
        seen.add(key)
        task = tasks.get(key) if tasks else None
        if tasks and task is None:
            errors.append("question_key_not_in_tasks")
        errors.extend(validate_row(row, taxonomy_by_version, task))
        if errors:
            error_rows.append({"line": row["_line_number"], "question_key": key, "errors": sorted(set(errors))})
    if tasks:
        for missing in sorted(set(tasks) - seen):
            error_rows.append({"line": None, "question_key": missing, "errors": ["missing_result"]})
    summary = {
        "task_count": len(task_rows) if args.tasks else None,
        "result_count": len(results),
        "valid_count": len(results) - sum(1 for row in error_rows if row["line"] is not None),
        "error_count": len(error_rows),
        "errors": error_rows,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(1 if error_rows else 0)


if __name__ == "__main__":
    main()
