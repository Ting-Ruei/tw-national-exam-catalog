#!/bin/bash
# Pull finished MinerU remote batches back from a remote worker to the
# controller's returned/<worker>/ area.
#
# Usage:
#   bash scripts/pull_remote_mineru_batches.sh 100.96.207.80
#   DRY_RUN=1 bash scripts/pull_remote_mineru_batches.sh 100.96.207.80
#   BATCH_LIMIT=5 MERGE_AFTER_PULL=1 bash scripts/pull_remote_mineru_batches.sh 100.96.207.80

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <remote-host>"
  exit 2
fi

REMOTE_HOST="$1"
REMOTE_USER="${REMOTE_USER:-tim}"
REMOTE_LABEL="${REMOTE_LABEL:-${REMOTE_HOST//./-}}"
REMOTE_WORKER_ROOT="${REMOTE_WORKER_ROOT:-/Users/tim/AI_workspace/national_exam_mineru_worker}"
REMOTE_FINISHED_ROOT="${REMOTE_FINISHED_ROOT:-$REMOTE_WORKER_ROOT/finished_batches}"
BATCH_LIMIT="${BATCH_LIMIT:-0}"
DRY_RUN="${DRY_RUN:-0}"
MERGE_AFTER_PULL="${MERGE_AFTER_PULL:-0}"
SKIP_MERGED="${SKIP_MERGED:-1}"
SKIP_RETURNED="${SKIP_RETURNED:-1}"
SKIP_LOCAL_DONE="${SKIP_LOCAL_DONE:-1}"
RSYNC_DELETE="${RSYNC_DELETE:-0}"
RSYNC_BIN="${RSYNC_BIN:-}"
RSYNC_SSH="${RSYNC_SSH:-}"

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BATCH_ROOT="$PROJECT_ROOT/國考題資料夾/Registry/mineru_remote_batches"
RETURNED_ROOT="$BATCH_ROOT/returned/$REMOTE_LABEL"
MERGED_ROOT="$BATCH_ROOT/merged/$REMOTE_LABEL"
LOCAL_DONE_ROOT="$BATCH_ROOT/local_done"

mkdir -p "$RETURNED_ROOT"

if [[ -z "$RSYNC_BIN" ]]; then
  if [[ -x /opt/homebrew/bin/rsync ]]; then
    RSYNC_BIN="/opt/homebrew/bin/rsync"
  else
    RSYNC_BIN="rsync"
  fi
fi

RSYNC_ARGS=(-avh --partial --progress)
if [[ "$DRY_RUN" == "1" ]]; then
  RSYNC_ARGS+=(--dry-run)
fi
if [[ "$RSYNC_DELETE" == "1" ]]; then
  RSYNC_ARGS+=(--delete)
fi
if [[ -n "$RSYNC_SSH" ]]; then
  RSYNC_ARGS+=(-e "$RSYNC_SSH")
fi

REMOTE_BATCH_NAMES=()
REMOTE_LIST_TMP="$(mktemp)"
SSH_CMD=(ssh)
if [[ -n "$RSYNC_SSH" ]]; then
  # shellcheck disable=SC2206
  SSH_CMD=($RSYNC_SSH)
fi

if ! "${SSH_CMD[@]}" "$REMOTE_USER@$REMOTE_HOST" \
  "find '$REMOTE_FINISHED_ROOT' -mindepth 1 -maxdepth 1 -type d -name 'mineru_remote_batch_*' -exec basename {} \\; | sort" \
  > "$REMOTE_LIST_TMP"; then
  rm -f "$REMOTE_LIST_TMP"
  echo "Failed to list remote finished batches on $REMOTE_HOST." >&2
  echo "Check that Remote Login / SSH is enabled on the remote worker and reachable on port 22." >&2
  exit 1
fi

while IFS= read -r line; do
  [[ -n "$line" ]] && REMOTE_BATCH_NAMES+=("$line")
done < "$REMOTE_LIST_TMP"
rm -f "$REMOTE_LIST_TMP"

if [[ ${#REMOTE_BATCH_NAMES[@]} -eq 0 ]]; then
  echo "No finished batches found on $REMOTE_HOST."
  exit 0
fi

count=0
skipped_merged=0
skipped_returned=0
skipped_local_done=0
for batch_name in "${REMOTE_BATCH_NAMES[@]}"; do
  if [[ "$BATCH_LIMIT" -gt 0 && "$count" -ge "$BATCH_LIMIT" ]]; then
    break
  fi

  if [[ "$SKIP_MERGED" == "1" && -d "$MERGED_ROOT/$batch_name" ]]; then
    echo "Skipping already merged batch: $batch_name"
    skipped_merged=$((skipped_merged + 1))
    continue
  fi

  if [[ "$SKIP_RETURNED" == "1" && -d "$RETURNED_ROOT/$batch_name" ]]; then
    echo "Skipping already returned batch: $batch_name"
    skipped_returned=$((skipped_returned + 1))
    continue
  fi

  if [[ "$SKIP_LOCAL_DONE" == "1" && -d "$LOCAL_DONE_ROOT/$batch_name" ]]; then
    echo "Skipping already local_done batch: $batch_name"
    skipped_local_done=$((skipped_local_done + 1))
    continue
  fi

  local_dest="$RETURNED_ROOT/$batch_name/"
  remote_src="$REMOTE_USER@$REMOTE_HOST:$REMOTE_FINISHED_ROOT/$batch_name/"

  echo "Pulling $batch_name <- $REMOTE_HOST"
  "$RSYNC_BIN" "${RSYNC_ARGS[@]}" "$remote_src" "$local_dest"
  count=$((count + 1))
done

echo "Pulled $count batch(es) from $REMOTE_HOST"
if [[ "$skipped_merged" -gt 0 ]]; then
  echo "Skipped $skipped_merged already merged batch(es)."
fi
if [[ "$skipped_returned" -gt 0 ]]; then
  echo "Skipped $skipped_returned already returned batch(es)."
fi
if [[ "$skipped_local_done" -gt 0 ]]; then
  echo "Skipped $skipped_local_done already local_done batch(es)."
fi

if [[ "$MERGE_AFTER_PULL" == "1" ]]; then
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "DRY_RUN=1, skipping merge."
  else
    python3 "$PROJECT_ROOT/scripts/merge_remote_mineru_batches.py" --worker "$REMOTE_LABEL"
  fi
fi
