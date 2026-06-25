#!/usr/bin/env python3
"""
Download MOEX PDFs from official category-track rows.

Unlike the category-name wrapper, this keeps the official catalog hierarchy as
the filter boundary:
exam_level + category_label + category_code + category_name.
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
DEFAULT_TRACK_LIST = PROJECT_ROOT / "catalogs" / "moex_official_category_track_summary__y100-115.csv"
DEFAULT_ASSET_ROOT = PROJECT_ROOT / "國考題資料夾_官方考科拓展"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track-list", type=Path, default=DEFAULT_TRACK_LIST)
    parser.add_argument("--catalog", type=Path, default=downloader.DEFAULT_CATALOG)
    parser.add_argument("--asset-root", type=Path, default=DEFAULT_ASSET_ROOT)
    parser.add_argument("--working-scope", action="append", help="Filter working_scope. Repeatable.")
    parser.add_argument("--exclude-working-scope", action="append", default=[])
    parser.add_argument("--year-start", type=int, default=115)
    parser.add_argument("--year-end", type=int, default=100)
    parser.add_argument("--track-limit", type=int, default=0)
    parser.add_argument("--document-limit-per-track", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def read_tracks(args: argparse.Namespace) -> list[dict[str, str]]:
    include = set(args.working_scope or [])
    exclude = set(args.exclude_working_scope or [])
    with args.track_list.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if include:
        rows = [row for row in rows if row["working_scope"] in include]
    if exclude:
        rows = [row for row in rows if row["working_scope"] not in exclude]
    rows.sort(
        key=lambda row: (
            row["working_scope"],
            row["exam_level"],
            row["category_label"],
            row["category_code"],
            row["category_name"],
        )
    )
    if args.track_limit:
        rows = rows[: args.track_limit]
    return rows


def main() -> int:
    args = parse_args()
    tracks = read_tracks(args)
    asset_root = args.asset_root
    manifest_dir = asset_root / "Registry" / "asset_manifests"
    log_dir = asset_root / "Registry" / "processing_logs"
    output_root = asset_root / "10_official_pdf" / "by_official_catalog"
    run_log = log_dir / f"official_track_download__{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"
    run_log.parent.mkdir(parents=True, exist_ok=True)

    print(f"tracks: {len(tracks)}")
    print(f"asset_root: {asset_root}")
    print(f"run_log: {run_log}")

    completed = 0
    for track in tracks:
        manifest_label = "__".join(
            [
                track["working_scope"],
                track["exam_level"] or "no_exam_level",
                track["category_code"] or "no_category_code",
                track["category_name"] or "no_category_name",
            ]
        )
        item_args = Namespace(
            catalog=args.catalog,
            category=track["category_name"],
            exam_level=track["exam_level"],
            category_label=track["category_label"],
            category_code=track["category_code"],
            manifest_label=manifest_label,
            path_mode="official-track",
            year_start=args.year_start,
            year_end=args.year_end,
            output_root=output_root,
            manifest_dir=manifest_dir,
            log_dir=log_dir,
            limit=args.document_limit_per_track,
            sleep=args.sleep,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
        )
        record = {
            "official_category_track_id": track["official_category_track_id"],
            "working_scope": track["working_scope"],
            "exam_level": track["exam_level"],
            "category_label": track["category_label"],
            "category_code": track["category_code"],
            "category_name": track["category_name"],
            "status": "started",
        }
        run_log.open("a", encoding="utf-8").write(json.dumps(record, ensure_ascii=False) + "\n")
        try:
            downloader.run(item_args)
        except Exception as exc:
            record = {**record, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
            run_log.open("a", encoding="utf-8").write(json.dumps(record, ensure_ascii=False) + "\n")
            print(json.dumps(record, ensure_ascii=False), flush=True)
            continue
        completed += 1
        record = {**record, "status": "ok", "completed": completed, "total": len(tracks)}
        run_log.open("a", encoding="utf-8").write(json.dumps(record, ensure_ascii=False) + "\n")
        print(json.dumps(record, ensure_ascii=False), flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
