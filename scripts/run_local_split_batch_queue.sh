#!/bin/bash
# Consume locally split MinerU batches in order on this machine.
#
# Default behavior:
# - reads from Registry/mineru_remote_batches/outgoing
# - processes highest part number first, which corresponds to the front side of
#   the catalog because remote batches were created in reverse order
# - rewrites batch pdf indexes to local absolute asset paths
# - moves completed batches to local_done/

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ASSET_ROOT="${ASSET_ROOT:-$PROJECT_ROOT/國考題資料夾}"
BATCH_ROOT="$PROJECT_ROOT/國考題資料夾/Registry/mineru_remote_batches"
OUTGOING_ROOT="$BATCH_ROOT/outgoing"
LOCAL_RUNNING_ROOT="$BATCH_ROOT/local_running"
LOCAL_DONE_ROOT="$BATCH_ROOT/local_done"
LOCAL_FAILED_ROOT="$BATCH_ROOT/local_failed"
QUEUE_LOG="${QUEUE_LOG:-$BATCH_ROOT/local_queue__$(date '+%Y%m%d-%H%M%S').log}"
WORKERS="${WORKERS:-2}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-900}"
MINERU_BIN="${MINERU_BIN:-$HOME/AI workspace/OCR_model/MinerU/venv_mineru/bin/mineru}"

mkdir -p "$OUTGOING_ROOT" "$LOCAL_RUNNING_ROOT" "$LOCAL_DONE_ROOT" "$LOCAL_FAILED_ROOT"
mkdir -p "$(dirname "$QUEUE_LOG")"

batch_has_failures() {
  local csv_path="$1"
  python3 - "$csv_path" <<'PY'
import csv
import sys

path = sys.argv[1]
bad = 0
with open(path, encoding="utf-8-sig", newline="") as f:
    for row in csv.DictReader(f):
        if row.get("status") in {"error", "timeout"}:
            bad += 1
print(bad)
raise SystemExit(1 if bad else 0)
PY
}

latest_result_csv_since() {
  local since_epoch="$1"
  local run_root="$2"
  python3 - "$since_epoch" "$run_root" <<'PY'
import sys
from pathlib import Path

since_epoch = float(sys.argv[1])
run_root = Path(sys.argv[2])
candidates = []
for path in run_root.glob("**/mineru_results__*.csv"):
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        continue
    if mtime >= since_epoch:
        candidates.append((mtime, str(path)))

if not candidates:
    raise SystemExit(1)

candidates.sort()
print(candidates[-1][1])
PY
}

echo "Starting local split batch queue" | tee "$QUEUE_LOG"
echo "  outgoing: $OUTGOING_ROOT" | tee -a "$QUEUE_LOG"
echo "  workers:  $WORKERS" | tee -a "$QUEUE_LOG"

while true; do
  BATCH_DIRS=()
  while IFS= read -r line; do
    BATCH_DIRS+=("$line")
  done < <(find "$OUTGOING_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'mineru_remote_batch_*' | sort -r)

  if [[ ${#BATCH_DIRS[@]} -eq 0 ]]; then
    echo "Queue empty, stopping." | tee -a "$QUEUE_LOG"
    break
  fi

  batch_dir="${BATCH_DIRS[0]}"
  batch_name="$(basename "$batch_dir")"
  running_dir="$LOCAL_RUNNING_ROOT/$batch_name"
  runtime_pdf_index="$running_dir/pdf_asset_index_runtime.csv"

  if [[ -e "$running_dir" ]]; then
    echo "Running directory already exists: $running_dir" | tee -a "$QUEUE_LOG"
    exit 1
  fi

  mv "$batch_dir" "$running_dir"
  echo "Running batch: $batch_name" | tee -a "$QUEUE_LOG"

  python3 - "$running_dir/pdf_asset_index_batch.csv" "$runtime_pdf_index" "$ASSET_ROOT" <<'PY'
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

  run_started_epoch="$(date +%s)"

  if python3 -u "$PROJECT_ROOT/scripts/run_mineru_pdf_batch.py" \
    --scope all-official \
    --workers "$WORKERS" \
    --mineru-bin "$MINERU_BIN" \
    --pdf-index "$runtime_pdf_index" \
    --output-root "$ASSET_ROOT/20_mineru_output/by_official_catalog" \
    --timeout-seconds "$TIMEOUT_SECONDS" \
    2>&1 | tee -a "$QUEUE_LOG"; then
    if ! after_latest="$(latest_result_csv_since "$run_started_epoch" "$PROJECT_ROOT/國考題資料夾/Registry/mineru_runs")"; then
      mv "$running_dir" "$LOCAL_FAILED_ROOT/$batch_name"
      echo "Failed batch (no new result csv): $batch_name" | tee -a "$QUEUE_LOG"
      exit 1
    fi
    if batch_has_failures "$after_latest"; then
      mv "$running_dir" "$LOCAL_DONE_ROOT/$batch_name"
      echo "Completed batch: $batch_name" | tee -a "$QUEUE_LOG"
    else
      mv "$running_dir" "$LOCAL_FAILED_ROOT/$batch_name"
      echo "Failed batch (error statuses present): $batch_name" | tee -a "$QUEUE_LOG"
      exit 1
    fi
  else
    mv "$running_dir" "$LOCAL_FAILED_ROOT/$batch_name"
    echo "Failed batch: $batch_name" | tee -a "$QUEUE_LOG"
    exit 1
  fi
done
