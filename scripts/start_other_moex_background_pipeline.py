#!/usr/bin/env python3
"""
Start the other-MOEX download/index/MinerU pipeline as a detached process.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = Path(os.environ.get("ASSET_ROOT", PROJECT_ROOT / "國考題資料夾_其他類型")).expanduser()
REGISTRY_ROOT = ASSET_ROOT / "Registry"
LOG_ROOT = REGISTRY_ROOT / "processing_logs"
PID_PATH = REGISTRY_ROOT / "other_moex_background_pipeline__active.pid"


def main() -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    pipeline_log = LOG_ROOT / f"other_moex_background_pipeline__{stamp}.log"
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    (REGISTRY_ROOT / "mineru_remote_batches" / "outgoing").mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.setdefault("ASSET_ROOT", str(ASSET_ROOT))
    env.setdefault("BATCH_SIZE", "25")
    env.setdefault("BATCH_COUNT", "9999")

    with pipeline_log.open("ab") as log_file:
        proc = subprocess.Popen(
            ["bash", "scripts/run_other_moex_pipeline.sh"],
            cwd=PROJECT_ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    PID_PATH.write_text(f"{proc.pid}\n", encoding="utf-8")
    print(f"pid={proc.pid}")
    print(f"pipeline_log={pipeline_log}")
    print(f"pid_file={PID_PATH}")


if __name__ == "__main__":
    main()
