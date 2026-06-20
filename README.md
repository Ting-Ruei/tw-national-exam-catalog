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
  database-ingestion-preflight.md
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
  build_question_candidates_from_mineru.py
  ingest_question_candidates_to_postgres.py
  serve_question_review_ui.py
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

資料庫架構草案與雲端儲存 / 雲資料庫發布規劃見 `docs/database-architecture.md`，PostgreSQL schema 草案見 `schemas/database/postgresql_schema.sql`。入庫前分科排查、Review UI 與人工審核規則見 `docs/database-ingestion-preflight.md`。AI 詳解、RAG、GraphRAG、概念圖與成本控管規劃見 `docs/ai-workflow-architecture.md`；本地 RAG 知識庫資源評估見 `docs/local-rag-resource-assessment.md`。若要把另一台 MacBook 接成 MinerU 算力節點，部署與 rsync 批次回傳流程見 `docs/remote-mineru-worker.md`。

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

## 入庫前 Review UI

題目內容正式入庫前，先走可丟棄、可重跑的 candidate 層：

```text
PDF
  ↓
MinerU markdown / images / layout PDF
  ↓
question_candidates JSONL
  ↓
question_parse_issues CSV
  ↓
Review UI 人工審核
  ↓
question_review_events JSONL
  ↓
未來才升級到正式 questions / answers 表
```

啟動 Review UI：

```bash
docker compose up -d review-ui
```

打開：

```text
http://127.0.0.1:8765/
```

Review UI 預設會綁定到本機所有網卡：

```text
0.0.0.0:8765
```

因此同一區網或 Tailscale 內的其他電腦可用這台主機的 IP 連線，例如：

```text
http://192.168.10.70:8765/
http://100.96.146.93:8765/
```

查看 log：

```bash
docker compose logs -f review-ui
```

停止：

```bash
docker compose stop review-ui
```

若使用 NPM / Nginx Proxy Manager 管理區網網址，可將 proxy host 指到 Review UI：

```text
Forward Hostname / IP: 這台主機的 LAN IP 或 Tailscale IP
Forward Port: 8765
Scheme: http
```

如果 NPM 本身是跑在同一台 Mac 的 Docker container，`Forward Hostname / IP` 也可嘗試使用 `host.docker.internal`。實際審核紀錄仍寫回本專案工作資料夾內的 `question_review_events.jsonl`。

Review UI 右側 PDF 檢視提供三種來源：

- `官方 PDF`：考選部原始 PDF。
- `MinerU layout`：MinerU 產出的 layout PDF，通常會以色塊或框線標示版面分區，適合判斷 MinerU 是否已經切壞。
- `MinerU origin`：MinerU 輸出資料夾中的原始 PDF 複本，適合和官方 PDF 對照。

人工審核預設只顯示 `未看過` 的題目。按下任一審核按鈕後，該題會寫入 `question_review_events.jsonl`，並自動跳到下一題。

題目審核畫面先專注在題幹、選項、圖片、題組與 parser 切題品質，不直接顯示答案。答案會在後續獨立的 `answer_review_events` 關卡集中核對，避免同時看題目與答案而分散注意力。

頁面上的 `資料庫層級` 按鈕可查看目前資料在各層的位置與數量，包括來源 PDF/MinerU raw、題目 candidate、QA flags、題目人工審核、答案核對與正式題庫表。

審核按鈕語意：

- `通過`：此題 candidate 可進入後續正式入庫佇列。
- `標記已看過`：已人工看過，但暫不表示可以入庫。
- `保留疑問`：需要後續再查。
- `阻擋入庫`：目前不可入正式題庫。
- `只加註記`：保存觀察或修正方向，供後續 parser 或人工校正使用。

如果 Review UI 裡發現「MinerU layout 正常，但 candidate 題號、題幹或選項切錯」，通常代表 parser 規則需要修正；如果 MinerU layout 本身已經分區錯誤，則優先回頭檢查 MinerU 解析模式或輸出品質。

## DBeaver 連線

若要用 DBeaver 直接讀 PostgreSQL，可使用：

```text
Database type: PostgreSQL
Host: 127.0.0.1
Port: 54329
Database: tw_national_exam_dev
Username: national_exam
Password: national_exam_dev_password
Schema: exam
```

建議先看：

- `exam.official_documents`：官方 PDF 索引。
- `exam.question_answer_document_pairs`：題目與 primary answer 配對，`MOD` 優先於 `ANS`。
- `exam.question_candidates`：parser 產生的候選題目，還不是正式題庫。
- `exam.question_parse_issues`：候選題目的機械檢查疑點。
- `exam.question_review_events`：人工審核紀錄。
- `exam.answer_review_events`：答案核對紀錄，獨立於題目結構審核。

## 授權與來源

請見：

- `LICENSE`
- `DATA_LICENSE.md`

本專案會區分「程式碼」、「官方 metadata」、「官方考題材料」與「社群整理出的衍生資料」，因為它們可能有不同的法律狀態、引用方式與再利用限制。
