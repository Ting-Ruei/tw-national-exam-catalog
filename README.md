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
  moex_expansion_node_summary__y100-115.csv
  moex_expansion_category_summary__y100-115.csv
  moex_expansion_subject_summary__y100-115.csv
  moex_official_category_track_summary__y100-115.csv
  moex_official_category_subjects__y100-115.csv
  expansion_download_lists/
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
  catalog-expansion-nodes.md
  full-batch-expansion-plan.md
schemas/
  moex_catalog.schema.json
  question_candidate.schema.json
  database/
    postgresql_schema.sql
scripts/
  export_moex_subject_catalog.py
  download_moex_pdfs_from_catalog.py
  download_moex_pdfs_from_official_track_list.py
  start_moex_official_track_pdf_download.py
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

`30_normalized_items/` 是 parser candidate、人工審核事件、AI advisory、manual assets 與入庫前暫存資料的衍生資料區。它可能因 `_repair_backups/` 的完整快照快速膨脹；清理前請先看 [docs/30-normalized-items-storage-policy.md](/Users/tim/tw-national-exam-catalog/docs/30-normalized-items-storage-policy.md)，不要直接刪除 active candidate、review events 或 manual assets。

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

Docker Compose 預設會用 `REVIEW_UI_BACKEND=sql` 啟動 Review UI，候選題列表與篩選從 PostgreSQL review staging 查詢；人工審核、答案審核與 AI advisory 仍會同步寫回 append-only JSONL，避免 SQL staging 測試時遺失審核紀錄。若要暫時回到舊的純 JSONL 查詢，可用：

```bash
REVIEW_UI_BACKEND=jsonl docker compose up -d review-ui
```

若 parser / candidate JSONL 已更新，或要把某個考別搬進 SQL review staging，可先套 schema，再匯入 candidate / issue 與 review events。例如醫事檢驗師全科：

```bash
bash scripts/postgres_apply_schema.sh
python3 scripts/ingest_question_candidates_to_postgres.py --category 醫事檢驗師
python3 scripts/ingest_review_events_to_postgres.py --category 醫事檢驗師
docker compose up -d review-ui
```

`ingest_question_candidates_to_postgres.py --category ...` 會讓 SQL staging 與目前 active candidate JSONL 對齊：同考別但已不在目前 JSONL 的舊 candidate 會從 SQL staging 移除，避免 Review UI 審到 parser 舊版本留下的幽靈題。這不會刪除本機 JSONL，也不會改官方 PDF / MinerU raw。

後續若要把全部考別一次轉入 SQL staging，可省略 `--category`；但在大規模匯入前，應先依 [SQL Review Staging Preflight](docs/sql-review-staging-preflight.md) 跑通用規則與科目覆寫掃描，確認選項切分、科學符號、圖表資產、題組線索與答案關卡分流沒有明顯回歸。

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

人工審核可用上方選單依考別、科目、年份、考次、parser 狀態與審核狀態篩選。篩選條件、目前題目與 PDF 模式會寫入 `exam.review_ui_preferences`，並在同資料夾保留 `review_ui_preferences.json` 備援。下一次開啟 Review UI 會回到上次進度；開頁後若切換成 `全部審核`、`未看過`、`未通過`、`全部狀態` 或其他篩選，畫面當下的選擇就是新的偏好，會同步保存，不能再被舊設定覆蓋。

Review UI 可以載入全量 candidate，但人工審核應以「流量分流」方式進行：先選考別、科目、年份與考次，再選 `未看過`、`退回未審`、`阻擋入庫` 或 `保留疑問`。其中 `退回未審` 代表題目曾因 parser 規則更新而被重整，例如科學符號、希臘字母、上下標或 OCR 字形正規化；這些題不會保留原本通過狀態，需人工重新確認後再按 `通過`。

Review UI 只會自動刷新 `question_review_events.jsonl`、`answer_review_events.jsonl` 與 `question_ai_review_events.jsonl` 這類小型 append-only logs；大型 `question_candidates__*.jsonl` 與 issue CSV 預設不會在每次請求自動重讀，避免 Docker 記憶體突然暴衝。若 parser / repair script 已改動 candidate 或 issue CSV，頁面上方會提示候選資料已更新，按 `重載資料` 或重啟 `review-ui` 後才會載入新的 candidate 內容。

`quality_status=pass` 只表示目前 parser 的機械規則沒有抓到 error/warning，不等於正式入庫通過。正式入庫仍需人工按下 `通過`，且後續答案核對關卡也要完成。自 `moex_mineru_candidate_v0.3` 起，題幹含公式、上下標或 markup 的 `markup_needs_review` 會進 `needs_review`，避免科學符號題被過早視為低風險。

答案表 parser 需同時接受考選部 PDF / MinerU OCR 可能出現的 `題號`、`題序`、`题序` 表頭。若同一科有 `_MOD`，仍以 `_MOD` 為 primary answer；但 `_MOD` 可能是完整更正後答案表，也可能只是局部更正說明，因此後續答案核對關卡要繼續保留 `raw_answer`、`accepted_values` 與 `is_special_correction`。

按下任一審核按鈕後，該題會寫入 `question_review_events.jsonl`，並自動跳到下一題。右側 PDF 不會因為按鈕刷新而跳回頂端；只有切換題目或切換 PDF 來源時才會載入新的 PDF。

題目卡片上方提供大型 `通過` / `阻擋入庫` 按鈕，適合快速瀏覽時連續審核。若看到 OCR 小錯、選項順序或題組標籤需要人工修正，可在 `人工校正` 區直接編輯題幹、選項、答案與題組；校正會以 `correction` 寫入 `question_review_events.jsonl`，標成有人工校正，不會覆蓋 parser 原始輸出，也不會單獨解除既有 `block` / `needs_review`。只有按 `儲存並通過` 或 `通過` 時，該題才會進入下一關。後續若再按 `通過`，該題仍會保留人工校正版，正式入庫時應優先使用人工校正版。

上方工具列的 `本頁 pass 批次通過` 用於快速瀏覽流程：先用目前篩選條件打開一批題目，把明顯錯誤的題目逐題標成 `阻擋入庫` 或 `保留疑問`，剩下 parser 狀態為 `pass` 的題目可一次寫入 `accept`。後端會再次防呆，只批次通過目前畫面傳入、parser `pass`、最新人工狀態不是 `block` / `needs_review` / 已通過，且 AI advisory 沒有 `needs_review` / `block` 的題目；批次事件會寫入 `batch_action=accept_visible_pass`，仍是 append-only。

題目審核畫面主要檢查題幹、選項、圖片、題組與 parser 切題品質。畫面仍會顯示目前 parser 抓到的答案，方便完整核對資料；但答案是否正確、整份答案表是否抓到、`MOD` / `ANS` 優先序與答案表解析，會在上方 `答案核對` 模式集中判定並寫入 `answer_review_events.jsonl`。因此 `missing_answer` 不應在題目結構審核階段造成整份考卷 blocked，而是留到答案核對關卡處理。

`題組審核` 模式是題目審核與答案核對之間的結構檢查層。它會把已有 `group_ref` 的題目與疑似題組但尚未綁定的題目，依考別、科目、年份、考次彙整成候選題組；每列提供 `回審此題`，可切回審題頁修正該題 `group_ref` 或人工狀態。這一層目前只做導流與檢查，不會直接把題組寫入正式 `exam.question_groups`；正式入庫仍需題目通過、題組綁定正確、答案核對通過。

Review UI 的 AI 區塊只顯示已批次產生的 advisory，不再提供單題即時 `AI 格式稽核` 或 `撤回 AI 稽核` 按鈕，避免人工審核時誤觸耗費模型流量。AI advisory 只做輔助判斷：檢查疑似 OCR 字形錯誤、簡繁混用、科學符號/上下標、選項數量、圖表線索與 parser 結構疑點。結果寫入 `question_ai_review_events.jsonl`，不會自動改變人工審核狀態。若 AI 原始結果是 `pass`，但同時帶有 findings、recommended action、advisory labels 或可套用的 OCR/簡繁校正建議，Review UI 會顯示成 `AI needs_review`，避免「有建議卻看起來通過」。AI 建議校正可以在畫面中套用，但套用後只會保留為 `needs_review` 或原本的 `block` / `exclude`，並停留在同一題讓人工立即核對；必須再由人工按 `通過` 才能進下一關。ChatGPT / Codex 協作通道與 LLM 稽核規劃見 [docs/chatgpt-codex-llm-review-channel.md](/Users/tim/tw-national-exam-catalog/docs/chatgpt-codex-llm-review-channel.md)。

若要指派 Codex 或其他模型掃描特定考別、科目、年份或考次，請使用 repo 內的 AI 稽核 skill：[docs/skills/national-exam-ai-audit/SKILL.md](/Users/tim/tw-national-exam-catalog/docs/skills/national-exam-ai-audit/SKILL.md)。規則採「通用核心 + 科目覆寫」：所有科目先套用 [core-rules.md](/Users/tim/tw-national-exam-catalog/docs/skills/national-exam-ai-audit/references/core-rules.md)，再依科目讀取 [subject-overrides.md](/Users/tim/tw-national-exam-catalog/docs/skills/national-exam-ai-audit/references/subject-overrides.md)。AI 輸出格式見 [output-schema.md](/Users/tim/tw-national-exam-catalog/docs/skills/national-exam-ai-audit/references/output-schema.md)。

若要穩定用 `5.4` / `5.4-mini` 逐科審核，優先使用「按科目分包」流程，避免 Review UI 的本機 heuristic 或 OpenAI API fallback 污染模型品質判斷。完整流程見 [docs/ai-audit-subject-workflow.md](/Users/tim/tw-national-exam-catalog/docs/ai-audit-subject-workflow.md)。目前可用以下指令產生每個考別＋科目的 task JSONL：

```bash
python3 scripts/export_subject_codex_audit_batches.py \
  --chunk-size 500 \
  --model-target 5.4 \
  --ai-policy pending-or-unreliable
```

若要把目前暫存庫全量切成 AI 稽核任務，不要一次丟整份 18 萬題給模型；使用切片輸出：

```bash
python3 scripts/export_codex_audit_batch.py \
  --all-matching \
  --include-accepted \
  --force \
  --limit 0 \
  --chunk-size 500
```

這會在 `國考題資料夾/30_normalized_items/question_candidates/codex_audit_tasks/<timestamp>/chunks/` 建立多個 task JSONL。模型逐份輸出 `codex_question_audit_results__...__partXXXX.jsonl` 後，可一次匯入整個 run：

```bash
python3 scripts/import_codex_audit_results.py \
  國考題資料夾/30_normalized_items/question_candidates/codex_audit_tasks/<timestamp>
```

AI 結果可以帶 `suggested_correction` / `suggested_changes`。Review UI 會在 `AI 有建議校正` 篩選中列出這些題目；按 `套用 AI 建議校正` 只會把修正寫成 review correction、停留在同一題並保留人工複核狀態，仍需人工按 `通過` 才能進入下一關。

若要讓訂閱制 ChatGPT 透過 MCP 連入本機專案，可用 DevSpace 通道。啟動方式：

```bash
bash scripts/start_devspace_chatgpt_mcp_screen.sh
```

本機 MCP endpoint 是 `http://127.0.0.1:7676/mcp`；ChatGPT 實際使用時需要一個公開 HTTPS tunnel 指到 `http://127.0.0.1:7676`。完整設定與安全注意事項見 [docs/devspace-chatgpt-mcp.md](/Users/tim/tw-national-exam-catalog/docs/devspace-chatgpt-mcp.md)。

`答案核對` 模式以整份答案表為單位，不以單題為單位。左側列表顯示這批答案使用 `ANS` 或 `MOD`，中間一次列出同一考次每個題號與 parser 抓到的答案，右側 PDF 固定顯示答案 PDF / 答案 MinerU layout，方便直接和官方答案表比對。只有前一關題目審核已 `accept` 或 `unblock` 的題目能進入答案核對；若題目未審核通過，即使答案頁被操作，也不能被答案通過事件推進正式入庫。

答案人工修正以點選 A-D 為主，避免人工輸入分隔符出錯。ANS 單選答案若無其他疑點，可沿用 parser 結果；MOD 多答案或 `#` 更正答案會在畫面標成需確認，人工看右側答案 PDF 後點選。儲存格式仍採簡明文字：單一答案 `A`；多個可接受答案 `A|C`；多個單選加複選皆可接受 `A|C|AC`；複選且需同時符合 `A+C`；送分或特殊答案可填 `送分`、`一律給分` 並在註記說明。若 MOD 仍為 `#` 或空白，整份答案不可通過。若整份答案表需要規則性修正，先用 `保留疑問` 與註記標出題號，再由後續批次工具正規化為入庫用 JSON。

若題目含圖片，圖片會直接融合在題目預覽卡片中，並保留下方圖片來源總覽，方便同時比對題文、圖片與右側 PDF。

若 MinerU 將表格拆成 `<table>`，但人工審核判斷仍需保留原始表格截圖，請不要修改 MinerU 的 `vlm/` 或 `images/` 輸出；改放到 `國考題資料夾/30_normalized_items/manual_assets/<candidate_key>/`，用 `manifest.json` 記錄來源，並透過 review event 的 `correction.image_refs` 掛回該題。這類 manual asset 入庫時應進 `question_assets`，與 parser 文字/table 並存。

在 Review UI 與正式入庫前處理時，題幹中的結構化 `<table>` 不應再直接當成主要閱讀內容顯示；應以乾淨題幹加 manual asset / 官方 PDF 為主。原始表格 HTML 仍可保留在 `raw_block` 或 parser 原始欄位中，作為追溯與後續結構化入庫參考。

parser 的題號偵測會依年份切換規則：早期卷面可能使用 `1 題幹`，新版卷面多為 `1.` / `1、` / `1．`。目前 `105` 年以前使用 legacy 題號規則，`106` 年起使用較嚴格的 modern 題號規則，避免把內文數字誤切成新題。

審核紀錄是 append-only。後續分析 parser 問題時，應以每題最新事件為準：若最後一次動作是 `通過`、`修正`、`標記已看過` 或 `解除阻擋`，舊的 `阻擋入庫` / `保留疑問` 註記只視為歷史，不再當成待修問題。可用以下指令查看目前仍有效的註記：

若 parser 規則全域更新導致候選題內容改變，應追加 `reset_review` 事件，而不是自動改成 `accept`。Review UI 會把 `reset_review` 視為 `未看過`，同時在篩選器中提供 `退回未審`，讓修整過的題目形成獨立工作隊列。

`reset_review` 必須以單題 `candidate_key` 為單位，只能退回實際因 parser / UI 規則更新而改變的題目。不要因為某個年度、考次或科目中有少數題被修整，就整批退回整份考卷；除非比對結果證明整份每一題的入庫欄位都被改動。建議流程是先重跑 parser 到 `tmp/`，逐題比對 `stem`、`options`、`answer`、`answer_payload`、`group_ref`、`image_refs`、`quality_status` 等入庫相關欄位，再只對有差異且原本已審過的題追加 `reset_review`。

追加 `reset_review` 時必須保留上下文：事件應包含上一個人工審核狀態、上一個審核時間、原人工註記 `previous_notes`，以及本次 parser / UI 修整摘要 `reset_notes`。Review UI 會把原註記與修整說明一起顯示，並預填到註記框，方便重審時知道當初為什麼擋下、這次又修了什麼。

```bash
python3 scripts/summarize_active_review_notes.py
```

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
- `exam.review_ui_preferences`：Review UI 的篩選條件、目前題目與 PDF 模式。
- `exam.questions` / `exam.question_options` / `exam.answers` / `exam.question_assets`：正式題庫表；目前只使用 `scripts/promote_ready_candidates_to_formal_postgres.py` 將已分科通過題目審核與答案核對的資料升級進入。

正式分科入庫先 dry-run：

```bash
python3 scripts/promote_ready_candidates_to_formal_postgres.py \
  --category 醫事檢驗師 \
  --subject 生物化學與臨床生化學 \
  --dry-run
```

確認題目、選項、答案與 skipped 數量後再寫入：

```bash
python3 scripts/promote_ready_candidates_to_formal_postgres.py \
  --category 醫事檢驗師 \
  --subject 生物化學與臨床生化學
```

## 授權與來源

請見：

- `LICENSE`
- `DATA_LICENSE.md`

本專案會區分「程式碼」、「官方 metadata」、「官方考題材料」與「社群整理出的衍生資料」，因為它們可能有不同的法律狀態、引用方式與再利用限制。
