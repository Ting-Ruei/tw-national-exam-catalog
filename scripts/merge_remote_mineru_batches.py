#!/usr/bin/env python3
"""
Merge returned MinerU remote batches into the controller's local output tree and
materialize a normalized result CSV with local absolute paths.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = PROJECT_ROOT / "國考題資料夾"
OUTPUT_ROOT = ASSET_ROOT / "20_mineru_output" / "by_official_catalog"
RUN_ROOT = ASSET_ROOT / "Registry" / "mineru_runs"
REMOTE_BATCH_ROOT = ASSET_ROOT / "Registry" / "mineru_remote_batches"
RETURNED_ROOT = REMOTE_BATCH_ROOT / "returned"
MERGED_ROOT = REMOTE_BATCH_ROOT / "merged"
REMOTE_IMPORT_ROOT = RUN_ROOT / "remote_imports"

RESULT_FIELDS = [
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
    parser = argparse.ArgumentParser(description="Merge returned MinerU remote batches into local output.")
    parser.add_argument("--worker", help="Filter returned/<worker>.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def copy_tree(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    for path in src.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(src)
        out_path = dst / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, out_path)


def batch_result_csv(batch_dir: Path) -> Path | None:
    matches = sorted((batch_dir / "國考題資料夾" / "Registry" / "mineru_runs").glob("**/mineru_results__*.csv"))
    return matches[-1] if matches else None


def local_output_paths(pdf_relative: str) -> tuple[Path, Path]:
    pdf_relative_path = Path(pdf_relative)
    stem = pdf_relative_path.stem
    output_parent = OUTPUT_ROOT / pdf_relative_path.parent.relative_to("10_official_pdf/by_official_catalog")
    expected_md = output_parent / stem / "vlm" / f"{stem}.md"
    return output_parent, expected_md


def merge_batch(batch_dir: Path, merged_rows: list[dict[str, object]], dry_run: bool) -> dict[str, object]:
    manifest_path = batch_dir / "batch_manifest.csv"
    result_csv = batch_result_csv(batch_dir)
    if not manifest_path.exists() or result_csv is None:
        return {"batch": batch_dir.name, "status": "skipped_missing_files"}

    manifest_rows = read_csv(manifest_path)
    manifest_by_relative = {row["pdf_relative"]: row for row in manifest_rows}
    result_rows = read_csv(result_csv)
    imported = 0

    for row in result_rows:
        pdf_path = Path(row["pdf_path"])
        try:
            relative_pdf = str(pdf_path.relative_to(batch_dir / "國考題資料夾"))
        except ValueError:
            continue

        if manifest_by_relative.get(relative_pdf) is None:
            continue

        local_pdf_path = (ASSET_ROOT / relative_pdf).resolve()
        local_output_parent, local_expected_md = local_output_paths(relative_pdf)
        remote_output_dir = Path(row["output_parent"]) / local_pdf_path.stem
        local_output_dir = local_output_parent / local_pdf_path.stem

        if not dry_run:
            copy_tree(remote_output_dir, local_output_dir)

        merged_rows.append(
            {
                "task_id": row.get("task_id", ""),
                "status": row.get("status", ""),
                "returncode": row.get("returncode", ""),
                "elapsed_seconds": row.get("elapsed_seconds", ""),
                "md_count": row.get("md_count", ""),
                "image_count": row.get("image_count", ""),
                "pdf_path": str(local_pdf_path),
                "output_parent": str(local_output_parent),
                "expected_md": str(local_expected_md),
                "error_tail": row.get("error_tail", ""),
            }
        )
        imported += 1

    return {"batch": batch_dir.name, "status": "merged", "rows": imported}


def main() -> None:
    args = parse_args()
    worker_roots = [RETURNED_ROOT / args.worker] if args.worker else sorted(path for path in RETURNED_ROOT.iterdir() if path.is_dir()) if RETURNED_ROOT.exists() else []
    batch_dirs: list[Path] = []
    for worker_root in worker_roots:
        batch_dirs.extend(sorted(path for path in worker_root.iterdir() if path.is_dir() and path.name.startswith("mineru_remote_batch_")))

    if args.limit:
        batch_dirs = batch_dirs[: args.limit]

    merged_rows: list[dict[str, object]] = []
    batch_summaries: list[dict[str, object]] = []
    for batch_dir in batch_dirs:
        summary = merge_batch(batch_dir, merged_rows, args.dry_run)
        batch_summaries.append(summary)

        worker_label = batch_dir.parent.name
        merged_dir = MERGED_ROOT / worker_label / batch_dir.name
        if summary.get("status") == "merged" and not args.dry_run:
            merged_dir.parent.mkdir(parents=True, exist_ok=True)
            if merged_dir.exists():
                raise SystemExit(f"Merged destination already exists: {merged_dir}")
            shutil.move(str(batch_dir), str(merged_dir))

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = REMOTE_IMPORT_ROOT / stamp
    summary = {
        "generated_at": stamp,
        "batch_count": len(batch_dirs),
        "merged_rows": len(merged_rows),
        "dry_run": args.dry_run,
        "batches": batch_summaries,
    }
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        write_csv(out_dir / f"mineru_results__remote-merge__{stamp}.csv", merged_rows)
        (out_dir / f"remote_merge_summary__{stamp}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
