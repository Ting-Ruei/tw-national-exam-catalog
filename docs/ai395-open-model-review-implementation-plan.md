# AI395 開源模型題目審核工作流實作規劃書

更新日期：2026-08-22（Asia/Taipei）  
文件狀態：可直接交由 GPT-5.6 Luna 實作的工程 handoff；不包含 production apply 授權。  
上位規格：`docs/dify-n8n-review-pipeline-spec.md`  
操作與逐關卡學習：`docs/ai395-review-workflow-operator-learning-guide.md`

## 0. 已確定的決策

1. 整套架構部署在 AI395：官方 PDF 歸檔、MinerU、parser、evidence、工作 queue、validator、Review UI、embedding 與 reranker 都由 AI395 執行。
2. 第一版審題 LLM 以 Ollama Cloud 為唯一必備推論池；其中 `kimi-k2.7-code:cloud` 納入可調式 vision challenger。OpenCode Go 預設停用、無需先訂閱，只有 Ollama Cloud 實測不足或中斷時才由 owner 選擇啟用。
3. AI395 預留本地 `Qwen/Qwen3.8-27B` 測試 profile；目前這台 MacBook 另提供 `qwen3.8-27b-mlx` 的 Ollama／MLX 測試算力，透過 Tailscale Serve 以 tailnet-only OpenAI-compatible endpoint 給 AI395。兩者都預設停用，不影響第一版跑通；完成 text、vision、JSON、效能、重啟與 MinerU 共存測試後，才作 shadow challenger 或受控 fallback。
4. production 審題 runtime 只允許開源／開放權重模型；GPT-5.6 Luna 只作為實作本計畫的 coding agent，不列入審題模型 allowlist。
5. 第一版使用 `n8n + PostgreSQL job state + Python workers`。Dify 可於後續作 prompt 實驗或 RAG 介面，但不保存權威狀態，也不直接寫 production review events。
6. deterministic-first：能由 PDF、MinerU、parser、答案規則或像素證據確定的工作，不呼叫 LLM。
7. LLM 結果一律 advisory。模型不得寫入人工 `accept`、`block`、`exclude`、`reset_review`、`confirm_group` 或答案接受事件。
8. n8n 與 Python node 不寫死模型名稱。所有模型節點以 `route_key` 解析版本化設定，owner 可調整 primary、challenger、fallback、batch size、timeout、budget 與啟用狀態。
9. Luna 的交付順序是「隔離環境整條跑通」優先，再逐 lane 提升真實能力；每個 node 都要有可單獨重跑、可觀察、可教學的入口。
10. 每次執行都保存 script、Git SHA、設定、輸入、輸出、prompt、模型與成本 lineage；日後發現某個 Python 腳本有錯，可以精確找出受影響資料並重跑，不覆寫歷史。

## 1. 「開源模型」的 production 定義

預設 allowlist 只接受：

- 模型權重可取得。
- MIT、Apache-2.0，或 owner 明確接受的其他商用條款。
- provider model id 能對應到明確的 upstream model／license。
- 通過 AI395 的 text、JSON、pixel transport、來源忠實度與 lane gold test。
- provider 改版後仍能固定或至少記錄實際 model id、response model、provider 與日期。

因此「某個模型家族有開源版本」不等於某個 API SKU 自動合格。例如 OpenCode Go 的 `qwen3.5-plus` 必須先證明它對應的可用權重與授權，才能進正式 allowlist；在此之前使用模型名稱明確的 Ollama Cloud tag。

### 1.1 授權分級

| 等級 | 條件 | 處理方式 |
|---|---|---|
| L0 | Apache-2.0／MIT，model id 可映射 upstream weights | 可進技術認證 |
| L1 | 開放權重但為自訂商用條款 | 先完成 owner／法律條款確認 |
| L2 | API SKU 無法證明對應開放權重 | 僅實驗，不得 production |
| L3 | proprietary model | 不得進審題 runtime |

目前可直接進 L0 技術認證的核心候選：

- DeepSeek V4 Flash：MIT，text-only。
- Qwen 3.5 開放權重版本：Apache-2.0，text＋image。
- Qwen 3.8 27B：Apache-2.0，text＋image；以本地 `Qwen/Qwen3.8-27B` profile 納入，等 AI395 設備測試後啟用，不等待 cloud provider 上架。
- Gemma 4 31B：Apache-2.0，text＋image。
- Mistral Large 3：Apache-2.0，text＋image。
- GLM 5.2：MIT，text-only。
- gpt-oss 20B／120B：Apache-2.0，text-only；目前只列 shadow，不作中文主審。

Kimi K2.7 Code 採 Modified MIT：可商用，但若產品／服務超過 1 億月活躍使用者或每月營收超過 US$20M，必須在 UI 顯著顯示模型名稱，因此先列 L1 並保存 owner 條款確認。MiniMax M3 與 Kimi K3 也使用自訂 license，先列 L1。它們可進候選或實驗，但不得略過 license metadata 與條款 gate。

## 2. 模型 CP 值結論

### 2.1 第一版候選與初始路由

| 優先級 | 模型與 provider | 角色 | 原因 | 初始狀態 |
|---|---|---|---|---|
| P0 | Ollama Cloud `deepseek-v4-flash:cloud` | 文字 residual、題組、答案文件文字解讀 | MIT、text-only；符合 Ollama Cloud 優先的訂閱策略 | primary text pilot |
| P0 | Ollama Cloud `gemma4:31b-cloud` | `need_visual`、圖片品質、PDF crop 視覺判斷 | Apache-2.0、低 usage、原生 vision；文件 OCR benchmark 對此工作直接相關 | primary vision pilot |
| P1 | Ollama Cloud `qwen3.5:397b-cloud` | 中文文字／視覺獨立複核、Gemma 衝突裁判 | Apache-2.0、中文與 multimodal；但 usage 高於 Gemma | challenger |
| P1 | Ollama Cloud `kimi-k2.7-code:cloud` | 高難度圖片／裁切／答案影像複核、Gemma 衝突裁判 | 原生 image／video、256K context、Modified MIT；訂閱成本可控，但 Ollama 標示 `high` usage，且 coding-focused／強制 thinking，須以本專案 pilot 驗證延遲與中文文件 fidelity | vision challenger；不作全量 primary |
| P1 | Ollama Cloud `mistral-large-3:675b-cloud` | 視覺 negative control、跨模型抽樣 | Apache-2.0、支援中文、JSON 與 vision | challenger |
| P1 | AI395 local `Qwen/Qwen3.8-27B` | 中文文字／vision shadow、雲端獨立對照、可選 fallback | Apache-2.0、text＋image；不消耗 cloud 額度，但必須先驗證設備、quantization 與 pixel transport | disabled until device certification |
| P2 | Ollama Cloud `gpt-oss:120b-cloud` | 文字 shadow／結構化輸出對照 | Apache-2.0、structured output；中文來源忠實度尚未在本專案證明 | research only |
| P3 | OpenCode Go `deepseek-v4-flash` | Ollama Cloud outage／額度不足時的選配文字備援 | Go 月費含 usage、MIT、API model id 清楚 | disabled; subscribe only if needed |
| P3 | OpenCode Go `glm-5.2` | 已訂 Go 後的極少量高風險文字／MOD escalation | MIT、中英能力強，但消耗 Go 額度顯著快於 Flash | disabled; escalation only |
| P3 | OpenCode Go `qwen3.8-max` | 已訂 Go 後的條件式高風險複核 | 先核對 API SKU 與 upstream license mapping | disabled; conditional |

### 2.2 暫不進主線

| 模型 | 原因 |
|---|---|
| Kimi K3 | vision 能力強，但目前每百萬 token 約 US$3 input／US$15 output，例外案件也未必需要到這個等級 |
| MiniMax M3 | OpenCode 價格漂亮且有 vision，但 license 為 MiniMax Community；先完成商用條款與 OpenCode pixel transport 驗證 |
| DeepSeek V4 Pro | 比 Flash 貴；只有 gold test 顯示 Flash 明顯不足時才增加 |
| OpenCode Go `qwen3.5-plus`／其他 Plus SKU | production 前要證明 API SKU 對應可辨識的開放權重版本；不能只以家族名稱推定 |
| OpenCode 免費模型 | 型號／供應期不穩定，部分 endpoint 的資料政策有例外，不可作兩週批次的核心依賴 |
| GPT-5.6 Luna | proprietary；只作本計畫 coding agent，不處理正式審題資料 |

### 2.3 不採常態三模型投票

「三個 PDF extractor」是來源 evidence，不是三個 LLM。建議每個 residual 最多使用：

```text
primary model
  → confidence／contract／evidence 不合格：Ollama Cloud challenger
  → vision 高難度／Gemma 衝突：可路由 Kimi K2.7 Code，不對全量題常態加跑
  → 本地 Qwen 已認證且該 route 啟用：可作獨立 shadow／fallback
  → 仍衝突：人工
  → 只有 owner 已訂閱並啟用 OpenCode Go：才可再走選配 provider
```

所有正常題都跑三模型會把成本、延遲與衝突量放大，卻不會把同一 PDF text layer 變成三份獨立真相。

## 3. 成本估算與排程

供應商預設組合為：

```text
Ollama Cloud Pro（必備主池）
AI395 local Qwen 3.8-27B（設備通過後的選配 challenger）
OpenCode Go（預設停用；需要時才訂閱與啟用）
```

第一版不能假設 AI395 存在 `OPENCODE_GO_API_KEY`，也不能因 quota 事件自動訂閱或啟用 OpenCode。OpenCode Go 目前首月 US$5、之後 US$10／月，包含以美元價值計算的 5 小時 US$12、每週 US$30、每月 US$60 usage limit；只有 owner 決定需要備援時才建立訂閱。超額時可以選擇接 Zen balance，但預設關閉，避免批次無上限扣款。以下價格只作 2026-08-22 planning snapshot；worker 必須從實際 usage 回寫成本，不可把表中價格寫死為永久真相。

| 模型 | input／1M | output／1M | 備註 |
|---|---:|---:|---|
| DeepSeek V4 Flash 離峰 | US$0.22 | US$0.66 | 台北時間 09:00–12:00、14:00–18:00 為 peak；其他時間為 off-peak |
| DeepSeek V4 Flash peak | US$0.44 | US$1.32 | 避免全量排在 peak |
| GLM 5.2 | US$1.40 | US$4.40 | 只處理 escalation |
| MiniMax M3 | US$0.30 | US$1.20 | 尚未通過 license gate |
| Kimi K3 | US$3.00 | US$15.00 | 不列 routine |

OpenCode Go 官方以典型 coding request 估算的額度如下；國考 packet 的 token 與 cache pattern 不同，因此只用於容量預估，不能當 SLA：

| 模型 | 5 小時估計請求 | 每週估計請求 | 每月估計請求 | 本計畫用途 |
|---|---:|---:|---:|---|
| DeepSeek V4 Flash | 7,600 | 18,900 | 37,800 | Ollama overflow／failover |
| DeepSeek V4 Flash Vision Exp | 3,800 | 9,450 | 18,900 | upstream／pixel probe 未通過前不啟用 |
| GLM 5.2 | 880 | 2,150 | 4,300 | 少量 escalation |
| Qwen3.8 Max | 160 | 400 | 810 | 極少量高風險複核 |

假設每個文字 residual 平均 1,200 input tokens、150 output tokens：

| 規模 | DeepSeek Flash 離峰 | DeepSeek Flash peak | GLM 5.2 |
|---|---:|---:|---:|
| 100,000 次模型呼叫 | 約 US$36.30 | 約 US$72.60 | 約 US$234.00 |
| 120,000 題中只有 10% residual | 約 US$4.36 | 約 US$8.71 | 約 US$28.08 |

這個估算不含圖片 token、失敗重試與信用卡手續費。圖片成本必須由 200 題 pilot 的 provider usage／Ollama usage dashboard 實測；不得用文字 token 估計視覺成本。

Ollama Cloud 的建議用法：

- Pro US$20／月作為第一版主池，3 個 concurrent cloud models，但 usage 是模型權重化額度，並有 5 小時 session 與 7 日 weekly reset。
- 因其不是固定 token 額度，不能在 pilot 前承諾可承擔整個兩週 corpus run。
- `kimi-k2.7-code:cloud` 雖包含在 Ollama Cloud 訂閱池，官方 usage 等級為 `high`；成本上限較容易控制，不代表可用量比 Gemma 高。只對 `crop_needed`、primary 低信心、模型衝突、MOD 影像或抽樣題啟用，另記錄每題 usage、延遲與成功率。
- worker 以 response token journal 與本地估算建立 80% 軟門檻；若 Ollama 沒有可用的 quota API，n8n 另外要求 operator 依 dashboard 確認。達門檻時先暫停新的 cloud LLM job，deterministic worker 與 Review UI 照常運作；由 owner 選擇加購 Ollama usage、啟用已認證本地 Qwen、訂閱 OpenCode Go，或等待額度重置。
- 若 Pro 實測不足，先比較 extra usage 與 Team；Team 目前最低五席、US$125／月，不在 pilot 前預購。
- 只有 `providers.opencode_go.enabled=true` 時，n8n 才建立 Go job；DeepSeek overflow 文字批次再依其峰／離峰價差排程。
- OpenCode Go `Use balance` 預設關閉；只有 owner 明確核准 corpus run 預算後才能打開。
- DeepSeek 的 Go zero-retention agreement 目前只確認到 2026-08-31；model profile 應設 `data_policy_expires_at`，到期未重新確認就自動 suspend。國考內容雖為公開資料，仍不把人工私人註記送出。

## 4. AI395 目標架構

```text
n8n coarse workflow
  ├─ catalog scan / PDF download
  ├─ enqueue MinerU / parser / evidence jobs
  ├─ enqueue lane batches
  ├─ monitor SLA / budget / provider health
  └─ notify operator

PostgreSQL control state
  ├─ pipeline run / job / lease / attempt
  ├─ immutable candidate revision
  ├─ lane result / finding / proposal
  ├─ component version / defect impact
  └─ existing append-only human and AI events

Python workers on AI395
  ├─ deterministic workers
  ├─ external-model adapter worker
  ├─ validator / coverage proof
  ├─ crop / contact-sheet worker
  └─ advisory importer

Open/open-weight model services
  ├─ Ollama Cloud: required primary text + primary vision
  ├─ AI395 local Qwen 3.8-27B: optional, disabled until certified
  ├─ MacBook Qwen 3.8-27B-MLX via Tailscale Serve: optional shadow/test only
  └─ OpenCode Go: optional provider, disabled until owner subscribes
```

模型 worker 只能：

- 讀 immutable task packet。
- 對外送出最小必要的公開國考內容與圖片 crop。
- 寫本地 artifact outbox。
- 回傳 raw response、normalized result、usage、latency、provider request id 與錯誤。

模型 worker 不持有 production PostgreSQL owner password，不得呼叫 Review UI 寫入 API，也不得收到人工 reviewer 的私人註記。

### 4.1 Docker 使用邊界

Docker／Docker Compose 適合用來提供可重建環境，但不能把 staging 與 production writer 混在同一個 Compose project。Luna 應新增隔離的 AI395 staging stack：

```text
deploy/ai395-review-staging/
  compose.yaml
  compose.mineru.yaml              # optional GPU profile
  compose.local-qwen.yaml          # optional profile
  env.example                      # 只有變數名，不含 secret
  README.md

services
  review-staging-db                # 獨立 PostgreSQL、獨立 volume、loopback port
  n8n-review-staging               # coarse orchestration
  pipeline-worker                  # parser/evidence/validator/import dry-run
  model-adapter-worker             # Ollama Cloud/local/OpenCode adapters
  mock-model                       # walking skeleton，無網路也能跑
  mineru-worker                    # optional GPU profile；真實 PDF 階段啟用
  qwen-local                       # optional GPU profile；設備測試後才啟用
```

要求：

- staging 使用獨立 project name、DB、port、credential、asset root 與 volume；不得掛載 AI395 production PostgreSQL volume。
- 官方 PDF／既有資料 snapshot 以 read-only mount 輸入，所有產物寫 staging artifact volume。
- container image、OS package、Python lock、MinerU runtime 與模型 serving runtime 都固定 version／digest；`run_manifest.json` 記錄 image digest。
- PostgreSQL migration 在 disposable／staging DB 實際 apply、rollback、idempotency 測試；production 只產生 migration proposal 與 dry-run evidence。
- `qwen-local` 使用 Compose profile，預設不啟動；GPU worker 由 resource lease 控制，未有共存 benchmark 前不得與 MinerU 同時占用 GPU。
- staging port 預設只 bind AI395 loopback；若要從 LAN 開 n8n 測試頁，另由 owner 核准固定 LAN bind 與防火牆，不重用 production Review UI port。
- secrets 只由 AI395 mode-600 EnvironmentFile／Docker secret 注入，不進 Compose YAML、Git、manifest 或 n8n export。
- production containerization 是獨立 G3 部署決策；walking skeleton 跑通不代表可以替換或重啟既有 production stack。

### 4.1.1 MacBook Qwen 3.8-27B-MLX 測試節點

這台 MacBook 的角色是「可拔除的外部測試 provider」，不是 AI395 production service，也不是本地 runner 的必要依賴：

```text
MacBook 原生 Ollama（MLX model）
  └─ 127.0.0.1:11434/v1
       └─ Tailscale Serve（HTTPS、tailnet-only）
            └─ AI395 pipeline-worker → local_qwen_mlx provider
```

設定與安全邊界：

- `local_qwen_mlx.enabled=false`、profile `certification=disabled`、route 只能放在 `optional_local`；未完成 probe 前不作 primary、fallback 或自動修正來源。
- `QWEN_MLX_BASE_URL` 只接受 MacBook 的 `https://<machine>.<tailnet>.ts.net/v1`；同機測試才允許 `http://127.0.0.1:11434/v1`。provider adapter 不接受公網 HTTP、`0.0.0.0` 或直接暴露 11434。
- MacBook 只用 `tailscale serve`，不使用 public Funnel；Tailscale ACL 應限制 AI395 節點才能存取該主機。不要把 auth key、Ollama credential 或 tailnet secret 放進 Git。
- exact model tag 不寫死在 workflow；先在 MacBook 執行 `ollama list`，以 `QWEN_MLX_MODEL` 指定實際 tag。預設值 `qwen3.8:27b-mlx` 只是待確認 placeholder。
- first probe 先 GET `/v1/models`，之後才由 owner 明確執行一次 text live probe 與一次 vision pixel probe；probe 不寫 SQL，也不改 route enable flag。
- AI395 worker 只保存 provider、實際 model、endpoint class、request／response hash、latency、錯誤與 usage；模型結果仍是 advisory，不能產生人工 accept／block 或 materialize 未核准修正。

MacBook owner 操作：

```bash
python3 scripts/setup_qwen_mlx_tailscale_serve.sh --plan
ollama list
QWEN_MLX_MODEL='<ollama list 顯示的精確 tag>' \
  python3 scripts/probe_qwen_mlx_tailscale.py \
  --base-url https://<machine>.<tailnet>.ts.net/v1 \
  --model '<ollama list 顯示的精確 tag>'
```

只有確認 local Ollama、model tag、Tailscale ACL 與 endpoint 後，owner 才能另外執行 `--apply`。AI395 端先只填 `QWEN_MLX_BASE_URL`／`QWEN_MLX_MODEL` 並保持 `QWEN_MLX_ENABLED=0`；完成 100 requests 穩定性、OOM／重啟、MinerU GPU lease 與 200 題 gold comparison 後，才由 owner 把 provider 從 disabled 改成 shadow。

### 4.2 Owner 可調整的模型路由層

n8n 只傳 `route_key`，例如 `text_residual`、`group_boundary`、`vision_need`、`vision_crop`、`answer_special_text`、`answer_special_vision`；不得在 workflow JSON 的每個 node 複製 provider/model name。Python resolver 從版本化設定解析實際 profile：

```text
configs/ai395_review_pipeline/
  pipeline.yaml                    # node 開關、batch、checkpoint
  provider_registry.yaml           # provider enable/health/credential env
  route_registry.yaml              # 每個 route 的 primary/challenger/fallback
  resource_policy.yaml             # CPU/GPU/cloud concurrency 與互斥
  budget_policy.yaml               # 軟硬門檻、停用策略
  schedule_policy.yaml             # catalog、批次與可選 provider 時段
  invalidation_matrix.yaml         # 欄位變更後要重跑哪些 lane

docs/skills/national-exam-ai-audit/profiles/
  *.yaml                           # provider/model/runtime/capability/certification
```

初始路由範例：

```yaml
schema_version: ai395_review_route_registry_v1
routes:
  text_residual:
    primary: deepseek-v4-flash-ollama-cloud
    challengers: [qwen3.5-397b-ollama-cloud]
    optional_local: [qwen3.8-27b-local]
    outage_fallbacks: []
  vision_need:
    primary: gemma4-31b-ollama-cloud
    challengers: [kimi-k2.7-code-ollama-cloud, qwen3.5-397b-ollama-cloud]
    optional_local: [qwen3.8-27b-local]
    outage_fallbacks: []
  vision_crop:
    primary: gemma4-31b-ollama-cloud
    challengers: [kimi-k2.7-code-ollama-cloud, qwen3.5-397b-ollama-cloud, mistral-large-3-ollama-cloud]
    optional_local: [qwen3.8-27b-local]
    outage_fallbacks: []
  answer_special_vision:
    primary: gemma4-31b-ollama-cloud
    challengers: [kimi-k2.7-code-ollama-cloud]
    optional_local: [qwen3.8-27b-local]
    outage_fallbacks: []
providers:
  ollama_cloud:
    enabled: true
  local_qwen:
    enabled: false
  opencode_go:
    enabled: false
    requires_manual_enable: true
```

當 owner 日後訂閱 Go，只需在通過 validation 的設定版本中啟用 provider，並把已認證 profile 加到指定 route 的 `outage_fallbacks`；不改 n8n 圖、不改 lane Python。設定優先序為「具 run id 的一次性 override → route registry → pipeline default」，每次解析後保存 effective config 與 SHA。未認證／已停用 profile 即使被誤填，也必須由 resolver fail closed。

## 5. 每個 lane 的實際路由

### 5.1 文字與繁簡／字形

```text
PDF extractor family 去重
  → exact consensus + pixel/rule 可證明：Python 修正 proposal
  → 完全一致：machine evidence pass，不呼叫 LLM
  → residual：route_key=text_residual（初始 DeepSeek V4 Flash／Ollama Cloud）
  → source fidelity 不足／低信心：route challenger、已認證本地 Qwen shadow 或 human_pdf
```

LLM 不執行 OCR、不解題、不用語感把官方原文「美化」。模型只能輸出 exact span、before、after、evidence class、source route 與 confidence。

### 5.2 上下標與科學符號

```text
normalize_science_markup / token rules
  → PDF text families
  → rendered PDF pixels
  → residual 才送 route_key=notation_residual
  → 模型只提 proposal，不直接改
```

任何新規則都必須記錄 token、左右 anchor、科目 scope、正例、負例、rule id 與版本。

### 5.3 題組

```text
Python markers + neighbors + range invariant
  → 明確範圍：group proposal
  → 邊界衝突：route_key=group_boundary
  → 低信心／模型與 marker 衝突：route challenger 或人工
```

初期所有 group proposal 仍由人確認。未來只以通過 gold 的 marker rule family 放寬，不因「某模型整體不錯」而全自動。

### 5.4 圖片

```text
文字 cue + asset metadata
  → need_visual candidate
  → route_key=vision_need / vision_crop（初始 Gemma 4 31B／Ollama Cloud）
  → no_visual_required / visual_asset_ok / crop_needed
  → crop_needed：模型 rough bbox → Python snap/crop
  → 產生 overlay + before/after contact sheet
  → 高難度／低信心／衝突時路由 Kimi K2.7 Code；Qwen／Mistral 作分層抽樣，已認證本地 Qwen 可 shadow
  → 人工接受後才綁定新 revision
```

Kimi K2.7 Code 不對每張圖常態呼叫，只處理 Gemma 低信心、`crop_needed`、PDF 多區塊干擾、答案影像或抽樣衝突；Mistral Large 3 用於 negative control 與獨立抽樣。所有 vision profile 必須用相同 pixel packet、JSON contract 與人工 gold 分開認證。

### 5.5 答案與 MOD

```text
ANS/MOD pairing + numbering + legal answer values
  → 正常單一答案：machine evidence
  → # / 空白 / 多答案 / 送分 / 衝突：route_key=answer_special_text
  → 文字層不足：route_key=answer_special_vision
  → unresolved：人工答案 queue
```

模型只讀官方答案文件，不重新解題。

## 6. Provider adapter 規格

保留現有 `docs/skills/national-exam-ai-audit/` 契約，不重建第二套 audit framework。

### 6.1 應修改／新增的檔案

```text
docs/skills/national-exam-ai-audit/adapters/
  ollama.py                         # 保留本地與 cloud，相容 /api/chat images
  openai_compatible.py              # vLLM/SGLang local endpoint
  opencode_go.py                    # 選配 Go；chat/completions 或 messages
  __init__.py                       # 註冊 adapter

docs/skills/national-exam-ai-audit/profiles/
  deepseek-v4-flash-opencode-go.yaml
  deepseek-v4-flash-ollama-cloud.yaml
  gemma4-31b-ollama-cloud.yaml
  kimi-k2.7-code-ollama-cloud.yaml
  qwen3.5-397b-ollama-cloud.yaml
  mistral-large-3-ollama-cloud.yaml
  qwen3.8-27b-local.yaml             # default disabled；設備認證後才能 shadow
  glm-5.2-opencode-go.yaml
  qwen3.8-max-opencode-go.yaml       # conditional，通過 license mapping 才啟用

configs/ai395_review_pipeline/
  pipeline.yaml
  provider_registry.yaml
  route_registry.yaml
  resource_policy.yaml
  budget_policy.yaml
  schedule_policy.yaml
  invalidation_matrix.yaml

docs/skills/national-exam-ai-audit/contracts/
  provider-capability.schema.json
  model-certification.schema.json
  visual-result.schema.json
  answer-result.schema.json

docs/skills/national-exam-ai-audit/scripts/
  run_external_shadow_batches.py
  certify_model_profile.py
```

### 6.2 統一 request／response

adapter 輸出至少包含：

```json
{
  "transport": "ollama_chat | openai_chat_completions | opencode_chat_completions | opencode_messages",
  "endpoint": "...",
  "model": "...",
  "payload": {},
  "advisory_only": true,
  "request_fingerprint": "sha256"
}
```

runner 的 normalized result 至少包含：

```json
{
  "provider": "ollama_cloud | ollama_local | local_openai_compatible | opencode_go",
  "requested_model": "...",
  "response_model": "...",
  "batch_id": "...",
  "request_fingerprint": "...",
  "strict_json": true,
  "usage": {
    "input_tokens": 0,
    "cached_input_tokens": 0,
    "output_tokens": 0
  },
  "latency_ms": 0,
  "attempt": 1,
  "raw_response_sha256": "..."
}
```

實作要求：

- OpenCode certified 主線使用 `https://opencode.ai/zen/go/v1/chat/completions`。`/messages` transport 只供通過 license mapping 的 Qwen conditional profile；不可順便把 proprietary Responses models 加入 allowlist。
- Ollama vision 使用 `/api/chat` 的 `images`，文字舊 profile 仍須 backward compatible。
- 本地 Qwen 可用 Ollama local、vLLM 或 SGLang，但 route 只認 `qwen3.8-27b-local` profile；實際 serving runtime、quantization、endpoint 與 model response id 都由 profile 記錄。若 runtime 的 vision template／pixel transport 未通過，就只認證 text lane。
- API key 只從 AI395 mode-600 systemd EnvironmentFile 讀取：`OPENCODE_GO_API_KEY`、Ollama 官方登入／token 所需設定；不得寫入 manifest、log、prompt 或 Git。
- 429、5xx、timeout 可 bounded retry；400、401、403、invalid JSON 不盲目重試。
- retry 使用 exponential backoff＋jitter，且同 batch 最多 2 次；第三次轉 failed queue。
- 每次 response 先保存 raw artifact，再做 schema validation；validator 不可靜默修補模型 JSON。

## 7. Model certification

任一 profile 上線前需完成五類 probe；第五類只適用本地模型：

1. Availability：model id、endpoint、auth、timeout、response model。
2. Contract：20 個 batch，strict JSON 與 checked-count 100%。
3. Text：至少 30 題，涵蓋 clean、繁簡、科學名、上下標、source-original、group cue。
4. Vision：至少 30 張，包含無圖、正確圖、錯裁、表格、公式、跨頁、負控制；確認模型真的收到 pixels。
5. Local resource（只適用本地模型）：權重／quantization hash、冷啟動、RAM／VRAM、tokens/s、p95、OOM recovery、連續 100 requests、與 MinerU 的互斥／共存測試。

每個 lane 獨立認證，profile 狀態：

```text
discovered → transport_verified → shadow → certified → suspended
```

`certified` 初始 gate：

- schema adherence 100%。
- coverage 100%，零漏 batch／漏 key／重複 key。
- source-fidelity violation 0。
- clean unsafe edit 0。
- route accuracy 100%。
- exact auto-patch precision 至少 99.5%；不足時只能 advisory。
- visual `need_visual` critical false negative 0。
- bbox 不直接自動套用，直到另行通過 crop-strategy gate。

既有 `scripts/probe_text_audit_model.py` 與 `scripts/probe_multimodal_model.py` 應擴充 `ollama_cloud`、`ollama_local`、`local_openai_compatible` 與選配 `opencode_go` provider。unit test 不能依賴真實網路；HTTP transport 使用 mock fixture。

### 7.1 Qwen 3.8-27B 本地設備驗證

本地模型採兩階段認證，不能因「成功回一句話」就加入正式路由：

1. `hardware_probe`：先測完整／量化權重是否能載入、最大安全 context、text 與 image request、單批／多批吞吐、100 次連跑、container restart、OOM 後恢復。
2. `lane_shadow`：用與 Ollama Cloud 相同的 200 題 stratified packets 盲測 text、notation、group、need_visual、crop；逐 lane 比 schema、coverage、source fidelity、precision、false negative、latency 與能耗。

認證結果可分開：例如 text 可為 `certified_shadow`，vision 若 pixel transport 或精度不足仍保持 `suspended`。初期只允許 `optional_local`，不可自動取代 Ollama Cloud primary。MinerU 與 Qwen 的 resource lease 預設互斥；只有共存 benchmark 證明不影響 OCR 完整率與 SLA，才允許有限並行。

## 8. 可糾錯 Python component lineage

這是本計畫的硬性工程要求。每個 Python 節點均須符合相同 CLI 與 manifest contract。

### 8.1 CLI contract

每個 batch worker至少支援：

```text
--input / --manifest
--output-dir
--run-id
--resume
--dry-run
--max-items
--config
--describe
```

規則：

- 不覆寫 completed run；修正後使用新 run id。
- item 級結果以 temporary file 原子 rename。
- stdout 只輸出一個 machine-readable summary；詳細內容寫 artifact。
- exit code：0 成功、2 輸入／contract 錯、3 可重試外部錯誤、4 不可重試資料錯、5 validation fail。
- 所有寫 production DB 的腳本預設 dry-run，且必須是獨立 importer，不得混在模型 runner。
- `--describe` 只輸出 machine-readable node contract、輸入／輸出、設定 key、exit codes 與 side-effect class，不執行工作或連線 production。

### 8.2 `run_manifest.json`

每次執行至少記錄：

```json
{
  "run_id": "...",
  "component_id": "pdf_reference_builder",
  "script_path": "scripts/build_pdf_reference_source.py",
  "git_sha": "...",
  "script_sha256": "...",
  "config_sha256": "...",
  "dependency_lock_sha256": "...",
  "container_or_runtime": "...",
  "container_image_digest": "...",
  "started_at": "...",
  "finished_at": "...",
  "input_artifacts": [{"path": "...", "sha256": "..."}],
  "output_artifacts": [{"path": "...", "sha256": "..."}],
  "model_profile_id": null,
  "prompt_version": null,
  "parent_run_ids": [],
  "database_written": false,
  "review_events_written": false
}
```

### 8.3 發現腳本錯誤時的 repair loop

```text
建立 pipeline defect
  → 鎖定 component_id + script_sha256 + config range
  → 由 lineage 算出所有 impacted artifacts / revisions / lane runs
  → 標記 stale_by_defect，不刪除、不覆寫
  → 修正腳本 + regression fixture
  → 新 Git SHA、新 run id 重跑
  → old/new diff + coverage proof
  → 人工／owner 決定是否匯入 advisory 或建立新 revision
```

建議 schema draft（先 PR，不直接套 production）：

```text
exam.pipeline_component_versions
exam.pipeline_runs
exam.pipeline_run_inputs
exam.pipeline_run_outputs
exam.pipeline_defects
exam.pipeline_defect_impacts
exam.question_revisions
exam.review_lane_runs
```

在 migration 尚未核准前，完整 manifest 存在非 Git artifact registry，並用 SQLite／JSONL 建 read-only index；不可因此阻擋第一個 shadow pilot。

## 9. GPT-5.6 Luna 派工包

### 9.0 第一段可直接交付 Luna 的主指令

```text
請在 tw-national-exam-catalog 建立 AI395 國考題審核工作流。先完整閱讀
AGENTS.md、docs/governance/README.md、governance/policy.json、
docs/dify-n8n-review-pipeline-spec.md、
docs/ai395-open-model-review-implementation-plan.md 與
docs/ai395-review-workflow-operator-learning-guide.md。

先完成 WP-00 與 WP-00S：建立隔離 Docker staging、可調 route/model 設定層、
mock provider、isolated PostgreSQL、20 題 fixtures 與可匯入 n8n walking skeleton，
證明從 run create 到 formal dry-run 能端到端、可重跑、可觀察。Ollama Cloud 是唯一
預設啟用的真實模型 provider；OpenCode Go 與本地 Qwen 3.8-27B 都預設停用。
不得等所有 lane 做完才第一次整合。

完成 walking skeleton 後，依 WP-01～WP-11 分成小型 codex/* branch／PR 逐步實作。
每個 PR 必須附 tests、fixture、SQL/資料影響、run manifest、操作手冊更新與驗收證據。
可自主完成 G0～G2 development/staging；不得部署 production、套 production migration、
寫 production advisory、寫人工審核事件、修改 immutable release 或把模型判斷冒充人工決策。
遇到需要 G3/G4 的步驟，停在 dry-run/evidence/PR，列出 exact action 供 owner 決定。
```

OpenAI 官方文件將 GPT-5.6 Luna 定位為成本敏感、高量工作負載，支援長 context、structured outputs 與 apply-patch 類工具；因此適合作為大量 scaffold、測試、文件同步與可重複工程工作的執行代理。重要架構、schema、治理與 production 邊界仍以本文件的 acceptance criteria 驗收，不能只因 Luna 已產生程式就視為通過。

### 共通派工指令

每個 work package 都必須：

1. 先讀 repository `AGENTS.md`、`docs/governance/README.md`、`governance/policy.json` 與本文件。
2. 新工作使用 `codex/*` branch；不可直接 push `main`，不可改 AI395 immutable release。
3. 不接觸 production writer，不寫人工事件，不把 API key 放進檔案。
4. 優先擴充 `docs/skills/national-exam-ai-audit/` 現有 contract、validator、runner 與 profiles。
5. 每個 package 都提交 tests、fixtures、dry-run 範例、資料影響說明與 rollback／staleness 說明。
6. 網路 integration test 預設 skip；unit test 使用錄製後去敏 fixture 或 mock server。
7. 每完成一個 package，就同步更新 `docs/ai395-review-workflow-operator-learning-guide.md` 的實際命令、輸入／輸出範例、常見錯誤與該 node 的學習驗收；不可留下與程式不一致的假命令。

### WP-00：基線與範圍凍結

輸出：

- 現有 downloader、MinerU、parser、PDF evidence、AI audit、Review UI 與 SQL schema inventory。
- `docs/ai395-open-model-review-gap-analysis.md`。
- 明確列出 reuse／extend／new，不搬移既有大型資料。
- 清查既有 `gpt-5.6-luna.yaml` 審題 profile，提出 legacy／retire migration；Luna 可作 coding agent，但新 route registry 不得引用 proprietary runtime profile。

驗收：

- 不改 runtime 行為。
- `python3 scripts/validate_agent_governance.py` 通過。
- 全部既有 tests baseline 有紀錄。

### WP-00S：可調模型設定與端到端 walking skeleton

這是 Luna 在各 lane 完整化前必須先交付的垂直骨架，目標是讓 owner 能看到整條流程真的移動資料，而不是等所有模型完成後才第一次整合。

輸出：

- 第 4.1 節 Docker staging stack、healthcheck 與一鍵 teardown；不接 production volume／credential。
- 第 4.2 節設定檔、JSON schema、resolver、effective-config dump 與 fail-closed tests。
- 20 題小型合法 fixture，含 Q／ANS／MOD、MinerU stub、parser pass／fail、五 lane stub、revision、Review UI exception 與 formal dry-run 結果。
- `mock-model` adapter：固定 deterministic response，可測 timeout、429、invalid JSON、漏 key、模型 finding 與 human queue；無 cloud 帳號也能 E2E。
- 隔離 PostgreSQL migration apply／rollback／idempotency 測試，至少涵蓋 run、job、revision、lane、finding、proposal、model run 與 queue。
- 可匯入 n8n skeleton：建立 run → enqueue → worker lease → 五 lane fan-out → aggregator → exception queue → formal dry-run report。
- 單一 operator entrypoint，例如 `make ai395-review-staging-e2e`；名稱以 Luna 實作後的真實命令為準並寫入操作手冊。

驗收：

- 從空 staging volume 開始，可用一個命令建立環境並跑完 20 題；第二次重跑 idempotent。
- SQL 能證明 20 題的 disjoint coverage、每個 revision／lane 狀態、1 筆安全新 revision、至少 1 筆人工 exception，以及 formal dry-run 不會寫 production。
- 關閉任何一個 worker、重啟 n8n／PostgreSQL後，lease 能恢復且不重複匯入。
- 把 `opencode_go.enabled=false`、`local_qwen.enabled=false` 時仍能完成 mock E2E。
- n8n JSON、Python、SQL 與手冊沒有寫死 cloud model；改 `route_registry.yaml` 後 effective config 與 manifest hash 跟著改。
- Luna 產出 walking-skeleton run report、SQL count proof、container health、測試報告與 10 分鐘 owner 教學步驟。

### WP-01：Provider contract 與 adapters

輸出：

- `opencode_go.py` adapter。
- 本地 OpenAI-compatible adapter；`ollama.py` 同時支援 local／cloud profile。
- Ollama `/api/chat` text＋image backward-compatible adapter。
- 統一 external runner、raw response、usage journal、bounded retry。
- provider mock tests。

驗收：

- API key 不出現在 request artifact。
- resume 不重送已完成 batch。
- 429／5xx／timeout／invalid JSON／oversized response 都有測試。
- runner 無任何 database write path。
- OpenCode 沒有 credential／未訂閱時，profile 顯示 `disabled_unconfigured`，不能讓主流程失敗或自動轉送資料。

### WP-02：Model profile 與 capability certification

輸出：

- 第 6.1 節 profiles。
- model/license/provider metadata snapshot。
- text／multimodal probes 支援 Ollama Cloud、本地 OpenAI-compatible／Ollama，以及選配 OpenCode Go。
- `certify_model_profile.py` 與 certification JSON。

執行順序：

1. DeepSeek V4 Flash／Ollama text。
2. Gemma 4 31B／Ollama vision。
3. Kimi K2.7 Code／Ollama vision challenger；先記錄 Modified MIT acknowledgement，再測 pixel transport、strict JSON、中文 PDF fidelity、crop／MOD gold、延遲與 weighted usage。
4. Qwen 3.5／Ollama text＋vision。
5. Mistral Large 3／Ollama vision。
6. Qwen 3.8 27B／AI395 local；等 owner 完成設備準備後執行第 7.1 節，不阻擋 cloud 主線。
7. DeepSeek V4 Flash／OpenCode Go；只有 owner 已訂閱並啟用 provider 才做 live probe，否則只完成 mock certification。
8. GLM 5.2／OpenCode Go text escalation；選配。
9. Qwen3.8 Max／OpenCode Go conditional challenger；選配且需 license mapping。

驗收：provider availability 與模型品質分開報告；「API 可呼叫」不能寫成「已認證」。

### WP-03：Component lineage 與 defect blast radius

輸出：

- 共用 `run_manifest` library。
- manifest schema 與 validator。
- `scripts/index_pipeline_manifests.py`。
- `scripts/build_pipeline_defect_impact.py`。
- schema migration draft；不可 apply production。

驗收：以一個故意有 bug 的 fixture 示範能找出所有受影響 output，修正後只重跑 affected scope。

### WP-04：文字三來源 evidence lane

輸出：

- 串接現有 `build_pdf_reference_source.py`、`build_three_source_audit_pilot.py`、sparse packet 與 validator。
- source-family 去重與 E0–E3 evidence policy。
- DeepSeek residual runner。
- 200 題 stratified shadow report。

驗收：

- 完全一致題不呼叫模型。
- source-original 不被繁簡偏好改寫。
- 新 replacement 未有 active rule 時只產 proposal。
- AI output 不寫人工狀態。

### WP-05：Notation 與 group lane

輸出：

- notation deterministic rule registry 與 pixel-required route。
- group range proposal contract、neighbors 與 negative controls。
- lane-specific gold／regression fixtures。

驗收：

- 題組模型不能產生 `confirm_group`。
- notation exact span 零／多命中時拒絕 materialize。
- parser boundary issue 退回 parser owner，不在 LLM lane 偷修。

### WP-06：Vision 與 crop proposal

輸出：

- `need_visual`、`asset_quality`、`crop_proposal` 三段 contract。
- 固定 DPI、頁碼、coordinate space、rough bbox、Python snap bbox。
- before／after contact sheet 與 PDF overlay。
- Gemma primary；Kimi K2.7 Code 處理高難度／低信心／衝突；Qwen、Mistral 作 sampled challenger／negative control。

驗收：

- pixel transport probe 通過才可執行。
- Kimi profile 只有在高難度／衝突子集相對 Gemma 能實質降低錯誤，且 weighted usage／延遲符合 budget gate 時才保留在 active challenger；否則降為 research-only。
- 模型 bbox 不直接綁定 candidate。
- 原圖、proposal、overlay、hash 均可回溯。
- 三個人工結果能進 Review UI queue，但仍寫人類事件。

### WP-07：Answer／MOD lane

輸出：

- normal ANS／MOD deterministic verifier。
- `#`、空白、多答案、送分、衝突 special task contract。
- text-first／vision-fallback 路由。

驗收：

- 模型不得重新解題。
- accepted values 必須能指回答案 PDF span 或 page pixels。
- unresolved 一律進人工答案 queue。

### WP-08：n8n coarse orchestration

輸出：

- 可匯入的 n8n workflow JSON。
- 將 WP-00S skeleton 強化成 production-ready coarse orchestration：run create、enqueue、lease monitor、budget guard、provider circuit breaker、通知。
- operator runbook 與 dry-run。

驗收：

- 不建立每題一個長生命 n8n execution。
- n8n crash 後 job lease 可恢復。
- 同 scope 重送具 idempotency。
- provider 每日／每月預算超限時停止新模型 job，不影響 deterministic workers 與 Review UI。
- provider 切換只改 route/provider config；OpenCode Go 未啟用時不產生 fallback job。

### WP-09：Review UI v2 exception queues

輸出：

- 文字 evidence、notation、group、三類圖片、MOD、oscillation、machine-pass sample queues。
- revision diff、PDF page、來源、模型／prompt／rule 與 before／after evidence。
- 手機快速接受／拒絕／轉桌面流程。

驗收：

- AI advisory 與 human action 視覺、資料表與 API 全部分離。
- 手機精確裁切不可誤觸直接完成。
- 批次接受只限相同 rule id、patch signature 與完整 evidence。

### WP-10：Corpus rehearsal 與兩週 runbook

輸出：

- 1,000 題 shadow，再跑一個完整類科 rehearsal。
- throughput、成本、false alert、人工 queue、retry、provider outage 報告。
- 兩週 corpus runbook、checkpoint、停止條件與 formal dry-run handoff。

驗收：

- deterministic＋AI scope coverage 為 disjoint union，100% 可解釋。
- 人工 exception queue 目標低於總題數 2%–3%；超過則停下改善規則。
- 不把 shadow 完成宣稱為 production 發布完成。
- production apply 仍需另一次 exact G3 approval。

### WP-11：Owner 操作與逐關卡學習手冊實證化

輸出：

- 以 `docs/ai395-review-workflow-operator-learning-guide.md` 為底稿，替換所有 planned command 為實際可執行命令。
- 每個 node 的輸入、輸出、SQL 查詢、artifact、成功條件、故障注入、重跑、停用模型與切換 profile 範例。
- 一份「先看全流程，再逐關解鎖」的 owner checklist；每一關都能在 20 題 fixture 與 200 題 shadow 各跑一次。
- n8n workflow 截圖／node 對照表可放小型文件資產；不得放真實大量題目或 secret。

驗收：owner 不修改 Python，即可依手冊完成 staging E2E、查看 SQL、停用一個 lane、替換一個 model profile、重跑 affected scope、審一筆 exception 並產出 formal dry-run report。人工 action 只在隔離 staging 模擬；production 人工事件仍由 owner 在 Review UI 執行。

## 10. 建議實作順序與平行化

```text
Milestone 0：先看到整條資料流
  WP-00
    └─ WP-00S Docker staging + config resolver + SQL/n8n walking skeleton

Milestone 1：替換骨架中的基礎能力
  WP-00S
    ├─ WP-01 provider adapters
    └─ WP-03 lineage / defect impact

Milestone 2：逐 lane 實作，可平行
  WP-01 → WP-02 Ollama Cloud certification
  WP-03 → WP-04 text evidence
  WP-04 → WP-05 notation/group
  WP-02 → WP-06 vision
  WP-03 → WP-07 answer

Milestone 3：整合與操作面
  WP-04/05/06/07 → WP-08 n8n
  WP-04/05/06/07 → WP-09 Review UI
  WP-00S～WP-09 → WP-11 owner manual evidence

Milestone 4：穩定化
  WP-10 rehearsal → owner review → production proposal

Optional tracks（不阻擋主線）
  device ready → Qwen 3.8-27B local certification → route shadow
  owner subscribes Go → OpenCode live certification → optional outage fallback
```

WP-00S 必須先證明 SQL、n8n、worker 與 queue 可以 E2E。之後 WP-01 與 WP-03 可平行；WP-05、WP-06、WP-07 在共同 contract 穩定後可平行。Review UI 不必等所有 lane 完成才開始，但正式合併前必須以同一 revision／finding contract 整合。手冊不是最後補寫：每個 WP 都同步更新，WP-11 只作最終實證與教學驗收。

## 11. Go／No-Go 指標

### Provider gate

- provider 連續 24 小時可用率、429、5xx、p50／p95 latency 有紀錄。
- 實際 model id／license mapping 完整。
- pixel transport、JSON、usage accounting 通過。
- 花費上限與 circuit breaker 經演練。
- `opencode_go.enabled=false` 與 `local_qwen.enabled=false` 是完整可運行的預設狀態；任一選配 provider 不得成為隱性必要依賴。
- route resolver 拒絕 disabled、uncertified、capability 不符或 data-policy 過期的 profile。

### Data gate

- 每個 source、candidate、revision、lane、finding、proposal、crop 都有 hash lineage。
- script defect 能產出 blast-radius report。
- 任何 patch 都不覆寫 raw PDF、MinerU 或歷史 revision。

### Quality gate

- clean unsafe edit = 0。
- source fidelity violation = 0。
- route accuracy = 100%。
- coverage = 100%。
- auto-patch precision ≥ 99.5%，且只有 active deterministic rule 或已核准 evidence policy 能 materialize。

### Governance gate

- AI 無法寫人工事件。
- production writer 仍只有 AI395 Review UI／受控 importer。
- schema、Review UI、governance、production deployment 經 CODEOWNERS／owner review。

## 12. 需要 owner 回覆、但不阻擋 WP-00～WP-04 的問題

1. 本地 Qwen 3.8-27B 最後採 Ollama、vLLM 或 SGLang，以及 quantization，由 AI395 hardware probe 決定；profile／route contract 不因 serving runtime 改變。
2. 「開源」是否只接受標準 MIT／Apache-2.0？Kimi K2.7 Code 是 Modified MIT，超過 1 億 MAU 或每月 US$20M 營收時有 UI 顯名義務；其 profile 可先完成技術認證，但進 production route 前仍需 owner 留下條款接受紀錄。MiniMax M3、Kimi K3 另做各自條款 gate。
3. OpenCode Go 不先訂閱。若 Ollama corpus pilot 顯示額度／可用率不足，再由 owner 決定是否訂閱；未訂閱不阻擋 WP-00S～WP-10 的 Ollama 主線。
4. 若未來啟用 OpenCode Go，`qwen3.8-max`／Plus SKU 是否接受自訂開放權重 license，仍需獨立確認；超額 Zen balance 預設關閉。
5. 未來是否允許 `machine_verified_* + 人工 batch approval`，或仍要求逐題人工 event？這影響兩週上線的人力，但不影響 shadow pipeline。

## 13. 官方資料快照

- OpenCode Go 訂閱、額度、endpoints、model ids 與 privacy：<https://opencode.ai/docs/go>
- OpenCode Go 即時模型清單：<https://opencode.ai/zen/go/v1/models>
- OpenCode Zen 按量價格（只供 Go overflow balance 比較）：<https://opencode.ai/docs/zen>
- Ollama Cloud plans、usage 與 concurrency：<https://ollama.com/pricing>
- Ollama Gemma 4：<https://ollama.com/library/gemma4>
- Ollama Qwen 3.5：<https://ollama.com/library/qwen3.5>
- Ollama DeepSeek V4 Flash：<https://ollama.com/library/deepseek-v4-flash>
- Ollama Mistral Large 3：<https://ollama.com/library/mistral-large-3>
- Ollama Kimi K2.7 Code：<https://ollama.com/library/kimi-k2.7-code>
- DeepSeek V4 Flash upstream／MIT：<https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash>
- Qwen 3.5 upstream／Apache-2.0：<https://huggingface.co/Qwen/Qwen3.5-397B-A17B>
- Qwen 3.8 27B upstream／Apache-2.0：<https://huggingface.co/Qwen/Qwen3.8-27B>
- Gemma 4 31B upstream／Apache-2.0：<https://huggingface.co/google/gemma-4-31B>
- GLM 5.2 upstream／MIT：<https://huggingface.co/zai-org/GLM-5.2>
- Mistral Large 3 upstream／Apache-2.0：<https://huggingface.co/mistralai/Mistral-Large-3-675B-Instruct-2512>
- Kimi K2.7 Code upstream／Modified MIT：<https://huggingface.co/moonshotai/Kimi-K2.7-Code>
- Kimi K2.7 Code license：<https://huggingface.co/moonshotai/Kimi-K2.7-Code/blob/main/LICENSE>
- MiniMax M3 custom license：<https://huggingface.co/MiniMaxAI/MiniMax-M3/blob/main/LICENSE>
- Kimi K3 custom license：<https://huggingface.co/moonshotai/Kimi-K3/blob/main/LICENSE>
- GPT-5.6 Luna 官方能力，只供本計畫實作代理評估：<https://developers.openai.com/api/docs/models/gpt-5.6-luna>
