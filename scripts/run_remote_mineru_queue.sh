#!/bin/bash
# Continuously consume incoming MinerU batches on the remote worker until the
# queue is empty.
#
# Usage:
#   bash scripts/run_remote_mineru_queue.sh
#   WORKERS=1 bash scripts/run_remote_mineru_queue.sh

set -euo pipefail

WORKSPACE_ROOT="${AI_WORKSPACE_ROOT:-/Users/tim/AI_workspace}"
WORKER_ROOT="${WORKER_ROOT:-$WORKSPACE_ROOT/national_exam_mineru_worker}"
REPO_ROOT="${REPO_ROOT:-$WORKER_ROOT/repo}"
QUEUE_LOG="${QUEUE_LOG:-$WORKER_ROOT/logs/remote_queue__$(date '+%Y%m%d-%H%M%S').log}"

mkdir -p "$(dirname "$QUEUE_LOG")"
cd "$REPO_ROOT"

echo "Starting remote MinerU queue" | tee "$QUEUE_LOG"
echo "  worker_root: $WORKER_ROOT" | tee -a "$QUEUE_LOG"

while true; do
  mapfile -t BATCH_NAMES < <(
    find "$WORKER_ROOT/incoming_batches" -mindepth 1 -maxdepth 1 -type d -name 'mineru_remote_batch_*' -exec basename {} \; | sort
  )

  if [[ ${#BATCH_NAMES[@]} -eq 0 ]]; then
    echo "Queue empty, stopping." | tee -a "$QUEUE_LOG"
    break
  fi

  batch_name="${BATCH_NAMES[0]}"
  echo "Running next batch: $batch_name" | tee -a "$QUEUE_LOG"
  bash "$REPO_ROOT/scripts/run_remote_mineru_batch.sh" "$batch_name" 2>&1 | tee -a "$QUEUE_LOG"
done
