# AI395 國考題審核工作流：操作與逐關卡學習手冊

更新日期：2026-08-22（Asia/Taipei）  
文件狀態：Luna 實作前的操作介面與教學驗收規格；標示為「目標介面」的命令尚待 WP-00S 實作，完成後必須改成實際命令並逐項驗證。  
工程規劃：`docs/ai395-open-model-review-implementation-plan.md`  
完整流程：`docs/dify-n8n-review-pipeline-spec.md`

## 0. 這份手冊要解決什麼

目標不是要求 owner 一開始就看懂所有 Python、SQL 與 n8n node，而是分兩步：

1. 先在 AI395 的隔離 Docker staging 中，確認 20 題 fixture 能從來源一路跑到 SQL、AI advisory、人工 exception queue 與 formal dry-run。
2. 再逐關卡學習每個 node 的輸入、輸出、判斷、模型路由、SQL 狀態、錯誤與重跑方式。

全程遵守：

- production 仍只有 AI395 Review UI／受控 importer 能寫入；staging 不掛 production DB volume。
- AI、mock model 與 Luna 都不能寫人工 `accept`、`block`、`reset_review` 或題組／答案接受事件。
- OpenCode Go 預設停用，不需先訂閱。
- Ollama Cloud 是正式模型主池；本地 Qwen 3.8-27B 完成設備認證後才加入 shadow。
- 每個修正建立新 revision；不覆寫 PDF、MinerU raw、舊 revision 或人工歷史。

## 1. 先記住這張地圖

```text
官方 catalog / PDF
  → Q / ANS / MOD 配對
  → MinerU
  → parser + 題數 / 題號 gate
  → isolated PostgreSQL candidate revision
  → 五條並行 lane
       文字證據 / notation / 題組 / 圖片 / 答案
  → revision aggregator
       無修改：通過或抽樣
       有安全修正：建立新 revision，只重跑 affected lane
       有疑慮：送 Review UI exception queue
  → formal dry-run
  → 另一次 owner 核准後才可能進 production apply
```

n8n 只負責啟動、監控與通知；Python worker 做實際資料處理；PostgreSQL 保存狀態；模型只輸出 advisory；人工決定在 Review UI。

## 2. 三個環境不要混用

| 環境 | 用途 | 可寫範圍 |
|---|---|---|
| development | Luna 寫程式、unit tests、fixtures | repository branch 與 disposable data |
| AI395 Docker staging | E2E、SQL migration、模型 shadow、故障演練 | 獨立 DB／volume／artifact；不得寫 production |
| AI395 production | 正式 Review UI、PostgreSQL、formal tables | 每次 G3 需 exact approval；人工決策為 G4 owner-only |

### 2.1 Docker staging 應包含

```text
review-staging-db
n8n-review-staging
pipeline-worker
model-adapter-worker
mock-model
mineru-worker（optional GPU profile，真實 PDF 階段啟用）
qwen-local（optional profile，預設關閉）
```

基本原則：

- Compose project、port、credential、DB、asset root 與 volume 都和 production 分開。
- source snapshot read-only；staging output read-write。
- base profile 不需要任何 cloud key，使用 mock model 也能跑通。
- Ollama Cloud key／登入資料與未來 Go key 只從 AI395 secret／mode-600 EnvironmentFile 注入。
- 本地 Qwen profile 未啟用時不下載、不載入、不占 GPU。
- 不用 `docker compose down -v` 當日常命令；測試 volume 的清除必須由精確命名、帶確認與保留 report 的 reset 工具處理。

## 3. 第一次操作：只看整條流程能不能跑通

### 3.1 Luna 必須交付的目標介面

以下是 WP-00S 的目標 UX，尚未實作前不可視為現有命令。Luna 可調整檔名，但完成後必須用真實命令替換本節：

```bash
python3 scripts/manage_ai395_review_pipeline.py doctor --environment staging
python3 scripts/manage_ai395_review_pipeline.py e2e --fixture mini20 --provider mock
python3 scripts/manage_ai395_review_pipeline.py status --latest
python3 scripts/manage_ai395_review_pipeline.py report --latest
```

`doctor` 應檢查 Docker、Compose、port collision、staging volume、DB、n8n、worker image、設定 schema、secret 名稱是否存在，以及 production volume 未被掛入。它不能修改 production。

`e2e` 應自動：

1. 啟動隔離 staging。
2. apply staging migration。
3. 載入 20 題 fixture。
4. 建立 pipeline run 與 jobs。
5. 執行 parser／deterministic gate。
6. fan-out 五條 lane；模型部分用可預測的 mock response。
7. 建立至少一個新 revision、至少一個人工 exception，並保留一批 machine-pass sample。
8. 產生 formal dry-run，不寫 production。
9. 輸出 report 路徑、run id、config SHA 與 SQL count proof。

### 3.2 第一次只看五個結果

| 檢查 | 合格結果 |
|---|---|
| container health | staging DB、n8n、workers、mock model 全為 healthy |
| coverage | 20 題全部有且只有一個可解釋的最終 route |
| revision | 至少一題由 v1 建立 v2，old/new 都存在 |
| exception | 至少一題進人工 queue，但沒有 AI 偽造人工決策 |
| formal dry-run | 產出可匯入／不可匯入與原因；production writes = 0 |

第二次執行同一 fixture 應 idempotent：不能新增重複題目、重複 model result 或重複人工事件。

## 4. 模型不是寫在 n8n 裡

owner 平常調整模型的地方是版本化設定，不是逐一打開 n8n node 改字串：

```text
configs/ai395_review_pipeline/provider_registry.yaml
configs/ai395_review_pipeline/route_registry.yaml
configs/ai395_review_pipeline/resource_policy.yaml
configs/ai395_review_pipeline/budget_policy.yaml
configs/ai395_review_pipeline/schedule_policy.yaml
docs/skills/national-exam-ai-audit/profiles/*.yaml
```

### 4.1 初始狀態

```yaml
providers:
  ollama_cloud:
    enabled: true
  local_qwen:
    enabled: false
  opencode_go:
    enabled: false
```

| 路由 | 初始 primary | challenger／備援 |
|---|---|---|
| `text_residual` | Ollama Cloud DeepSeek V4 Flash | Ollama Qwen 3.5；本地 Qwen 認證後可 shadow |
| `notation_residual` | Ollama Cloud DeepSeek V4 Flash | Ollama Qwen 3.5；人工 PDF |
| `group_boundary` | Ollama Cloud DeepSeek V4 Flash | Ollama Qwen 3.5；人工 |
| `vision_need`／`vision_crop` | Ollama Cloud Gemma 4 31B | Ollama Kimi K2.7 Code（高難度／低信心／衝突）；Qwen 3.5／Mistral 作抽樣；本地 Qwen 認證後可 shadow |
| `answer_special_text` | Ollama Cloud DeepSeek V4 Flash | 人工；選配 challenger |
| `answer_special_vision` | Ollama Cloud Gemma 4 31B | Ollama Kimi K2.7 Code challenger；人工 |

### 4.2 安全換模型流程

1. 新增或修改 model profile，不先改 primary。
2. 執行 profile schema validation。
3. 跑 transport probe；vision 必須證明 pixels 真的送達。
4. 跑該 lane 的 gold／shadow packets。
5. certification 通過後，先放 `challengers` 或 `optional_local`。
6. 查看差異與人工抽樣，再由 owner 決定是否改 primary。
7. route 變更必須產生新 config SHA；舊 run 不受影響。

resolver 必須拒絕 disabled、uncertified、license 不合格、capability 不符或資料政策過期的 profile。

`kimi-k2.7-code:cloud` 雖屬 Ollama Cloud 訂閱池，但官方 usage 等級為 `high`。它不是「每張圖再跑一次」的固定投票模型；預設只進高難度 crop、Gemma 低信心／衝突、答案影像與抽樣 queue。切換前要看 profile 的 Modified MIT acknowledgement、pixel transport、strict JSON、中文文件 gold、延遲與本期 usage 報告。

## 5. OpenCode Go 何時才需要

正常情況不需要訂閱。Ollama Cloud 達軟門檻或中斷時，系統先暫停新 cloud LLM jobs；parser、deterministic workers、SQL 與 Review UI 繼續運作。owner 再選：

1. 等 Ollama 額度重置。
2. 加購 Ollama usage。
3. 啟用已認證本地 Qwen 的合格 lane。
4. 確實需要跨 provider 備援時，才訂閱 OpenCode Go。

Go 啟用時的順序是：建立 secret → live transport probe → model/license mapping → lane certification → `provider_registry` 手動 enable → 指定 route 加入 `outage_fallbacks`。不能因缺少 Go key 讓正常主流程失敗，也不能自動用 Zen balance。

## 6. 本地 Qwen 3.8-27B 加入方式

模型：`Qwen/Qwen3.8-27B`；上游為 Apache-2.0、text＋image。可由 Ollama local、vLLM 或 SGLang 提供服務，但工作流只依賴統一 profile／adapter contract。

### 6.1 設備測試

| 類別 | 要量測 |
|---|---|
| 載入 | runtime、權重／quantization hash、冷啟動、RAM／VRAM |
| 文字 | JSON adherence、tokens/s、p50／p95、最大安全 context |
| 圖片 | pixel transport、DPI、單圖／雙圖、錯裁／無圖 negative controls |
| 穩定 | 100 requests、timeout、container restart、OOM recovery |
| 共存 | 與 MinerU 同時／互斥時的 OCR 完整率、吞吐與 GPU 記憶體 |
| 品質 | 200 題相同 packets 對照 Ollama Cloud 與 human gold |

初始只進 `optional_local` shadow。text 與 vision 分開認證；text 通過不代表 vision 自動通過。未證明共存安全前，MinerU 與 Qwen 的 GPU lease 互斥。

## 7. 每個工作節點要看什麼

Luna 應讓每個 worker 支援 `--describe` 或中央 `explain-node`，只輸出 node 說明而不執行資料。目標介面：

```bash
python3 scripts/manage_ai395_review_pipeline.py explain-node catalog_scan
python3 scripts/manage_ai395_review_pipeline.py run-node text_evidence --run-id RUN_ID --max-items 20 --dry-run
```

| node id | 主要輸入 | 主要輸出 | owner 先學的重點 |
|---|---|---|---|
| `catalog_scan` | 已下載清單、考選部 catalog | new／unchanged／missing manifest | 如何判斷真的有新考試，不把 scan 當下載完成 |
| `pdf_archive` | 官方 URL | PDF、SHA-256、Q／ANS／MOD metadata | MOD 優先與原始檔不可覆寫 |
| `mineru_extract` | immutable PDF | markdown、layout、images、run manifest | OCR 產物不是 source truth；失敗如何 resume |
| `candidate_parse` | MinerU output | candidate、parse issues、題數 proof | 題號、選項、題組、圖片與題數 invariant |
| `staging_ingest` | validated candidate | SQL candidate revision | merge、idempotency、reviewed diff gate |
| `text_evidence` | candidate＋三來源＋pixels | pass／finding／proposal | 三 extractor 不是三份真相；何時不能改簡繁 |
| `notation` | plain／markup／pixels | notation finding／proposal | deterministic-first、上下標 exact span |
| `group` | neighbors＋markers | group range proposal | 模型只能提範圍，不能 `confirm_group` |
| `vision_need` | cues＋assets＋page pixels | no visual／asset check route | 先消除無圖誤判 |
| `vision_crop` | page pixels＋existing asset | rough bbox、snapped crop、overlay | 看 before／after；模型 bbox 不直接套用 |
| `answer_special` | ANS／MOD evidence | answer kind／accepted values proposal | 只讀答案文件，不重新解題 |
| `revision_aggregate` | 五 lane results | carry／new revision／exception | 哪些欄位改變會讓哪些 lane stale |
| `review_exception` | findings＋evidence | 人工 append-only event | AI advisory 與人工 action 分離 |
| `formal_preflight` | human-ready revision | dry-run report／sync proposal | dry-run 不等於 production apply |

## 8. 建議的逐關卡學習順序

### Level 0：20 題 mock E2E

目的：看懂 run、job、revision、lane、exception、formal dry-run 的關係。暫時不碰真實 cloud 模型。

通過條件：能指出一題為何 pass、一題為何建立新 revision、一題為何送人工。

### Level 1：200 題 deterministic snapshot

目的：學 catalog／PDF／MinerU／parser／SQL，不使用 LLM。量出完全一致、parse issue、像素必看與 special answer 比例。

通過條件：題數與 coverage 100%，能從 SQL／manifest 回到 PDF hash 與 script SHA。

### Level 2：文字三來源與 DeepSeek residual

目的：先做風險最低的完整 lane。比較「完全一致不呼叫模型」與 residual packet。

通過條件：source-original 不被改寫；所有 proposal 有 exact span、evidence 與 route。

### Level 3：notation 與題組

目的：學 active rule、pixel-required 與 group boundary。先人工確認所有 group proposal。

通過條件：模型不能 materialize 未核准規則，也不能產生人工 `confirm_group`。

### Level 4：圖片三分類與裁切

目的：分辨 `no_visual_required`、`visual_asset_ok`、`visual_asset_problem`，看 overlay 與 before／after；並比較 Gemma primary 與 Kimi K2.7 Code 高難度 challenger，確認增加的用量確實降低誤判或改善 bbox。

通過條件：critical false negative 為 0；任何新 crop 都能回到頁碼、bbox、DPI 與 hash；Kimi 只有在高難度子集確有品質增益且 usage／延遲過 gate 時才保留為 active challenger。

### Level 5：答案與 MOD

目的：區分普通答案 deterministic pass 與 `#`、多答案、送分、空白、衝突。

通過條件：accepted values 能指回官方答案 span／pixels；模型沒有解題。

### Level 6：本地 Qwen challenger

目的：設備認證後，以同一批 packets 和 Ollama Cloud 盲測，不改 primary。

通過條件：text／vision 分 lane 報告；resource、品質與失敗恢復都達 gate。

### 6.1 目前這台 MacBook 的 Qwen 3.8-27B-MLX 測試節點

這不是剛才本機 SQLite E2E 的必要元件，也不是 AI395 production。它是一個預設關閉的 shadow provider：MacBook 原生 Ollama 載入 MLX model，AI395 透過 Tailscale Serve 呼叫；模型掛掉時，AI395 主流程仍應繼續使用 mock／Ollama Cloud 或進人工例外。

先在 MacBook 確認模型實際名稱，不要直接假設 tag：

```bash
ollama list
python3 scripts/setup_qwen_mlx_tailscale_serve.sh --plan
```

`--plan` 只顯示檢查與 Serve 命令。確認 Tailscale 已登入、ACL 只允許 AI395 存取後，才由 owner 執行：

```bash
QWEN_MLX_MODEL='<ollama list 的精確 tag>' \
  python3 scripts/setup_qwen_mlx_tailscale_serve.sh --apply

QWEN_MLX_MODEL='<ollama list 的精確 tag>' \
  python3 scripts/probe_qwen_mlx_tailscale.py \
  --base-url https://<machine>.<tailnet>.ts.net/v1 \
  --model '<ollama list 的精確 tag>'
```

第一個 probe 只做 `/v1/models`；不要用 `--live` 當健康檢查。要測生成時再單獨執行 `--live`，要測 vision 才加 `--image <fixture image>`。腳本只接受 localhost 或 HTTPS `*.ts.net`，不會呼叫 public Funnel，也不會寫資料庫。Serve 代理的是整個 Ollama API，同一 tailnet 的其他裝置也能依 ACL 使用，不限 AI395。

AI395 staging 的 `QWEN_MLX_BASE_URL`、`QWEN_MLX_MODEL` 可以先填入，但 `QWEN_MLX_ENABLED` 維持 `0`；provider registry 也維持 `local_qwen_mlx.enabled=false`。完成 text／JSON、vision pixel transport、100 requests、OOM／重啟、MinerU 共存與 gold comparison 後，才把它加入指定 lane 的 shadow。這次本機跑通的 SQLite 結果只證明 runner contract，不代表 AI395 Docker/PostgreSQL staging 已驗收。

### Level 7：1,000 題 rehearsal 與 Review UI

目的：測 provider outage、重試、stale lane、人工 queue、成本與手機操作。

通過條件：exception queue 可控、coverage 100%、無 source-fidelity violation、production writes 仍為 0。

## 9. SQL 要學會看的六個問題

以下表名屬規劃 schema，Luna 完成 WP-00S／WP-03 後必須放入實際可執行的 read-only query 檔案，並在此連結；在 migration 核准前不能把範例當成 production 現存表。

1. 這次 run 現在在哪一關？看 `pipeline_runs`、`pipeline_jobs`。
2. 哪些 job 在 retry／failed／leased？看 attempt、lease expiry、error class。
3. 這題目前是哪個 revision，誰造成的？看 `question_revisions` 與 parent／cause。
4. 五個 lane 哪個 fresh、stale、failed？看 `review_lane_runs`。
5. 模型看了什麼設定、花多少、回什麼？看 `model_runs` 與 raw artifact hash。
6. 為什麼送人工或不能 formal？看 `review_findings`、`correction_proposals` 與 formal dry-run reasons。

Luna 應交付：

```text
docs/sql/ai395_review_operator_queries.sql
scripts/report_ai395_review_run.py
```

read-only report 不需要 production owner password；若查 production，只能走既有 AI395 read-only／SSH tunnel 規範。

## 10. 故障時先做什麼

| 現象 | 第一動作 | 不要做 |
|---|---|---|
| Ollama Cloud quota／429 | 暫停新 cloud jobs，保留 lease／attempt，繼續 deterministic | 自動訂 Go、無限 retry、略過必要 vision |
| OpenCode 未訂閱 | 保持 disabled，主線照常 | 讓缺 key 成為 E2E blocker |
| 本地 Qwen OOM | suspend local profile、保存 probe、釋放 GPU lease | 與 MinerU 反覆同時啟動 |
| invalid model JSON | 保存 raw response、validation fail、有限重試 | validator 靜默補 JSON |
| parser 題數不符 | 停在 parser exception | 讓 LLM 猜缺哪題 |
| crop 不正確 | 保留 before／proposal／overlay，進人工 | 直接替換正式 asset |
| script 發現 bug | 建 defect、算 blast radius、標 stale、用新 SHA 重跑 | 覆寫舊 artifact／revision |
| n8n 重啟 | 由 PostgreSQL lease 恢復 | 依 n8n execution memory 猜進度 |

## 11. 日常操作的目標介面

Luna 完成後，本節應提供真實命令與輸出範例，至少涵蓋：

```text
doctor                    檢查 staging／provider／resource
e2e                       跑 mini20 walking skeleton
start-run                 建立指定 scope run
pause-run / resume-run    安全停／續 queue
status / report           看 SQL 與 artifact summary
explain-node              看 node contract
run-node                  單跑一關、max-items、dry-run
validate-config           驗 provider／route／profile
probe-model               transport／text／vision／resource probe
compare-models            同 packet blind comparison
defect-impact             腳本錯誤 blast radius
formal-preflight          只產 dry-run evidence
stop-staging              停 container、不刪 volume
reset-staging-fixture     精確 fixture reset；先確認並保留 report
```

所有會寫 DB 的命令都要顯示 target environment；預設為 staging／dry-run。production apply 不能靠 `--production` 一個 flag 就執行，必須走 exact G3 approval 與受控入口。

## 12. 每次逐關審核的紀錄表

| 欄位 | 要填什麼 |
|---|---|
| run id／scope | 這次看哪 20／200／1,000 題 |
| component／script SHA | 哪個 node 與程式版本 |
| config／profile／prompt | 實際解析後版本，不只寫模型家族 |
| input／output hash | 能否精確重建 |
| pass／finding／exception | 數量與 disjoint coverage |
| false positive／negative | 人工抽樣結果 |
| decision | 保持 shadow、修 prompt、修 Python、升 rule、suspend model |
| affected scope | 若修正，需要重跑哪些 revision／lane |
| next gate | 下一次要證明什麼 |

這份表應由 report 自動填大部分欄位，owner 只補人工判斷與下一步。

## 13. 完成定義

「整套流程已建立」至少代表：

- Docker staging 可從空環境重建，20 題 E2E 與第二次 idempotent run 都通過。
- PostgreSQL migration 在隔離 DB 通過 apply、rollback、idempotency 與 count proof。
- 五條 lane 都有真實 contract、fixture、單跑、重跑、故障注入與 report。
- Ollama Cloud primary 路由完成 certification；OpenCode Go 保持可選，無訂閱也能運作。
- 本地 Qwen profile 已存在但可保持 disabled；設備測試不阻擋 cloud 主線。
- Review UI 顯示 advisory、revision diff、PDF evidence 與 exception，人工 action 保持 append-only。
- owner 能依本手冊停用一個 lane、換 challenger、查看 SQL、重跑 affected scope。
- 1,000 題 rehearsal 的 coverage、成本、人工 queue、錯誤與 throughput 有報告。
- production migration／deploy／advisory import／formal apply 尚未自動執行，會停在待 owner 核准的證據包。

## 14. 手冊維護規則

每個 Luna work package 都必須更新本手冊：

- 將目標介面替換為真實命令。
- 每個 node 補一個最小 input／output 範例。
- 補成功截圖或文字 evidence、常見錯誤、重跑與停止方法。
- 若檔案、設定 key、SQL table 或 n8n node 改名，同一 PR 更新手冊。
- CI 至少驗證文件提到的本地路徑存在；命令範例以 smoke／help test 防止腐化。

手冊只教安全操作。production G3/G4 行動仍需查看當次 release、migration、backup、rollback 與 approval evidence，不能把這份一般說明當成長期授權。
