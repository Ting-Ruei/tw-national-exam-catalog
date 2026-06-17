#!/usr/bin/env python3
"""
Build question-to-answer PDF pairing indexes from latest download manifests.

This script does not parse PDFs and does not write to a database. It creates
reviewable CSV files for downstream MinerU and answer comparison workflows.

Pairing rule:

- one question PDF produces one row;
- if a correction/MOD PDF exists, it is the primary answer document;
- otherwise the standard ANS PDF is the primary answer document;
- ANS and MOD paths are both preserved when available.
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
DEFAULT_OUTPUT_DIR = DEFAULT_ASSET_ROOT / "Registry" / "paired_indexes"


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
    if not path_text:
        return ""
    path = Path(path_text)
    try:
        return str(path.relative_to(asset_root))
    except ValueError:
        return path_text


def base_registry_key(registry_key: str) -> str:
    parts = registry_key.split(":")
    if parts[-1] in {"question", "answer", "correction"}:
        return ":".join(parts[:-1])
    return registry_key


def read_latest_rows(manifest_dir: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for choice in latest_manifests(manifest_dir):
        with choice.path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                row = dict(row)
                row["manifest_path"] = str(choice.path)
                row["manifest_timestamp"] = choice.timestamp
                row["group_name"] = group_name(row["category_name"])
                row["normalized_category_name"] = normalize_name(row["category_name"])
                row["normalized_subject_name"] = normalize_name(row["subject_name"])
                row["base_registry_key"] = base_registry_key(row["registry_key"])
                rows.append(row)
    return rows


def row_path(row: dict[str, str], asset_root: Path) -> tuple[str, str, str, str]:
    return (
        row.get("destination", ""),
        relative_to_asset_root(row.get("destination", ""), asset_root),
        row.get("sha256", ""),
        row.get("bytes", ""),
    )


def pair_status(question: dict[str, str], answer: dict[str, str] | None, correction: dict[str, str] | None) -> str:
    if correction:
        return "paired_mod_primary"
    if answer:
        return "paired_ans_only"
    return "missing_answer"


def build_pairs(args: argparse.Namespace) -> tuple[Path, Path]:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    detail_path = args.output_dir / f"question_answer_pairs_detail__{stamp}.csv"
    summary_path = args.output_dir / f"question_answer_pairs_summary__{stamp}.csv"

    rows = read_latest_rows(args.manifest_dir)
    by_key: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for row in rows:
        by_key[row["base_registry_key"]][row["document_role"]] = row

    detail_fields = [
        "pair_key",
        "pair_status",
        "answer_role_primary",
        "group_name",
        "official_category_name",
        "normalized_category_name",
        "year",
        "exam_ordinal",
        "exam_code",
        "category_code",
        "subject_code",
        "official_subject_name",
        "normalized_subject_name",
        "question_registry_key",
        "answer_registry_key_primary",
        "answer_registry_key_ans",
        "answer_registry_key_mod",
        "question_pdf",
        "question_pdf_relative",
        "question_sha256",
        "question_bytes",
        "answer_pdf_primary",
        "answer_pdf_primary_relative",
        "answer_sha256_primary",
        "answer_bytes_primary",
        "answer_pdf_ans",
        "answer_pdf_ans_relative",
        "answer_sha256_ans",
        "answer_pdf_mod",
        "answer_pdf_mod_relative",
        "answer_sha256_mod",
        "question_source_url",
        "answer_source_url_primary",
        "answer_source_url_ans",
        "answer_source_url_mod",
        "question_manifest_path",
        "notes",
    ]

    summary: dict[str, Counter] = defaultdict(Counter)
    with detail_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=detail_fields)
        writer.writeheader()
        for key in sorted(by_key):
            docs = by_key[key]
            question = docs.get("question")
            if not question:
                continue

            answer = docs.get("answer")
            correction = docs.get("correction")
            primary = correction or answer
            status = pair_status(question, answer, correction)
            answer_role_primary = primary["document_role"] if primary else ""
            q_path, q_rel, q_sha, q_bytes = row_path(question, args.asset_root)
            primary_path, primary_rel, primary_sha, primary_bytes = row_path(primary or {}, args.asset_root)
            ans_path, ans_rel, ans_sha, _ = row_path(answer or {}, args.asset_root)
            mod_path, mod_rel, mod_sha, _ = row_path(correction or {}, args.asset_root)
            notes = []
            if question["exam_code"] in EXAM_CODE_NOTES:
                notes.append(EXAM_CODE_NOTES[question["exam_code"]])
            if status == "paired_mod_primary":
                notes.append("MOD exists; use correction PDF as primary answer")
            if status == "missing_answer":
                notes.append("No ANS or MOD PDF available in latest manifest")

            writer.writerow(
                {
                    "pair_key": key,
                    "pair_status": status,
                    "answer_role_primary": answer_role_primary,
                    "group_name": question["group_name"],
                    "official_category_name": question["category_name"],
                    "normalized_category_name": question["normalized_category_name"],
                    "year": question["year"],
                    "exam_ordinal": question["exam_ordinal"],
                    "exam_code": question["exam_code"],
                    "category_code": question["category_code"],
                    "subject_code": question["subject_code"],
                    "official_subject_name": question["subject_name"],
                    "normalized_subject_name": question["normalized_subject_name"],
                    "question_registry_key": question["registry_key"],
                    "answer_registry_key_primary": primary.get("registry_key", "") if primary else "",
                    "answer_registry_key_ans": answer.get("registry_key", "") if answer else "",
                    "answer_registry_key_mod": correction.get("registry_key", "") if correction else "",
                    "question_pdf": q_path,
                    "question_pdf_relative": q_rel,
                    "question_sha256": q_sha,
                    "question_bytes": q_bytes,
                    "answer_pdf_primary": primary_path,
                    "answer_pdf_primary_relative": primary_rel,
                    "answer_sha256_primary": primary_sha,
                    "answer_bytes_primary": primary_bytes,
                    "answer_pdf_ans": ans_path,
                    "answer_pdf_ans_relative": ans_rel,
                    "answer_sha256_ans": ans_sha,
                    "answer_pdf_mod": mod_path,
                    "answer_pdf_mod_relative": mod_rel,
                    "answer_sha256_mod": mod_sha,
                    "question_source_url": question["source_url"],
                    "answer_source_url_primary": primary.get("source_url", "") if primary else "",
                    "answer_source_url_ans": answer.get("source_url", "") if answer else "",
                    "answer_source_url_mod": correction.get("source_url", "") if correction else "",
                    "question_manifest_path": question["manifest_path"],
                    "notes": " | ".join(notes),
                }
            )
            summary[question["group_name"]]["questions"] += 1
            summary[question["group_name"]][status] += 1
            if primary:
                summary[question["group_name"]][f"primary:{answer_role_primary}"] += 1

    summary_fields = [
        "group_name",
        "question_rows",
        "paired_mod_primary",
        "paired_ans_only",
        "missing_answer",
        "primary_answer",
        "primary_correction",
    ]
    with summary_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=summary_fields)
        writer.writeheader()
        for gname in sorted(summary):
            counts = summary[gname]
            writer.writerow(
                {
                    "group_name": gname,
                    "question_rows": counts["questions"],
                    "paired_mod_primary": counts["paired_mod_primary"],
                    "paired_ans_only": counts["paired_ans_only"],
                    "missing_answer": counts["missing_answer"],
                    "primary_answer": counts["primary:answer"],
                    "primary_correction": counts["primary:correction"],
                }
            )
    return detail_path, summary_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-root", type=Path, default=DEFAULT_ASSET_ROOT)
    parser.add_argument("--manifest-dir", type=Path, default=DEFAULT_MANIFEST_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    detail_path, summary_path = build_pairs(parse_args())
    print(f"pair detail: {detail_path}")
    print(f"pair summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
