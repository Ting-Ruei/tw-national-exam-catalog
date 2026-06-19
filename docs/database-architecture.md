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

`Registry/paired_indexes/` 用來放題目 PDF 與答案 PDF 的配對清單。若同一科目同時有一般答案與更正答案，配對清單會以更正答案作為 primary answer，同時保留一般答案欄位供追溯。

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

## 雲端儲存與雲資料庫規劃

雲端部署要分成兩件事：公開資料集下載，以及線上查詢 / demo。前者需要大容量、可版本化、可被程式下載；後者需要資料庫查詢能力，但免費額度通常很小，不適合作為完整資料源。

建議分工：

- GitHub repo：放程式碼、schema、文件、manifest、小樣本與 export script，不放完整圖片、OCR output、大型 database dump。
- GitHub Releases：放每版壓縮包，適合跟 repo tag 綁定；單一 release asset 需切在 2 GiB 以下。
- Hugging Face Datasets：作為主要公開資料集位置，放 Parquet / JSONL / WebDataset / image assets，適合讓使用者用 Python 直接下載。
- Zenodo：作為正式版本封存與 DOI 來源，適合重要 release 長期保存；多檔案時應包成 ZIP / tar.zst。
- Cloudflare R2：需要網站或 API 即時讀圖時再使用，適合放 `assets/images/` 這類 object storage。
- Neon Postgres：適合小型 demo DB、metadata 查詢、少量 pgvector 相似題 demo。
- Supabase：適合 demo API、管理後台、小樣本資料庫與小量 storage。
- MongoDB Atlas M0：只適合 JSON 原型或小樣本，不作為本專案主資料庫。

不建議把完整題庫塞進免費雲資料庫。免費 Postgres / MongoDB 額度多在數百 MB 級距，而完整題庫加上 OCR、圖片、向量索引會超過這個範圍。完整共享應以檔案型 dataset 為主，雲資料庫只放 demo 子集或查詢索引。

建議公開資料集結構：

```text
tw-national-exam-dataset/
  manifest.json
  README.md
  checksums.sha256
  parquet/
    exam_sessions.parquet
    categories.parquet
    subjects.parquet
    official_documents.parquet
    questions.parquet
    question_options.parquet
    answers.parquet
    question_assets.parquet
    question_relations.parquet
  assets/
    images/
      official_exam/
        moex_100030_104_0303_1/
          page_001_block_0001.jpg
  sqlite/
    tw-national-exam-catalog.sqlite
```

圖片不放進資料庫欄位本身。資料庫或 Parquet 只保存：

- `asset_id`
- `asset_role`
- `relative_asset_path`
- `sha256`
- `mime_type`
- `bytes`
- `page_number`
- `bbox_json`
- `mineru_block_id`
- `source_document_id`

使用者下載完整包後，透過 `question_assets.relative_asset_path` 找到本機圖片。若部署到 R2 或其他 object storage，則由 manifest 另外提供 `asset_base_url`，讓同一份 metadata 可以在本機檔案與雲端 URL 之間切換。

建議發布版本：

- `metadata-only`：catalog、PDF index、paired index、hash，不含 OCR 與圖片。
- `text-lite`：結構化題目、選項、答案、品質旗標，不含圖片。
- `full-official`：題目文字、答案、官方題目圖片、表格、MinerU 解析 assets。
- `demo-db`：抽樣或壓縮後的小型 Postgres / SQLite，供 Neon / Supabase demo 使用。
- `private-knowledge`：本機私人教材知識庫，不發布。

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

## 入庫前審核與 Human-in-the-loop

MinerU 解析結果不得直接寫入正式題目表。正式入庫前先產生可重跑、可丟棄的候選層：

```text
MinerU markdown / images
        ↓
question_candidates JSONL
        ↓
parse_issues QA flags
        ↓
本地 Review UI 人工核查
        ↓
review_events
        ↓
正式 questions / question_groups / question_assets / answers
```

審核重點先鎖定三類高風險：

- 題目文字疑似 OCR 錯字、亂碼、上下標或科學符號遺失。
- 題組題的共同題幹與題號範圍是否正確。
- 圖片題、表格題、選項圖片是否擷取完整並正確綁定題目。

候選資料的狀態建議分為：

- `pass`：機械檢查通過，可進正式入庫佇列。
- `needs_review`：需要人工確認，不進正式表。
- `blocked`：缺必要資料或配對錯誤，禁止入正式表。

人工審核不直接覆蓋官方 PDF、MinerU raw markdown 或 parser 輸出。所有人工判斷以 `review_events` 記錄，必要時再由正式入庫腳本合併 `human_corrected_text`、題組修正與圖片綁定。

## 特殊符號、上下標與公式文字

國考題可能包含科學符號、希臘字母、羅馬數字、上下標與少量公式。資料庫在本階段要保留語意與原始表現，但不綁死未來前端的渲染技術。

每個重要文字欄位建議保留：

- `raw_text`：MinerU 原始輸出，不修改。
- `normalized_text`：清理換行、空白與全半形後的搜尋用文字。
- `display_markup`：可選的 Markdown / HTML / LaTeX 表示。
- `human_corrected_text`：人工校對後文字。

正式題目表保留純文字欄位供搜尋，並用 JSONB 保存 markup span、原始 block 與 parser metadata。未來寫題網站可再決定使用 KaTeX、MathJax、HTML `<sup>/<sub>` 或純 Unicode 顯示。Review UI 則應先提供基本預覽，讓人工審核時能發現上下標或公式遺失。

範例：

```json
{
  "plain": "HbA1c、Ca2+、α-glucosidase、H2O",
  "markup": "HbA<sub>1c</sub>、Ca<sup>2+</sup>、\\alpha-glucosidase、H<sub>2</sub>O",
  "format": "html+latex"
}
```

## 目前先做的事情

1. 設計 PostgreSQL schema 草案。
2. 把已下載且分類好的 PDF 產生索引 CSV。
3. 保留每個官方類科的 subject variant markdown。
4. 建立 `question_candidates`、`parse_issues`、`review_events` 作為入庫前審核層。
5. 產生候選題目 JSONL 與 QA flags，先不污染正式題目表。
6. 建立最小本地 Review UI，可並排查看 PDF、候選題、圖片與疑點，人工標記 accept / block。

目前最小可用指令：

```bash
# 先小批測試
python3 scripts/build_question_candidates_from_mineru.py --limit 10

# 全量已配對且有 MinerU markdown 的題目候選產生
python3 scripts/build_question_candidates_from_mineru.py

# 開啟本地人工審核介面
python3 scripts/serve_question_review_ui.py --port 8765
```

Review UI 預設讀取最新的 `30_normalized_items/question_candidates/*/question_candidates__*.jsonl` 與對應 `question_parse_issues__*.csv`。人工審核動作會追加到同一資料夾的 `question_review_events.jsonl`，後續再由正式入庫腳本讀取 review events 合併進 PostgreSQL。

## PDF 命名與分類規則

更多制度演進與命名特例見 `docs/historical-transition-notes.md`。

- PDF 檔名與類科資料夾一律使用半形括號。
- 官方 raw 類科名稱與科目名稱仍保存在 manifest / index 欄位。
- 藥師四年制到六年制交叉期，`藥師`、`藥師（一）`、`藥師（二）`、`藥師(一)`、`藥師(二)` 全部保留，整理群組歸為 `藥師`。
- 中醫師早期未分階段與後續分階段全部保留，整理群組歸為 `中醫師`。
- 民國 106 年 `exam_code=106111` 是花東考區補辦考試試題，影響中醫師、營養師、心理師、護理師、社會工作師、法醫師、驗光師等同場次類科；檔名次序記為 `1063`，並在索引 notes 註記。
- 若未來仍出現同年同次序同類科同科目但官方 `exam_code` 不同、且無明確補辦次序可歸類，檔名才會加 `_E{exam_code}` 避免撞名。
