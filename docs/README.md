# docs/ 索引

**先看這裡，再看個別文件。** 這個目錄有 47 份文件，跨越了**兩代管線**與**三條不同的軸**；
沒有索引時，最常見的錯誤是照著一份已被取代的文件做事。

逐份評估與理由：[`DOCS_EVALUATION_2026-09-19.md`](DOCS_EVALUATION_2026-09-19.md)。

---

## 先確認你在哪一條軸上

```text
軸 1  考題建立（PDF → 可驗封裝）        -> qbr/            ← 2026-09 併入主線
軸 2  審核（人做決定、record）          -> review_ui/ 與 AI395 SQL
軸 3  catalog／registry／發布治理        -> 本目錄與 catalogs/
```

**這三條軸的關係**：軸 1 讀官方 PDF 產出 package，**不寫資料庫**；
軸 2 是人對題目做決定，寫 append-only event，AI395 是 production writer；
軸 3 是來源與發布。`qbr` 取代的是軸 1 的**舊做法**（MinerU + 全文件 VLM），
**不是**軸 2 或軸 3。

| 你要做什麼 | 讀這份 |
|---|---|
| **審完一批題，要跑 AI 審核迴圈、建新規則、重掃** | [skill](skills/run-question-review-loop/SKILL.md) |
| 建立考題（官方 PDF → 封裝） | [`../qbr/AGENTS.md`](../qbr/AGENTS.md) ＋ [skill](skills/build-exam-question-bank/SKILL.md) |
| **管線現在到哪了**（穩定／待優化／待決策） | [skill](skills/qbr-pipeline-status/SKILL.md) |
| 修抽取／切題的缺陷（讀錯題數、文字重複、選項被截斷） | [skill](skills/repair-qbr-extraction/SKILL.md) |
| 審題介面 | [`../review_ui/AGENTS.md`](../review_ui/AGENTS.md) ＋ [skill](skills/review-ui-v2/SKILL.md) |
| **把審題介面開成常駐服務**（Docker、LAN、開機自動起） | [skill](skills/deploy-qbr-review/SKILL.md) |
| 舊介面為什麼被換掉 | [`ROUTE_HISTORY.md`](ROUTE_HISTORY.md) |
| 管線最佳化的兩個方向 | [`PIPELINE_OPTIMIZATION_DIRECTIONS.md`](PIPELINE_OPTIMIZATION_DIRECTIONS.md) |
| 使用本機模型 | [skill](skills/operate-local-open-models/SKILL.md) |
| 專案治理 | [`governance/README.md`](governance/README.md) |

---

## 類 1：現行有效（與 `qbr` 不衝突）

描述 catalog、來源、命名與發布 —— `qbr` 依賴它們，但沒有取代它們。

- [`source-policy.md`](source-policy.md) — 官方來源與 checkbox id 解析
- [`known-issues.md`](known-issues.md) — 官方 catalog 普查與 issue 分類
- [`locked-27-category-name-stability.md`](locked-27-category-name-stability.md) — 27 類科名稱穩定性
- [`historical-transition-notes.md`](historical-transition-notes.md) — 全形/半形括號、制度交叉期命名
- [`catalog-expansion-nodes.md`](catalog-expansion-nodes.md)、[`full-batch-expansion-plan.md`](full-batch-expansion-plan.md) — 下載範圍規劃
- [`publication-roadmap.md`](publication-roadmap.md) — dataset 發布策略
- [`contribution-guide.md`](contribution-guide.md) — 貢獻原則
- [`external-data-location.md`](external-data-location.md) — 大型資料根目錄
- [`30-normalized-items-storage-policy.md`](30-normalized-items-storage-policy.md) — 衍生資料政策
- [`question-bank-release-validation-flow.md`](question-bank-release-validation-flow.md) — SQL → package 的發布閘門
- [`devspace-chatgpt-mcp.md`](devspace-chatgpt-mcp.md) — 開發通道
- [`local-review-workflow.md`](local-review-workflow.md) — 本機離線 staging 工作流（從 parser candidate 開始，產出 SQLite/JSONL 供人工檢查；**不寫 production**）

## 類 2：**歷史** —— 已被 `qbr` 取代，保留為證據

**不要照這些做事。** 它們描述 MinerU + 全文件 VLM 那一代管線。
保留的理由：`qbr/PROPOSED_WORKFLOW.md` §1／§4 用它們當**對照組**，
刪掉就消滅了「為什麼要換」的量測。**這些是證據，不是規範。**

- [`sustainable-question-bank-workflow-todo.md`](sustainable-question-bank-workflow-todo.md) — 舊目標流程圖
- [`parser-rule-inventory.md`](parser-rule-inventory.md) — 舊「規則三層」
- [`text-normalization-rules.md`](text-normalization-rules.md) — 舊字形修正 registry
- [`ocr-correction-inbox.md`](ocr-correction-inbox.md) — 舊人工修正輸入區
- [`review-automation-strategy.md`](review-automation-strategy.md) — 舊三層自動判斷
- [`database-ingestion-preflight.md`](database-ingestion-preflight.md)、[`group-and-layout-ingestion-policy.md`](group-and-layout-ingestion-policy.md)、[`sql-review-staging-preflight.md`](sql-review-staging-preflight.md) — 舊入庫前流程（SQL 那半仍有效）
- [`visual-ai-audit-workflow.md`](visual-ai-audit-workflow.md) — 舊圖片三結論
- [`llmshare-tri-model-question-audit-plan.md`](llmshare-tri-model-question-audit-plan.md) — 舊四路初審
- [`compact-initial-audit-pilot-2026-07-29.md`](compact-initial-audit-pilot-2026-07-29.md)、[`local-model-nine-model-comparison-2026-07-29.md`](local-model-nine-model-comparison-2026-07-29.md)、[`local-qwen36-batch-comparison-2026-07-29.md`](local-qwen36-batch-comparison-2026-07-29.md) — 已換代的模型量測快照
- [`moex-incremental-review-pipeline.md`](moex-incremental-review-pipeline.md) — 增量 worker
- [`ai-workflow-architecture.md`](ai-workflow-architecture.md)、[`database-architecture.md`](database-architecture.md)、[`local-rag-resource-assessment.md`](local-rag-resource-assessment.md) — 架構草案（硬體基準已變）

## 類 3：**介面已被 v2 取代**

- [`review-ui-workflow-console.md`](review-ui-workflow-console.md) — v1 八線道工作台（v1 已降為參考）
- [`review-ui-system-audit-2026-07-29.md`](review-ui-system-audit-2026-07-29.md) — 審計對象是 Mac Studio `.70`，現為 **stopped rollback standby**

## 類 4：現行 —— **AI395 SQL／模型路由**那條線

**這批是活的。** 它們描述 AI395 staging 的 `glm-5.3-flash` 五 lane 路由，
與 `qbr` 是不同軌道（`qbr` 產出 package，**不寫 SQL**；AI395 才是 production writer）。

- [`dify-n8n-review-pipeline-spec.md`](dify-n8n-review-pipeline-spec.md) — 現行整合規格
- [`ai395-open-model-review-implementation-plan.md`](ai395-open-model-review-implementation-plan.md) — 實作規劃（handoff）
- [`ai395-review-workflow-operator-learning-guide.md`](ai395-review-workflow-operator-learning-guide.md) — 操作手冊
- [`glm-5.3-flash-litellm-runbook.md`](glm-5.3-flash-litellm-runbook.md) — 現行路由手冊
- [`ai395-review-context-control.md`](ai395-review-context-control.md) — context admission policy
- [`ai395-runtime-maintenance.md`](ai395-runtime-maintenance.md) — runtime 維護
- [`ai395-production-cutover-2026-08-09.md`](ai395-production-cutover-2026-08-09.md) — cutover 證據（已完成）
- [`chatgpt-codex-llm-review-channel.md`](chatgpt-codex-llm-review-channel.md) — 兩條通道的劃分
- [`ai-audit-subject-workflow.md`](ai-audit-subject-workflow.md) — 分科 AI audit 的任務匯出／模型審查／結果匯入（`gpt-5.6-luna` 路徑）
- [`ryzen-ai-max-395-migration-runbook.md`](ryzen-ai-max-395-migration-runbook.md) — 已有 cutover 完成告示
- [`mineru-environment-sync.md`](mineru-environment-sync.md) — 已有架構警告
- [`remote-mineru-worker.md`](remote-mineru-worker.md) — 已有路徑告示

## Meta

- [`DOCS_EVALUATION_2026-09-19.md`](DOCS_EVALUATION_2026-09-19.md) — 逐份評估（47 份全讀）
- [`ROUTE_HISTORY.md`](ROUTE_HISTORY.md) — 審題介面路線史
- [`PIPELINE_OPTIMIZATION_DIRECTIONS.md`](PIPELINE_OPTIMIZATION_DIRECTIONS.md) — 最佳化的兩個方向
- [`skills/`](skills/) — 可重複的工作程序

---

## 已知未修（誠實列出）

1. **30 個 `/Users/tim/tw-national-exam-catalog` 連結失效**（該目錄不存在，實際在
   `AI workspace/ai_learning_platform/` 下）。集中在 7 份文件。未批次修改，因為每一處都要判斷
   該改成 repo 相對路徑還是留在說明文字裡。
2. **13 個 `scripts/<name>.py` 路徑失效**（那些檔案現在在 `qbr/scripts/`）。
3. **類 2、類 3 的文件沒有頂部狀態告示**，只有這份索引標明它們的地位。
   直接開檔的讀者不會看到。
4. **`Ollama` 出現在 8 份、`medgemma` 出現在 2 份**（現行決策是 MTPLX，兩者都不用）。
   這些在類 2 的歷史文件裡，**保留原樣是刻意的** —— 它們記錄的是當時的決策。
