#!/usr/bin/env python3
"""Audit MinerU batch directories against result CSVs.

The queue scripts classify whole batches. This audit compares batch manifests
against the latest known MinerU result CSVs and reports whether a batch is
fully successful, partially successful, or fully failed.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BATCH_ROOT = PROJECT_ROOT / "國考題資料夾" / "Registry" / "mineru_remote_batches"
RUN_ROOT = PROJECT_ROOT / "國考題資料夾" / "Registry" / "mineru_runs"
LOCAL_FAILED_ROOT = BATCH_ROOT / "local_failed"
LOCAL_DONE_ROOT = BATCH_ROOT / "local_done"


@dataclass
class BatchAudit:
    batch_name: str
    batch_dir: Path
    total: int
    ok: int
    skipped: int
    error: int
    timeout: int
    missing: int
    classification: str
    result_csvs: int


def read_pdf_paths(batch_dir: Path) -> list[str]:
    candidates = [
        batch_dir / "pdf_asset_index_batch.csv",
        batch_dir / "question_answer_pairs_batch.csv",
        batch_dir / "batch_manifest.csv",
    ]
    for path in candidates:
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            continue
        keys = [
            "pdf_path",
            "asset_path",
            "question_pdf",
            "answer_pdf_primary",
            "answer_pdf_ans",
            "answer_pdf_mod",
        ]
        result: list[str] = []
        for row in rows:
            for key in keys:
                value = (row.get(key) or "").strip()
                if value:
                    path = Path(value)
                    if not path.is_absolute():
                        path = (PROJECT_ROOT / path).resolve()
                    result.append(str(path))
        if result:
            seen = set()
            ordered: list[str] = []
            for item in result:
                if item not in seen:
                    seen.add(item)
                    ordered.append(item)
            return ordered
    return []


def build_latest_status_map(run_root: Path) -> tuple[dict[str, str], int]:
    status_map: dict[str, str] = {}
    csv_paths = sorted(run_root.glob("**/mineru_results__*.csv"), key=lambda p: p.stat().st_mtime)
    for path in csv_paths:
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                pdf_path = (row.get("pdf_path") or "").strip()
                status = (row.get("status") or "").strip()
                if pdf_path:
                    status_map[pdf_path] = status
    return status_map, len(csv_paths)


def audit_batch(batch_dir: Path, status_map: dict[str, str], result_csv_count: int) -> BatchAudit:
    pdf_paths = read_pdf_paths(batch_dir)
    counts = Counter()
    missing = 0
    for pdf_path in pdf_paths:
        status = status_map.get(pdf_path)
        if status is None:
            missing += 1
            counts["missing"] += 1
            continue
        counts[status] += 1

    total = len(pdf_paths)
    ok = counts.get("ok", 0)
    skipped = counts.get("skipped_existing", 0)
    error = counts.get("error", 0)
    timeout = counts.get("timeout", 0)

    if total == 0 or missing == total:
        classification = "unknown"
    elif error == 0 and timeout == 0:
        classification = "done"
    elif ok + skipped > 0:
        classification = "partial"
    else:
        classification = "failed"

    return BatchAudit(
        batch_name=batch_dir.name,
        batch_dir=batch_dir,
        total=total,
        ok=ok,
        skipped=skipped,
        error=error,
        timeout=timeout,
        missing=missing,
        classification=classification,
        result_csvs=result_csv_count,
    )


def move_batch(src: Path, dest_root: Path) -> Path:
    dest = dest_root / src.name
    dest_root.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise FileExistsError(dest)
    src.rename(dest)
    return dest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix", action="store_true", help="Move purely successful failed batches back to local_done.")
    parser.add_argument("--root", type=Path, default=LOCAL_FAILED_ROOT, help="Batch root to audit.")
    parser.add_argument("--report", type=Path, default=BATCH_ROOT / "batch_state_audit.csv")
    args = parser.parse_args()

    status_map, result_csv_count = build_latest_status_map(RUN_ROOT)
    audits: list[BatchAudit] = []
    for batch_dir in sorted(p for p in args.root.iterdir() if p.is_dir()):
        audits.append(audit_batch(batch_dir, status_map, result_csv_count))

    with args.report.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "batch_name",
            "classification",
            "total",
            "ok",
            "skipped",
            "error",
            "timeout",
            "missing",
            "result_csvs",
            "batch_dir",
        ])
        for audit in audits:
            writer.writerow([
                audit.batch_name,
                audit.classification,
                audit.total,
                audit.ok,
                audit.skipped,
                audit.error,
                audit.timeout,
                audit.missing,
                audit.result_csvs,
                str(audit.batch_dir),
            ])

    if args.fix:
        moved = 0
        for audit in audits:
            if audit.classification != "done":
                continue
            move_batch(audit.batch_dir, LOCAL_DONE_ROOT)
            moved += 1
        print(f"moved={moved}")
    else:
        summary = Counter(a.classification for a in audits)
        print(json.dumps(summary, ensure_ascii=False))
        print(str(args.report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
