# ai395 系列退場紀錄（retirement）

狀態：**不再適用於現行架構**，保留作考古
裁決：owner，2026-09-23（Asia/Taipei）
性質：唯讀紀錄。**不得**從本系列恢復 backend、writer、deployment 或資料庫依賴。

---

## 1. 裁決

Owner 2026-09-23 明確表示：

> ai_395 已經沒有在目前的架構規劃，任何與它相關的都不適用。

因此本系列：

- **不屬**任何現行流程、契約或部署單元
- **不得**作為新工作的設計依據或範例
- 保留在樹中僅為了：考古、以及其中與 ai395 無關的**契約設計**可能被未來借鏡

---

## 2. 為什麼「留著」而不是「刪掉」

三個實測事實支持保留而非移除：

1. **它已經完全脫離現行入口。** 搜尋 `AGENTS.md`、`qbr/AGENTS.md`、
   `review_ui/AGENTS.md`、`docs/skills/` 對 `ai395` 的引用：**零命中**。
   沒有任何現行契約指向它，所以它不會誤導新 session。
2. **它的測試今天就已經壞了。** 實測 `test_*ai395*.py`：
   `Ran 8 tests → FAILED (errors=2)`，
   失敗原因是 `ModuleNotFoundError: No module named 'build_three_source_spec'`
   （舊匯入名，早於 `build_ai395_three_source_spec` 的更名）。
   這證明**沒有任何人正在維護它**，也證明它早已不在 CI 意圖內。
3. **其中有可複用的設計資產**（見第 4 節）。直接刪除會丟掉這些契約。

**「改進要用取代，不是分岔」**：本系列的退場方式是在此留下明確紀錄，
而不是讓它繼續以「看起來還在運作」的姿態留在樹中。

---

## 3. 完整清單（51 個 tracked 檔案，實測於 2026-09-23）

### 3.1 設定（7）

`configs/ai395_review_pipeline/`：
`budget_policy.yaml`、`invalidation_matrix.yaml`、`pipeline.yaml`、
`provider_registry.yaml`、`resource_policy.yaml`、`route_registry.yaml`、
`schedule_policy.yaml`

### 3.2 Schema 契約（2）

`schemas/ai395_review/mineru-artifact.schema.json`、
`schemas/ai395_review/source-artifact.schema.json`

### 3.3 部署（9）

`deploy/ai395/compose.production.yaml`、
`deploy/ai395-review-staging/`：`README.md`、`compose.yaml`、`env.example`、
`n8n/ai395-review-staging-e2e.json`、
`sql/001_review_staging.sql`、`sql/001_review_staging.sqlite.sql`、
`docker/ai395-review-staging/Dockerfile`、
`requirements/ai395-review-staging.txt`

### 3.4 Fixture（10）

`fixtures/ai395_review/mini20/`：`candidates.jsonl`、`issues.csv`、
`source_catalog.json`、`source_manifest.json`、`mineru_manifest.json`、
`mineru_output/doc-001.md`、`assets/figure-006.svg`、`figure-007.svg`、`figure-008.svg`

### 3.5 腳本（14）

| 檔案 | 性質 |
|---|---|
| `ai395_feedback.py` | **相容層**，8 行 `from review_feedback import *`；見第 5 節 |
| `ai395_context_guard.py` | 上下文控制 |
| `ai395_evidence_tools.py` | 證據工具 |
| `ai395_llm_adapter.py` | 模型 adapter |
| `ai395_source_adapter.py` | 來源 adapter |
| `ai395_review_staging.py` | staging 入口 |
| `ai395_review_staging_http.py` | staging HTTP |
| `ai395_catalog_production.sh` | production |
| `ai395_catalog_runtime.sh` | runtime |
| `analyze_ai395_three_source_pilot.py` | 分析 |
| `build_ai395_real_audit_scope.py` | 範圍 |
| `build_ai395_three_source_spec.py` | 規格 |
| `export_ai395_staging_review_bundle.py` | 匯出 |
| `inspect_ai395_llm_payloads.py` | 檢視 |

### 3.6 測試（5）

`tests/test_ai395_production_deployment.py`、
`tests/test_ai395_review_staging.py`、
`tests/test_ai395_runtime_config.py`、
`tests/test_analyze_ai395_three_source_pilot.py`、
`tests/test_build_ai395_three_source_spec.py`

### 3.7 文件（5）

`docs/ai395-open-model-review-implementation-plan.md`、
`docs/ai395-production-cutover-2026-08-09.md`、
`docs/ai395-review-context-control.md`、
`docs/ai395-review-workflow-operator-learning-guide.md`、
`docs/ai395-runtime-maintenance.md`

### 3.8 未追蹤的附屬檔（8，含 4 個 README）

`configs/ai395_review_pipeline/.project-map-ignore`、
`deploy/ai395-review-staging/.project-map-ignore`、
`deploy/ai395/.project-map-ignore`、`deploy/ai395/README.md`、
`docker/ai395-review-staging/.project-map-ignore`、
`docker/ai395-review-staging/README.md`、
`fixtures/ai395_review/.project-map-ignore`、
`schemas/ai395_review/.project-map-ignore`

> 注意：`.project-map-ignore` 的存在表示掃描工具已被指示**跳過**這些目錄。
> 這也是它們長期未被發現的原因之一。

---

## 4. 可能值得借鏡的設計資產（未來另案評估）

以下屬**契約設計**，與 ai395 的部署／執行無關，未來設計 AI worker 時可參考：

| 資產 | 為何可能有用 |
|---|---|
| `budget_policy.yaml` | soft/hard 用量比、硬停新模型任務——**AI worker 的成本閘門** |
| `invalidation_matrix.yaml` | 哪個上游欄位變動使哪些下游失效（parser/text/vision/answer） |
| `route_registry.yaml` | 哪個任務走哪個模型（primary/backup） |
| `provider_registry.yaml` | provider 抽象層 |
| `source-artifact.schema.json` | `immutable`、`read_only`、`sha256` 的來源契約 |
| `mineru-artifact.schema.json` | 同上，MinerU 產物版 |
| `schedule_policy.yaml` | 掃描／批次的排程與啟用開關 |

**嚴禁**直接復活其 deployment（`deploy/ai395*`、`docker/ai395*`、
`requirements/ai395-review-staging.txt`、`ai395_catalog_production.sh`）。
現行 Charter 要求任何 AI 任務都必須重新定義 producer、權限、artifact store、
版本與 checksum 契約；舊部署不具備這些授權。

---

## 5. 與 `review_feedback` 的界線（重要）

**不要把兩條線混為一談。**

| | `review_feedback.py` | `ai395_feedback.py` |
|---|---|---|
| 行數 | 444（真實作） | 8（純相容層） |
| 現行依賴者 | `review_state.py`（**運作中的審題伺服器**） | 無 |
| 狀態 | **保留**，待另案設計 | 隨 ai395 **退場** |
| 理由 | 服務現在運作的審核流程 | 只為封存的舊 fixture 而存在 |

`review_feedback.py` 的完整說明與待決設計問題：
[`docs/preserved/review-feedback-agent.md`](review-feedback-agent.md)。

---

## 6. 後續（尚未執行，需 owner 決定）

本文件只做紀錄，**沒有刪除或搬移任何檔案**。若 owner 要進一步收斂，選項有：

1. **原地標記**：在 `docs/README.md` 索引中註明本系列為考古（低風險）
2. **搬移至考古區**：集中到單一 `archive/ai395/` 目錄（動 51 個檔案）
3. **移除**：連同 5 個測試一起刪（會少 8 個測試，其中 2 個已壞）
4. **維持現狀**：只留本紀錄

建議先做 **1**；其餘等有需求再說。
