# 資料庫與資料資產架構草案

本文件描述 `tw-national-exam-catalog` 未來如何把官方 PDF、分類資訊、MinerU 解析結果、題目資料庫與向量索引組成一套可共享的獨立專案。

目前階段只做架構設計與 PDF 索引，不把題目內容入庫。

## 核心原則

1. 官方來源要可追溯：官方 catalog、PDF URL、registry key、檔案 hash 都要保留。
2. PDF、圖片、MinerU 輸出的圖片與 markdown 不直接塞進資料庫，資料庫只保存路徑、hash、類型與狀態。
3. PostgreSQL 可作為主要工作資料庫；SQLite、Parquet、JSONL、向量資料庫都從它或中介索引匯出。
4. 向量資料庫是衍生索引，不是唯一真相來源。
5. 題組題、圖表題、答案更正、科目改名都要以資料模型處理，不靠檔名硬猜。

## 建議分層

```text
官方 catalog / 官方 PDF
        ↓
本機資產層：PDF、MinerU output、圖片、markdown
        ↓
索引層：CSV / JSONL manifest，記錄分類、hash、路徑、來源 URL
        ↓
主資料庫層：PostgreSQL
        ↓
發布與應用層：SQLite / Parquet / DuckDB / cmed import / vector chunks
        ↓
向量索引層：pgvector / Qdrant / Milvus / Chroma
```

## 資料夾規劃

```text
國考題資料夾/
  10_official_pdf/
    by_official_catalog/
  20_mineru_output/
  30_normalized_items/
  40_databases/
    postgres_schema_snapshots/
    sqlite_exports/
  50_vector_exports/
  Registry/
    asset_manifests/
    pdf_indexes/
    processing_logs/
```

`Registry/pdf_indexes/` 用來放目前已分類 PDF 的索引 CSV。這些索引是入庫前的審核面，不等於資料庫。

## PostgreSQL 的角色

PostgreSQL 適合當主資料庫，因為它同時支援：

- 關聯資料：考試、年度、類科、科目、PDF、題目、題組、答案。
- JSONB：保存 MinerU block、OCR 中間結果、解析器輸出。
- 索引與 view：做整理進度、缺漏檢查、人工校對工作台。
- 擴充：需要時可加 `pgvector`，但不把向量索引當主資料。

## MongoDB 的角色

MongoDB 可以作為 MinerU 原始解析結果的文件型暫存庫，尤其是 layout tree、blocks、page objects 這類結構常變的資料。

但它不建議作為唯一主資料庫，因為答案更正、官方來源追溯、科目 mapping、人工校對狀態更適合用關聯模型維護。

## SQLite / DuckDB / Parquet 的角色

SQLite 適合做單檔共享與 cmed 輕量接入；DuckDB / Parquet 適合分析與公開資料集發布。它們都應該從主索引或 PostgreSQL 匯出，而不是成為唯一工作來源。

## 題組題與圖題

題組題使用 `question_groups` 表示共同題幹，再由 `questions.group_id` 連回。圖題、表格題、頁面截圖與選項圖片都用 `question_assets` 連到 `assets`。

```text
question_groups
  id
  shared_stem_text
  source_document_id

questions
  id
  group_id nullable
  question_number
  question_text

question_assets
  question_id
  asset_id
  role = figure | table | page_image | option_image
```

## 目前先做的事情

1. 設計 PostgreSQL schema 草案。
2. 把已下載且分類好的 PDF 產生索引 CSV。
3. 保留每個官方類科的 subject variant markdown。
4. 暫不匯入 PostgreSQL，等分類與欄位審核後再入庫。

## PDF 命名與分類規則

更多制度演進與命名特例見 `docs/historical-transition-notes.md`。

- PDF 檔名與類科資料夾一律使用半形括號。
- 官方 raw 類科名稱與科目名稱仍保存在 manifest / index 欄位。
- 藥師四年制到六年制交叉期，`藥師`、`藥師（一）`、`藥師（二）`、`藥師(一)`、`藥師(二)` 全部保留，整理群組歸為 `藥師`。
- 中醫師早期未分階段與後續分階段全部保留，整理群組歸為 `中醫師`。
- 民國 106 年中醫師 `exam_code=106111` 是花東考區補辦考試試題，檔名次序記為 `1063`，並在索引 notes 註記。
- 若未來仍出現同年同次序同類科同科目但官方 `exam_code` 不同、且無明確補辦次序可歸類，檔名才會加 `_E{exam_code}` 避免撞名。
