# 資料庫入庫前分科排查計畫

本文件記錄正式入庫前的安全流程。現階段目標不是把全部題目一次寫進正式題庫，而是逐一類科產生候選資料、標記格式異常、人工審核，再把通過的題目升級到正式表。

## 核心原則

1. 官方 PDF、MinerU 原始輸出、candidate JSONL 都視為可追溯原始層，不直接覆蓋。
2. 題目內容不得直接由 MinerU markdown 寫入 `exam.questions`。
3. 每個類科獨立產生 candidate 與 issue 報告，先排查 parser 問題。
4. 題目結構審核與答案核對分開進行，避免人工審核時同時看題目與答案而分心。
5. `MOD` 答案優先於 `ANS`，只有沒有 `MOD` 時才使用 `ANS`，但此規則在答案核對關卡集中檢查。
6. 有圖、題組、公式、上下標、希臘字母、羅馬數字、答案缺漏、題號不連續者，先進 `needs_review` 或 `blocked`。
7. 人工審核只寫 `review_events` / `answer_review_events`，不改官方檔、不改 MinerU raw markdown。

## 建議入庫層級

```text
10_official_pdf
  ↓
20_mineru_output
  ↓
30_normalized_items/question_candidates/*.jsonl
30_normalized_items/question_candidates/*/question_parse_issues__*.csv
  ↓
exam.question_candidates
exam.question_parse_issues
exam.question_review_events
exam.answer_review_events
  ↓
題目結構與答案核對都接受後才進入
exam.question_groups
exam.questions
exam.question_options
exam.answers
exam.question_assets
```

## 分科流程

每次只處理一個 `group_name`。建議順序先從已經測過、風險較熟悉的類科開始：

1. `醫事檢驗師`
2. `藥師`
3. `醫師`
4. `中醫師`
5. 其他類科依 paired count 與圖片題比例逐步加入

產生某一類科的 candidate：

```bash
python3 scripts/build_question_candidates_from_mineru.py --group-name 醫事檢驗師
```

只匯入候選層到 PostgreSQL：

```bash
python3 scripts/ingest_question_candidates_to_postgres.py \
  --candidate-jsonl 國考題資料夾/30_normalized_items/question_candidates/<run>/question_candidates__<run>.jsonl \
  --issue-csv 國考題資料夾/30_normalized_items/question_candidates/<run>/question_parse_issues__<run>.csv
```

先看將匯入多少筆，不寫 DB：

```bash
python3 scripts/ingest_question_candidates_to_postgres.py \
  --candidate-jsonl 國考題資料夾/30_normalized_items/question_candidates/<run>/question_candidates__<run>.jsonl \
  --issue-csv 國考題資料夾/30_normalized_items/question_candidates/<run>/question_parse_issues__<run>.csv \
  --dry-run
```

開本地 Review UI：

```bash
python3 scripts/serve_question_review_ui.py \
  --candidate-jsonl 國考題資料夾/30_normalized_items/question_candidates/<run>/question_candidates__<run>.jsonl \
  --issue-csv 國考題資料夾/30_normalized_items/question_candidates/<run>/question_parse_issues__<run>.csv \
  --port 8765
```

## 每科排查清單

題目結構審核時，每個類科至少檢查：

- candidate 總數是否接近預期題數。
- 每份考卷題號是否連續。
- 每題是否有 4 或 5 個選項。
- 題組題是否被標出 `group_ref`，共同題幹是否沒有被重複切壞。
- 題幹提到圖、表、影像、心電圖、X 光、切片時，是否有 image asset。
- Markdown 引用圖片是否存在且大小不為 0。
- 科學符號、上下標、希臘字母、羅馬數字是否在 UI 中可辨識。
- 早期科目名稱或類科名稱變體是否保留官方名稱，並另外歸到 canonical/group 名稱。

答案核對在題目結構審核後獨立進行，至少檢查：

- 答案表是否能對上每個題號。
- `MOD` 是否正確覆蓋 `ANS`。
- 更正答案是否保留原始答案與修正後答案。
- 申論題、無答案 PDF、答案缺題是否有明確狀態。
- 答案解析若由 LLM 或人工補充，必須和官方答案分欄保存。

## DB 使用界線

目前允許自動寫入：

- `exam.source_systems`
- `exam.exam_sessions`
- `exam.categories`
- `exam.subjects`
- `exam.official_documents`
- `exam.assets`
- `exam.document_assets`
- `exam.question_answer_document_pairs`
- `exam.question_candidates`
- `exam.question_parse_issues`
- `exam.question_review_events`
- `exam.answer_review_events`

目前暫停自動大量寫入：

- `exam.question_groups`
- `exam.questions`
- `exam.question_options`
- `exam.answers`
- `exam.question_assets`

正式題目表必須等 candidate parser、QA flags、Review UI 題目結構審核、答案核對與人工校正流程穩定後，再用 accepted/corrected candidate 升級。

## Review UI

Review UI 由 Docker Compose 管理，和 PostgreSQL 放在同一套開發環境裡。啟動：

```bash
docker compose up -d review-ui
```

打開：

```text
http://127.0.0.1:8765/
```

查看 log：

```bash
docker compose logs -f review-ui
```

目前 UI 預設讀取最新的 candidate JSONL 與 issue CSV。人工審核結果會寫回 candidate 資料夾裡的 `question_review_events.jsonl`。

上方篩選器可依考別、科目、年份、考次、parser 狀態與審核狀態縮小範圍。篩選條件、目前題目與 PDF 模式會寫入 `exam.review_ui_preferences`，並在 candidate 資料夾保留 `review_ui_preferences.json` 備援。偏好只在頁面初次載入時還原；開頁後使用者切換成 `全部審核`、`未看過`、`未通過`、`全部狀態` 或其他篩選時，畫面當下選擇會立即成為新的偏好，避免舊資料庫設定把下拉選單拉回去。

`quality_status=pass` 只代表目前 QA flags 沒有 error/warning，不代表題目已經可以正式入庫。正式入庫至少還要人工審核事件為 `accept` 或明確校正通過，並完成後續答案核對。自 `moex_mineru_candidate_v0.3` 起，`markup_needs_review` 從 info 提升為 warning，因此含公式、上下標、希臘字母、羅馬數字或 MinerU markup 的題目會先進 `needs_review`，讓人工優先確認顯示品質。

答案表 parser 需同時接受 `題號`、`題序`、`题序` 這類表頭。1151 醫事檢驗師微生物曾出現 `_MOD` 內含完整 1-80 答案表，但 MinerU 將表頭辨識為 `题序`，造成舊 parser 整批找不到答案；此類應視為答案表 OCR / parser 規則問題，而不是題目 PDF 缺答案。若 `_MOD` 內的某題答案為 `#`，需用備註解析成 `accepted_values`，例如 `B|C|BC`。

題目審核畫面仍顯示目前 parser 抓到的答案，避免遮蔽資訊；但答案是否正確、`MOD` / `ANS` 優先序與答案表解析，會在下一個 `answer_review_events` 關卡統一核對。

Review UI 的題目卡片上方有大型 `通過` / `阻擋入庫` 按鈕，可用於快速瀏覽。若需要人工修正，使用 `人工校正` 區編輯題幹、選項、答案與題組；校正內容會寫入 review event 的 `correction` 欄位，並保留 parser 原始輸出。正式入庫時，若最新有效 review event 帶有 `correction`，應優先使用人工校正版；後續 `accept` 事件也會繼承既有校正，避免通過後遺失人工修正。

題目含圖片時，Review UI 會將圖片直接放入題目預覽卡片，也會保留下方圖片來源總覽。右側 PDF 檢視不會因為審核按鈕刷新而跳回頂端，只有切換題目或 PDF 來源時才重新載入。

parser 題號偵測需依歷史卷面格式切換。早期卷面可能是 `1 題幹`，新版卷面則常見 `1.` / `1、` / `1．`，且有時標點後沒有空格。現行規則以民國 `105` 年以前為 legacy，民國 `106` 年起為 modern；若後續人工審核發現某類科分界不同，應記錄在歷史變革文件並調整規則。

審核紀錄採 append-only。分析人工註記或交給 AI 修 parser 時，必須只看每題最新事件；如果某題後來被標成 `accept`、`correct`、`reviewed` 或 `unblock`，舊的 `block` / `needs_review` 註記只保留為歷史，不再視為待修問題。查看目前仍有效註記：

```bash
python3 scripts/summarize_active_review_notes.py
```

右側 PDF 檢視提供三種來源：

- `官方 PDF`：考選部原始 PDF。
- `MinerU layout`：MinerU 產生的 layout PDF，會以色塊/框線標示版面分區，適合判斷 MinerU 是否已經切壞。
- `MinerU origin`：MinerU 輸出資料夾中的原始 PDF 複本，適合和官方 PDF 對照。

人工審核按鈕的語意：

- `通過`：此題 candidate 可進入後續正式入庫佇列。
- `標記已看過`：已人工看過，但暫不表示可以入庫。
- `保留疑問`：需要後續再查。
- `阻擋入庫`：目前不可入正式題庫。
- `只加註記`：保存觀察或修正方向，供後續 parser 或人工校正使用。

Review UI 的 `資料庫層級` 按鈕會顯示目前資料所在關卡，包括來源 PDF/MinerU raw、題目 candidate、QA flags、題目人工審核、答案核對與正式題庫表。此視圖用於理解目前 pipeline 狀態，不代表所有資料都已正式入庫。

如果要停掉：

```bash
docker compose stop review-ui
```

## DBeaver 連線設定

PostgreSQL 仍由 Docker Compose 管理。DBeaver 可用以下設定連線：

```text
Database type: PostgreSQL
Host: 127.0.0.1
Port: 54329
Database: tw_national_exam_dev
Username: national_exam
Password: national_exam_dev_password
Schema: exam
```

建議先熟悉這幾張表：

- `exam.official_documents`：官方 PDF 索引，每份題目、答案、修正答案各一筆。
- `exam.question_answer_document_pairs`：題目 PDF 與 primary answer PDF 的配對，`MOD` 優先於 `ANS`。
- `exam.question_candidates`：parser 產生的候選題目，還不是正式題庫。
- `exam.question_parse_issues`：候選題目的機械檢查疑點。
- `exam.question_review_events`：人工審核紀錄。
- `exam.answer_review_events`：答案核對紀錄，獨立於題目結構審核。
- `exam.review_ui_preferences`：Review UI 篩選條件、目前題目與 PDF 模式。
- `exam.questions` / `exam.question_options` / `exam.answers`：正式題庫表，目前不要全量寫入。

推薦先在 DBeaver 跑：

```sql
SELECT quality_status, review_status, count(*)
FROM exam.question_candidates
GROUP BY quality_status, review_status
ORDER BY quality_status, review_status;
```

找出目前最需要排查的文件：

```sql
SELECT
  source_registry_key,
  count(*) AS candidate_count,
  sum(issue_count) AS issue_count_sum
FROM exam.question_candidates
GROUP BY source_registry_key
ORDER BY candidate_count DESC
LIMIT 20;
```

## 已知風險

- 舊的 `scripts/ingest_ready_pairs_questions_to_postgres.py` 會批次寫正式 `questions/options/answers`，現階段不要用於全量入庫。
- `scripts/ingest_sample_questions_to_postgres.py` 是早期 sample 測試路線，不代表正式流程。
- 1407 份 MinerU output 屬於 `truncated_name_match`，入庫時應使用 audit resolved path，不要只靠預期 stem 推導。
- 圖片題與題組題需要人工抽查；不能只靠題目文字 parser 判定成功。
