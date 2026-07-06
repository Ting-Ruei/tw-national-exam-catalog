#!/usr/bin/env python3
"""
Restore MOEX official PDFs from a previously built PDF asset index.

This is intentionally index-driven: it writes each PDF back to the exact
relative_asset_path recorded in the index so existing MinerU batch references
remain valid.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import ssl
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ASSET_ROOT = PROJECT_ROOT / "國考題資料夾_非醫學剩餘全集"
DEFAULT_INDEX = DEFAULT_ASSET_ROOT / "Registry" / "pdf_indexes" / "pdf_asset_index_detail__20260626-110327.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; tw-national-exam-catalog/0.1)"}


@dataclass(frozen=True)
class PdfRow:
    row_number: int
    source_url: str
    relative_asset_path: str
    expected_bytes: int | None
    expected_sha256: str
    group_name: str
    document_role: str


def ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


SSL_CTX = ssl_ctx()
LOG_LOCK = threading.Lock()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--asset-root", type=Path, default=DEFAULT_ASSET_ROOT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--sleep", type=float, default=0.1, help="Delay after each network attempt per worker.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--strict-index-hash",
        action="store_true",
        help="Fail instead of saving when the current MOEX response differs from the old index hash/bytes.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def read_rows(index_path: Path, limit: int = 0) -> list[PdfRow]:
    rows: list[PdfRow] = []
    with index_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row_number, row in enumerate(reader, start=2):
            bytes_text = row.get("bytes", "").strip()
            expected_bytes = int(bytes_text) if bytes_text.isdigit() else None
            rows.append(
                PdfRow(
                    row_number=row_number,
                    source_url=row["source_url"],
                    relative_asset_path=row["relative_asset_path"],
                    expected_bytes=expected_bytes,
                    expected_sha256=row.get("sha256", "").strip(),
                    group_name=row.get("group_name", ""),
                    document_role=row.get("document_role", ""),
                )
            )
            if limit and len(rows) >= limit:
                break
    return rows


def safe_destination(asset_root: Path, relative_asset_path: str) -> Path:
    root = asset_root.resolve()
    destination = (asset_root / relative_asset_path).resolve()
    if root != destination and root not in destination.parents:
        raise ValueError(f"path escapes asset root: {relative_asset_path}")
    return destination


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def existing_status(path: Path, row: PdfRow, overwrite: bool) -> tuple[str, int, str] | None:
    if overwrite or not path.exists():
        return None
    actual_bytes = path.stat().st_size
    actual_sha = sha256_file(path)
    if row.expected_sha256 and actual_sha == row.expected_sha256:
        return "exists_verified", actual_bytes, actual_sha
    return None


def write_jsonl(path: Path, record: dict[str, object]) -> None:
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    with LOG_LOCK:
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def fetch(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=timeout) as response:
        return response.read()


def restore_one(
    row: PdfRow,
    asset_root: Path,
    run_log: Path,
    retries: int,
    timeout: float,
    sleep: float,
    overwrite: bool,
    strict_index_hash: bool,
    dry_run: bool,
) -> dict[str, object]:
    destination = safe_destination(asset_root, row.relative_asset_path)
    base_record: dict[str, object] = {
        "row_number": row.row_number,
        "source_url": row.source_url,
        "relative_asset_path": row.relative_asset_path,
        "group_name": row.group_name,
        "document_role": row.document_role,
    }

    existing = existing_status(destination, row, overwrite)
    if existing:
        status, actual_bytes, actual_sha = existing
        record = {**base_record, "status": status, "bytes": actual_bytes, "sha256": actual_sha}
        write_jsonl(run_log, record)
        return record

    if dry_run:
        record = {**base_record, "status": "would_download"}
        write_jsonl(run_log, record)
        return record

    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            payload = fetch(row.source_url, timeout)
            actual_sha = sha256_bytes(payload)
            actual_bytes = len(payload)
            warnings = []
            if row.expected_sha256 and actual_sha != row.expected_sha256:
                warnings.append(f"sha256 mismatch expected={row.expected_sha256} actual={actual_sha}")
            if row.expected_bytes is not None and actual_bytes != row.expected_bytes:
                warnings.append(f"byte mismatch expected={row.expected_bytes} actual={actual_bytes}")
            if warnings and strict_index_hash:
                raise ValueError("; ".join(warnings))
            destination.parent.mkdir(parents=True, exist_ok=True)
            tmp = destination.with_name(destination.name + ".part")
            tmp.write_bytes(payload)
            tmp.replace(destination)
            record = {
                **base_record,
                "status": "downloaded_with_index_mismatch" if warnings else "downloaded",
                "attempt": attempt,
                "bytes": actual_bytes,
                "sha256": actual_sha,
            }
            if warnings:
                record["warning"] = "; ".join(warnings)
            write_jsonl(run_log, record)
            return record
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if sleep:
                time.sleep(sleep * attempt)

    record = {**base_record, "status": "error", "error": last_error}
    write_jsonl(run_log, record)
    return record


def write_manifest(manifest_path: Path, records: list[dict[str, object]]) -> None:
    fields = [
        "status",
        "row_number",
        "group_name",
        "document_role",
        "relative_asset_path",
        "source_url",
        "bytes",
        "sha256",
        "attempt",
        "error",
    ]
    with manifest_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(record)


def main() -> int:
    args = parse_args()
    asset_root = args.asset_root.expanduser()
    registry_root = asset_root / "Registry"
    log_dir = registry_root / "processing_logs"
    manifest_dir = registry_root / "asset_manifests"
    log_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_log = log_dir / f"restore_moex_pdfs_from_asset_index__{stamp}.jsonl"
    manifest_path = manifest_dir / f"restore_moex_pdfs_from_asset_index__{stamp}.csv"

    rows = read_rows(args.index, args.limit)
    print(f"index={args.index}")
    print(f"asset_root={asset_root}")
    print(f"rows={len(rows)}")
    print(f"workers={args.workers}")
    print(f"run_log={run_log}")
    print(f"manifest={manifest_path}")

    records: list[dict[str, object]] = []
    counts: dict[str, int] = {}
    started = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [
            pool.submit(
                restore_one,
                row,
                asset_root,
                run_log,
                args.retries,
                args.timeout,
                args.sleep,
                args.overwrite,
                args.strict_index_hash,
                args.dry_run,
            )
            for row in rows
        ]
        for completed, future in enumerate(as_completed(futures), start=1):
            record = future.result()
            records.append(record)
            status = str(record.get("status", "unknown"))
            counts[status] = counts.get(status, 0) + 1
            if completed == 1 or completed % 250 == 0 or completed == len(futures):
                elapsed = max(time.time() - started, 0.001)
                rate = completed / elapsed
                print(
                    json.dumps(
                        {
                            "completed": completed,
                            "total": len(futures),
                            "rate_per_sec": round(rate, 2),
                            "counts": counts,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

    write_manifest(manifest_path, records)
    summary_path = log_dir / f"restore_moex_pdfs_from_asset_index__{stamp}.summary.json"
    summary = {
        "index": str(args.index),
        "asset_root": str(asset_root),
        "rows": len(rows),
        "workers": args.workers,
        "counts": counts,
        "run_log": str(run_log),
        "manifest": str(manifest_path),
        "elapsed_seconds": round(time.time() - started, 3),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"summary={summary_path}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if counts.get("error", 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
