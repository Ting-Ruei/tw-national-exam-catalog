#!/usr/bin/env python3
"""
Download MOEX PDFs for a list of official categories into a separate asset root.

This is a thin batch wrapper around download_moex_pdfs_from_catalog.py. It keeps
the original catalog-driven naming and manifests, but lets non-medical or other
category sets live outside the main medical asset tree.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from argparse import Namespace
from pathlib import Path

import download_moex_pdfs_from_catalog as downloader


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATEGORY_LIST = PROJECT_ROOT / "catalogs" / "other_professional_high_categories_excluding_locked27__y100-115.csv"
DEFAULT_ASSET_ROOT = PROJECT_ROOT / "國考題資料夾_其他類型"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category-list", type=Path, default=DEFAULT_CATEGORY_LIST)
    parser.add_argument("--category-field", default="category_name")
    parser.add_argument("--catalog", type=Path, default=downloader.DEFAULT_CATALOG)
    parser.add_argument("--asset-root", type=Path, default=DEFAULT_ASSET_ROOT)
    parser.add_argument("--year-start", type=int, default=115)
    parser.add_argument("--year-end", type=int, default=100)
    parser.add_argument("--category-limit", type=int, default=0)
    parser.add_argument("--document-limit-per-category", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def normalized_fieldnames(reader: csv.DictReader) -> dict[str, str]:
    return {(name or "").lstrip("\ufeff"): name for name in (reader.fieldnames or [])}


def read_categories(path: Path, field: str) -> list[str]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = normalized_fieldnames(reader)
        source_field = fields.get(field)
        if not source_field:
            raise SystemExit(f"Category field not found: {field}")
        categories = []
        for row in reader:
            value = (row.get(source_field) or "").strip()
            if value:
                categories.append(value)
    return categories


def main() -> int:
    args = parse_args()
    categories = read_categories(args.category_list, args.category_field)
    if args.category_limit:
        categories = categories[: args.category_limit]

    asset_root = args.asset_root
    manifest_dir = asset_root / "Registry" / "asset_manifests"
    log_dir = asset_root / "Registry" / "processing_logs"
    output_root = asset_root / "10_official_pdf" / "by_official_catalog"
    run_log = log_dir / f"category_list_download__{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"
    run_log.parent.mkdir(parents=True, exist_ok=True)

    print(f"categories: {len(categories)}")
    print(f"asset_root: {asset_root}")
    print(f"run_log: {run_log}")

    completed = 0
    for category in categories:
        item_args = Namespace(
            catalog=args.catalog,
            category=category,
            exam_level="",
            category_label="",
            category_code="",
            manifest_label=category,
            path_mode="category",
            year_start=args.year_start,
            year_end=args.year_end,
            output_root=output_root,
            manifest_dir=manifest_dir,
            log_dir=log_dir,
            limit=args.document_limit_per_category,
            sleep=args.sleep,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
        )
        record = {"category": category, "status": "started"}
        run_log.open("a", encoding="utf-8").write(json.dumps(record, ensure_ascii=False) + "\n")
        try:
            downloader.run(item_args)
        except Exception as exc:
            record = {"category": category, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
            run_log.open("a", encoding="utf-8").write(json.dumps(record, ensure_ascii=False) + "\n")
            print(json.dumps(record, ensure_ascii=False), flush=True)
            continue
        completed += 1
        record = {"category": category, "status": "ok", "completed": completed, "total": len(categories)}
        run_log.open("a", encoding="utf-8").write(json.dumps(record, ensure_ascii=False) + "\n")
        print(json.dumps(record, ensure_ascii=False), flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
