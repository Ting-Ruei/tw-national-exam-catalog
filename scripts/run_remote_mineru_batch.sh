#!/bin/bash
# Run one received MinerU remote batch on the worker machine.
#
# Usage on the remote worker:
#   bash scripts/run_remote_mineru_batch.sh mineru_remote_batch_001
#   WORKERS=1 bash scripts/run_remote_mineru_batch.sh mineru_remote_batch_001

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <batch-name-or-path>"
  exit 2
fi

WORKSPACE_ROOT="${AI_WORKSPACE_ROOT:-/Users/tim/AI_workspace}"
WORKER_ROOT="${WORKER_ROOT:-$WORKSPACE_ROOT/national_exam_mineru_worker}"
MINERU_BIN="${MINERU_BIN:-$WORKSPACE_ROOT/OCR_model/MinerU/venv_mineru/bin/mineru}"
REMOTE_ASSET_ROOT="${REMOTE_ASSET_ROOT:-$WORKER_ROOT/repo/國考題資料夾}"
WORKERS="${WORKERS:-2}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-900}"
SCOPE="${SCOPE:-all-official}"

BATCH_ARG="$1"
if [[ "$BATCH_ARG" = /* ]]; then
  BATCH_PATH="$BATCH_ARG"
  BATCH_NAME="$(basename "$BATCH_PATH")"
else
  BATCH_NAME="$BATCH_ARG"
  BATCH_PATH="$WORKER_ROOT/incoming_batches/$BATCH_NAME"
fi

RUNNING_PATH="$WORKER_ROOT/running_batches/$BATCH_NAME"
FINISHED_PATH="$WORKER_ROOT/finished_batches/$BATCH_NAME"
LOG_FILE="$WORKER_ROOT/logs/${BATCH_NAME}__$(date '+%Y%m%d-%H%M%S').log"
RUNTIME_PDF_INDEX="$RUNNING_PATH/pdf_asset_index_runtime.csv"

if [[ ! -x "$MINERU_BIN" ]]; then
  echo "MinerU executable not found: $MINERU_BIN"
  exit 1
fi

if [[ ! -d "$BATCH_PATH" && -d "$RUNNING_PATH" ]]; then
  BATCH_PATH="$RUNNING_PATH"
fi

if [[ ! -d "$BATCH_PATH" ]]; then
  echo "Batch not found: $BATCH_ARG"
  exit 1
fi

if [[ "$BATCH_PATH" != "$RUNNING_PATH" ]]; then
  if [[ -e "$RUNNING_PATH" ]]; then
    echo "Running path already exists: $RUNNING_PATH"
    exit 1
  fi
  mv "$BATCH_PATH" "$RUNNING_PATH"
fi

cd "$RUNNING_PATH"

if [[ ! -f "scripts/run_mineru_pdf_batch.py" ]]; then
  echo "Missing scripts/run_mineru_pdf_batch.py in batch: $RUNNING_PATH"
  exit 1
fi

echo "Running remote MinerU batch" | tee "$LOG_FILE"
echo "  batch:  $BATCH_NAME" | tee -a "$LOG_FILE"
echo "  scope:  $SCOPE" | tee -a "$LOG_FILE"
echo "  workers:$WORKERS" | tee -a "$LOG_FILE"
echo "  mineru: $MINERU_BIN" | tee -a "$LOG_FILE"
echo "  assets: $REMOTE_ASSET_ROOT" | tee -a "$LOG_FILE"

python3 - "$RUNNING_PATH/pdf_asset_index_batch.csv" "$RUNTIME_PDF_INDEX" "$REMOTE_ASSET_ROOT" <<'PY'
import csv
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
asset_root = Path(sys.argv[3])

with src.open(encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))

fieldnames = rows[0].keys() if rows else []
with dst.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        relative = row.get("relative_asset_path", "")
        row["asset_path"] = str((asset_root / relative).resolve())
        writer.writerow(row)
PY

python3 -u scripts/run_mineru_pdf_batch.py \
  --scope "$SCOPE" \
  --workers "$WORKERS" \
  --mineru-bin "$MINERU_BIN" \
  --pdf-index "$RUNTIME_PDF_INDEX" \
  --output-root "國考題資料夾/20_mineru_output/by_official_catalog" \
  --timeout-seconds "$TIMEOUT_SECONDS" \
  2>&1 | tee -a "$LOG_FILE"

mkdir -p "$(dirname "$FINISHED_PATH")"
if [[ -e "$FINISHED_PATH" ]]; then
  echo "Finished path already exists: $FINISHED_PATH"
  exit 1
fi
mv "$RUNNING_PATH" "$FINISHED_PATH"

echo "Finished: $FINISHED_PATH" | tee -a "$LOG_FILE"
