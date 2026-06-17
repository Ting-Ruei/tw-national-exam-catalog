#!/usr/bin/env python3
"""
Download official MOEX PDFs from the subject catalog.

This downloader is intentionally catalog-driven: it does not guess exam names
from local folders, and it keeps source URL / registry-key lineage beside every
downloaded file.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import re
import ssl
import time
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = PROJECT_ROOT / "catalogs" / "moex_subject_catalog__y100-115.csv"
DEFAULT_ASSET_ROOT = PROJECT_ROOT / "國考題資料夾"
DEFAULT_OUTPUT_ROOT = DEFAULT_ASSET_ROOT / "10_official_pdf" / "by_official_catalog"
DEFAULT_MANIFEST_DIR = DEFAULT_ASSET_ROOT / "Registry" / "asset_manifests"
DEFAULT_LOG_DIR = DEFAULT_ASSET_ROOT / "Registry" / "processing_logs"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; tw-national-exam-catalog/0.1)"}
DOCUMENTS = [
    ("question", "question_url", ""),
    ("answer", "answer_url", "_ANS"),
    ("correction", "correction_url", "_MOD"),
]


@dataclass(frozen=True)
class CatalogRow:
    year: int
    exam_code: str
    exam_label: str
    category_code: str
    category_name: str
    subject_code: str
    subject_name: str
    question_set: str
    question_url: str
    answer_url: str
    correction_url: str
    registry_key: str


def ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


SSL_CTX = ssl_ctx()


def read_catalog(path: Path) -> list[CatalogRow]:
    rows: list[CatalogRow] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rows.append(
                CatalogRow(
                    year=int(row["year"]),
                    exam_code=row["exam_code"],
                    exam_label=row["exam_label"],
                    category_code=row["category_code"],
                    category_name=row["category_name"],
                    subject_code=row["subject_code"],
                    subject_name=row["subject_name"],
                    question_set=row["question_set"],
                    question_url=row["question_url"],
                    answer_url=row["answer_url"],
                    correction_url=row["correction_url"],
                    registry_key=row["registry_key"],
                )
            )
    return rows


def explicit_exam_ordinal(row: CatalogRow) -> int | None:
    if row.exam_code == "106111":
        return 3

    label = row.exam_label
    if "第三次" in label or "第3次" in label or "第三梯次" in label or "第3梯次" in label:
        return 3
    if "第二次" in label or "第2次" in label or "第二梯次" in label or "第2梯次" in label:
        return 2
    if "第一次" in label or "第1次" in label or "第一梯次" in label or "第1梯次" in label:
        return 1

    return None


def compute_exam_ordinals(rows: list[CatalogRow]) -> dict[str, int]:
    by_year_and_code: dict[int, dict[str, CatalogRow]] = defaultdict(dict)
    for row in rows:
        by_year_and_code[row.year][row.exam_code] = row

    ordinals: dict[str, int] = {}
    for year, exam_rows in by_year_and_code.items():
        next_ordinal = 1
        for exam_code in sorted(exam_rows, key=int):
            row = exam_rows[exam_code]
            explicit = explicit_exam_ordinal(row)
            if explicit is None:
                ordinal = next_ordinal
            elif explicit >= next_ordinal:
                ordinal = explicit
            else:
                ordinal = next_ordinal
            ordinals[exam_code] = ordinal
            next_ordinal = ordinal + 1
    return ordinals


def normalize_filename_part(value: str) -> str:
    value = value.replace("（", "(").replace("）", ")")
    value = re.sub(r"\s+", "", value)
    for ch in '\\/:*?"<>|':
        value = value.replace(ch, "_")
    value = value.strip("._ ")
    return value


def filename_for(row: CatalogRow, exam_ordinal_value: int, role_suffix: str, collision_suffix: str = "") -> str:
    category = normalize_filename_part(row.category_name)
    subject = normalize_filename_part(row.subject_name)
    return f"{row.year}{exam_ordinal_value}_{category}_{subject}{collision_suffix}{role_suffix}.pdf"


def destination_for(
    output_root: Path,
    row: CatalogRow,
    exam_ordinal_value: int,
    role_suffix: str,
    collision_suffix: str = "",
) -> Path:
    category_dir = normalize_filename_part(row.category_name)
    return (
        output_root
        / category_dir
        / str(row.year)
        / f"第{exam_ordinal_value}次"
        / filename_for(row, exam_ordinal_value, role_suffix, collision_suffix)
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, overwrite: bool) -> tuple[str, int, str]:
    if dest.exists() and not overwrite:
        return "exists", dest.stat().st_size, sha256_file(dest)

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=60) as response:
        payload = response.read()
    tmp.write_bytes(payload)
    tmp.replace(dest)
    return "downloaded", len(payload), hashlib.sha256(payload).hexdigest()


def filtered_rows(rows: list[CatalogRow], category: str, year_start: int, year_end: int) -> list[CatalogRow]:
    return [
        r
        for r in rows
        if r.category_name == category and year_end <= r.year <= year_start
    ]


def write_subject_variant_report(rows: list[CatalogRow], log_dir: Path, category: str, year_start: int, year_end: int) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    out = log_dir / f"subject_name_variants__{category}__y{year_end}-{year_start}.md"
    by_name: dict[str, list[CatalogRow]] = defaultdict(list)
    by_slot: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        by_name[row.subject_name].append(row)
        by_slot[row.subject_code].add(row.subject_name)

    lines = [
        f"# Subject Name Variants: {category} (ROC {year_end}-{year_start})",
        "",
        "This report preserves official subject names from the MOEX catalog.",
        "It is intended for manual review of exam reform / subject naming changes.",
        "",
        "## Distinct Official Subject Names",
        "",
    ]
    for name in sorted(by_name):
        seen = by_name[name]
        years = sorted({r.year for r in seen})
        exam_ordinals = compute_exam_ordinals(seen)
        sessions = sorted({f"{r.year}{exam_ordinals[r.exam_code]}" for r in seen})
        codes = sorted({r.subject_code for r in seen})
        lines.append(f"- `{name}`")
        lines.append(f"  - years: {years[0]}-{years[-1]}" if len(years) > 1 else f"  - year: {years[0]}")
        lines.append(f"  - sessions: {', '.join(sessions)}")
        lines.append(f"  - subject_codes: {', '.join(codes)}")
    lines.extend(["", "## Subject Code Slots With Multiple Names", ""])
    for code in sorted(by_slot):
        names = sorted(by_slot[code])
        if len(names) <= 1:
            continue
        lines.append(f"- subject_code `{code}`")
        for name in names:
            matched_rows = [r for r in rows if r.subject_code == code and r.subject_name == name]
            exam_ordinals = compute_exam_ordinals(matched_rows)
            sessions = sorted({f"{r.year}{exam_ordinals[r.exam_code]}" for r in matched_rows})
            lines.append(f"  - `{name}`: {', '.join(sessions)}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def run(args: argparse.Namespace) -> int:
    rows = filtered_rows(read_catalog(args.catalog), args.category, args.year_start, args.year_end)
    if not rows:
        raise SystemExit(f"No catalog rows found for category={args.category!r} years={args.year_end}-{args.year_start}")
    exam_ordinals = compute_exam_ordinals(rows)
    rows = sorted(
        rows,
        key=lambda r: (-r.year, exam_ordinals[r.exam_code], r.subject_code, r.subject_name),
    )

    variant_report = write_subject_variant_report(rows, args.log_dir, args.category, args.year_start, args.year_end)
    args.manifest_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    manifest_path = args.manifest_dir / f"moex_pdf_download__{args.category}__y{args.year_end}-{args.year_start}__{stamp}.csv"

    count = 0
    with manifest_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "status",
                "year",
                "exam_ordinal",
                "exam_code",
                "category_code",
                "category_name",
                "subject_code",
                "subject_name",
                "document_role",
                "source_url",
                "destination",
                "bytes",
                "sha256",
                "registry_key",
            ],
        )
        writer.writeheader()
        planned_destinations: dict[Path, str] = {}
        for row in rows:
            for role, url_field, suffix in DOCUMENTS:
                url = getattr(row, url_field)
                if not url:
                    continue
                registry_key = f"{row.registry_key}:{role}"
                ordinal = exam_ordinals[row.exam_code]
                dest = destination_for(args.output_root, row, ordinal, suffix)
                if dest in planned_destinations and planned_destinations[dest] != registry_key:
                    dest = destination_for(args.output_root, row, ordinal, suffix, f"_E{row.exam_code}")
                planned_destinations[dest] = registry_key
                if args.limit and count >= args.limit:
                    status, size, digest = "planned_limit_reached", 0, ""
                elif args.dry_run:
                    status, size, digest = "planned", 0, ""
                else:
                    try:
                        status, size, digest = download(url, dest, args.overwrite)
                    except Exception as exc:  # keep batch manifest even when one URL fails
                        status, size, digest = f"error:{type(exc).__name__}:{exc}", 0, ""
                writer.writerow(
                    {
                        "status": status,
                        "year": row.year,
                        "exam_ordinal": ordinal,
                        "exam_code": row.exam_code,
                        "category_code": row.category_code,
                        "category_name": row.category_name,
                        "subject_code": row.subject_code,
                        "subject_name": row.subject_name,
                        "document_role": role,
                        "source_url": url,
                        "destination": dest,
                        "bytes": size,
                        "sha256": digest,
                        "registry_key": registry_key,
                    }
                )
                count += 1
                if not args.dry_run and args.sleep:
                    time.sleep(args.sleep)

    print(f"catalog rows: {len(rows)}")
    print(f"document rows: {count}")
    print(f"manifest: {manifest_path}")
    print(f"subject variant report: {variant_report}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download MOEX PDFs from catalog metadata.")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--category", default="醫事檢驗師")
    parser.add_argument("--year-start", type=int, default=115)
    parser.add_argument("--year-end", type=int, default=100)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--manifest-dir", type=Path, default=DEFAULT_MANIFEST_DIR)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--limit", type=int, default=0, help="Limit downloaded/planned document count; 0 means no limit.")
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
