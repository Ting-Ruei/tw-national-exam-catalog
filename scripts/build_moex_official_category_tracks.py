#!/usr/bin/env python3
"""
Build official MOEX category-track planning tables.

Primary grouping follows the catalog's official examination structure:
exam_level -> category_label -> category_code/category_name -> subject_code/name.
This intentionally avoids free-form subject-domain classification.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = PROJECT_ROOT / "catalogs" / "moex_subject_catalog__y100-115.csv"
DEFAULT_LOCKED27 = PROJECT_ROOT / "catalogs" / "locked_27_canonical_category_names.csv"
DEFAULT_OTHER_SEED = PROJECT_ROOT / "catalogs" / "other_professional_high_categories_excluding_locked27__y100-115.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "catalogs"
DOCUMENT_FIELDS = ("question_url", "answer_url", "correction_url")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_name_set(path: Path, field: str) -> set[str]:
    rows = read_csv(path)
    names: set[str] = set()
    for row in rows:
        value = (row.get(field) or row.get(f"\ufeff{field}") or "").strip()
        if value:
            names.add(value)
    return names


def pdf_document_count(rows: list[dict[str, str]]) -> int:
    return sum(1 for row in rows for field in DOCUMENT_FIELDS if row.get(field))


def working_scope(category_name: str, locked27: set[str], other_seed: set[str]) -> str:
    if category_name in locked27:
        return "locked27_medical_current"
    if category_name in other_seed:
        return "professional_high_other_seed"
    return "future_expansion"


def track_id(row: dict[str, str]) -> str:
    return "|".join(
        [
            row["exam_level"],
            row["category_label"],
            row["category_code"],
            row["category_name"],
        ]
    )


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build(args: argparse.Namespace) -> tuple[Path, Path]:
    catalog_rows = read_csv(args.catalog)
    locked27 = read_name_set(args.locked27, "canonical_category_name")
    other_seed = read_name_set(args.other_seed, "category_name")

    rows_by_track: dict[str, list[dict[str, str]]] = defaultdict(list)
    rows_by_track_subject: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)

    for row in catalog_rows:
        key = track_id(row)
        rows_by_track[key].append(row)
        rows_by_track_subject[(key, row["subject_code"], row["subject_name"])].append(row)

    track_rows: list[dict[str, object]] = []
    for key, rows in rows_by_track.items():
        first = rows[0]
        years = sorted({int(row["year"]) for row in rows})
        exam_labels = Counter(row["exam_label"] for row in rows)
        scope = working_scope(first["category_name"], locked27, other_seed)
        track_rows.append(
            {
                "official_category_track_id": key,
                "working_scope": scope,
                "exam_level": first["exam_level"],
                "category_label": first["category_label"],
                "category_code": first["category_code"],
                "category_name": first["category_name"],
                "catalog_rows": len(rows),
                "pdf_url_documents": pdf_document_count(rows),
                "distinct_exam_codes": len({row["exam_code"] for row in rows}),
                "distinct_subject_codes": len({row["subject_code"] for row in rows}),
                "distinct_subject_names": len({row["subject_name"] for row in rows}),
                "year_min": years[0] if years else "",
                "year_max": years[-1] if years else "",
                "top_exam_labels": " | ".join(label for label, _ in exam_labels.most_common(3)),
            }
        )

    track_rows.sort(
        key=lambda row: (
            row["working_scope"],
            row["exam_level"],
            row["category_label"],
            row["category_code"],
            row["category_name"],
        )
    )

    subject_rows: list[dict[str, object]] = []
    for (key, subject_code, subject_name), rows in rows_by_track_subject.items():
        first = rows[0]
        years = sorted({int(row["year"]) for row in rows})
        scope = working_scope(first["category_name"], locked27, other_seed)
        subject_rows.append(
            {
                "official_category_track_id": key,
                "working_scope": scope,
                "exam_level": first["exam_level"],
                "category_label": first["category_label"],
                "category_code": first["category_code"],
                "category_name": first["category_name"],
                "subject_code": subject_code,
                "subject_name": subject_name,
                "catalog_rows": len(rows),
                "pdf_url_documents": pdf_document_count(rows),
                "distinct_exam_codes": len({row["exam_code"] for row in rows}),
                "year_min": years[0] if years else "",
                "year_max": years[-1] if years else "",
            }
        )

    subject_rows.sort(
        key=lambda row: (
            row["working_scope"],
            row["exam_level"],
            row["category_label"],
            row["category_code"],
            row["subject_code"],
            row["subject_name"],
        )
    )

    track_path = args.output_dir / "moex_official_category_track_summary__y100-115.csv"
    subject_path = args.output_dir / "moex_official_category_subjects__y100-115.csv"

    write_csv(
        track_path,
        track_rows,
        [
            "official_category_track_id",
            "working_scope",
            "exam_level",
            "category_label",
            "category_code",
            "category_name",
            "catalog_rows",
            "pdf_url_documents",
            "distinct_exam_codes",
            "distinct_subject_codes",
            "distinct_subject_names",
            "year_min",
            "year_max",
            "top_exam_labels",
        ],
    )
    write_csv(
        subject_path,
        subject_rows,
        [
            "official_category_track_id",
            "working_scope",
            "exam_level",
            "category_label",
            "category_code",
            "category_name",
            "subject_code",
            "subject_name",
            "catalog_rows",
            "pdf_url_documents",
            "distinct_exam_codes",
            "year_min",
            "year_max",
        ],
    )
    return track_path, subject_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--locked27", type=Path, default=DEFAULT_LOCKED27)
    parser.add_argument("--other-seed", type=Path, default=DEFAULT_OTHER_SEED)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    track_path, subject_path = build(parse_args())
    print(f"official category tracks: {track_path}")
    print(f"official category subjects: {subject_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
