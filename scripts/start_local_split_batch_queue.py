#!/usr/bin/env python3
"""
Start the local split MinerU queue as a detached background process.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = Path(os.environ.get("ASSET_ROOT", PROJECT_ROOT / "國考題資料夾")).expanduser()
BATCH_ROOT = ASSET_ROOT / "Registry" / "mineru_remote_batches"
PID_PATH = BATCH_ROOT / "local_queue__active.pid"


def main() -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    launcher_log = BATCH_ROOT / f"local_queue__launcher__{stamp}.log"
    queue_log = BATCH_ROOT / f"local_queue__{stamp}.log"
    launcher_log.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.setdefault("QUEUE_LOG", str(queue_log))

    with launcher_log.open("ab") as log_file:
        proc = subprocess.Popen(
            ["bash", "scripts/run_local_split_batch_queue.sh"],
            cwd=PROJECT_ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    PID_PATH.write_text(f"{proc.pid}\n", encoding="utf-8")
    print(f"pid={proc.pid}")
    print(f"launcher_log={launcher_log}")
    print(f"queue_log={queue_log}")
    print(f"pid_file={PID_PATH}")


if __name__ == "__main__":
    main()
