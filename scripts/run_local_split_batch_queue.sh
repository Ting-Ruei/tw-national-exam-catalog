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
BATCH_ROOT="$ASSET_ROOT/Registry/mineru_remote_batches"
OUTGOING_ROOT="$BATCH_ROOT/outgoing"
LOCAL_RUNNING_ROOT="$BATCH_ROOT/local_running"
LOCAL_DONE_ROOT="$BATCH_ROOT/local_done"
LOCAL_PARTIAL_ROOT="$BATCH_ROOT/local_partial"
LOCAL_FAILED_ROOT="$BATCH_ROOT/local_failed"
ASSIGNED_ROOT="$BATCH_ROOT/assigned"
QUEUE_LOG="${QUEUE_LOG:-$BATCH_ROOT/local_queue__$(date '+%Y%m%d-%H%M%S').log}"
WORKERS="${WORKERS:-2}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-900}"
MINERU_BIN="${MINERU_BIN:-$HOME/AI workspace/OCR_model/MinerU/venv_mineru/bin/mineru}"

mkdir -p "$OUTGOING_ROOT" "$LOCAL_RUNNING_ROOT" "$LOCAL_DONE_ROOT" "$LOCAL_PARTIAL_ROOT" "$LOCAL_FAILED_ROOT" "$ASSIGNED_ROOT"
mkdir -p "$(dirname "$QUEUE_LOG")"

batch_result_counts() {
  local csv_path="$1"
  python3 - "$csv_path" <<'PY'
import csv
import sys

path = sys.argv[1]
ok = skipped = error = timeout = 0
with open(path, encoding="utf-8-sig", newline="") as f:
    for row in csv.DictReader(f):
        status = (row.get("status") or "").strip()
        if status == "ok":
            ok += 1
        elif status == "skipped_existing":
            skipped += 1
        elif status == "error":
            error += 1
        elif status == "timeout":
            timeout += 1
print(f"{ok},{skipped},{error},{timeout}")
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

batch_scope() {
  local batch_dir="$1"
  python3 - "$batch_dir/batch_metadata.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
default_scope = "paired-primary"
if not path.exists():
    print(default_scope)
    raise SystemExit(0)

try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print(default_scope)
    raise SystemExit(0)

print(data.get("scope") or default_scope)
PY
}

pick_batch_dir() {
  local -a candidate_dirs=()

  while IFS= read -r line; do
    candidate_dirs+=("$line")
  done < <(find "$LOCAL_RUNNING_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'mineru_remote_batch_*' | sort -r)

  if [[ ${#candidate_dirs[@]} -gt 0 ]]; then
    printf '%s\n' "${candidate_dirs[0]}"
    return 0
  fi

  candidate_dirs=()
  while IFS= read -r line; do
    if find "$ASSIGNED_ROOT" -mindepth 2 -maxdepth 2 -type d -name "$(basename "$line")" | grep -q .; then
      echo "Skipping assigned batch: $(basename "$line")" >> "$QUEUE_LOG"
      continue
    fi
    candidate_dirs+=("$line")
  done < <(find "$OUTGOING_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'mineru_remote_batch_*' | sort -r)

  if [[ ${#candidate_dirs[@]} -eq 0 ]]; then
    return 1
  fi

  printf '%s\n' "${candidate_dirs[0]}"
}

echo "Starting local split batch queue" | tee "$QUEUE_LOG"
echo "  outgoing: $OUTGOING_ROOT" | tee -a "$QUEUE_LOG"
echo "  workers:  $WORKERS" | tee -a "$QUEUE_LOG"

while true; do
  if ! batch_dir="$(pick_batch_dir)"; then
    echo "Queue empty, stopping." | tee -a "$QUEUE_LOG"
    break
  fi

  batch_name="$(basename "$batch_dir")"
  batch_scope_name="$(batch_scope "$batch_dir")"
  running_dir="$LOCAL_RUNNING_ROOT/$batch_name"
  runtime_pdf_index="$running_dir/pdf_asset_index_runtime.csv"

  if [[ -e "$running_dir" && "$batch_dir" != "$running_dir" ]]; then
    echo "Running directory already exists: $running_dir" | tee -a "$QUEUE_LOG"
    exit 1
  fi

  if [[ "$batch_dir" != "$running_dir" ]]; then
    mv "$batch_dir" "$running_dir"
  fi
  echo "Running batch: $batch_name" | tee -a "$QUEUE_LOG"
  echo "  scope:  $batch_scope_name" | tee -a "$QUEUE_LOG"

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
    --scope "$batch_scope_name" \
    --workers "$WORKERS" \
    --mineru-bin "$MINERU_BIN" \
    --pdf-index "$runtime_pdf_index" \
    --output-root "$ASSET_ROOT/20_mineru_output/by_official_catalog" \
    --timeout-seconds "$TIMEOUT_SECONDS" \
    2>&1 | tee -a "$QUEUE_LOG"; then
    if ! after_latest="$(latest_result_csv_since "$run_started_epoch" "$ASSET_ROOT/Registry/mineru_runs")"; then
      mv "$running_dir" "$LOCAL_FAILED_ROOT/$batch_name"
      echo "Failed batch (no new result csv): $batch_name" | tee -a "$QUEUE_LOG"
      continue
    fi
    IFS=',' read -r ok_count skipped_count error_count timeout_count <<<"$(batch_result_counts "$after_latest")"
    total_ok=$((ok_count + skipped_count))
    total_bad=$((error_count + timeout_count))
    if [[ "$total_bad" -eq 0 ]]; then
      mv "$running_dir" "$LOCAL_DONE_ROOT/$batch_name"
      echo "Completed batch: $batch_name" | tee -a "$QUEUE_LOG"
    elif [[ "$total_ok" -eq 0 ]]; then
      mv "$running_dir" "$LOCAL_FAILED_ROOT/$batch_name"
      echo "Failed batch (all items failed): $batch_name" | tee -a "$QUEUE_LOG"
    else
      mv "$running_dir" "$LOCAL_PARTIAL_ROOT/$batch_name"
      echo "Partial batch (mixed success/error): $batch_name" | tee -a "$QUEUE_LOG"
    fi
  else
    mv "$running_dir" "$LOCAL_FAILED_ROOT/$batch_name"
    echo "Failed batch: $batch_name" | tee -a "$QUEUE_LOG"
    continue
  fi
done
