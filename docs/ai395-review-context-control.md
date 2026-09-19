# AI395 題目審核的模型 context 控制

本文件描述 staging worker 如何調度 LiteLLM 上的 GLM-5.3-Flash；不代表 `local-model-manager` 已部署，也不改動 AI395 的 systemd 模型服務。模型仍只能產生 advisory，不能取代人工 review event。

## 目前可用的路由

- `litellm_glm`：目前五條 lane 的 staging primary，透過 LiteLLM OpenAI-compatible `/v1/chat/completions` 呼叫 `glm-5.3-flash`；key 只從 runtime environment 注入。
- `local_qwen_mlx`：MacBook 的 `qwen3.8:27b-mlx`，透過 tailnet HTTPS Serve，Ollama native `/api/chat`，只作 shadow／測試。
- AI395 本地 Qwen：目前只做 read-only preflight；AI395 runtime 與 `--ctx-size` 不在本次變更範圍。以下 192K policy 只套用 MacBook Qwen staging。
- `local-model-manager`：指南標示為 target contract，沒有 manager listener 時，workflow 必須停在 staging，不猜 port、不自行啟動模型。

## Context admission

GLM staging 預設使用：

- hard limit：262,144 tokens（256K）
- safety margin：8,192 tokens
- 單 lane context 失敗時最多一次 extension：393,216 tokens（384K）
- extension 次數：每 lane 1 次，第二次不足就 fail-closed
- output reserve：由 `--model-max-tokens` 保留
- 估算方法：UTF-8 JSON byte upper bound；這是保守估算，超過就於 transport 前 fail closed

模型官方 context 能力與 workflow admission cap 是兩件事：GLM 官方文件列出 1M context，但本工作流仍以 packet、lane、evidence tool 與 revision 邊界控管實際輸入。第一次超過 256K 時才使用一次 384K 上限，不會因模型支援 1M 就直接把整份 PDF 塞入請求。

離線檢查：

```bash
python3 scripts/inspect_ai395_llm_payloads.py \
  --candidate-jsonl /path/to/candidates.jsonl \
  --fixture-root '/path/to/國考題資料夾/20_mineru_output' \
  --context-limit-tokens 262144 \
  --context-safety-margin-tokens 8192 \
  --output-json /private/tmp/ai395-payload-inspection.json
```

這個命令不會呼叫模型。`context_budget_exceeded` 題目會留在人工例外，不會繞過 guard。

## 每條 lane 的輸入

模型不再收到完整 candidate metadata、絕對路徑、完整 `raw_block` 或任意檔案名稱。第一輪只收到：

- text：題幹、選項與 parser allow-listed metadata
- notation：題幹、選項 markup 與 notation metadata
- group：題號、題幹、group cue 與相鄰題證據（需要時）
- vision：題幹、選項視覺 slot 與實際 raster pixels
- answer：題幹、選項、答案 payload 與答案 metadata

Qwen 可以回傳 `requested_evidence`，但只能請求下列五種 bounded tool：

1. `question_markdown`
2. `answer_markdown`
3. `adjacent_questions`
4. `pdf_reference`（現有三 extractor family 的 PDF reference cache）
5. `image_manifest`

controller 依 candidate metadata 解析來源，工具不接受模型傳入的路徑、shell 或 command。每條 lane 最多一個 evidence follow-up turn；工具結果仍會再次經過 context guard。

## 延遲適應

單次模型回應超過 `model_stage.context_policy.slow_threshold_seconds`（目前 30 秒）時：

1. 記錄 `latency_status=slow`、elapsed time 與 model profile。
2. 後續 lane 改用 compact level，縮短 evidence 與 metadata。
3. 不自動重試同一個慢 request，不平行啟動第二個模型。
4. GLM 第一次無法通過 context 時，最多使用一次 `one_time_extension_limit_tokens`；若仍無法通過 context 或 timeout，保留 `context_budget_exceeded`／`provider_error`，交 ReviewUI 人工例外。

所有結果仍是 advisory-only；模型不能直接改題目、答案、圖片或人工 review event。
