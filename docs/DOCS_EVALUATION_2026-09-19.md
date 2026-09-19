# docs/ 逐份評估（2026-09-19）

## 這份評估回答什麼

使用者要求「剩下 39 份先逐份評估一次」。**39 份全部讀過**，這份文件記每一份的處置與理由。

評估的觸發點是一個量測：**39 份裡沒有一份提到 `qbr`、`/v2` 或 `golden_path`**。
它們是 `qbr` 之前那一代管線的文件集。

而 `qbr/PROPOSED_WORKFLOW.md` 自己已經對這批文件做過分類與反駁：它把管線分成
**A. legacy（MinerU + 全文件 VLM）**、**B. 先前改進方案（就是 `docs/` 這批）**、
**C. MOEX protocol v1**、**D. proposed（`qbr`，已量測）**，並給了 B 的判語：

> same centre, so same cost（規則想降低它，但**預算沒有量**）

也就是說：**這批文件不是「另一套做法」，是 `qbr` 已經量過並取代的那一套。**
處置不能是「把它們更新成 qbr」，因為那不是補充，是改寫它們的結論。

---

## 量測到的具體陳舊（不是印象）

| 項目 | 數 | 可驗證的事實 |
|---|---|---|
| 指向 `/Users/tim/tw-national-exam-catalog` 的連結 | **30** | 該目錄**不存在**（實測 `No such file or directory`）；實際在 `AI workspace/ai_learning_platform/` 下 |
| 引用 `scripts/<name>.py` 但檔案不在該路徑 | **13** | 例如 `batch_run.py`、`measure_skeleton.py`、`survey_categories.py` 現在在 `qbr/scripts/` |
| 提到 `Ollama` | 8 份 | 現行決策是 MTPLX，不是 Ollama |
| 提到 `medgemma` | 2 份 | 已明確停用 |
| 沒有索引 | — | `docs/` 沒有 `README.md`／`index.md` |

**30 個失效連結與 13 個失效指令是硬的事實**，不是風格問題：照著文件做會失敗。

---

## 處置分四類

### 類 1：**ACTIVE** —— 現行有效，與 `qbr` 不衝突（保留原樣）

這些描述的是**另一條軸**（catalog／registry／治理／發布），`qbr` 沒有取代它們。

| 文件 | 為什麼仍有效 |
|---|---|
| `source-policy.md` | 官方來源與 checkbox id 解析；`qbr` 也依賴這些 registry key |
| `known-issues.md` | 官方 catalog 的 70,555 列普查與三類 issue；純資料事實 |
| `locked-27-category-name-stability.md` | 27 類科名稱穩定性；`qbr` 依賴這個拼法對照 |
| `historical-transition-notes.md` | 全形/半形括號、制度交叉期命名；`answer_sheets._swap_brackets` 正是為此而寫 |
| `catalog-expansion-nodes.md` / `full-batch-expansion-plan.md` | 下載範圍規劃；與解析方式無關 |
| `publication-roadmap.md` | package 發布到 GitHub Releases／HF 的策略 |
| `contribution-guide.md` | 貢獻原則（官方 raw 不覆寫） |
| `external-data-location.md` | **已有 2026-08-07 現況補充**，自我修正過 |
| `question-bank-release-validation-flow.md` | SQL 正式表 → package 的發布閘門；`qbr` 不寫 SQL，所以這仍是那個環節 |
| `30-normalized-items-storage-policy.md` | 衍生資料根目錄政策 |
| `devspace-chatgpt-mcp.md` | 開發通道；但**要修 `/Users/tim/` 路徑** |

### 類 2：**HISTORICAL** —— 已被 `qbr` 取代，但必須保留（加註記，不改內容）

**這些是 `qbr` 的對照組，刪掉就消滅了「為什麼要換」的證據。**
`PROPOSED_WORKFLOW.md` 的 §1、§4 引用了它們。應加一行狀態告示，指向 `qbr`。

| 文件 | 被 `qbr` 取代的什麼 |
|---|---|
| `sustainable-question-bank-workflow-todo.md` | 目標流程圖的 `MinerU OCR → parser → 本地小模型 → Ollama Cloud`；`qbr` 量到 30/30 native text，**不需要那條路** |
| `parser-rule-inventory.md` | 「規則三層」；`qbr` 的立場是**腳本只留紙張性質，其餘轉提示詞** |
| `text-normalization-rules.md` | 字形修正 registry；`qbr` 改成「PUA/相容字是**警報類別，永不自動映射**」 |
| `ocr-correction-inbox.md` | 同上的人工輸入區 |
| `review-automation-strategy.md` | 三層自動判斷；「AI advisory 同一 provider 同盲點」正是 `qbr` 要修的 |
| `database-ingestion-preflight.md` / `group-and-layout-ingestion-policy.md` / `sql-review-staging-preflight.md` | MinerU→candidate→SQL 的入庫前流程；`qbr` 是**另一個上游**，但 SQL 那半仍有效 → 加註記而非廢除 |
| `visual-ai-audit-workflow.md` | 三個圖片結論；`qbr` 的爭議是**量測**，不是人工三選一 |
| `llmshare-tri-model-question-audit-plan.md` | 四路 compact 初審；本地模型清單已換代 |
| `compact-initial-audit-pilot-2026-07-29.md`、`local-model-nine-model-comparison-2026-07-29.md`、`local-qwen36-batch-comparison-2026-07-29.md` | 模型比較的**量測快照**；當時的模型已不在用（含 Ollama / medgemma） |
| `moex-incremental-review-pipeline.md` | 增量 worker |
| `ai-workflow-architecture.md`、`database-architecture.md`、`local-rag-resource-assessment.md` | 架構草案；硬體基準（M4 Max）已變 |

### 類 3：**SUPERSEDED-UX** —— 介面部分已被 v2 取代

| 文件 | 處置 |
|---|---|
| `review-ui-workflow-console.md` | v1 八線道工作台的文件。**v1 已降為參考**，這份文件描述的東西仍在服務但不再維護 → 加註記指向 `ROUTE_HISTORY.md` |
| `review-ui-system-audit-2026-07-29.md` | 審計對象是 **Mac Studio `.70`**，而那是 **stopped rollback standby** → 加註記 |

### 類 4：**CURRENT-OTHER-TRACK** —— 現行，但屬 AI395 SQL／模型路由那條線

**這批是活的，不是歷史。** 它們描述 AI395 staging 的 GLM-5.3-Flash 五 lane 路由，
與 `qbr` 是**不同軌道**（`qbr` 產出 package，不寫 SQL；AI395 是 production writer）。
不應改內容，只應在索引裡標明它們屬於哪條線。

| 文件 | 狀態 |
|---|---|
| `dify-n8n-review-pipeline-spec.md` | 現行整合規格（2026-08-22＋08-28 覆蓋） |
| `ai395-open-model-review-implementation-plan.md` | Luna handoff（2026-08-22） |
| `ai395-review-workflow-operator-learning-guide.md` | 操作手冊（2026-08-22） |
| `glm-5.3-flash-litellm-runbook.md` | 現行路由手冊（2026-08-28） |
| `ai395-review-context-control.md` | context admission policy |
| `ai395-runtime-maintenance.md` | runtime 維護 |
| `ai395-production-cutover-2026-08-09.md` | cutover 證據（**已完成**，是歷史證據但仍是權威事實來源） |
| `chatgpt-codex-llm-review-channel.md` | 兩條通道的劃分 |
| `ryzen-ai-max-395-migration-runbook.md` | **已有 cutover 完成告示** |
| `mineru-environment-sync.md` | **已有架構警告** |
| `remote-mineru-worker.md` | **已有路徑告示** |

---

## 本次實際動了什麼

只動**會讓讀者做錯事**的部分，不改寫結論：

1. 新增 `docs/README.md` —— **索引**，把四類標出來，並說明 `qbr` 與 AI395 是兩條軸。
   這是 39 份文件最缺的東西：沒有任何一處告訴讀者哪份是現行、哪份是歷史。
2. 修正 `docs/skills/extract-exam-paper-structure/SKILL.md`（指向舊沙盒）。
3. 修正 `qbr/scripts/fetch_corrections.py`、`scripts/review_run.sh` 的陳舊路徑。
4. 更正 `qbr/reports/merge_into_catalog.md` 我自己寫錯的一段（見下）。

**沒有動的**：39 份的內文（除上述兩處）。理由：它們是證據與決策史，
改寫證據會讓 `PROPOSED_WORKFLOW.md` 的引用失去所指。

---

## 未解（誠實列出）

1. **30 個 `/Users/tim/tw-national-exam-catalog` 連結仍然失效。** 我沒有批次改，
   因為那是 7 份文件、30 處，且要逐處判斷「該指向 repo 內相對路徑還是留在說明文字裡」。
   這是**已知、有量測、未修**的一項。
2. **13 個失效的 script 路徑**同理未改。
3. **29 份文件沒有狀態告示。** 我只建了索引；沒有在每份頂端加 banner。
   索引能解決「找不到」，但一個直接開檔的讀者仍不會看到告示。
4. **類 2 的「加註記」尚未執行**，目前只存在於這份評估與索引裡。
