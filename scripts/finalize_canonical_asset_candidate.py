#!/usr/bin/env python3
"""Regenerate the approved canonical-candidate omissions and seal the result.

This tool is intentionally narrow: it accepts only the two AI395 MinerU latest
pointers and the catalog-derived subject-variant reports currently produced by
the canonical conflict-resolution workflow.  It verifies the complete initial
candidate before writing, stages every regenerated file outside the candidate,
atomically installs exactly the queued paths, and emits a fresh SHA-256 physical
manifest plus a promotion gate report.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

import build_migration_asset_manifest as asset_manifest
import download_moex_pdfs_from_catalog as moex_catalog
import report_mineru_status as mineru_status


QUEUE_FIELDS = (
    "logical_relative_path",
    "resolution",
    "canonical_action",
    "authority",
    "rebuild_source",
    "decision_id",
)
MINERU_LATEST_PATHS = {
    "Registry/mineru_runs/status_snapshots/mineru_status__latest.json",
    "Registry/mineru_runs/status_snapshots/mineru_status__latest.txt",
}
SUBJECT_REPORT_PREFIX = "Registry/processing_logs/subject_name_variants__"
SUBJECT_REPORT_SUFFIX = "__y100-115.md"


class FinalizationError(RuntimeError):
    """A fail-closed canonical finalization error."""


def sha256_file(path: Path) -> str:
    return asset_manifest.sha256_file(path)


def require_sha256(path: Path, expected: str, label: str) -> str:
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise FinalizationError(f"{label} expected SHA-256 is invalid")
    actual = sha256_file(path)
    if actual != expected:
        raise FinalizationError(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")
    return actual


def safe_relative(raw: str) -> PurePosixPath:
    relative = PurePosixPath(raw)
    if not raw or raw.startswith("/") or relative.as_posix() != raw:
        raise FinalizationError(f"unsafe regeneration path: {raw!r}")
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise FinalizationError(f"unsafe regeneration path: {raw!r}")
    return relative


def load_queue(path: Path, expected_hash: str) -> list[dict[str, str]]:
    require_sha256(path, expected_hash, "regeneration queue")
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [field for field in QUEUE_FIELDS if field not in (reader.fieldnames or [])]
        if missing:
            raise FinalizationError(f"regeneration queue is missing fields: {missing}")
        rows = list(reader)
    if not rows:
        raise FinalizationError("regeneration queue is empty; no finalization is needed")
    paths: set[str] = set()
    for row in rows:
        relative = safe_relative(row["logical_relative_path"]).as_posix()
        if relative in paths:
            raise FinalizationError(f"duplicate regeneration path: {relative}")
        paths.add(relative)
        expected_pair = (row["resolution"], row["canonical_action"])
        if expected_pair not in {
            ("regenerate_on_ai395", "omit_then_regenerate"),
            ("regenerate_from_final_catalog", "omit_then_regenerate"),
        }:
            raise FinalizationError(f"unsupported regeneration resolution/action: {relative}")
        if relative in MINERU_LATEST_PATHS:
            if row["rebuild_source"] != "scripts/report_mineru_status.py":
                raise FinalizationError(f"unexpected MinerU rebuild source: {relative}")
            continue
        if relative.startswith(SUBJECT_REPORT_PREFIX) and relative.endswith(SUBJECT_REPORT_SUFFIX):
            if row["rebuild_source"] != "scripts/download_moex_pdfs_from_catalog.py":
                raise FinalizationError(f"unexpected subject-report rebuild source: {relative}")
            continue
        raise FinalizationError(f"unsupported regeneration path: {relative}")
    if paths & MINERU_LATEST_PATHS and not MINERU_LATEST_PATHS.issubset(paths):
        raise FinalizationError("MinerU latest JSON and text pointers must be regenerated together")
    return rows


def load_manifest_paths(path: Path) -> set[str]:
    result: set[str] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [field for field in asset_manifest.FIELDS if field not in (reader.fieldnames or [])]
        if missing:
            raise FinalizationError(f"initial physical manifest is missing fields: {missing}")
        for row in reader:
            if row["root_label"] != "canonical-main":
                raise FinalizationError(f"unexpected initial manifest root label: {row['root_label']}")
            relative = safe_relative(row["relative_path"]).as_posix()
            if relative in result:
                raise FinalizationError(f"duplicate initial manifest path: {relative}")
            result.add(relative)
    if not result:
        raise FinalizationError("initial physical manifest is empty")
    return result


def verify_initial_candidate(candidate_root: Path, manifest_path: Path, queue_paths: set[str]) -> set[str]:
    expected_paths = load_manifest_paths(manifest_path)
    mismatches: list[str] = []
    checked = 0
    with manifest_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            mismatch = asset_manifest.verify_row(
                row,
                {"canonical-main": candidate_root},
                check_mtime=False,
            )
            if mismatch:
                mismatches.append(mismatch)
            checked += 1
            if checked % 1000 == 0:
                print(f"initial candidate verification: {checked:,} entries", flush=True)
    actual_paths = {
        str(row["relative_path"])
        for row in asset_manifest.iter_entries("canonical-main", candidate_root, with_sha256=False)
    }
    extras = sorted(actual_paths - expected_paths)
    missing = sorted(expected_paths - actual_paths)
    queued_present = sorted(queue_paths & actual_paths)
    if mismatches or extras or missing or queued_present:
        raise FinalizationError(
            "initial candidate does not match its bound manifest; "
            f"mismatches={mismatches[:3]}, extras={extras[:3]}, "
            f"missing={missing[:3]}, queued_present={queued_present[:3]}"
        )
    return expected_paths


def category_from_report_path(relative: str) -> str:
    return relative.removeprefix(SUBJECT_REPORT_PREFIX).removesuffix(SUBJECT_REPORT_SUFFIX)


def stage_regenerated_files(
    staging_root: Path,
    candidate_root: Path,
    queue_rows: list[dict[str, str]],
    catalog_path: Path,
) -> None:
    catalog_rows = moex_catalog.read_catalog(catalog_path)
    queue_paths = {row["logical_relative_path"] for row in queue_rows}
    for relative in sorted(queue_paths - MINERU_LATEST_PATHS):
        category = category_from_report_path(relative)
        rows = moex_catalog.filtered_rows(catalog_rows, category, 115, 100)
        if not rows:
            raise FinalizationError(f"final catalog has no rows for queued category: {category}")
        output = moex_catalog.write_subject_variant_report(
            rows,
            staging_root / "Registry" / "processing_logs",
            category,
            115,
            100,
        )
        expected = staging_root.joinpath(*safe_relative(relative).parts)
        if output != expected:
            raise FinalizationError(f"subject report generator returned an unexpected path: {output}")

    if MINERU_LATEST_PATHS.issubset(queue_paths):
        mineru_status.configure_asset_root(candidate_root)
        report = mineru_status.build_report()
        status_root = staging_root / "Registry" / "mineru_runs" / "status_snapshots"
        status_root.mkdir(parents=True, exist_ok=True)
        (status_root / "mineru_status__latest.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (status_root / "mineru_status__latest.txt").write_text(
            mineru_status.format_text(report),
            encoding="utf-8",
        )

    staged_paths = {
        str(row["relative_path"])
        for row in asset_manifest.iter_entries("staged", staging_root, with_sha256=False)
    }
    if staged_paths != queue_paths:
        raise FinalizationError(
            f"staged regeneration path set mismatch; missing={sorted(queue_paths - staged_paths)}, "
            f"extra={sorted(staged_paths - queue_paths)}"
        )


def atomic_install(staging_root: Path, candidate_root: Path, queue_paths: set[str]) -> None:
    for relative in sorted(queue_paths):
        source = staging_root.joinpath(*safe_relative(relative).parts)
        destination = candidate_root.joinpath(*safe_relative(relative).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() or destination.is_symlink():
            raise FinalizationError(f"queued destination already exists: {relative}")
        temporary = destination.with_name(f".{destination.name}.regen-{os.getpid()}")
        try:
            temporary.write_bytes(source.read_bytes())
            if sha256_file(temporary) != sha256_file(source):
                raise FinalizationError(f"staged copy verification failed: {relative}")
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()


def write_manifest(path: Path, candidate_root: Path) -> tuple[int, int, str]:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    count = 0
    regular_files = 0
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=asset_manifest.FIELDS, lineterminator="\n")
            writer.writeheader()
            for row in asset_manifest.iter_entries("canonical-main", candidate_root, with_sha256=True):
                writer.writerow(row)
                count += 1
                regular_files += row["entry_type"] == "file"
                if count % 1000 == 0:
                    print(f"final candidate manifest: {count:,} entries", flush=True)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return count, regular_files, sha256_file(path)


def write_report(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def run(args: argparse.Namespace) -> int:
    candidate_root = args.candidate_root.expanduser().resolve()
    queue_path = args.regeneration_queue.expanduser().resolve()
    initial_manifest = args.initial_physical_manifest.expanduser().resolve()
    catalog_path = args.catalog.expanduser().resolve()
    report_dir = args.report_dir.expanduser().resolve()
    if not candidate_root.is_dir() or candidate_root.is_symlink():
        raise FinalizationError(f"candidate root must be a real directory: {candidate_root}")
    for required in (queue_path, initial_manifest, catalog_path):
        if not required.is_file():
            raise FinalizationError(f"required evidence file is missing: {required}")
    if report_dir.exists():
        raise FinalizationError(f"report directory already exists; use a new path: {report_dir}")
    report_dir.mkdir(parents=True)

    report_path = report_dir / "canonical-finalization-report.json"
    report: dict[str, Any] = {
        "schema_version": "tw_exam_canonical_finalization_v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "state": "verifying_initial_candidate",
        "ready_for_promotion": False,
        "candidate_root": str(candidate_root),
        "evidence": {
            "regeneration_queue": str(queue_path),
            "regeneration_queue_sha256": args.expect_regeneration_queue_sha256,
            "initial_physical_manifest": str(initial_manifest),
            "initial_physical_manifest_sha256": args.expect_initial_physical_manifest_sha256,
            "catalog": str(catalog_path),
            "catalog_sha256": sha256_file(catalog_path),
        },
    }
    write_report(report_path, report)
    try:
        require_sha256(
            initial_manifest,
            args.expect_initial_physical_manifest_sha256,
            "initial physical manifest",
        )
        queue_rows = load_queue(queue_path, args.expect_regeneration_queue_sha256)
        queue_paths = {row["logical_relative_path"] for row in queue_rows}
        initial_paths = verify_initial_candidate(candidate_root, initial_manifest, queue_paths)
        report["state"] = "regenerating"
        report["counts"] = {
            "initial_entries": len(initial_paths),
            "regeneration_queue": len(queue_paths),
        }
        write_report(report_path, report)
        with tempfile.TemporaryDirectory(prefix="canonical-regeneration-", dir=report_dir) as temporary_dir:
            staging_root = Path(temporary_dir)
            stage_regenerated_files(staging_root, candidate_root, queue_rows, catalog_path)
            atomic_install(staging_root, candidate_root, queue_paths)

        final_manifest = report_dir / "canonical-final-physical-manifest.csv"
        entry_count, regular_count, manifest_hash = write_manifest(final_manifest, candidate_root)
        final_paths = {
            str(row["relative_path"])
            for row in asset_manifest.iter_entries("canonical-main", candidate_root, with_sha256=False)
        }
        if final_paths != initial_paths | queue_paths:
            raise FinalizationError("final candidate path set is not initial manifest plus regeneration queue")
        report["state"] = "candidate_finalized"
        report["ready_for_promotion"] = True
        report["counts"].update(
            {
                "final_entries": entry_count,
                "final_regular_files": regular_count,
                "final_symlinks": entry_count - regular_count,
                "remaining_regeneration_items": 0,
            }
        )
        report["evidence"].update(
            {
                "final_physical_manifest": str(final_manifest),
                "final_physical_manifest_sha256": manifest_hash,
            }
        )
        report["gates"] = {
            "initial_manifest_hash_matches": True,
            "initial_candidate_verified": True,
            "queue_hash_matches": True,
            "only_supported_generators_used": True,
            "regeneration_queue_empty": True,
            "final_candidate_manifested": True,
        }
        write_report(report_path, report)
    except Exception as exc:
        report["state"] = "finalization_failed"
        report["ready_for_promotion"] = False
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        write_report(report_path, report)
        raise

    print(f"state: {report['state']}")
    print(f"regenerated: {report['counts']['regeneration_queue']}")
    print(f"final manifest SHA-256: {report['evidence']['final_physical_manifest_sha256']}")
    print(f"report: {report_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--regeneration-queue", type=Path, required=True)
    parser.add_argument("--expect-regeneration-queue-sha256", required=True)
    parser.add_argument("--initial-physical-manifest", type=Path, required=True)
    parser.add_argument("--expect-initial-physical-manifest-sha256", required=True)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "catalogs" / "moex_subject_catalog__y100-115.csv",
    )
    parser.add_argument("--report-dir", type=Path, required=True)
    return parser


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except (FinalizationError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
