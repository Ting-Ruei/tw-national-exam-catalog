#!/usr/bin/env python3
"""
Build expansion-node summaries for the full MOEX catalog.

The first production focus is locked medical categories. This report records
the larger non-locked catalog as future expansion nodes, so download/MinerU
work can be planned by domain instead of treating all remaining exams as one
large queue.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = PROJECT_ROOT / "catalogs" / "moex_subject_catalog__y100-115.csv"
DEFAULT_LOCKED27 = PROJECT_ROOT / "catalogs" / "locked_27_canonical_category_names.csv"
DEFAULT_OTHER_SEED = PROJECT_ROOT / "catalogs" / "other_professional_high_categories_excluding_locked27__y100-115.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "catalogs"

DOCUMENT_FIELDS = ("question_url", "answer_url", "correction_url")


@dataclass(frozen=True)
class ExpansionNode:
    node_id: str
    label: str
    priority: int
    rationale: str


NODES = {
    "locked27_medical_current": ExpansionNode(
        "locked27_medical_current",
        "醫事 locked27 現行主線",
        0,
        "目前主要入庫與 parser 品質控管範圍。",
    ),
    "professional_high_other_seed": ExpansionNode(
        "professional_high_other_seed",
        "專技高普考其他類科種子集",
        10,
        "已建立獨立資料根與 MinerU 背景 queue 的第一個非醫事拓展節點。",
    ),
    "professional_technical_remaining": ExpansionNode(
        "professional_technical_remaining",
        "其餘專門職業及技術人員考試",
        20,
        "專技考試但尚未納入 97 類種子集者。",
    ),
    "civil_service_core": ExpansionNode(
        "civil_service_core",
        "公務人員高普初等與一般行政類",
        30,
        "最大宗公職考試，可再按職系或共同科目拆分。",
    ),
    "civil_service_special": ExpansionNode(
        "civil_service_special",
        "公務人員特種考試",
        40,
        "警察、司法、調查、關務、外交、原民、身障、地方等特殊考試。",
    ),
    "promotion_rank_exam": ExpansionNode(
        "promotion_rank_exam",
        "升官等與升資考試",
        50,
        "公務、警察、交通等升等/升資考試。",
    ),
    "language_tourism": ExpansionNode(
        "language_tourism",
        "導遊領隊與外語類",
        60,
        "外語導遊、華語導遊、外語領隊、華語領隊等可獨立處理。",
    ),
    "other_unclassified": ExpansionNode(
        "other_unclassified",
        "其他未分類節點",
        90,
        "尚待人工檢查或新增規則的 catalog rows。",
    ),
}


def read_name_set(path: Path, field: str) -> set[str]:
    names: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = {(name or "").lstrip("\ufeff"): name for name in (reader.fieldnames or [])}
        source_field = fields[field]
        for row in reader:
            value = (row.get(source_field) or "").strip()
            if value:
                names.add(value)
    return names


def read_catalog(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def pdf_document_count(rows: list[dict[str, str]]) -> int:
    return sum(1 for row in rows for field in DOCUMENT_FIELDS if row.get(field))


def classify(row: dict[str, str], locked27: set[str], other_seed: set[str]) -> str:
    category_name = row["category_name"]
    exam_label = row["exam_label"]
    exam_level = row["exam_level"]
    joined = f"{exam_label} {exam_level} {row['category_label']} {category_name}"

    if category_name in locked27:
        return "locked27_medical_current"
    if category_name in other_seed:
        return "professional_high_other_seed"
    if "導遊" in joined or "領隊" in joined:
        return "language_tourism"
    if "升官等" in joined or "升資" in joined:
        return "promotion_rank_exam"
    if "特種考試" in exam_label or any(
        keyword in joined
        for keyword in [
            "警察",
            "司法",
            "調查",
            "關務",
            "外交",
            "國家安全",
            "移民",
            "原住民族",
            "身心障礙",
            "身障",
            "地方政府",
            "鐵路",
            "海岸巡防",
            "民航",
        ]
    ):
        return "civil_service_special"
    if "專門職業及技術人員" in exam_label or "專技" in joined:
        return "professional_technical_remaining"
    if any(keyword in joined for keyword in ["高考", "普通考試", "初等考試", "高等考試", "公務人員"]):
        return "civil_service_core"
    return "other_unclassified"


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    catalog_rows = read_catalog(args.catalog)
    locked27 = read_name_set(args.locked27, "canonical_category_name")
    other_seed = read_name_set(args.other_seed, "category_name")

    rows_by_node: dict[str, list[dict[str, str]]] = defaultdict(list)
    rows_by_category: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    rows_by_subject: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)

    for row in catalog_rows:
        node_id = classify(row, locked27, other_seed)
        rows_by_node[node_id].append(row)
        rows_by_category[(node_id, row["category_name"])].append(row)
        rows_by_subject[(node_id, row["category_name"], row["subject_name"])].append(row)

    node_rows: list[dict[str, object]] = []
    for node_id, rows in sorted(rows_by_node.items(), key=lambda item: (NODES[item[0]].priority, item[0])):
        node = NODES[node_id]
        years = sorted({int(row["year"]) for row in rows})
        node_rows.append(
            {
                "node_id": node.node_id,
                "node_label": node.label,
                "priority": node.priority,
                "catalog_rows": len(rows),
                "pdf_url_documents": pdf_document_count(rows),
                "distinct_exam_codes": len({row["exam_code"] for row in rows}),
                "distinct_exam_category_pairs": len({(row["exam_code"], row["category_code"]) for row in rows}),
                "distinct_categories": len({row["category_name"] for row in rows}),
                "distinct_subject_names": len({row["subject_name"] for row in rows}),
                "year_min": years[0] if years else "",
                "year_max": years[-1] if years else "",
                "rationale": node.rationale,
            }
        )

    category_rows: list[dict[str, object]] = []
    for (node_id, category_name), rows in sorted(rows_by_category.items()):
        years = sorted({int(row["year"]) for row in rows})
        levels = Counter(row["exam_level"] for row in rows)
        category_rows.append(
            {
                "node_id": node_id,
                "node_label": NODES[node_id].label,
                "category_name": category_name,
                "catalog_rows": len(rows),
                "pdf_url_documents": pdf_document_count(rows),
                "distinct_exam_codes": len({row["exam_code"] for row in rows}),
                "distinct_exam_category_pairs": len({(row["exam_code"], row["category_code"]) for row in rows}),
                "distinct_subject_names": len({row["subject_name"] for row in rows}),
                "year_min": years[0] if years else "",
                "year_max": years[-1] if years else "",
                "top_exam_level": levels.most_common(1)[0][0] if levels else "",
            }
        )
    category_rows.sort(key=lambda row: (row["node_id"], -int(row["pdf_url_documents"]), row["category_name"]))

    subject_rows: list[dict[str, object]] = []
    for (node_id, category_name, subject_name), rows in sorted(rows_by_subject.items()):
        years = sorted({int(row["year"]) for row in rows})
        subject_rows.append(
            {
                "node_id": node_id,
                "node_label": NODES[node_id].label,
                "category_name": category_name,
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
            row["node_id"],
            row["category_name"],
            -int(row["catalog_rows"]),
            row["subject_name"],
        )
    )

    node_path = args.output_dir / "moex_expansion_node_summary__y100-115.csv"
    category_path = args.output_dir / "moex_expansion_category_summary__y100-115.csv"
    subject_path = args.output_dir / "moex_expansion_subject_summary__y100-115.csv"

    write_csv(
        node_path,
        node_rows,
        [
            "node_id",
            "node_label",
            "priority",
            "catalog_rows",
            "pdf_url_documents",
            "distinct_exam_codes",
            "distinct_exam_category_pairs",
            "distinct_categories",
            "distinct_subject_names",
            "year_min",
            "year_max",
            "rationale",
        ],
    )
    write_csv(
        category_path,
        category_rows,
        [
            "node_id",
            "node_label",
            "category_name",
            "catalog_rows",
            "pdf_url_documents",
            "distinct_exam_codes",
            "distinct_exam_category_pairs",
            "distinct_subject_names",
            "year_min",
            "year_max",
            "top_exam_level",
        ],
    )
    write_csv(
        subject_path,
        subject_rows,
        [
            "node_id",
            "node_label",
            "category_name",
            "subject_name",
            "catalog_rows",
            "pdf_url_documents",
            "distinct_exam_codes",
            "year_min",
            "year_max",
        ],
    )
    return node_path, category_path, subject_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--locked27", type=Path, default=DEFAULT_LOCKED27)
    parser.add_argument("--other-seed", type=Path, default=DEFAULT_OTHER_SEED)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    node_path, category_path, subject_path = build(parse_args())
    print(f"node summary: {node_path}")
    print(f"category summary: {category_path}")
    print(f"subject summary: {subject_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
