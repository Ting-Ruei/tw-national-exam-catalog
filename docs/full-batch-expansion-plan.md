# Full Batch Expansion Plan

本文記錄從目前 97 類專技種子集，往完整非 locked27 國考 catalog 擴展的執行順序。這是排程計畫，不代表立即啟動全量下載。

## 現況

已完成或已啟動：

- `國考題資料夾/`：醫事 locked27 主線。
- `國考題資料夾_其他類型/`：97 類專技其他種子集，已完成 PDF 下載，MinerU 以 1 worker 背景拆解。

尚未整批處理：

- 非 locked27 完整集合共 1,087 類、67,358 catalog rows、104,257 PDF URL 文件。
- 扣掉 97 類種子集後，仍約 96,922 PDF URL 文件。

## 官方考科軌下載

先產生官方考科類別軌：

```bash
python3 scripts/build_moex_official_category_tracks.py
```

輸出：

- `catalogs/moex_official_category_track_summary__y100-115.csv`
- `catalogs/moex_official_category_subjects__y100-115.csv`

下載時使用 official-track downloader，不只用 `category_name`：

```bash
python3 scripts/download_moex_pdfs_from_official_track_list.py \
  --asset-root "$PWD/國考題資料夾_非醫學剩餘全集" \
  --working-scope future_expansion
```

若只要先下載、整理官方 PDF，不跑 MinerU，可用背景 launcher：

```bash
ASSET_ROOT="$PWD/國考題資料夾_非醫學剩餘全集" \
WORKING_SCOPE=future_expansion \
  python3 scripts/start_moex_official_track_pdf_download.py
```

## 建議批次順序

1. `professional_high_other_seed`
   - 已在 `國考題資料夾_其他類型/` 跑。
   - 用來驗證非醫事資料根、MinerU queue、PDF index 與 batch 切分。

2. 官方考科類別軌
   - 先用 `moex_official_category_track_summary__y100-115.csv` 依 `exam_level`、`category_label`、`category_code`、`category_name` 排程。
   - 同一 `category_name` 若出現在不同官方軌道，不可混併。
   - 共同科目只作為 parser 輔助觀察，不作為主分類。

3. `civil_service_core`
   - 公務人員高普初等與一般行政類。
   - 建議獨立 asset root，例如 `國考題資料夾_公職核心/`。

4. `civil_service_special`
   - 最大節點，約 52,993 PDF URL 文件。
   - 建議再按警察司法、關務外交、原民身障、地方特考拆次級資料根。

5. `promotion_rank_exam`
   - 升官等與升資考試。
   - 題型和共同科目重疊，但職務專業科目需要另看。

6. `professional_technical_remaining`
   - 其餘專技考試。
   - 可接在 97 類種子集驗證穩定後處理。

7. `language_tourism`
   - 導遊領隊與外語類。
   - 外語題、觀光資源題和共同科目可獨立處理。

## 執行範例

跑單一節點：

```bash
ASSET_ROOT="$PWD/國考題資料夾_官方考科拓展" \
WORKING_SCOPE=future_expansion \
BATCH_SIZE=25 WORKERS=1 \
  bash scripts/run_moex_official_track_pipeline.sh
```

若只要 smoke test，可先限制 official track 數量：

```bash
python3 scripts/download_moex_pdfs_from_official_track_list.py \
  --asset-root "$PWD/tmp/official-track-smoke" \
  --working-scope future_expansion \
  --track-limit 5 \
  --dry-run
```

## 風險控管

- 完整非 locked27 全集不要直接混入 `國考題資料夾_其他類型/`。
- PDF 下載、MinerU output、run logs 必須按 asset root 分開。
- `all-official` MinerU 可以先跑；題目結構化 parser 不要直接套用醫事選擇題規則。
- 申論、公文、製圖、報告、外語和法律長文先進 markdown/Review UI 層，再依官方考科類別決定 candidate schema。
- 每個新節點先做小批 smoke，再開長時間背景 queue。
