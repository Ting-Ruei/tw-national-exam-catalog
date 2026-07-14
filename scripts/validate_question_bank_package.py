#!/usr/bin/env python3
"""
Validate a versioned question-bank package before platform import.

This checks the delivery boundary: manifest, questions, groups, subjects,
asset manifest, package-relative paths, visual tags, and checksums.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REQUIRED_FILES = ("manifest.json", "subjects.json", "questions.jsonl", "groups.jsonl", "asset_manifest.jsonl")
SUPPORTED_QUESTION_TYPES = {"single_choice", "single", "multiple", "truefalse", "group"}
ABSOLUTE_LOCAL_RE = re.compile(r"(/Users/|/Volumes/|file://|[A-Za-z]:\\\\)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a question-bank package directory.")
    parser.add_argument("package_dir", type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-examples", type=int, default=20)
    return parser.parse_args()


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise SystemExit(f"Invalid JSONL object at {path}:{line_number}")
            rows.append(row)
    return rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_issue(severity: str, code: str, message: str, count: int, examples: list[Any] | None = None) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "count": int(count or 0),
        "examples": examples or [],
    }


def limited(args: argparse.Namespace, rows: list[Any]) -> list[Any]:
    return rows[: max(0, args.max_examples)]


def contains_abs_local(value: Any) -> bool:
    if isinstance(value, str):
        return bool(ABSOLUTE_LOCAL_RE.search(value))
    if isinstance(value, list):
        return any(contains_abs_local(item) for item in value)
    if isinstance(value, dict):
        return any(contains_abs_local(item) for item in value.values())
    return False


def asset_paths_from_question(question: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    stem_image = question.get("stem_image")
    if isinstance(stem_image, str) and stem_image:
        paths.append(stem_image)
    elif isinstance(stem_image, dict) and stem_image.get("path"):
        paths.append(str(stem_image["path"]))
    for option in question.get("options") or []:
        if not isinstance(option, dict):
            continue
        image = option.get("image")
        if isinstance(image, str) and image:
            paths.append(image)
        elif isinstance(image, dict) and image.get("path"):
            paths.append(str(image["path"]))
        explanation_image = option.get("explanation_image")
        if isinstance(explanation_image, str) and explanation_image:
            paths.append(explanation_image)
        elif isinstance(explanation_image, dict) and explanation_image.get("path"):
            paths.append(str(explanation_image["path"]))
    for ref in question.get("metadata", {}).get("asset_refs", []) if isinstance(question.get("metadata"), dict) else []:
        if isinstance(ref, dict):
            path = ref.get("package_path") or ref.get("path") or ref.get("relative_asset_path")
            if path:
                paths.append(str(path))
    return paths


def validate_files(args: argparse.Namespace, package_dir: Path, issues: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    missing = [name for name in REQUIRED_FILES if not (package_dir / name).is_file()]
    if missing:
        issues.append(build_issue("error", "package_required_file_missing", "Package is missing required files.", len(missing), missing))
        return {}, {}, [], [], []
    manifest = read_json(package_dir / "manifest.json")
    subjects = read_json(package_dir / "subjects.json")
    questions = read_jsonl(package_dir / "questions.jsonl")
    groups = read_jsonl(package_dir / "groups.jsonl")
    assets = read_jsonl(package_dir / "asset_manifest.jsonl")
    return manifest, subjects, questions, groups, assets


def validate_manifest_counts(args: argparse.Namespace, package_dir: Path, manifest: dict[str, Any], subjects: dict[str, Any], questions: list[dict[str, Any]], groups: list[dict[str, Any]], assets: list[dict[str, Any]], issues: list[dict[str, Any]]) -> None:
    asset_files = sorted(path for path in (package_dir / "assets").rglob("*") if path.is_file()) if (package_dir / "assets").exists() else []
    subjects_rows = subjects.get("subjects") if isinstance(subjects.get("subjects"), list) else []
    expected = {
        "questions": len(questions),
        "subjects": len(subjects_rows),
        "groups": len(groups),
        "asset_references": len(assets),
        "asset_files": len(asset_files),
    }
    actual = manifest.get("counts") if isinstance(manifest.get("counts"), dict) else {}
    mismatches = [
        {"field": key, "manifest": actual.get(key), "actual": value}
        for key, value in expected.items()
        if actual.get(key) != value
    ]
    if mismatches:
        issues.append(build_issue("error", "package_manifest_count_mismatch", "Manifest counts must match package files.", len(mismatches), limited(args, mismatches)))
    if int(actual.get("warnings") or 0) > 0:
        issues.append(build_issue("error", "package_export_warnings_present", "Exporter warnings must be resolved before formal import.", int(actual.get("warnings") or 0), []))


def validate_questions(args: argparse.Namespace, questions: list[dict[str, Any]], groups: list[dict[str, Any]], assets: list[dict[str, Any]], issues: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    duplicate_keys: list[str] = []
    invalid_required: list[dict[str, Any]] = []
    duplicate_options: list[dict[str, Any]] = []
    unsupported_types: list[dict[str, Any]] = []
    missing_visual: list[str] = []
    abs_local: list[str] = []
    asset_manifest_paths = {str(row.get("package_path") or "") for row in assets}
    group_refs = {str(row.get("group_ref") or "") for row in groups}
    question_group_refs: Counter[str] = Counter()
    missing_asset_refs: list[dict[str, Any]] = []
    visual_dependency_without_asset: list[str] = []

    for question in questions:
        key = str(question.get("source_question_key") or "")
        if key in seen:
            duplicate_keys.append(key)
        seen.add(key)
        metadata = question.get("metadata") if isinstance(question.get("metadata"), dict) else {}
        required_missing = [
            field
            for field in ("source_question_key", "source_registry_key", "stem", "options", "answer", "question_type", "metadata")
            if not question.get(field)
        ]
        if metadata.get("review_status") != "accepted":
            required_missing.append("metadata.review_status=accepted")
        if not metadata.get("source_content_hash"):
            required_missing.append("metadata.source_content_hash")
        if not metadata.get("canonical_subject_name"):
            required_missing.append("metadata.canonical_subject_name")
        if required_missing:
            invalid_required.append({"source_question_key": key, "missing": required_missing})
        option_keys = [str(option.get("key") or "").strip().upper() for option in question.get("options") or [] if isinstance(option, dict)]
        repeated = sorted(label for label, count in Counter(option_keys).items() if label and count > 1)
        if repeated:
            duplicate_options.append({"source_question_key": key, "duplicate_option_keys": repeated})
        if question.get("question_type") not in SUPPORTED_QUESTION_TYPES:
            unsupported_types.append({"source_question_key": key, "question_type": question.get("question_type")})
        if "visual_profile" not in question or "feature_tags" not in question:
            missing_visual.append(key)
        group_ref = str(question.get("group_ref") or "")
        if group_ref:
            question_group_refs[group_ref] += 1
            if group_ref not in group_refs:
                invalid_required.append({"source_question_key": key, "missing": [f"group_ref not in groups.jsonl: {group_ref}"]})
        if contains_abs_local(question):
            abs_local.append(key)
        for path in asset_paths_from_question(question):
            if path not in asset_manifest_paths:
                missing_asset_refs.append({"source_question_key": key, "asset_path": path})
        profile = question.get("visual_profile") if isinstance(question.get("visual_profile"), dict) else {}
        if profile.get("has_visual_dependency") and not profile.get("has_visual_asset"):
            visual_dependency_without_asset.append(key)

    if duplicate_keys:
        issues.append(build_issue("error", "package_duplicate_source_question_key", "source_question_key must be unique.", len(duplicate_keys), limited(args, duplicate_keys)))
    if invalid_required:
        issues.append(build_issue("error", "package_question_required_fields_invalid", "Question records are missing required import fields.", len(invalid_required), limited(args, invalid_required)))
    if duplicate_options:
        issues.append(build_issue("error", "package_duplicate_option_keys", "Question options must not contain duplicate keys.", len(duplicate_options), limited(args, duplicate_options)))
    if unsupported_types:
        issues.append(build_issue("error", "package_unsupported_question_type", "Question type must be supported by the platform adapter.", len(unsupported_types), limited(args, unsupported_types)))
    if missing_visual:
        issues.append(build_issue("error", "package_visual_markers_missing", "Every question must carry visual_profile and feature_tags.", len(missing_visual), limited(args, missing_visual)))
    if abs_local:
        issues.append(build_issue("error", "package_absolute_local_path", "Package JSON must not contain local absolute paths.", len(abs_local), limited(args, abs_local)))
    if missing_asset_refs:
        issues.append(build_issue("error", "package_question_asset_ref_missing_manifest", "Question asset references must appear in asset_manifest.jsonl.", len(missing_asset_refs), limited(args, missing_asset_refs)))
    if visual_dependency_without_asset:
        issues.append(build_issue("warning", "package_visual_dependency_without_asset", "Questions mention visual dependencies but do not have packaged assets; importer/display should decide fallback behavior.", len(visual_dependency_without_asset), limited(args, visual_dependency_without_asset)))


def validate_groups(args: argparse.Namespace, questions: list[dict[str, Any]], groups: list[dict[str, Any]], issues: list[dict[str, Any]]) -> None:
    question_members: dict[str, set[str]] = defaultdict(set)
    for question in questions:
        group_ref = str(question.get("group_ref") or "")
        if group_ref:
            question_members[group_ref].add(str(question.get("source_question_key") or ""))
    invalid_groups: list[dict[str, Any]] = []
    seen_groups: set[str] = set()
    duplicate_groups: list[str] = []
    for group in groups:
        group_ref = str(group.get("group_ref") or "")
        if group_ref in seen_groups:
            duplicate_groups.append(group_ref)
        seen_groups.add(group_ref)
        members = [str(item or "") for item in group.get("member_source_question_keys") or []]
        problems: list[str] = []
        if not group_ref:
            problems.append("missing group_ref")
        if len(members) < 2:
            problems.append("member_count < 2")
        if any(not item for item in members):
            problems.append("null/empty member_source_question_keys")
        if set(members) != question_members.get(group_ref, set()):
            problems.append("members do not match questions.jsonl group_ref")
        if problems:
            invalid_groups.append({"group_ref": group_ref, "problems": problems, "members": members})
    missing_group_records = sorted(set(question_members) - seen_groups)
    if duplicate_groups:
        issues.append(build_issue("error", "package_duplicate_group_ref", "groups.jsonl group_ref must be unique.", len(duplicate_groups), limited(args, duplicate_groups)))
    if invalid_groups:
        issues.append(build_issue("error", "package_group_members_invalid", "Group records must match question group_ref membership.", len(invalid_groups), limited(args, invalid_groups)))
    if missing_group_records:
        issues.append(build_issue("error", "package_group_record_missing", "Every question group_ref must have a groups.jsonl record.", len(missing_group_records), limited(args, missing_group_records)))


def validate_assets(args: argparse.Namespace, package_dir: Path, assets: list[dict[str, Any]], issues: list[dict[str, Any]]) -> None:
    missing_file: list[dict[str, Any]] = []
    bad_sha: list[dict[str, Any]] = []
    invalid_path: list[dict[str, Any]] = []
    duplicate_paths: list[str] = []
    seen_paths: set[str] = set()
    for row in assets:
        package_path = str(row.get("package_path") or "")
        if package_path in seen_paths:
            duplicate_paths.append(package_path)
        seen_paths.add(package_path)
        if not package_path or Path(package_path).is_absolute() or contains_abs_local(row):
            invalid_path.append(row)
            continue
        path = package_dir / package_path
        if not path.exists():
            missing_file.append(row)
            continue
        expected_sha = str(row.get("sha256") or "")
        if not expected_sha or sha256_file(path) != expected_sha:
            bad_sha.append(row)
    if invalid_path:
        issues.append(build_issue("error", "package_asset_path_invalid", "Asset manifest paths must be package-relative and free of local absolute paths.", len(invalid_path), limited(args, invalid_path)))
    if missing_file:
        issues.append(build_issue("error", "package_asset_file_missing", "Every asset_manifest row must point to an existing package file.", len(missing_file), limited(args, missing_file)))
    if bad_sha:
        issues.append(build_issue("error", "package_asset_sha256_mismatch", "Asset manifest sha256 must match packaged files.", len(bad_sha), limited(args, bad_sha)))
    if duplicate_paths:
        issues.append(build_issue("warning", "package_duplicate_asset_package_path", "Multiple asset manifest rows point to the same package path.", len(duplicate_paths), limited(args, duplicate_paths)))


def validate_subjects(args: argparse.Namespace, subjects: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    rows = subjects.get("subjects") if isinstance(subjects.get("subjects"), list) else []
    missing_canonical = [row for row in rows if not row.get("canonical_subject_name")]
    missing_notes = [row for row in rows if row.get("canonical_subject_name") and not row.get("subject_mapping_note")]
    if missing_canonical:
        issues.append(build_issue("error", "package_subject_canonical_missing", "subjects.json rows must include canonical_subject_name.", len(missing_canonical), limited(args, missing_canonical)))
    if missing_notes:
        issues.append(build_issue("warning", "package_subject_mapping_note_missing", "Subject mappings should preserve historical-name explanation notes.", len(missing_notes), limited(args, missing_notes)))


def duplicate_content_report(args: argparse.Namespace, questions: list[dict[str, Any]], issues: list[dict[str, Any]]) -> None:
    by_hash: dict[str, list[str]] = defaultdict(list)
    for question in questions:
        metadata = question.get("metadata") if isinstance(question.get("metadata"), dict) else {}
        content_hash = str(metadata.get("source_content_hash") or "")
        if content_hash:
            by_hash[content_hash].append(str(question.get("source_question_key") or ""))
    duplicates = [
        {"source_content_hash": content_hash, "source_question_keys": keys, "question_count": len(keys)}
        for content_hash, keys in by_hash.items()
        if len(keys) > 1
    ]
    duplicates.sort(key=lambda item: (-item["question_count"], item["source_content_hash"]))
    if duplicates:
        issues.append(build_issue("warning", "package_duplicate_content_hash", "Duplicate content hashes should be imported as duplicate/reused lineage, not silently collapsed.", len(duplicates), limited(args, duplicates)))


def render_text(report: dict[str, Any]) -> str:
    lines = [
        f"Question-bank package validation: {report['status']}",
        f"package_dir: {report['package_dir']}",
        "summary:",
    ]
    for key, value in sorted(report.get("summary", {}).items()):
        lines.append(f"  - {key}: {value}")
    if report["issues"]:
        lines.append("issues:")
        for issue in report["issues"]:
            lines.append(f"  - [{issue['severity']}] {issue['code']}: {issue['count']} - {issue['message']}")
            for example in issue.get("examples", [])[:5]:
                lines.append(f"      {json.dumps(example, ensure_ascii=False, sort_keys=True)}")
            if len(issue.get("examples", [])) > 5:
                lines.append("      ...")
    else:
        lines.append("issues: none")
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    package_dir = args.package_dir.resolve()
    issues: list[dict[str, Any]] = []
    if not package_dir.is_dir():
        raise SystemExit(f"Package directory not found: {package_dir}")
    manifest, subjects, questions, groups, assets = validate_files(args, package_dir, issues)
    if manifest:
        validate_manifest_counts(args, package_dir, manifest, subjects, questions, groups, assets, issues)
        validate_questions(args, questions, groups, assets, issues)
        validate_groups(args, questions, groups, issues)
        validate_assets(args, package_dir, assets, issues)
        validate_subjects(args, subjects, issues)
        duplicate_content_report(args, questions, issues)
    error_count = sum(1 for issue in issues if issue["severity"] == "error" and issue["count"] > 0)
    warning_count = sum(1 for issue in issues if issue["severity"] == "warning" and issue["count"] > 0)
    summary = {
        "questions": len(questions),
        "groups": len(groups),
        "asset_manifest_rows": len(assets),
        "package_version": manifest.get("package_version") if isinstance(manifest, dict) else None,
    }
    report = {
        "status": "fail" if error_count else "pass",
        "package_dir": str(package_dir),
        "summary": summary,
        "error_count": error_count,
        "warning_count": warning_count,
        "issues": issues,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n" if args.format == "json" else render_text(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if error_count:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
