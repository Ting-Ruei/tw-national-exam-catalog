#!/usr/bin/env python3
"""
Build CSV progress reports for the locked 27 categories and their official variants.

This script reads the latest download manifest for each official category, compares
it with the source catalog, and emits reviewable progress CSVs without writing to a
database.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = PROJECT_ROOT / "catalogs" / "moex_subject_catalog__y100-115.csv"
DEFAULT_LOCKED27 = PROJECT_ROOT / "catalogs" / "locked_27_canonical_category_names.csv"
DEFAULT_ASSET_ROOT = PROJECT_ROOT / "國考題資料夾"
DEFAULT_MANIFEST_DIR = DEFAULT_ASSET_ROOT / "Registry" / "asset_manifests"
DEFAULT_LOG_DIR = DEFAULT_ASSET_ROOT / "Registry" / "processing_logs"
DEFAULT_OUTPUT_DIR = DEFAULT_ASSET_ROOT / "Registry" / "processing_logs"


GROUP_OVERRIDES = {
    "牙醫師": "牙醫師",
    "牙醫師（一）": "牙醫師",
    "牙醫師（二）": "牙醫師",
    "牙醫師(一)": "牙醫師",
    "牙醫師(二)": "牙醫師",
    "藥師": "藥師",
    "藥師（一）": "藥師",
    "藥師（二）": "藥師",
    "藥師(一)": "藥師",
    "藥師(二)": "藥師",
    "中醫師": "中醫師",
    "中醫師(一)": "中醫師",
    "中醫師(二)": "中醫師",
    "醫師(一)": "醫師",
    "醫師(二)": "醫師",
}

TRANSITION_NOTES = {
    "牙醫師": "early unsplit official category; keep with staged exams",
    "牙醫師（一）": "official transition variant; keep all documents | official full-width parentheses; folder and filenames normalized to half-width parentheses",
    "牙醫師（二）": "official transition variant; keep all documents | official full-width parentheses; folder and filenames normalized to half-width parentheses",
    "藥師": "official transition variant; keep all documents | 4-year to 6-year pharmacy transition / overlap; keep unsplit and staged exams",
    "藥師（一）": "official transition variant; keep all documents | 4-year to 6-year pharmacy transition / overlap; keep unsplit and staged exams | official full-width parentheses; folder and filenames normalized to half-width parentheses",
    "藥師（二）": "official transition variant; keep all documents | 4-year to 6-year pharmacy transition / overlap; keep unsplit and staged exams | official full-width parentheses; folder and filenames normalized to half-width parentheses",
    "中醫師": "official transition variant; keep all documents | early unsplit official category; keep with staged exams",
}


@dataclass(frozen=True)
class CatalogSummary:
    group_name: str
    category_name: str
    normalized_folder_name: str
    catalog_year_min: int
    catalog_year_max: int
    catalog_sessions: int
    catalog_rows: int
    expected_documents: int


@dataclass(frozen=True)
class ManifestChoice:
    category: str
    path: Path
    timestamp: str


def normalize_name(value: str) -> str:
    value = value.replace("（", "(").replace("）", ")")
    value = re.sub(r"\s+", "", value)
    return value


def group_name(category: str) -> str:
    return GROUP_OVERRIDES.get(category, normalize_name(category))


def parse_manifest_filename(path: Path) -> tuple[str, str] | None:
    match = re.match(r"moex_pdf_download__(.+)__y\d+-\d+__(\d{8}-\d{6})\.csv$", path.name)
    if not match:
        return None
    return match.group(1), match.group(2)


def latest_manifests(manifest_dir: Path) -> dict[str, ManifestChoice]:
    choices: dict[str, ManifestChoice] = {}
    for path in manifest_dir.glob("moex_pdf_download__*.csv"):
        parsed = parse_manifest_filename(path)
        if not parsed:
            continue
        category, timestamp = parsed
        current = choices.get(category)
        if current is None or timestamp > current.timestamp:
            choices[category] = ManifestChoice(category=category, path=path, timestamp=timestamp)
    return choices


def read_locked27(path: Path) -> set[str]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return {row["canonical_category_name"] for row in csv.DictReader(f)}


def read_catalog_summaries(path: Path, locked27: set[str]) -> dict[str, CatalogSummary]:
    rows_by_category: dict[str, list[dict[str, str]]] = defaultdict(list)
    locked_groups = {group_name(name) for name in locked27}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            category = row["category_name"]
            if group_name(category) not in locked_groups:
                continue
            rows_by_category[category].append(row)

    summaries: dict[str, CatalogSummary] = {}
    for category, rows in rows_by_category.items():
        years = sorted({int(r["year"]) for r in rows})
        exam_codes = {r["exam_code"] for r in rows}
        expected_documents = 0
        for row in rows:
            expected_documents += int(bool(row["question_url"]))
            expected_documents += int(bool(row["answer_url"]))
            expected_documents += int(bool(row["correction_url"]))
        summaries[category] = CatalogSummary(
            group_name=group_name(category),
            category_name=category,
            normalized_folder_name=normalize_name(category),
            catalog_year_min=years[0],
            catalog_year_max=years[-1],
            catalog_sessions=len(exam_codes),
            catalog_rows=len(rows),
            expected_documents=expected_documents,
        )
    return summaries


def summarize_manifest(path: Path) -> dict[str, object]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    status_counts = Counter()
    role_counts = Counter()
    destinations = Counter()
    errors = 0
    for row in rows:
        status_counts[row["status"].split(":", 1)[0]] += 1
        role_counts[row["document_role"]] += 1
        destinations[row["destination"]] += 1
        if row["status"].startswith("error:"):
            errors += 1

    duplicate_destinations = sum(1 for count in destinations.values() if count > 1)
    return {
        "rows": rows,
        "row_count": len(rows),
        "status_summary": ";".join(f"{k}:{status_counts[k]}" for k in sorted(status_counts)),
        "role_summary": ";".join(f"{k}:{role_counts[k]}" for k in sorted(role_counts)),
        "errors": errors,
        "duplicate_destinations": duplicate_destinations,
    }


def count_folder_pdfs(asset_root: Path, folder_name: str) -> tuple[int, int]:
    folder = asset_root / "10_official_pdf" / "by_official_catalog" / folder_name
    if not folder.exists():
        return 0, 0
    pdfs = list(folder.rglob("*.pdf"))
    zero_byte = sum(1 for path in pdfs if path.stat().st_size == 0)
    return len(pdfs), zero_byte


def write_detail_report(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_reports(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    locked27 = read_locked27(args.locked27)
    catalog_summaries = read_catalog_summaries(args.catalog, locked27)
    manifest_choices = latest_manifests(args.manifest_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d")

    detail_path = args.output_dir / f"download_progress_detail__locked27_and_current_variants__{stamp}.csv"
    legacy_path = args.output_dir / f"download_progress__locked27_and_current_variants__{stamp}.csv"
    summary_path = args.output_dir / f"download_progress_group_summary__{stamp}.csv"

    detail_fields = [
        "group_name",
        "official_category_name",
        "normalized_folder_name",
        "row_type",
        "status",
        "catalog_year_min",
        "catalog_year_max",
        "catalog_sessions",
        "catalog_rows",
        "expected_documents",
        "download_manifest_rows",
        "download_status_summary",
        "download_role_summary",
        "download_errors",
        "duplicate_destinations_in_manifest",
        "pdf_files_present_in_normalized_folder",
        "zero_byte_pdf_files",
        "latest_manifest",
        "subject_variant_report",
        "notes",
    ]

    group_rows: dict[str, list[dict[str, object]]] = defaultdict(list)
    detail_rows: list[dict[str, object]] = []
    for category in sorted(catalog_summaries):
        summary = catalog_summaries[category]
        manifest = manifest_choices.get(category)
        row_type = "locked27" if category in locked27 else "official_variant"
        folder_count, zero_byte = count_folder_pdfs(args.asset_root, summary.normalized_folder_name)
        variant_report = args.log_dir / f"subject_name_variants__{category}__y100-115.md"
        record: dict[str, object] = {
            "group_name": summary.group_name,
            "official_category_name": category,
            "normalized_folder_name": summary.normalized_folder_name,
            "row_type": row_type,
            "status": "not_started",
            "catalog_year_min": summary.catalog_year_min,
            "catalog_year_max": summary.catalog_year_max,
            "catalog_sessions": summary.catalog_sessions,
            "catalog_rows": summary.catalog_rows,
            "expected_documents": summary.expected_documents,
            "download_manifest_rows": "",
            "download_status_summary": "",
            "download_role_summary": "",
            "download_errors": 0,
            "duplicate_destinations_in_manifest": "",
            "pdf_files_present_in_normalized_folder": folder_count,
            "zero_byte_pdf_files": zero_byte,
            "latest_manifest": "",
            "subject_variant_report": str(variant_report) if variant_report.exists() else "",
            "notes": TRANSITION_NOTES.get(category, ""),
        }
        if manifest is not None:
            manifest_summary = summarize_manifest(manifest.path)
            record.update(
                {
                    "status": "done" if manifest_summary["errors"] == 0 else "partial",
                    "download_manifest_rows": manifest_summary["row_count"],
                    "download_status_summary": manifest_summary["status_summary"],
                    "download_role_summary": manifest_summary["role_summary"],
                    "download_errors": manifest_summary["errors"],
                    "duplicate_destinations_in_manifest": manifest_summary["duplicate_destinations"],
                    "latest_manifest": str(manifest.path),
                }
            )
        detail_rows.append(record)
        group_rows[summary.group_name].append(record)

    write_detail_report(detail_path, detail_fields, detail_rows)
    write_detail_report(legacy_path, detail_fields, detail_rows)

    summary_fields = [
        "group_name",
        "status",
        "official_category_names",
        "catalog_year_min",
        "catalog_year_max",
        "expected_documents",
        "download_manifest_rows",
        "download_errors",
        "notes",
    ]
    with summary_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=summary_fields)
        writer.writeheader()
        for gname in sorted(group_rows):
            rows = group_rows[gname]
            statuses = {row["status"] for row in rows}
            notes = [row["notes"] for row in rows if row["notes"]]
            writer.writerow(
                {
                    "group_name": gname,
                    "status": "done" if statuses == {"done"} else ("partial" if "done" in statuses else "not_started"),
                    "official_category_names": "; ".join(row["official_category_name"] for row in rows),
                    "catalog_year_min": min(int(row["catalog_year_min"]) for row in rows),
                    "catalog_year_max": max(int(row["catalog_year_max"]) for row in rows),
                    "expected_documents": sum(int(row["expected_documents"]) for row in rows),
                    "download_manifest_rows": sum(int(row["download_manifest_rows"] or 0) for row in rows),
                    "download_errors": sum(int(row["download_errors"]) for row in rows),
                    "notes": " | ".join(dict.fromkeys(notes)),
                }
            )

    return detail_path, legacy_path, summary_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--locked27", type=Path, default=DEFAULT_LOCKED27)
    parser.add_argument("--asset-root", type=Path, default=DEFAULT_ASSET_ROOT)
    parser.add_argument("--manifest-dir", type=Path, default=DEFAULT_MANIFEST_DIR)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    detail_path, legacy_path, summary_path = build_reports(parse_args())
    print(f"detail report: {detail_path}")
    print(f"legacy report: {legacy_path}")
    print(f"group summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
