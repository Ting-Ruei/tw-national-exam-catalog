#!/bin/bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATUS_DIR="$PROJECT_ROOT/國考題資料夾/Registry/mineru_runs/status_snapshots"
STAMP="$(date '+%Y%m%d-%H%M%S')"

mkdir -p "$STATUS_DIR"
cd "$PROJECT_ROOT"

python3 scripts/report_mineru_status.py --write-snapshot --write-latest \
  > "$STATUS_DIR/mineru_status__cron_stdout__${STAMP}.json"
