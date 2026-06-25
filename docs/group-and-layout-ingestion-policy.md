# 題組題、圖表題與正式入庫防跑版規格

本文件定義 candidate 升級到正式 SQL 題庫前的資料保護規則。目標是讓題組題、圖片題、表格題、公式與上下標在 PostgreSQL 與後續前端顯示時都能保持可追溯、可審核、可修正。

## 核心原則

正式表不直接吃 MinerU markdown。流程必須先經過 candidate、QA flags、人工題目審核與答案核對。

```text
MinerU raw markdown/images/layout PDF
        ↓
question_candidates JSONL
        ↓
question_parse_issues
        ↓
question_review_events + answer_review_events
        ↓
preflight_formal_ingest.py
        ↓
exam.question_groups / questions / question_options / answers / question_assets
```

SQL 不是跑版來源，跑版通常來自「只保存純文字，沒有保存顯示結構」。因此正式入庫時，每個重要文字欄位都應保留：

- raw: MinerU 或 parser 原始輸出。
- normalized: 搜尋與比對用純文字。
- display: Review UI 或前端可直接顯示的文字。
- markup_json: 上下標、希臘字母、公式、表格、圖片引用等結構。
- human_corrected_json: 人工校正版，正式顯示時優先。

## 題組題

題組題使用 `exam.question_groups` 保存共同題幹或共同案例，再由 `exam.questions.question_group_id` 連回。

入庫規則：

- `group_ref` 不為空的 candidate，升級時必須能建立或連到 `question_groups.group_key`。
- 題幹出現 `下列資料`、`依下圖`、`此病人`、`此案例`、`前述`、`承上題`、`上題` 等語句，但 `group_ref` 為空，必須擋在 preflight。
- 共同題幹、共同圖表與各小題自己的題幹要分層保存，不把共同題幹重複塞進每一題的主題幹。
- Review UI 可以暫時用 `group_ref` 顯示題組，正式入庫時再轉成 `question_group_id`。

## 圖片與表格

圖片、表格與人工補圖都進 `question_assets`，不可直接混進題幹文字。

建議 role：

- `figure`: 一般題幹圖。
- `stem_figure`: 題幹核心圖。
- `option_image`: 選項圖。
- `table_structured`: MinerU 或 parser 解析出的結構化表格。
- `table_manual_screenshot`: 人工截圖補上的表格視覺資產。
- `source_pdf_region`: PDF 區域截圖，用於回溯。
- `group_shared_asset`: 題組共同圖表。

若 MinerU 把表格拆成不穩定 HTML，Review UI 主畫面不應強迫顯示破碎表格。正式入庫時可以同時保存結構化表格與人工截圖：結構化表格利於搜尋，人工截圖利於審題與前端顯示。

人工補圖建議放在：

```text
國考題資料夾/30_normalized_items/manual_assets/<candidate_key>/
```

並以 review event 的 `correction.image_refs` 或 `correction.stem_image` 掛回題目。補圖只代表修正資料，不代表自動通過，仍需人工 `accept`。

## 正式入庫前預檢

`scripts/preflight_formal_ingest.py` 是正式入庫前的門檻檢查。它只讀 candidate 與 review logs，輸出 CSV/JSONL 報告，不寫正式題庫表。

必要通過條件：

- candidate `quality_status` 必須是 `pass`。
- 最新題目人工審核必須是 `accept` 或 `unblock`。
- 若最新題目狀態是 `reset_review` 或沒有人工通過，不能入庫。
- 最新答案審核必須是 `accept` 或 `unblock`。
- 最新人工狀態為 `block`、`exclude`、`needs_review`、`comment`、`reviewed` 時不能入庫。
- candidate 不可有 error/blocked 等級 parse issue。
- 題組疑似詞出現但 `group_ref` 為空時不能入庫。
- 題幹提到圖、表、影像、X 光、心電圖等視覺依賴詞，但沒有 image/table/manual asset 時不能入庫。
- markup/table/HTML 殘片可先擋下，避免進正式表後前端跑版。

執行：

```bash
python3 scripts/preflight_formal_ingest.py
```

輸出摘要會顯示 ready / blocked 數量。若要看逐題原因：

```bash
python3 scripts/preflight_formal_ingest.py --format csv --output /tmp/formal_ingest_preflight.csv
```

正式入庫器後續應只讀取 `status=ready` 的 candidate，並合併最新 review correction；不可直接從 candidate JSONL 全量寫入 `exam.questions`。
