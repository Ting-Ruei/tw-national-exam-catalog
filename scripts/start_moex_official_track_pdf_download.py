#!/usr/bin/env python3
"""
Start official-track PDF downloads as a detached background process.

This downloads and organizes official PDFs only. It does not build MinerU
batches and does not run MinerU.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = Path(os.environ.get("ASSET_ROOT", PROJECT_ROOT / "國考題資料夾_非醫學剩餘全集")).expanduser()
TRACK_LIST = Path(
    os.environ.get("TRACK_LIST", PROJECT_ROOT / "catalogs" / "moex_official_category_track_summary__y100-115.csv")
).expanduser()
WORKING_SCOPE = os.environ.get("WORKING_SCOPE", "future_expansion")
TRACK_LIMIT = os.environ.get("TRACK_LIMIT", "")
DOCUMENT_LIMIT_PER_TRACK = os.environ.get("DOCUMENT_LIMIT_PER_TRACK", "")
SLEEP_SECONDS = os.environ.get("DOWNLOAD_SLEEP", "0.2")


def main() -> None:
    registry_root = ASSET_ROOT / "Registry"
    log_root = registry_root / "processing_logs"
    log_root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = log_root / f"official_track_pdf_download_launcher__{stamp}.log"
    pid_path = registry_root / "official_track_pdf_download__active.pid"

    cmd = [
        "python3",
        "-u",
        "scripts/download_moex_pdfs_from_official_track_list.py",
        "--asset-root",
        str(ASSET_ROOT),
        "--track-list",
        str(TRACK_LIST),
        "--sleep",
        SLEEP_SECONDS,
    ]
    if WORKING_SCOPE:
        cmd.extend(["--working-scope", WORKING_SCOPE])
    if TRACK_LIMIT:
        cmd.extend(["--track-limit", TRACK_LIMIT])
    if DOCUMENT_LIMIT_PER_TRACK:
        cmd.extend(["--document-limit-per-track", DOCUMENT_LIMIT_PER_TRACK])

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
    print(f"log={log_path}")
    print(f"pid_file={pid_path}")


if __name__ == "__main__":
    main()
