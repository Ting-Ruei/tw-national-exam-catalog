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

題組題、圖表題、人工補圖與正式入庫防跑版規格見 [group-and-layout-ingestion-policy.md](/Users/tim/tw-national-exam-catalog/docs/group-and-layout-ingestion-policy.md)。正式入庫前先執行 read-only 預檢：

JSONL-heavy review 逐步轉成 SQL review staging 的方向、全量 SQL 匯入前的通用掃描規則、科目覆寫與目前醫事檢驗師未通過狀態盤點，見 [sql-review-staging-preflight.md](/Users/tim/tw-national-exam-catalog/docs/sql-review-staging-preflight.md)。

```bash
python3 scripts/preflight_formal_ingest.py
```

若要輸出逐題阻擋原因：

```bash
python3 scripts/preflight_formal_ingest.py --format csv --output /tmp/formal_ingest_preflight.csv
```

分科人工審核與答案核對都完成後，可先用 dry-run 檢查正式 promote 數量：

```bash
python3 scripts/promote_ready_candidates_to_formal_postgres.py \
  --category 醫事檢驗師 \
  --subject 生物化學與臨床生化學 \
  --dry-run
```

確認 `skipped_count=0` 後，再寫入正式題庫表：

```bash
python3 scripts/promote_ready_candidates_to_formal_postgres.py \
  --category 醫事檢驗師 \
  --subject 生物化學與臨床生化學
```

此 promote 腳本會先跑同一套 preflight 門檻，只允許題目人工狀態與答案核對狀態都為 `accept` / `unblock` 的題目進正式表。若人工校正過題幹、選項、圖片或答案，正式表會使用校正後的有效版本；原始 candidate 仍保留在審核層以利追溯。

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
scripts/preflight_formal_ingest.py
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

目前暫停未審核的自動大量寫入；已分科完成題目審核與答案核對者，可用 `scripts/promote_ready_candidates_to_formal_postgres.py` 逐科升級：

- `exam.question_groups`
- `exam.questions`
- `exam.question_options`
- `exam.answers`
- `exam.question_assets`

正式題目表必須使用 accepted/corrected candidate 升級，不得直接從 MinerU markdown 或尚未通過答案核對的 candidate 寫入。

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

全量 candidate 可以先進入 Review UI，但不應以全量清單直接人工處理。審核流量應先用考別、科目、年份、考次切小，再用審核狀態分流。`退回未審` 專門用於 parser 規則更新後被重整的題目，例如希臘字母、上下標、科學單位或 OCR 字形修正；這些題會以 `reset_review` 事件退回未審，必須人工重新確認，不能沿用舊的 `accept`。

`reset_review` 的粒度必須是單題，不是整份考卷。當 parser 或 Review UI 規則更新後，應先用新舊 candidate 逐題 diff，只退回實際改變且曾被人工審過的 `candidate_key`。不可因為某個年份或科目有少數題被修正，就把同年度、同考次或同科目的所有題目整批退回；整批退回只適用於 diff 證明整批所有題目的入庫欄位都被改動的情況。

`reset_review` 事件不得遮蔽人工審核脈絡。追加事件時應帶入 `previous_action`、`previous_reviewed_at`、`previous_notes` 與 `reset_notes`；其中 `previous_notes` 是使用者原本的 block / needs_review / comment 註記，`reset_notes` 說明 parser 或 UI 實際修了什麼。正式入庫前若某題仍處於 `reset_review`，應視為未審，不可使用更早的 `accept` 直接入庫。

`quality_status=pass` 只代表目前 QA flags 沒有 error/warning，不代表題目已經可以正式入庫。正式入庫至少還要人工審核事件為 `accept` 或明確校正通過，並完成後續答案核對。自 `moex_mineru_candidate_v0.3` 起，`markup_needs_review` 從 info 提升為 warning，因此含公式、上下標、希臘字母、羅馬數字或 MinerU markup 的題目會先進 `needs_review`，讓人工優先確認顯示品質。

答案表 parser 需同時接受 `題號`、`題序`、`题序` 這類表頭。1151 醫事檢驗師微生物曾出現 `_MOD` 內含完整 1-80 答案表，但 MinerU 將表頭辨識為 `题序`，造成舊 parser 整批找不到答案；此類應視為答案表 OCR / parser 規則問題，而不是題目 PDF 缺答案。若 `_MOD` 內的某題答案為 `#`，需用備註解析成 `accepted_values`，例如 `B|C|BC`。

題目審核畫面仍顯示目前 parser 抓到的答案，避免遮蔽資訊；但答案是否正確、整份答案表是否抓到、`MOD` / `ANS` 優先序與答案表解析，會在 `答案核對` 模式統一核對並寫入 `answer_review_events.jsonl`。`missing_answer` 應視為答案關卡疑點，不應在題目結構審核時把整份考卷打成 blocked。

`答案核對` 模式以答案表為 review unit。左側是一份答案表 / 一個考次，中間顯示該考次所有已通過題目與答案的對應，右側顯示答案 PDF 或答案 MinerU layout，而不是題目 PDF。答案來源需明確標示 `ANS` 或 `MOD`；若有 `MOD`，正式入庫仍以 `MOD` 優先。此關卡不得繞過題目審核：只有題目審核最新狀態為 `accept` 或 `unblock` 的題目可以被答案通過推進正式入庫，否則必須保留「題目未審核通過」狀態或註記。

答案人工修正以點選 A-D 為主，文字格式只是事件儲存格式。ANS 單選答案若無其他疑點，可沿用 parser 結果；MOD 多答案、特殊更正或 `#` 會在畫面標示警示，人工需看答案 PDF 後點選確認。儲存格式暫定為：單一答案 `A`；多個可接受答案 `A|C`；多個單選加複選皆可接受 `A|C|AC`；複選且需同時符合 `A+C`；送分或特殊答案 `送分` / `一律給分`。若 MOD 需人工確認但仍為 `#` 或空白，前端與後端都不得讓整份答案通過。這些文字格式先寫入 `answer_review_events.jsonl`，正式入庫前再正規化成 answers JSON，例如 `accepted_values`、`requires_all`、`is_special_correction`。

Review UI 的題目卡片上方有大型 `通過` / `阻擋入庫` 按鈕，可用於快速瀏覽。若需要人工修正，使用 `人工校正` 區編輯題幹、選項、答案與題組；校正內容會寫入 review event 的 `correction` 欄位，並保留 parser 原始輸出。單純 `儲存人工校正` 只保存內容修補，會保留該題原本的 `block` / `needs_review` / `accept` 狀態；只有 `儲存並通過` 或 `通過` 才會把題目送往答案核對。正式入庫時，若最新有效 review event 帶有 `correction`，應優先使用人工校正版；後續 `accept` 事件也會繼承既有校正，避免通過後遺失人工修正。

快速瀏覽大量題目時，可使用上方 `本頁 pass 批次通過`。建議流程是先依考別、科目、年份、考次與 `pass` 篩出一批題目，人工快速瀏覽 PDF 與 candidate，將錯題標成 `block` 或 `needs_review`，再按批次通過處理剩餘題目。批次通過只會處理目前畫面已載入的 candidate，且後端會跳過 parser 非 `pass`、最新人工狀態為 `block` / `needs_review`、已經 `accept` / `unblock`，或 AI advisory 仍為 `needs_review` / `block` 的題目；事件會保留 `batch_action=accept_visible_pass`，方便之後追蹤哪些題是批次通過。

AI advisory 的顯示狀態採「保守有效狀態」。如果模型或 Codex 輸出的原始狀態是 `pass`，但同時有 findings、recommended action、非 pass label，或 Review UI 可由內容推得 OCR/簡繁校正建議，畫面應顯示為 `AI needs_review`。這個狀態不會自動更改人工審核，但會阻止批次通過，直到人工確認。若使用「套用 AI 建議校正」，應以 review event 保存 correction，並將該題留在 `needs_review` 或原本的 `block` / `exclude`，不可因 AI 改字而自動 `accept`。

AI 簡繁/OCR 校正只針對明顯字形錯誤或簡體字，例如 `麸胺` 轉 `麩胺`、`胰岛` 轉 `胰島`、`肾` 轉 `腎`、`氢` 轉 `氫`、`辅` 轉 `輔`。不應把台灣教材可接受但風格不同的用字硬改成另一套，例如 `酶` 不視為錯字。若某題後方英文原文可確認中文翻譯被 MinerU 誤辨，AI 可以提出 suggested correction，但仍必須人工比對 PDF 後通過。

生化題常見胺基酸中英並列，英文原文可作為 OCR 錨點。若出現 `valine`、`glutamine`、`tyrosine`、`phenylalanine` 等英文，但附近中文沒有對應的 `纈胺酸`、`麩醯胺`、`酪胺酸`、`苯丙胺酸` 等譯名，或出現形近誤字，應標成 `amino_acid_translation_suspect`。這類警示只影響人工審核排序與 AI suggested correction，不得自動改成人工通過。

攝氏溫度是高頻漏網符號。MinerU 可能輸出 `65^{\circ} C`、`65^\circ C`、`65° C` 或類似格式，parser 與 AI advisory 都應統一視為 `65℃` 顯示問題；若候選題仍殘留這類格式，應標成 `science_notation_suspect` 並退回人工確認。

全量 AI advisory 不應直接在單一 prompt 中處理。暫存庫目前可能包含十萬級 candidate，應先用 `scripts/export_codex_audit_batch.py --all-matching --include-accepted --force --limit 0 --chunk-size 500` 切成小型 task JSONL，再由 Codex、ChatGPT MCP、OpenAI API 或本地模型逐批產生 result JSONL。結果匯入一律使用 `scripts/import_codex_audit_results.py <run_dir>`，只追加 `question_ai_review_events.jsonl`，不得改動人工審核狀態。

若 AI 結果包含 `suggested_correction`，Review UI 會把它顯示在 `AI 有建議校正` 篩選中。使用者按 `套用 AI 建議校正` 後，系統只寫入 review correction，並保留在 `needs_review` 或既有 `block` / `exclude`；正式入庫仍以最新人工 `accept` / `unblock` 為準。

題目含圖片時，Review UI 會將圖片直接放入題目預覽卡片，也會保留下方圖片來源總覽。右側 PDF 檢視不會因為審核按鈕刷新而跳回頂端，只有切換題目或 PDF 來源時才重新載入。

若 MinerU 將題幹中的表格拆成 `<table>` 結構，但人工審核判斷「前端顯示或正式入庫仍需要保留原始視覺版面」時，不應直接把人工截圖混入 MinerU 的 `images/` 或 `vlm/` 輸出資料夾。建議建立 `國考題資料夾/30_normalized_items/manual_assets/<candidate_key>/`，放入人工截圖與 `manifest.json`，再透過 review event 的 `correction.image_refs` / `correction.stem_image` 掛回該題。正式入庫時，結構化 table 可進文字/表格欄位以利搜尋，manual asset 則進 `question_assets`，`asset_role` 建議使用 `question_table_manual_screenshot`。

人工補圖不代表題目自動通過。若該題原本是 `block` 或 `needs_review`，補圖事件應以 `correct` 保存，並保留原人工狀態，等人工重新確認後才改成 `accept`。113-1 醫事檢驗師生化第 7 題就是此類案例：MinerU 已解析出表格文字，但因表格視覺版面對審題重要，另補人工截圖作為 manual asset。

PDF 表頭不得被當成題目。若疑似題目區塊含有多個表頭欄位，例如 `代號`、`類科名稱`、`科目名稱`、`考試時間`、`座號`、`本試題`、`禁止使用電子計算器`、`單一選擇題`，且沒有 A-D 選項，parser 應直接跳過，不產生 candidate。112-2 醫事檢驗師生化曾出現表頭第一行 `112 年第二次...` 被誤切成第 112 題的案例；這類資料不是題目，不應透過人工校正保留。

在既有 candidate JSONL 尚未重建前，表頭誤切或其他確認為非題目的 candidate 應以人工 `exclude` 事件標記，代表「排除入庫、排除批次通過」，而不是直接刪除 JSONL。AI advisory 若曾誤判 `pass`，應追加 `reset_ai_review` 後再以 `block` 標記原因，避免 Review UI 顯示互相矛盾的提示。下一次重建 candidate 時，parser 規則會讓這些假題不再產生。

Review UI 顯示層應優先服務人工審核，而不是完整重播 MinerU 的 table HTML。因此題幹若含結構化 `<table>`，主畫面應只顯示表格前的乾淨題幹，並提示「請以附圖或 PDF 為準」；原始表格 HTML 留在 parser 原始欄位、`raw_block` 或未來專用 table 欄位即可，不直接當成人工審核畫面的主內容。

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
- `exam.questions` / `exam.question_options` / `exam.answers`：正式題庫表；只接受分科 preflight ready 且題目/答案都已人工通過的資料。

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
- `scripts/promote_ready_candidates_to_formal_postgres.py` 是目前正式分科入庫路線。先跑 `--dry-run`，確認 `skipped_count=0` 與題數、選項數、答案數合理後再寫入。
- 1407 份 MinerU output 屬於 `truncated_name_match`，入庫時應使用 audit resolved path，不要只靠預期 stem 推導。
- 圖片題與題組題需要人工抽查；不能只靠題目文字 parser 判定成功。
