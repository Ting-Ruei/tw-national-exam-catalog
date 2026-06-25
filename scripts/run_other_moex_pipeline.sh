#!/bin/bash
# Foreground pipeline for the separate non-locked MOEX asset root.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ASSET_ROOT="${ASSET_ROOT:-$PROJECT_ROOT/國考題資料夾_其他類型}"

cd "$PROJECT_ROOT"
echo "asset_root=$ASSET_ROOT"

python3 -u scripts/download_moex_pdfs_from_category_list.py \
  --asset-root "$ASSET_ROOT"

python3 scripts/build_pdf_asset_index.py \
  --asset-root "$ASSET_ROOT" \
  --manifest-dir "$ASSET_ROOT/Registry/asset_manifests" \
  --output-dir "$ASSET_ROOT/Registry/pdf_indexes"

ASSET_ROOT="$ASSET_ROOT" python3 scripts/create_mineru_remote_batch.py \
  --scope all-official \
  --batch-size "${BATCH_SIZE:-50}" \
  --batch-count "${BATCH_COUNT:-9999}" \
  --order forward

ASSET_ROOT="$ASSET_ROOT" WORKERS=1 MINERU_METHOD=ocr MINERU_BACKEND=vlm-engine \
  MINERU_IMAGE_ANALYSIS=false python3 scripts/start_local_split_batch_queue.py
