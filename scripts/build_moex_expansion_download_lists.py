#!/usr/bin/env python3
"""
Build category-list CSVs that can feed download_moex_pdfs_from_category_list.py.

The expansion planning tables describe nodes and domains. This script turns the
node-level category summary into concrete download lists, one CSV per node plus
a combined non-locked full list.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATEGORY_SUMMARY = PROJECT_ROOT / "catalogs" / "moex_expansion_category_summary__y100-115.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "catalogs" / "expansion_download_lists"
LOCKED_NODE = "locked27_medical_current"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_category_list(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "category_name",
        "node_id",
        "node_label",
        "catalog_rows",
        "pdf_url_documents",
        "distinct_exam_codes",
        "distinct_exam_category_pairs",
        "distinct_subject_names",
        "year_min",
        "year_max",
        "top_exam_level",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build(args: argparse.Namespace) -> list[Path]:
    rows = read_csv(args.category_summary)
    node_ids = sorted({row["node_id"] for row in rows})
    written: list[Path] = []

    for node_id in node_ids:
        node_rows = [row for row in rows if row["node_id"] == node_id]
        node_rows.sort(key=lambda row: (-int(row["pdf_url_documents"]), row["category_name"]))
        path = args.output_dir / f"{node_id}__categories.csv"
        write_category_list(path, node_rows)
        written.append(path)

    nonlocked_rows = [row for row in rows if row["node_id"] != LOCKED_NODE]
    nonlocked_rows.sort(key=lambda row: (row["node_id"], -int(row["pdf_url_documents"]), row["category_name"]))
    nonlocked_path = args.output_dir / "nonlocked_full__categories.csv"
    write_category_list(nonlocked_path, nonlocked_rows)
    written.append(nonlocked_path)

    remaining_rows = [
        row
        for row in rows
        if row["node_id"] not in {LOCKED_NODE, "professional_high_other_seed"}
    ]
    remaining_rows.sort(key=lambda row: (row["node_id"], -int(row["pdf_url_documents"]), row["category_name"]))
    remaining_path = args.output_dir / "nonlocked_remaining_after_other_seed__categories.csv"
    write_category_list(remaining_path, remaining_rows)
    written.append(remaining_path)
    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category-summary", type=Path, default=DEFAULT_CATEGORY_SUMMARY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    for path in build(parse_args()):
        print(f"wrote={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
