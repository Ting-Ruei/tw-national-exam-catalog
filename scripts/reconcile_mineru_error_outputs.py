#!/usr/bin/env python3
"""
Reconcile MinerU result rows marked error because the expected output path was
too strict.

MinerU may truncate very long PDF stems when creating output directories. The
original batch runner checks only one exact expected markdown path, so these
successful conversions can be reported as errors. This script finds the actual
outputs, writes an audit report, and optionally moves fully reconciled local
partial batches to local_done.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = Path(os.environ.get("ASSET_ROOT", PROJECT_ROOT / "國考題資料夾_其他類型")).expanduser()
PDF_ROOT = ASSET_ROOT / "10_official_pdf" / "by_official_catalog"
OUTPUT_ROOT = ASSET_ROOT / "20_mineru_output" / "by_official_catalog"
REGISTRY_ROOT = ASSET_ROOT / "Registry"
RUN_ROOT = REGISTRY_ROOT / "mineru_runs"
BATCH_ROOT = REGISTRY_ROOT / "mineru_remote_batches"


@dataclass(frozen=True)
class Match:
    status: str
    actual_md: str
    actual_content_list: str
    actual_middle_json: str
    strategy: str
    score: int
    notes: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reconcile MinerU error rows against actual output files.")
    parser.add_argument("--asset-root", type=Path, default=ASSET_ROOT)
    parser.add_argument("--scope", default="all-official")
    parser.add_argument("--move-resolved-partials", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


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


def common_prefix_len(left: str, right: str) -> int:
    count = 0
    for a, b in zip(left, right):
        if a != b:
            break
        count += 1
    return count


def candidate_files(candidate_dir: Path) -> tuple[Path | None, Path | None, Path | None]:
    md_files = sorted(candidate_dir.glob("vlm/*.md"))
    content_files = sorted(candidate_dir.glob("vlm/*content_list.json"))
    middle_files = sorted(candidate_dir.glob("vlm/*middle.json"))
    return (
        md_files[0] if md_files else None,
        content_files[0] if content_files else None,
        middle_files[0] if middle_files else None,
    )


def match_output(pdf_path: Path, output_parent: Path, expected_md: Path) -> Match:
    if expected_md.exists():
        content = next(iter(expected_md.parent.glob("*content_list.json")), None)
        middle = next(iter(expected_md.parent.glob("*middle.json")), None)
        return Match(
            status="ok_exact",
            actual_md=str(expected_md),
            actual_content_list=str(content or ""),
            actual_middle_json=str(middle or ""),
            strategy="expected_md_exists",
            score=len(pdf_path.stem),
            notes="",
        )

    if not output_parent.exists():
        return Match("unresolved", "", "", "", "missing_output_parent", 0, "output_parent does not exist")

    stem = pdf_path.stem
    candidates: list[tuple[int, Path, Path, Path | None, Path | None]] = []
    for child in output_parent.iterdir():
        if not child.is_dir():
            continue
        md, content, middle = candidate_files(child)
        if md is None:
            continue
        dir_score = common_prefix_len(stem, child.name)
        md_score = common_prefix_len(stem, md.stem)
        score = max(dir_score, md_score)
        if score >= 24 or stem.startswith(child.name) or child.name.startswith(stem):
            candidates.append((score, child, md, content, middle))

    if not candidates:
        return Match("unresolved", "", "", "", "no_candidate_md", 0, "no nearby md file matched the PDF stem")

    candidates.sort(key=lambda item: (item[0], len(item[1].name)), reverse=True)
    best = candidates[0]
    ambiguous = len(candidates) > 1 and candidates[1][0] == best[0]
    status = "ambiguous" if ambiguous else "ok_reconciled"
    note = "multiple candidates share the best score" if ambiguous else ""
    return Match(
        status=status,
        actual_md=str(best[2]),
        actual_content_list=str(best[3] or ""),
        actual_middle_json=str(best[4] or ""),
        strategy="prefix_match_truncated_stem",
        score=best[0],
        notes=note,
    )


def expected_paths(pdf_path: Path, output_root: Path) -> tuple[Path, Path]:
    rel = pdf_path.resolve().relative_to(PDF_ROOT.resolve())
    output_parent = output_root / rel.parent
    return output_parent, output_parent / pdf_path.stem / "vlm" / f"{pdf_path.stem}.md"


def collect_error_rows(scope: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for result_csv in sorted(RUN_ROOT.glob(f"*/mineru_results__{scope}__*.csv")):
        for row in read_csv(result_csv):
            if row.get("status") in {"error", "timeout"}:
                row = dict(row)
                row["source_result_csv"] = str(result_csv)
                row["source_run_dir"] = result_csv.parent.name
                rows.append(row)
    return rows


def reconcile_errors(scope: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    report_rows: list[dict[str, object]] = []
    rerun_rows: list[dict[str, object]] = []
    for row in collect_error_rows(scope):
        pdf_path = Path(row["pdf_path"])
        output_parent = Path(row["output_parent"])
        expected_md = Path(row["expected_md"])
        match = match_output(pdf_path, output_parent, expected_md)
        report = {
            "source_run_dir": row["source_run_dir"],
            "source_result_csv": row["source_result_csv"],
            "task_id": row["task_id"],
            "original_status": row["status"],
            "reconciled_status": match.status,
            "returncode": row.get("returncode", ""),
            "pdf_path": row["pdf_path"],
            "output_parent": row["output_parent"],
            "expected_md": row["expected_md"],
            "actual_md": match.actual_md,
            "actual_content_list": match.actual_content_list,
            "actual_middle_json": match.actual_middle_json,
            "match_strategy": match.strategy,
            "match_score": match.score,
            "notes": match.notes,
        }
        report_rows.append(report)
        if match.status not in {"ok_exact", "ok_reconciled"}:
            rerun_rows.append(report)
    return report_rows, rerun_rows


def batch_is_resolved(batch_dir: Path) -> tuple[bool, int, int]:
    runtime_index = batch_dir / "pdf_asset_index_runtime.csv"
    batch_index = batch_dir / "pdf_asset_index_batch.csv"
    index_path = runtime_index if runtime_index.exists() else batch_index
    rows = read_csv(index_path)
    resolved = 0
    unresolved = 0
    for row in rows:
        asset_path = row.get("asset_path") or row.get("pdf_path")
        if not asset_path:
            unresolved += 1
            continue
        pdf_path = Path(asset_path)
        if not pdf_path.is_absolute():
            rel = row.get("relative_asset_path") or row.get("pdf_relative") or ""
            pdf_path = ASSET_ROOT / rel if rel else pdf_path
        try:
            output_parent, expected_md = expected_paths(pdf_path, OUTPUT_ROOT)
        except ValueError:
            unresolved += 1
            continue
        match = match_output(pdf_path, output_parent, expected_md)
        if match.status in {"ok_exact", "ok_reconciled"}:
            resolved += 1
        else:
            unresolved += 1
    return unresolved == 0, resolved, unresolved


def move_resolved_partials(*, dry_run: bool) -> list[dict[str, object]]:
    moves: list[dict[str, object]] = []
    partial_root = BATCH_ROOT / "local_partial"
    done_root = BATCH_ROOT / "local_done"
    done_root.mkdir(parents=True, exist_ok=True)
    if not partial_root.exists():
        return moves
    for batch_dir in sorted(partial_root.glob("mineru_remote_batch_*")):
        ok, resolved, unresolved = batch_is_resolved(batch_dir)
        destination = done_root / batch_dir.name
        action = "move_to_done" if ok else "keep_partial"
        if ok and destination.exists():
            action = "done_destination_exists"
        elif ok and not dry_run:
            shutil.move(str(batch_dir), str(destination))
        moves.append(
            {
                "batch_name": batch_dir.name,
                "resolved": resolved,
                "unresolved": unresolved,
                "action": action,
                "source": str(batch_dir),
                "destination": str(destination) if ok else "",
            }
        )
    return moves


def main() -> None:
    args = parse_args()
    global ASSET_ROOT, PDF_ROOT, OUTPUT_ROOT, REGISTRY_ROOT, RUN_ROOT, BATCH_ROOT
    ASSET_ROOT = args.asset_root.expanduser().resolve()
    PDF_ROOT = ASSET_ROOT / "10_official_pdf" / "by_official_catalog"
    OUTPUT_ROOT = ASSET_ROOT / "20_mineru_output" / "by_official_catalog"
    REGISTRY_ROOT = ASSET_ROOT / "Registry"
    RUN_ROOT = REGISTRY_ROOT / "mineru_runs"
    BATCH_ROOT = REGISTRY_ROOT / "mineru_remote_batches"

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = RUN_ROOT / "reconciliations" / stamp
    report_rows, rerun_rows = reconcile_errors(args.scope)
    move_rows = move_resolved_partials(dry_run=args.dry_run) if args.move_resolved_partials else []

    report_csv = out_dir / f"mineru_error_reconciliation__{args.scope}__{stamp}.csv"
    rerun_csv = out_dir / f"mineru_unresolved_for_rerun__{args.scope}__{stamp}.csv"
    moves_csv = out_dir / f"mineru_partial_batch_moves__{args.scope}__{stamp}.csv"
    write_csv(report_csv, report_rows)
    write_csv(rerun_csv, rerun_rows, fieldnames=list(report_rows[0].keys()) if report_rows else [])
    if move_rows:
        write_csv(moves_csv, move_rows)

    counts: dict[str, int] = {}
    for row in report_rows:
        status = str(row["reconciled_status"])
        counts[status] = counts.get(status, 0) + 1
    summary = {
        "status": "ok",
        "scope": args.scope,
        "asset_root": str(ASSET_ROOT),
        "error_rows": len(report_rows),
        "reconciled_statuses": counts,
        "unresolved_rows": len(rerun_rows),
        "moved_batches": sum(1 for row in move_rows if row.get("action") == "move_to_done"),
        "kept_partial_batches": sum(1 for row in move_rows if row.get("action") == "keep_partial"),
        "dry_run": args.dry_run,
        "report_csv": str(report_csv),
        "rerun_csv": str(rerun_csv),
        "moves_csv": str(moves_csv) if move_rows else "",
    }
    summary_json = out_dir / f"mineru_error_reconciliation_summary__{args.scope}__{stamp}.json"
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
