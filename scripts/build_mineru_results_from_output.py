#!/usr/bin/env python3
"""
Build a MinerU result CSV by scanning an existing MinerU output directory.

This is useful for benchmark outputs that were produced outside
run_mineru_pdf_batch.py but should still exercise the same ingestion path.
"""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = PROJECT_ROOT / "國考題資料夾"
PDF_INDEX_DIR = ASSET_ROOT / "Registry" / "pdf_indexes"
OUTPUT_DIR = ASSET_ROOT / "Registry" / "mineru_runs" / "converted_outputs"

FIELDS = [
    "task_id",
    "status",
    "returncode",
    "elapsed_seconds",
    "md_count",
    "image_count",
    "pdf_path",
    "output_parent",
    "expected_md",
    "error_tail",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert existing MinerU outputs into a result CSV.")
    parser.add_argument("output_root", type=Path, help="Root that contains per-PDF MinerU output folders.")
    parser.add_argument("--pdf-index", type=Path, default=latest_path(PDF_INDEX_DIR, "pdf_asset_index_detail__*.csv"))
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--scope-name", default="converted-output")
    return parser.parse_args()


def latest_path(directory: Path, pattern: str) -> Path:
    paths = sorted(directory.glob(pattern))
    if not paths:
        raise SystemExit(f"No file found: {directory}/{pattern}")
    return paths[-1]


def read_pdf_index(path: Path) -> dict[str, Path]:
    by_stem: dict[str, Path] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            asset_path = Path(row["asset_path"]).resolve()
            by_stem[asset_path.stem] = asset_path
    return by_stem


def count_images(vlm_dir: Path) -> int:
    image_dir = vlm_dir / "images"
    if not image_dir.exists():
        return 0
    return sum(1 for path in image_dir.glob("*") if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"})


def build_rows(output_root: Path, pdf_by_stem: dict[str, Path], scope_name: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for md_path in sorted(output_root.glob("**/vlm/*.md")):
        stem = md_path.stem
        pdf_path = pdf_by_stem.get(stem)
        if pdf_path is None:
            rows.append(
                {
                    "task_id": f"{scope_name}:unmatched:{md_path}",
                    "status": "error",
                    "returncode": "",
                    "elapsed_seconds": "",
                    "md_count": 1,
                    "image_count": count_images(md_path.parent),
                    "pdf_path": "",
                    "output_parent": str(md_path.parent.parent.parent),
                    "expected_md": str(md_path.resolve()),
                    "error_tail": f"No official PDF stem matched: {stem}",
                }
            )
            continue
        output_parent = md_path.parent.parent.parent
        rows.append(
            {
                "task_id": f"{scope_name}:{pdf_path.relative_to(PROJECT_ROOT)}",
                "status": "ok",
                "returncode": 0,
                "elapsed_seconds": "",
                "md_count": 1,
                "image_count": count_images(md_path.parent),
                "pdf_path": str(pdf_path),
                "output_parent": str(output_parent.resolve()),
                "expected_md": str(md_path.resolve()),
                "error_tail": "",
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    output_root = args.output_root.resolve()
    if not output_root.exists():
        raise SystemExit(f"Output root not found: {output_root}")
    rows = build_rows(output_root, read_pdf_index(args.pdf_index), args.scope_name)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_path = args.output_dir / f"mineru_results__{args.scope_name}__{stamp}.csv"
    write_csv(out_path, rows)
    ok_count = sum(1 for row in rows if row["status"] == "ok")
    print(f"wrote={out_path}")
    print(f"rows={len(rows)} ok={ok_count} error={len(rows) - ok_count}")


if __name__ == "__main__":
    main()
