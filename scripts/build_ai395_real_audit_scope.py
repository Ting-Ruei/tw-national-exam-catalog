#!/usr/bin/env python3
"""Build a deterministic, read-only real MinerU audit scope for AI395 staging.

The full parser output can contain tens of thousands of questions. This tool
selects a small, reproducible cross-year/category sample that deliberately
covers parser warnings, notation, groups, visual assets, answer corrections,
and clean text. It writes only derived files under the requested output
directory and creates source/MinerU manifests for the staging contract.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ASSET_ROOT = PROJECT_ROOT / "國考題資料夾"
DEFAULT_MINERU_ROOT = DEFAULT_ASSET_ROOT / "20_mineru_output"
TARGET_GROUPS = ("醫事檢驗師", "藥師")
TAG_PRIORITY = (
    "parser_blocked",
    "parser_warning",
    "visual_missing",
    "vision",
    "notation",
    "group",
    "answer_special",
    "option_anomaly",
    "clean",
)
NOTATION_CODES = {"markup_needs_review", "suspicious_ocr_chars", "amino_acid_translation_suspect"}
PARSER_BLOCK_CODES = {
    "empty_option",
    "empty_stem",
    "too_few_options",
    "missing_question_markdown",
    "question_answer_number_set_mismatch",
    "fixed_exam_question_count_missing",
    "fixed_exam_question_count_out_of_range",
    "question_number_gap",
}
OPTION_CODES = {"empty_option", "too_few_options", "option_order_unusual", "duplicate_option_label"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"candidate row {line_number} is not an object")
                rows.append(value)
    return rows


def read_issues(path: Path) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            raw_json = row.get("issue_json") or "{}"
            try:
                issue_json = json.loads(raw_json)
            except json.JSONDecodeError:
                issue_json = {"raw": raw_json}
            row["issue_json"] = issue_json
            result[str(row.get("candidate_key") or "")].append(row)
    return result


def image_refs(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    refs = [ref for ref in candidate.get("image_refs") or [] if isinstance(ref, dict)]
    stem = candidate.get("stem_image")
    if isinstance(stem, dict):
        refs.append(stem)
    for option in candidate.get("options") or []:
        if isinstance(option, dict) and isinstance(option.get("image"), dict):
            refs.append(option["image"])
    return refs


def has_raster(ref: dict[str, Any]) -> bool:
    raw = str(ref.get("path") or ref.get("relative_path") or ref.get("raw_ref") or "").lower()
    return bool(re.search(r"\.(?:png|jpe?g|webp)$", raw)) and ref.get("exists") is not False


def classify(candidate: dict[str, Any], issues: list[dict[str, Any]]) -> list[str]:
    codes = {str(row.get("issue_code") or "") for row in issues}
    metadata = candidate.get("metadata") or {}
    stem = str(candidate.get("stem") or "")
    refs = image_refs(candidate)
    tags: set[str] = set()
    if any(str(row.get("severity") or "") in {"blocked", "error"} or code in PARSER_BLOCK_CODES for row, code in ((row, str(row.get("issue_code") or "")) for row in issues)):
        tags.add("parser_blocked")
    elif issues:
        tags.add("parser_warning")
    if codes & NOTATION_CODES or (candidate.get("stem_markup") or {}).get("needs_review") or re.search(r"(?:<sub>|<sup>|\\[A-Za-z]+|[⁰¹²³⁴⁵⁶⁷⁸⁹α-ωΑ-Ω])", stem):
        tags.add("notation")
    if refs:
        tags.add("vision")
    if "image_hint_without_asset" in codes or "missing_image_asset" in codes or re.search(r"(下列圖|如圖|附圖|圖示|圖片|影像|照片|X光|切片圖|電泳圖|曲線圖|流程圖|家系圖)", stem, re.I) and not refs:
        tags.add("visual_missing")
    if candidate.get("group_ref") or re.search(r"(承上題|本題組|下列題組|共同題幹)", stem):
        tags.add("group")
    answer = candidate.get("answer")
    payload = candidate.get("answer_payload") or {}
    if (
        metadata.get("answer_role_primary") == "correction"
        or payload.get("is_special_correction")
        or answer in {"#", None}
        or isinstance(answer, list)
        or (isinstance(answer, str) and bool(re.search(r"[|/或、]", answer)))
    ):
        tags.add("answer_special")
    if codes & OPTION_CODES:
        tags.add("option_anomaly")
    if not tags:
        tags.add("clean")
    return sorted(tags)


def lane_targets(tags: list[str]) -> list[str]:
    targets = {"text_evidence"}
    tag_set = set(tags)
    if "notation" in tag_set:
        targets.add("notation")
    if "group" in tag_set:
        targets.add("group")
    if "vision" in tag_set or "visual_missing" in tag_set:
        targets.add("vision")
    if "answer_special" in tag_set:
        targets.add("answer")
    return sorted(targets)


def select_scope(
    candidates: list[dict[str, Any]],
    issues_by_key: dict[str, list[dict[str, Any]]],
    groups: set[str],
    years: set[str] | None,
    max_per_year_category: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    eligible = [
        row for row in candidates
        if str((row.get("metadata") or {}).get("group_name") or "") in groups
        and (years is None or str((row.get("metadata") or {}).get("year") or "") in years)
    ]
    buckets: dict[tuple[str, str], list[tuple[dict[str, Any], list[str]]]] = defaultdict(list)
    for row in eligible:
        key = str(row.get("candidate_key") or "")
        category = str((row.get("metadata") or {}).get("group_name") or "")
        year = str((row.get("metadata") or {}).get("year") or "")
        buckets[(category, year)].append((row, classify(row, issues_by_key.get(key, []))))

    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    bucket_report: list[dict[str, Any]] = []
    for bucket in sorted(buckets, key=lambda value: (value[0], int(value[1]) if value[1].isdigit() else value[1])):
        rows = sorted(buckets[bucket], key=lambda pair: str(pair[0].get("candidate_key") or ""))
        chosen: list[tuple[dict[str, Any], list[str]]] = []
        for tag in TAG_PRIORITY:
            if len(chosen) >= max_per_year_category:
                break
            match = next((pair for pair in rows if tag in pair[1] and str(pair[0].get("candidate_key")) not in {str(item[0].get("candidate_key")) for item in chosen}), None)
            if match is not None:
                chosen.append(match)
        if len(chosen) < max_per_year_category:
            for pair in rows:
                if len(chosen) >= max_per_year_category:
                    break
                if str(pair[0].get("candidate_key")) not in {str(item[0].get("candidate_key")) for item in chosen}:
                    chosen.append(pair)
        for row, tags in chosen:
            key = str(row.get("candidate_key") or "")
            if key in selected_keys:
                continue
            copied = deepcopy(row)
            metadata = dict(copied.get("metadata") or {})
            metadata["parser_status"] = "blocked" if copied.get("quality_status") == "blocked" else "needs_review" if copied.get("quality_status") == "needs_review" else "pass"
            metadata["ai395_scope_tags"] = tags
            metadata["llm_lane_targets"] = lane_targets(tags)
            metadata["ai395_scope_selection"] = "year_category_coverage_v1"
            copied["metadata"] = metadata
            selected.append(copied)
            selected_keys.add(key)
        bucket_report.append({
            "group_name": bucket[0],
            "year": bucket[1],
            "available_candidates": len(rows),
            "selected_candidates": len(chosen),
            "selected_tags": [tags for _, tags in chosen],
        })
    return sorted(selected, key=lambda row: str(row.get("candidate_key") or "")), {
        "available_candidates": len(eligible),
        "selected_candidates": len(selected),
        "buckets": bucket_report,
    }


def write_candidates(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_issues(path: Path, rows: list[dict[str, Any]], issues_by_key: dict[str, list[dict[str, Any]]]) -> None:
    fields = ["candidate_key", "source_registry_key", "question_number", "issue_code", "severity", "message", "issue_json"]
    selected_keys = {str(row.get("candidate_key") or "") for row in rows}
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for key in sorted(selected_keys):
            for issue in issues_by_key.get(key, []):
                writer.writerow({
                    field: json.dumps(issue.get(field) or {}, ensure_ascii=False, sort_keys=True)
                    if field == "issue_json" else issue.get(field, "")
                    for field in fields
                })


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-jsonl", type=Path, required=True)
    parser.add_argument("--issue-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scope-id", required=True)
    parser.add_argument("--group-name", action="append", default=list(TARGET_GROUPS))
    parser.add_argument("--year", action="append", default=[])
    parser.add_argument("--max-per-year-category", type=int, default=8)
    parser.add_argument("--mineru-root", type=Path, default=DEFAULT_MINERU_ROOT)
    parser.add_argument("--parser-summary", type=Path)
    parser.add_argument("--pair-index", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_per_year_category < 1:
        raise SystemExit("--max-per-year-category must be positive")
    candidate_path = args.candidate_jsonl.resolve()
    issue_path = args.issue_csv.resolve()
    output_dir = args.output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise SystemExit(f"refusing to overwrite non-empty output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = read_jsonl(candidate_path)
    issues_by_key = read_issues(issue_path)
    years = set(args.year) if args.year else None
    selected, selection = select_scope(candidates, issues_by_key, set(args.group_name), years, args.max_per_year_category)
    if not selected:
        raise SystemExit("scope selection is empty")

    scoped_candidates = output_dir / "candidates.jsonl"
    scoped_issues = output_dir / "issues.csv"
    write_candidates(scoped_candidates, selected)
    write_issues(scoped_issues, selected, issues_by_key)

    parser_summary = None
    if args.parser_summary and args.parser_summary.exists():
        parser_summary = json.loads(args.parser_summary.read_text(encoding="utf-8"))
    source_artifact_prefix = f"{args.scope_id}:real-mineru"
    source_artifacts = [
        {
            "artifact_id": f"{source_artifact_prefix}:parser-candidates",
            "artifact_type": "parser_candidate_jsonl",
            "status": "existing_artifact",
            "path": str(candidate_path),
            "sha256": sha256_file(candidate_path),
            "producer": "build_question_candidates_from_mineru",
            "producer_version": "moex_mineru_candidate_v0.12",
            "immutable": True,
            "read_only": True,
        },
        {
            "artifact_id": f"{source_artifact_prefix}:parser-issues",
            "artifact_type": "parser_issue_csv",
            "status": "existing_artifact",
            "path": str(issue_path),
            "sha256": sha256_file(issue_path),
            "producer": "build_question_candidates_from_mineru",
            "producer_version": "moex_mineru_candidate_v0.12",
            "immutable": True,
            "read_only": True,
        },
    ]
    if args.pair_index:
        pair_index = args.pair_index.resolve()
        source_artifacts.append({
            "artifact_id": f"{source_artifact_prefix}:pair-index",
            "artifact_type": "question_answer_pair_index",
            "status": "existing_artifact",
            "path": str(pair_index),
            "sha256": sha256_file(pair_index),
            "producer": "paired-index-builder",
            "producer_version": "existing",
            "immutable": True,
            "read_only": True,
        })

    scope_manifest = {
        "scope_id": args.scope_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_candidate_jsonl": str(candidate_path),
        "source_issue_csv": str(issue_path),
        "source_parser_summary": str(args.parser_summary.resolve()) if args.parser_summary else None,
        "groups": sorted(args.group_name),
        "years": sorted(years) if years is not None else "all",
        "max_per_year_category": args.max_per_year_category,
        "selection": selection,
        "selected_issue_rows": sum(len(issues_by_key.get(str(row.get("candidate_key") or ""), [])) for row in selected),
        "parser_document_status_counts": (parser_summary or {}).get("document_status_counts"),
        "parser_missing_documents": [
            item for item in (parser_summary or {}).get("documents", []) if item.get("status") != "ok"
        ],
    }
    scope_manifest_path = output_dir / "scope_manifest.json"
    write_json(scope_manifest_path, scope_manifest)

    source_manifest = {
        "manifest_version": "ai395-source-manifest-v1",
        "fixture_id": f"real:{args.scope_id}",
        "source_artifacts": source_artifacts,
        "candidate_jsonl": {"path": "candidates.jsonl", "sha256": sha256_file(scoped_candidates)},
        "issue_csv": {"path": "issues.csv", "sha256": sha256_file(scoped_issues)},
    }
    write_json(output_dir / "source_manifest.json", source_manifest)
    mineru_root = args.mineru_root.resolve()
    mineru_manifest = {
        "manifest_version": "ai395-mineru-manifest-v1",
        "mineru_run_id": f"{args.scope_id}:mineru-existing-artifact",
        "source_artifact_id": f"{source_artifact_prefix}:parser-candidates",
        "status": "existing_artifact",
        "mode": "existing_artifact",
        "mineru_version": "existing-mineru-output",
        "input_sha256": sha256_file(candidate_path),
        "output_root": str(mineru_root),
        "image_root": str(mineru_root),
        "read_only": True,
        "scope_manifest": "scope_manifest.json",
    }
    write_json(output_dir / "mineru_manifest.json", mineru_manifest)
    print(json.dumps({
        "scope_id": args.scope_id,
        "output_dir": str(output_dir),
        "candidate_jsonl": str(scoped_candidates),
        "issue_csv": str(scoped_issues),
        "source_manifest": str(output_dir / "source_manifest.json"),
        "mineru_manifest": str(output_dir / "mineru_manifest.json"),
        "selected_candidates": len(selected),
        "selected_issue_rows": scope_manifest["selected_issue_rows"],
        "selection": selection,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
