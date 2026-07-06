#!/usr/bin/env python3
"""
Start PDF restoration from the historical asset index as a background process.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = Path(os.environ.get("ASSET_ROOT", PROJECT_ROOT / "國考題資料夾_非醫學剩餘全集")).expanduser()
INDEX = Path(
    os.environ.get(
        "PDF_ASSET_INDEX",
        ASSET_ROOT / "Registry" / "pdf_indexes" / "pdf_asset_index_detail__20260626-110327.csv",
    )
).expanduser()
WORKERS = os.environ.get("RESTORE_PDF_WORKERS", "4")
RETRIES = os.environ.get("RESTORE_PDF_RETRIES", "4")
TIMEOUT = os.environ.get("RESTORE_PDF_TIMEOUT", "90")
SLEEP = os.environ.get("RESTORE_PDF_SLEEP", "0.15")
LIMIT = os.environ.get("RESTORE_PDF_LIMIT", "")


def main() -> None:
    registry_root = ASSET_ROOT / "Registry"
    log_root = registry_root / "processing_logs"
    log_root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = log_root / f"restore_moex_pdfs_from_asset_index_launcher__{stamp}.log"
    pid_path = registry_root / "restore_moex_pdfs_from_asset_index__active.pid"

    cmd = [
        "python3",
        "-u",
        "scripts/restore_moex_pdfs_from_asset_index.py",
        "--asset-root",
        str(ASSET_ROOT),
        "--index",
        str(INDEX),
        "--workers",
        WORKERS,
        "--retries",
        RETRIES,
        "--timeout",
        TIMEOUT,
        "--sleep",
        SLEEP,
    ]
    if LIMIT:
        cmd.extend(["--limit", LIMIT])

    with log_path.open("ab") as log_file:
        proc = subprocess.Popen(
            cmd,
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    pid_path.write_text(f"{proc.pid}\n", encoding="utf-8")
    print(f"pid={proc.pid}")
    print(f"asset_root={ASSET_ROOT}")
    print(f"index={INDEX}")
    print(f"log={log_path}")
    print(f"pid_file={pid_path}")


if __name__ == "__main__":
    main()
