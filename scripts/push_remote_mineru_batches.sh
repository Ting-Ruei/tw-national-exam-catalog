#!/bin/bash
# Push prepared MinerU remote batches from controller to a remote worker, then
# move them from outgoing/ to assigned/<worker>/ locally.
#
# Usage:
#   bash scripts/push_remote_mineru_batches.sh 100.96.207.80
#   BATCH_LIMIT=2 bash scripts/push_remote_mineru_batches.sh 100.96.207.80

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <remote-host>"
  exit 2
fi

REMOTE_HOST="$1"
REMOTE_USER="${REMOTE_USER:-tim}"
REMOTE_LABEL="${REMOTE_LABEL:-${REMOTE_HOST//./-}}"
REMOTE_WORKER_ROOT="${REMOTE_WORKER_ROOT:-/Users/tim/AI_workspace/national_exam_mineru_worker}"
BATCH_LIMIT="${BATCH_LIMIT:-0}"

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BATCH_ROOT="$PROJECT_ROOT/國考題資料夾/Registry/mineru_remote_batches"
OUTGOING_ROOT="$BATCH_ROOT/outgoing"
ASSIGNED_ROOT="$BATCH_ROOT/assigned/$REMOTE_LABEL"

mkdir -p "$OUTGOING_ROOT" "$ASSIGNED_ROOT"

BATCH_DIRS=()
while IFS= read -r line; do
  BATCH_DIRS+=("$line")
done < <(find "$OUTGOING_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'mineru_remote_batch_*' | sort)

if [[ ${#BATCH_DIRS[@]} -eq 0 ]]; then
  echo "No outgoing batches found."
  exit 0
fi

count=0
for batch_dir in "${BATCH_DIRS[@]}"; do
  if [[ "$BATCH_LIMIT" -gt 0 && "$count" -ge "$BATCH_LIMIT" ]]; then
    break
  fi

  batch_name="$(basename "$batch_dir")"
  remote_dest="$REMOTE_USER@$REMOTE_HOST:$REMOTE_WORKER_ROOT/incoming_batches/$batch_name/"

  echo "Pushing $batch_name -> $REMOTE_HOST"
  rsync -avh --partial --progress "$batch_dir/" "$remote_dest"

  mv "$batch_dir" "$ASSIGNED_ROOT/$batch_name"
  echo "Assigned locally: $ASSIGNED_ROOT/$batch_name"
  count=$((count + 1))
done

echo "Pushed $count batch(es) to $REMOTE_HOST"
