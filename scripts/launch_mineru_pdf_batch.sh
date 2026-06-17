#!/bin/bash
# Launch a background MinerU PDF batch for the national exam catalog.
#
# Default:
#   paired-primary scope, 2 workers, then all-official supplement.
#
# Usage:
#   bash scripts/launch_mineru_pdf_batch.sh
#   bash scripts/launch_mineru_pdf_batch.sh all-official 2
#   bash scripts/launch_mineru_pdf_batch.sh paired-primary 2 "--group 醫事檢驗師 --limit 20"
#   bash scripts/launch_mineru_pdf_batch.sh paired-primary 2 "--chain-all-official"

set -euo pipefail

SCOPE="${1:-paired-primary}"
WORKERS="${2:-2}"
EXTRA_ARGS="${3:-}"

if [[ "$SCOPE" == "paired-primary" && "$EXTRA_ARGS" != *"--chain-all-official"* ]]; then
  if [[ -n "$EXTRA_ARGS" ]]; then
    EXTRA_ARGS="$EXTRA_ARGS --chain-all-official"
  else
    EXTRA_ARGS="--chain-all-official"
  fi
fi

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date '+%Y%m%d-%H%M%S')"
LOG_DIR="$PROJECT_ROOT/國考題資料夾/Registry/mineru_runs/background_logs"
LOG_FILE="$LOG_DIR/mineru_batch__${SCOPE}__w${WORKERS}__${STAMP}.log"
PID_FILE="$LOG_DIR/mineru_batch__${SCOPE}__w${WORKERS}__${STAMP}.pid"

mkdir -p "$LOG_DIR"
cd "$PROJECT_ROOT"

echo "Launching MinerU batch"
echo "  scope:   $SCOPE"
echo "  workers: $WORKERS"
echo "  log:     $LOG_FILE"
echo "  pid:     $PID_FILE"

# shellcheck disable=SC2086
nohup python3 -u scripts/run_mineru_pdf_batch.py \
  --scope "$SCOPE" \
  --workers "$WORKERS" \
  $EXTRA_ARGS \
  >> "$LOG_FILE" 2>&1 &

PID=$!
echo "$PID" > "$PID_FILE"
echo "Started PID: $PID"
echo "Follow log: tail -f '$LOG_FILE'"
echo "Stop: kill \$(cat '$PID_FILE')"
