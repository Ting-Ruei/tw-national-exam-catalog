# 台灣國考題目錄資料專案

這是一個開放、可機器讀取的台灣國家考試詮釋資料專案，資料來源從考選部歷年試題與解答查詢頁開始整理。

本專案目前整理的是「目錄層級」資料，包含：

- 考試年度
- 考試代碼
- 考試名稱
- 類科代碼
- 類科名稱
- 科目代碼
- 科目名稱
- 官方試題、答案、更正答案 PDF 連結是否存在
- 後續下載、解析、入庫可使用的穩定 registry key

本專案不是考選部官方專案，也不代表考選部立場。

## 目前範圍

第一階段公開範圍是 metadata，不包含題目 PDF 或題文解析結果：

- 民國 100-115 年
- 來源：考選部歷年試題與解答查詢系統
- 不包含 PDF 檔
- 尚不包含已解析的題文
- 尚不包含 AI 解析或詳解

目前實作上鎖定的是 27 個醫事相關專門職業及技術人員考試類科。公職類科，例如 `公職醫事檢驗師`，會在原始 catalog 中被偵測到，但不列入目前入庫整理範圍。

## 專案結構

```text
catalogs/
  moex_subject_catalog__y100-115.csv
  moex_subject_catalog__y100-115.md
  moex_subject_catalog_overrides.csv
  locked_27_canonical_category_names.csv
  other_professional_high_categories_excluding_locked27__y100-115.csv
docs/
  source-policy.md
  contribution-guide.md
  known-issues.md
  locked-27-category-name-stability.md
  historical-transition-notes.md
  publication-roadmap.md
  database-architecture.md
  ai-workflow-architecture.md
  local-rag-resource-assessment.md
  remote-mineru-worker.md
schemas/
  moex_catalog.schema.json
  question_candidate.schema.json
  database/
    postgresql_schema.sql
scripts/
  export_moex_subject_catalog.py
  download_moex_pdfs_from_catalog.py
  build_pdf_asset_index.py
  build_question_answer_pairs.py
  create_mineru_remote_batch.py
  push_remote_mineru_batches.sh
  pull_remote_mineru_batches.sh
  merge_remote_mineru_batches.py
  setup_remote_mineru_worker.sh
  run_remote_mineru_batch.sh
  run_remote_mineru_queue.sh
examples/
  sample-question-candidate.json
國考題資料夾/              # 本機工作資料夾，已加入 .gitignore
```

## 本機工作資料夾

PDF 下載、MinerU 輸出、人工檢查佇列、入庫前候選資料等大型或中間產物，預設放在：

```text
./國考題資料夾
```

這個資料夾刻意不納入 git。它可以放在專案旁邊方便工作，但不會被發布到 repository 歷史裡。

## Registry Key

科目層級 key：

```text
moex:{exam_code}:{category_code}:{subject_code}:{question_set}
```

文件層級 key：

```text
moex:{exam_code}:{category_code}:{subject_code}:{question_set}:{document_role}
```

其中 `document_role` 目前有三種：

- `question`：試題
- `answer`：答案
- `correction`：更正答案

## 資料品質原則

catalog 會保留考選部官方原始名稱。若未來需要標準化名稱，應該新增衍生欄位或 mapping 表，而不是覆寫官方原始資料。

目前已知狀況包括：

- 官方類科或科目名稱中，同時可能出現全形與半形括號。
- 有些申論或特殊考試沒有答案 PDF，這不一定代表下載失敗。
- 有些官方頁面會出現沒有父層類科標籤的孤立科目列，這類狀況會透過明確的 override 檔處理。
- 有些科目名稱曾在不同年度改名，例如醫事檢驗師的 `臨床鏡檢學（包括寄生蟲學）` 後來出現為 `醫學分子檢驗學與臨床鏡檢學（包括寄生蟲學）`。這類差異應保留官方當年度名稱，入庫時再用 canonical mapping 歸併。

制度演進、補辦考試、括號混用、分階段過渡期等特例集中記錄在 `docs/historical-transition-notes.md`。

## 後續資料層

未來可逐步加入：

1. PDF asset manifest，記錄 SHA-256、官方來源 URL、下載檔名。
2. MinerU / OCR 產出的 markdown。
3. 結構化題目候選資料 JSONL。
4. 經人工校對後，可供練習系統或研究使用的資料集。
5. SQLite / Parquet / PostgreSQL 等匯出格式。

大型 PDF、圖片、OCR markdown、資料庫檔案，不建議直接 commit 到 git；較適合放在 GitHub Releases、Hugging Face Datasets、Zenodo 或物件儲存服務。

## 資料庫與索引

資料庫架構草案與雲端儲存 / 雲資料庫發布規劃見 `docs/database-architecture.md`，PostgreSQL schema 草案見 `schemas/database/postgresql_schema.sql`。AI 詳解、RAG、GraphRAG、概念圖與成本控管規劃見 `docs/ai-workflow-architecture.md`；本地 RAG 知識庫資源評估見 `docs/local-rag-resource-assessment.md`。若要把另一台 MacBook 接成 MinerU 算力節點，部署與 rsync 批次回傳流程見 `docs/remote-mineru-worker.md`。

目前可先用 `scripts/build_pdf_asset_index.py` 將已下載、已分類的 PDF manifest 整理成 CSV 索引。這個步驟只產生可審閱的索引檔，不會把資料寫入 PostgreSQL 或其他資料庫。

題目 PDF 與答案 PDF 的 paired 清單可用 `scripts/build_question_answer_pairs.py` 產生；若同時有一般答案與更正答案，會以更正答案 `_MOD` 作為 primary answer，並保留 `_ANS` 欄位供追溯。

若要先測試 PostgreSQL schema，可使用專案內的 Docker Compose 開發環境。預設映像是 PostgreSQL 18，並已安裝 pgvector：

```bash
cp .env.example .env
bash scripts/postgres_up.sh
bash scripts/postgres_apply_schema.sh
bash scripts/postgres_smoke_test.sh
```

預設使用 `localhost:54329`，避免和本機既有 PostgreSQL 撞 port。這個流程只部署與測試 schema，尚不會把 PDF 或 MinerU 解析內容入庫；pgvector 先啟用 extension，向量表與索引等到題文 chunk 設計確認後再加入。

## 授權與來源

請見：

- `LICENSE`
- `DATA_LICENSE.md`

本專案會區分「程式碼」、「官方 metadata」、「官方考題材料」與「社群整理出的衍生資料」，因為它們可能有不同的法律狀態、引用方式與再利用限制。
