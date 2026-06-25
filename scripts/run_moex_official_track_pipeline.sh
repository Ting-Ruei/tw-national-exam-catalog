#!/bin/bash
# Foreground pipeline for official-category-track based MOEX expansion.
#
# Required:
#   ASSET_ROOT=/path/to/asset-root
# Optional:
#   TRACK_LIST=catalogs/moex_official_category_track_summary__y100-115.csv
#   WORKING_SCOPE=future_expansion
#   BATCH_SIZE=25
#   BATCH_COUNT=9999
#   WORKERS=1

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${ASSET_ROOT:?ASSET_ROOT is required}"
TRACK_LIST="${TRACK_LIST:-$PROJECT_ROOT/catalogs/moex_official_category_track_summary__y100-115.csv}"

cd "$PROJECT_ROOT"
echo "asset_root=$ASSET_ROOT"
echo "track_list=$TRACK_LIST"
echo "working_scope=${WORKING_SCOPE:-}"

download_args=(
  --asset-root "$ASSET_ROOT"
  --track-list "$TRACK_LIST"
)
if [[ -n "${WORKING_SCOPE:-}" ]]; then
  download_args+=(--working-scope "$WORKING_SCOPE")
fi

python3 -u scripts/download_moex_pdfs_from_official_track_list.py "${download_args[@]}"

python3 scripts/build_pdf_asset_index.py \
  --asset-root "$ASSET_ROOT" \
  --manifest-dir "$ASSET_ROOT/Registry/asset_manifests" \
  --output-dir "$ASSET_ROOT/Registry/pdf_indexes"

ASSET_ROOT="$ASSET_ROOT" python3 scripts/create_mineru_remote_batch.py \
  --scope all-official \
  --batch-size "${BATCH_SIZE:-25}" \
  --batch-count "${BATCH_COUNT:-9999}" \
  --order forward

ASSET_ROOT="$ASSET_ROOT" WORKERS="${WORKERS:-1}" MINERU_METHOD=ocr MINERU_BACKEND=vlm-engine \
  MINERU_IMAGE_ANALYSIS=false python3 scripts/start_local_split_batch_queue.py
