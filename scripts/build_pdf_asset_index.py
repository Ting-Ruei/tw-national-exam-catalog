#!/usr/bin/env python3
"""
Build reviewable CSV indexes for already-downloaded official PDF assets.

This script does not write to any database. It reads the latest download
manifest for each official category and produces CSV indexes that can be
reviewed before PostgreSQL / SQLite / Parquet ingestion.
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
DEFAULT_ASSET_ROOT = PROJECT_ROOT / "國考題資料夾"
DEFAULT_MANIFEST_DIR = DEFAULT_ASSET_ROOT / "Registry" / "asset_manifests"
DEFAULT_OUTPUT_DIR = DEFAULT_ASSET_ROOT / "Registry" / "pdf_indexes"
DEFAULT_LOCKED27 = PROJECT_ROOT / "catalogs" / "locked_27_canonical_category_names.csv"


GROUP_OVERRIDES = {
    "牙醫師": "牙醫師",
    "牙醫師（一）": "牙醫師",
    "牙醫師（二）": "牙醫師",
    "醫師(一)": "醫師",
    "醫師(二)": "醫師",
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
}

TRANSITION_NOTES = {
    "牙醫師": "早期未分階段官方類科；與分階段牙醫師一起保留",
    "牙醫師（一）": "官方全形括號過渡名稱；檔名與資料夾使用半形括號",
    "牙醫師（二）": "官方全形括號過渡名稱；檔名與資料夾使用半形括號",
    "藥師": "藥師四年制到六年制交叉期；未分階段與分階段全部保留",
    "藥師（一）": "官方全形括號過渡名稱；檔名與資料夾使用半形括號",
    "藥師（二）": "官方全形括號過渡名稱；檔名與資料夾使用半形括號",
    "中醫師": "早期未分階段官方類科；與分階段中醫師一起保留",
}

EXAM_CODE_NOTES = {
    "106111": "花東考區補辦考試試題",
}


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


def read_locked27(path: Path) -> set[str]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return {row["canonical_category_name"] for row in csv.DictReader(f)}


def parse_manifest_filename(path: Path) -> tuple[str, str] | None:
    match = re.match(r"moex_pdf_download__(.+)__y\d+-\d+__(\d{8}-\d{6})\.csv$", path.name)
    if not match:
        return None
    return match.group(1), match.group(2)


def latest_manifests(manifest_dir: Path) -> list[ManifestChoice]:
    choices: dict[str, ManifestChoice] = {}
    for path in manifest_dir.glob("moex_pdf_download__*.csv"):
        parsed = parse_manifest_filename(path)
        if not parsed:
            continue
        category, timestamp = parsed
        current = choices.get(category)
        if current is None or timestamp > current.timestamp:
            choices[category] = ManifestChoice(category=category, path=path, timestamp=timestamp)
    return [choices[k] for k in sorted(choices)]


def relative_to_asset_root(path_text: str, asset_root: Path) -> str:
    path = Path(path_text)
    try:
        return str(path.relative_to(asset_root))
    except ValueError:
        return path_text


def has_collision_suffix(path_text: str) -> bool:
    return bool(re.search(r"_E\d{5,6}(?:_ANS|_MOD)?\.pdf$", Path(path_text).name))


def build_indexes(args: argparse.Namespace) -> tuple[Path, Path]:
    locked27 = read_locked27(args.locked27)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    detail_path = args.output_dir / f"pdf_asset_index_detail__{stamp}.csv"
    summary_path = args.output_dir / f"pdf_asset_index_group_summary__{stamp}.csv"

    detail_fields = [
        "group_name",
        "official_category_name",
        "normalized_category_name",
        "is_locked27",
        "manifest_timestamp",
        "manifest_path",
        "status",
        "year",
        "exam_ordinal",
        "exam_code",
        "category_code",
        "subject_code",
        "official_subject_name",
        "normalized_subject_name",
        "document_role",
        "source_url",
        "asset_path",
        "relative_asset_path",
        "bytes",
        "sha256",
        "registry_key",
        "has_collision_suffix",
        "notes",
    ]

    summary: dict[str, Counter] = defaultdict(Counter)
    group_categories: dict[str, set[str]] = defaultdict(set)
    years_by_group: dict[str, set[int]] = defaultdict(set)

    with detail_path.open("w", newline="", encoding="utf-8-sig") as detail_file:
        writer = csv.DictWriter(detail_file, fieldnames=detail_fields)
        writer.writeheader()
        for choice in latest_manifests(args.manifest_dir):
            with choice.path.open(newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    category = row["category_name"]
                    gname = group_name(category)
                    status_family = row["status"].split(":", 1)[0]
                    collision = has_collision_suffix(row["destination"])
                    notes = []
                    if category in TRANSITION_NOTES:
                        notes.append(TRANSITION_NOTES[category])
                    if row["exam_code"] in EXAM_CODE_NOTES:
                        notes.append(EXAM_CODE_NOTES[row["exam_code"]])
                    if collision:
                        notes.append("同年同次序命名撞名，使用 _E{exam_code} 保留不同官方文件")
                    if "（" in category or "）" in category:
                        notes.append("官方 raw name 含全形括號；檔名與資料夾已半形化")

                    writer.writerow(
                        {
                            "group_name": gname,
                            "official_category_name": category,
                            "normalized_category_name": normalize_name(category),
                            "is_locked27": "yes" if category in locked27 else "no",
                            "manifest_timestamp": choice.timestamp,
                            "manifest_path": str(choice.path),
                            "status": row["status"],
                            "year": row["year"],
                            "exam_ordinal": row["exam_ordinal"],
                            "exam_code": row["exam_code"],
                            "category_code": row["category_code"],
                            "subject_code": row["subject_code"],
                            "official_subject_name": row["subject_name"],
                            "normalized_subject_name": normalize_name(row["subject_name"]),
                            "document_role": row["document_role"],
                            "source_url": row["source_url"],
                            "asset_path": row["destination"],
                            "relative_asset_path": relative_to_asset_root(row["destination"], args.asset_root),
                            "bytes": row["bytes"],
                            "sha256": row["sha256"],
                            "registry_key": row["registry_key"],
                            "has_collision_suffix": "yes" if collision else "no",
                            "notes": " | ".join(notes),
                        }
                    )
                    summary[gname]["documents"] += 1
                    summary[gname][f"role:{row['document_role']}"] += 1
                    summary[gname][f"status:{status_family}"] += 1
                    if collision:
                        summary[gname]["collision_suffix_documents"] += 1
                    group_categories[gname].add(category)
                    years_by_group[gname].add(int(row["year"]))

    summary_fields = [
        "group_name",
        "official_category_names",
        "year_min",
        "year_max",
        "documents",
        "question_documents",
        "answer_documents",
        "correction_documents",
        "downloaded",
        "exists",
        "errors",
        "collision_suffix_documents",
    ]
    with summary_path.open("w", newline="", encoding="utf-8-sig") as summary_file:
        writer = csv.DictWriter(summary_file, fieldnames=summary_fields)
        writer.writeheader()
        for gname in sorted(summary):
            counts = summary[gname]
            years = sorted(years_by_group[gname])
            writer.writerow(
                {
                    "group_name": gname,
                    "official_category_names": "; ".join(sorted(group_categories[gname])),
                    "year_min": years[0] if years else "",
                    "year_max": years[-1] if years else "",
                    "documents": counts["documents"],
                    "question_documents": counts["role:question"],
                    "answer_documents": counts["role:answer"],
                    "correction_documents": counts["role:correction"],
                    "downloaded": counts["status:downloaded"],
                    "exists": counts["status:exists"],
                    "errors": sum(v for k, v in counts.items() if k.startswith("status:error")),
                    "collision_suffix_documents": counts["collision_suffix_documents"],
                }
            )
    return detail_path, summary_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-root", type=Path, default=DEFAULT_ASSET_ROOT)
    parser.add_argument("--manifest-dir", type=Path, default=DEFAULT_MANIFEST_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--locked27", type=Path, default=DEFAULT_LOCKED27)
    return parser.parse_args()


def main() -> int:
    detail_path, summary_path = build_indexes(parse_args())
    print(f"detail index: {detail_path}")
    print(f"group summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
