#!/usr/bin/env python3
"""
Create rsyncable MinerU remote batches from unfinished official PDFs.

The produced batch is a small, self-contained workspace: it contains a
batch-scoped PDF index, a manifest, and the MinerU runner scripts needed by the
remote worker. PDF copying is optional; the default flow is manifest-only so the
worker can use its own local synced official PDF tree.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = PROJECT_ROOT / "國考題資料夾"
PDF_ROOT = ASSET_ROOT / "10_official_pdf" / "by_official_catalog"
OUTPUT_ROOT = ASSET_ROOT / "20_mineru_output" / "by_official_catalog"
REGISTRY_ROOT = ASSET_ROOT / "Registry"
PDF_INDEX_DIR = REGISTRY_ROOT / "pdf_indexes"
PAIR_INDEX_DIR = REGISTRY_ROOT / "paired_indexes"
RUN_LOG_DIR = REGISTRY_ROOT / "mineru_runs"
REMOTE_BATCH_ROOT = REGISTRY_ROOT / "mineru_remote_batches"
DEFAULT_OUTGOING_ROOT = REMOTE_BATCH_ROOT / "outgoing"


@dataclass(frozen=True)
class Candidate:
    source_kind: str
    group_name: str
    document_role: str
    pair_status: str
    year: str
    exam_ordinal: str
    registry_key: str
    pdf_path: Path
    pdf_relative: str
    sha256: str
    source_url: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create MinerU remote worker batches.")
    parser.add_argument("--scope", choices=["paired-primary", "all-official", "questions-only"], default="paired-primary")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--batch-count", type=int, default=1)
    parser.add_argument("--order", choices=["reverse", "forward"], default="reverse")
    parser.add_argument("--group", action="append", help="Filter group_name. Repeatable.")
    parser.add_argument("--year-start", type=int)
    parser.add_argument("--year-end", type=int)
    parser.add_argument("--pdf-index", type=Path, default=latest_csv(PDF_INDEX_DIR, "pdf_asset_index_detail__*.csv"))
    parser.add_argument("--pair-index", type=Path, default=latest_csv(PAIR_INDEX_DIR, "question_answer_pairs_detail__*.csv"))
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTGOING_ROOT)
    parser.add_argument("--batch-mode", choices=["manifest-only", "copy-pdfs"], default="manifest-only")
    parser.add_argument("--stamp", default=time.strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def latest_csv(directory: Path, pattern: str) -> Path:
    paths = sorted(directory.glob(pattern))
    if not paths:
        raise SystemExit(f"No CSV found: {directory}/{pattern}")
    return paths[-1]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def within_filters(row: dict[str, str], groups: set[str] | None, year_start: int | None, year_end: int | None) -> bool:
    if groups and row.get("group_name") not in groups:
        return False
    if year_start is None and year_end is None:
        return True
    try:
        year = int(row.get("year", "0"))
    except ValueError:
        return False
    if year_start is not None and year > year_start:
        return False
    if year_end is not None and year < year_end:
        return False
    return True


def project_pdf_path(relative_pdf: str) -> Path:
    return (ASSET_ROOT / relative_pdf).resolve()


def expected_markdown_for_relative(relative_pdf: str) -> Path:
    pdf_path = project_pdf_path(relative_pdf)
    rel_under_pdf_root = pdf_path.relative_to(PDF_ROOT)
    return OUTPUT_ROOT / rel_under_pdf_root.parent / pdf_path.stem / "vlm" / f"{pdf_path.stem}.md"


def result_completed_relatives() -> set[str]:
    completed: set[str] = set()
    for path in RUN_LOG_DIR.glob("**/mineru_results__*.csv"):
        try:
            rows = read_csv(path)
        except UnicodeDecodeError:
            continue
        for row in rows:
            if row.get("status") not in {"ok", "skipped_existing"}:
                continue
            pdf_path = row.get("pdf_path", "")
            if not pdf_path:
                continue
            try:
                relative = str(Path(pdf_path).resolve().relative_to(ASSET_ROOT.resolve()))
            except ValueError:
                continue
            completed.add(relative)
    return completed


def reserved_relatives() -> set[str]:
    reserved: set[str] = set()
    if not REMOTE_BATCH_ROOT.exists():
        return reserved
    for manifest_path in REMOTE_BATCH_ROOT.glob("**/batch_manifest.csv"):
        try:
            rows = read_csv(manifest_path)
        except UnicodeDecodeError:
            continue
        for row in rows:
            relative = row.get("pdf_relative", "")
            if relative:
                reserved.add(relative)
    return reserved


def is_done(relative_pdf: str, completed_relatives: set[str]) -> bool:
    if relative_pdf in completed_relatives:
        return True
    return expected_markdown_for_relative(relative_pdf).exists()


def add_candidate(candidates: dict[str, Candidate], candidate: Candidate) -> None:
    existing = candidates.get(candidate.pdf_relative)
    if existing is None:
        candidates[candidate.pdf_relative] = candidate


def build_candidates(args: argparse.Namespace) -> list[Candidate]:
    groups = set(args.group) if args.group else None
    candidates: dict[str, Candidate] = {}

    if args.scope in {"paired-primary", "questions-only"}:
        for row in read_csv(args.pair_index):
            if not within_filters(row, groups, args.year_start, args.year_end):
                continue
            q_rel = row["question_pdf_relative"]
            add_candidate(
                candidates,
                Candidate(
                    source_kind="pair_index",
                    group_name=row["group_name"],
                    document_role="question",
                    pair_status=row["pair_status"],
                    year=row["year"],
                    exam_ordinal=row["exam_ordinal"],
                    registry_key=row["question_registry_key"],
                    pdf_path=project_pdf_path(q_rel),
                    pdf_relative=q_rel,
                    sha256=row["question_sha256"],
                    source_url=row["question_source_url"],
                ),
            )
            if args.scope == "paired-primary" and row.get("answer_pdf_primary_relative"):
                a_rel = row["answer_pdf_primary_relative"]
                add_candidate(
                    candidates,
                    Candidate(
                        source_kind="pair_index",
                        group_name=row["group_name"],
                        document_role=row["answer_role_primary"] or "answer_primary",
                        pair_status=row["pair_status"],
                        year=row["year"],
                        exam_ordinal=row["exam_ordinal"],
                        registry_key=row["answer_registry_key_primary"],
                        pdf_path=project_pdf_path(a_rel),
                        pdf_relative=a_rel,
                        sha256=row["answer_sha256_primary"],
                        source_url=row["answer_source_url_primary"],
                    ),
                )
    else:
        for row in read_csv(args.pdf_index):
            if not within_filters(row, groups, args.year_start, args.year_end):
                continue
            relative = row["relative_asset_path"]
            add_candidate(
                candidates,
                Candidate(
                    source_kind="pdf_index",
                    group_name=row["group_name"],
                    document_role=row["document_role"],
                    pair_status="",
                    year=row["year"],
                    exam_ordinal=row["exam_ordinal"],
                    registry_key=row["registry_key"],
                    pdf_path=project_pdf_path(relative),
                    pdf_relative=relative,
                    sha256=row["sha256"],
                    source_url=row["source_url"],
                ),
            )

    completed_relatives = result_completed_relatives()
    reserved = reserved_relatives()
    unfinished = [
        candidate
        for candidate in candidates.values()
        if candidate.pdf_path.exists()
        and not is_done(candidate.pdf_relative, completed_relatives)
        and candidate.pdf_relative not in reserved
    ]
    return sorted(unfinished, key=lambda item: item.pdf_relative, reverse=args.order == "reverse")


def batch_pdf_index_rows(batch_dir: Path, candidates: list[Candidate], pdf_index_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    rows_by_relative = {row["relative_asset_path"]: row for row in pdf_index_rows}
    output_rows: list[dict[str, str]] = []
    for candidate in candidates:
        row = dict(rows_by_relative.get(candidate.pdf_relative, {}))
        if not row:
            row = {
                "group_name": candidate.group_name,
                "year": candidate.year,
                "exam_ordinal": candidate.exam_ordinal,
                "document_role": candidate.document_role,
                "source_url": candidate.source_url,
                "relative_asset_path": candidate.pdf_relative,
                "sha256": candidate.sha256,
                "registry_key": candidate.registry_key,
            }
        remote_relative = f"國考題資料夾/{candidate.pdf_relative}"
        row["asset_path"] = remote_relative
        row["relative_asset_path"] = candidate.pdf_relative
        output_rows.append(row)
    return output_rows


def batch_pair_rows(candidates: list[Candidate], pair_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    selected = {candidate.pdf_relative for candidate in candidates}
    rows: list[dict[str, str]] = []
    for row in pair_rows:
        if row.get("question_pdf_relative") in selected or row.get("answer_pdf_primary_relative") in selected:
            updated = dict(row)
            for key in ["question_pdf", "answer_pdf_primary", "answer_pdf_ans", "answer_pdf_mod"]:
                rel_key = f"{key}_relative"
                if updated.get(rel_key):
                    updated[key] = f"國考題資料夾/{updated[rel_key]}"
            rows.append(updated)
    return rows


def copy_support_scripts(batch_dir: Path) -> None:
    scripts_dir = batch_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for script_name in ["run_mineru_pdf_batch.py", "run_remote_mineru_batch.sh"]:
        shutil.copy2(PROJECT_ROOT / "scripts" / script_name, scripts_dir / script_name)


def create_batch(batch_dir: Path, candidates: list[Candidate], args: argparse.Namespace) -> dict[str, object]:
    pdf_index_rows = read_csv(args.pdf_index)
    pair_rows = read_csv(args.pair_index)
    batch_dir.mkdir(parents=True, exist_ok=False)
    (batch_dir / "logs").mkdir()
    (batch_dir / "國考題資料夾" / "20_mineru_output" / "by_official_catalog").mkdir(parents=True)
    (batch_dir / "國考題資料夾" / "Registry" / "mineru_runs").mkdir(parents=True)
    if args.batch_mode == "copy-pdfs":
        (batch_dir / "國考題資料夾" / "10_official_pdf" / "by_official_catalog").mkdir(parents=True)

    manifest_rows: list[dict[str, object]] = []
    for candidate in candidates:
        row = asdict(candidate)
        row["pdf_path"] = str(candidate.pdf_path)
        row["batch_pdf_path"] = str(Path("國考題資料夾") / candidate.pdf_relative)
        manifest_rows.append(row)
        if args.batch_mode == "copy-pdfs":
            destination = batch_dir / "國考題資料夾" / candidate.pdf_relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate.pdf_path, destination)

    write_csv(batch_dir / "batch_manifest.csv", manifest_rows)
    pdf_rows = batch_pdf_index_rows(batch_dir, candidates, pdf_index_rows)
    write_csv(batch_dir / "pdf_asset_index_batch.csv", pdf_rows, fieldnames=list(pdf_rows[0].keys()) if pdf_rows else [])
    pair_batch_rows = batch_pair_rows(candidates, pair_rows)
    if pair_batch_rows:
        write_csv(batch_dir / "question_answer_pairs_batch.csv", pair_batch_rows, fieldnames=list(pair_batch_rows[0].keys()))

    copy_support_scripts(batch_dir)
    metadata = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "scope": args.scope,
        "batch_size": len(candidates),
        "batch_mode": args.batch_mode,
        "order": args.order,
        "pdf_index": str(args.pdf_index),
        "pair_index": str(args.pair_index),
    }
    (batch_dir / "batch_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def main() -> None:
    args = parse_args()
    candidates = build_candidates(args)
    total_requested = args.batch_size * args.batch_count
    selected = candidates[:total_requested]
    batches = [selected[i : i + args.batch_size] for i in range(0, len(selected), args.batch_size)]

    planned = {
        "scope": args.scope,
        "unfinished_candidates": len(candidates),
        "selected": len(selected),
        "batch_count": len(batches),
        "batch_size": args.batch_size,
        "batch_mode": args.batch_mode,
        "order": args.order,
        "out_dir": str(args.out_dir),
        "dry_run": args.dry_run,
    }
    print(json.dumps(planned, ensure_ascii=False, indent=2))

    if args.dry_run:
        for idx, batch in enumerate(batches, start=1):
            print(f"part{idx:03d}: {len(batch)} PDFs")
            for candidate in batch[:5]:
                print(f"  {candidate.pdf_relative}")
        return

    created: list[str] = []
    for idx, batch in enumerate(batches, start=1):
        batch_name = f"mineru_remote_batch_{args.stamp}_part{idx:03d}"
        batch_dir = args.out_dir / batch_name
        create_batch(batch_dir, batch, args)
        created.append(str(batch_dir))
        print(f"created={batch_dir}")

    summary_path = args.out_dir / f"mineru_remote_batches__{args.stamp}.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps({**planned, "created": created}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"summary={summary_path}")


if __name__ == "__main__":
    main()
