#!/bin/bash
# Foreground pipeline for any category-list based MOEX expansion node.
#
# Required environment:
#   ASSET_ROOT=/path/to/asset-root
#   CATEGORY_LIST=/path/to/category-list.csv
#
# Optional:
#   BATCH_SIZE=25
#   BATCH_COUNT=9999
#   WORKERS=1

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${ASSET_ROOT:?ASSET_ROOT is required}"
: "${CATEGORY_LIST:?CATEGORY_LIST is required}"

cd "$PROJECT_ROOT"
echo "asset_root=$ASSET_ROOT"
echo "category_list=$CATEGORY_LIST"

python3 -u scripts/download_moex_pdfs_from_category_list.py \
  --asset-root "$ASSET_ROOT" \
  --category-list "$CATEGORY_LIST"

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
