# AI395 題目審核的本地模型 context 控制

本文件描述 staging worker 如何調度 MacBook Qwen；不代表 `local-model-manager` 已部署，也不改動 AI395 的 systemd 模型服務。

## 目前可用的路由

- `local_qwen_mlx`：MacBook 的 `qwen3.8:27b-mlx`，透過 tailnet HTTPS Serve，Ollama native `/api/chat`。
- AI395 本地 Qwen：目前只做 read-only preflight；AI395 runtime 與 `--ctx-size` 不在本次變更範圍。以下 192K policy 只套用 MacBook Qwen staging。
- `local-model-manager`：指南標示為 target contract，沒有 manager listener 時，workflow 必須停在 staging，不猜 port、不自行啟動模型。

## Context admission

MacBook staging 預設使用：

- hard limit：196,608 tokens（192K；模型宣稱支援 256K，但保留 64K 未使用空間以控制延遲）
- safety margin：8,192 tokens
- output reserve：由 `--model-max-tokens` 保留
- 估算方法：UTF-8 JSON byte upper bound；這是保守估算，超過就於 transport 前 fail closed

離線檢查：

```bash
python3 scripts/inspect_ai395_llm_payloads.py \
  --candidate-jsonl /path/to/candidates.jsonl \
  --fixture-root '/path/to/國考題資料夾/20_mineru_output' \
  --context-limit-tokens 196608 \
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
4. 若仍無法通過 context 或 timeout，保留 `context_budget_exceeded`／`provider_error`，交 ReviewUI 人工例外。

所有結果仍是 advisory-only；模型不能直接改題目、答案、圖片或人工 review event。
