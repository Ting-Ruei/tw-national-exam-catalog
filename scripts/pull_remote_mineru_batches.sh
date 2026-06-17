#!/bin/bash
# Pull finished MinerU remote batches back from a remote worker to the
# controller's returned/<worker>/ area.
#
# Usage:
#   bash scripts/pull_remote_mineru_batches.sh 100.96.207.80

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <remote-host>"
  exit 2
fi

REMOTE_HOST="$1"
REMOTE_USER="${REMOTE_USER:-tim}"
REMOTE_LABEL="${REMOTE_LABEL:-${REMOTE_HOST//./-}}"
REMOTE_WORKER_ROOT="${REMOTE_WORKER_ROOT:-/Users/tim/AI_workspace/national_exam_mineru_worker}"

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BATCH_ROOT="$PROJECT_ROOT/國考題資料夾/Registry/mineru_remote_batches"
RETURNED_ROOT="$BATCH_ROOT/returned/$REMOTE_LABEL"

mkdir -p "$RETURNED_ROOT"

REMOTE_BATCH_NAMES=()
while IFS= read -r line; do
  REMOTE_BATCH_NAMES+=("$line")
done < <(
  ssh "$REMOTE_USER@$REMOTE_HOST" \
    "find '$REMOTE_WORKER_ROOT/finished_batches' -mindepth 1 -maxdepth 1 -type d -name 'mineru_remote_batch_*' -exec basename {} \\; | sort"
)

if [[ ${#REMOTE_BATCH_NAMES[@]} -eq 0 ]]; then
  echo "No finished batches found on $REMOTE_HOST."
  exit 0
fi

count=0
for batch_name in "${REMOTE_BATCH_NAMES[@]}"; do
  local_dest="$RETURNED_ROOT/$batch_name/"
  remote_src="$REMOTE_USER@$REMOTE_HOST:$REMOTE_WORKER_ROOT/finished_batches/$batch_name/"

  echo "Pulling $batch_name <- $REMOTE_HOST"
  rsync -avh --partial --progress "$remote_src" "$local_dest"
  count=$((count + 1))
done

echo "Pulled $count batch(es) from $REMOTE_HOST"
