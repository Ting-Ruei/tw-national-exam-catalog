# AI395 review staging

這是與 AI395 production 完全分離的 Docker staging。第一階段只讀既有 source/MinerU fixture，執行 parser、SQL staging、五條 advisory lane、revision、exception queue 與 formal dry-run；source download 與 MinerU execution 在設定中維持 disabled。

## AI395 上的第一次啟動

在 AI395 operator checkout 執行：

```bash
cd /home/tim/src/tw-national-exam-catalog
cp deploy/ai395-review-staging/env.example deploy/ai395-review-staging/.env
# 編輯 .env，替換兩個 CHANGE_ME 值；不要提交 .env
docker compose --env-file deploy/ai395-review-staging/.env \
  -f deploy/ai395-review-staging/compose.yaml config --quiet
docker compose --env-file deploy/ai395-review-staging/.env \
  -f deploy/ai395-review-staging/compose.yaml up -d review-staging-db pipeline-worker n8n-review-staging
curl --fail http://127.0.0.1:58080/health
curl --fail -X POST http://127.0.0.1:58080/runs/e2e \
  -H 'Content-Type: application/json' \
  -d '{"fixture":"mini20","run_id":"ai395-mini20-first"}'
curl --fail http://127.0.0.1:58080/runs/ai395-mini20-first
```

`pipeline-worker` 的 HTTP bridge 只允許 fixture E2E，不接受任意 shell command、任意 database URL 或 production mode。n8n workflow 可從 `n8n/ai395-review-staging-e2e.json` 匯入，匯入後保持 inactive，先用 webhook／manual 測試。

這個 stack 的 port、PostgreSQL volume、n8n volume 與 artifact volume 都是 staging 專用。不要掛 production DB volume，也不要把 staging port 改成 Review UI 的 8765／8766。日常停止用 `docker compose ... stop`；不要用 `down -v` 清除證據。

## LiteLLM GLM-5.3-Flash staging provider

目前五條 lane 的模型入口是可替換的 OpenAI-compatible adapter；預設仍是 mock，只有 owner 明確開啟才會對 LiteLLM 發出請求。`glm-5.3-flash` 的文字／圖片請求都會以單題 bounded packet 傳送，模型輸出只能形成 advisory finding 或 guardrail candidate，不能直接改題目或啟用 Skill／rule。

AI395 上的 key 請由 owner-controlled secret file 注入，不要把 key 貼進命令、workflow JSON 或 Git。依使用者提供的環境變數名稱，可在啟動同一個 shell 先執行：

```bash
set -a
. /etc/ai395/tw-national-exam-catalog/litellm-client.env
set +a
export LITELLM_BASE_URL="$LITELLM_API_BASE"
export LITELLM_MODEL='glm-5.3-flash'
export LITELLM_GLM_ENABLED=1
export AI395_STAGING_ALLOW_LIVE_LLM=1
export AI395_STAGING_MODEL_MODE=litellm_glm
```

先做 read-only model probe，再讓 n8n 或 HTTP bridge 啟動 staging run：

```bash
python3 scripts/probe_litellm_glm.py --model "$LITELLM_MODEL"
curl --fail -X POST http://127.0.0.1:58080/runs/e2e \
  -H 'Content-Type: application/json' \
  -d '{"fixture":"mini20","run_id":"ai395-mini20-glm-test","model_mode":"litellm_glm"}'
curl --fail http://127.0.0.1:58080/runs/ai395-mini20-glm-test
```

若要暫停外部模型，只把 `AI395_STAGING_ALLOW_LIVE_LLM=0` 或 `LITELLM_GLM_ENABLED=0` 設回去並重建 worker；n8n 圖不需要重畫。可調整的 context／慢呼叫參數是 `AI395_STAGING_CONTEXT_LIMIT_TOKENS`、`AI395_STAGING_CONTEXT_SAFETY_MARGIN_TOKENS`、`AI395_STAGING_MAX_TOOL_TURNS` 與 `AI395_STAGING_MODEL_SLOW_THRESHOLD`；留白時使用版本化 pipeline policy（256K hard limit，必要時最多單次 384K extension）。GLM 的官方多模態與 OpenAI-compatible transport 仍要以 probe、gold corpus 與 latency report 認證，不能把「API 可呼叫」當成「題目已正確」。

原有 MinerU 產物可能是 JPG；GLM vision staging 不會把 JPG bytes 偽裝成 PNG。先在隔離 scope 執行 deterministic conversion，讓新 manifest 指向新的 PNG view：

```bash
python3 scripts/normalize_vision_assets_to_png.py \
  --source-manifest /path/to/scope/source_manifest.json \
  --mineru-manifest /path/to/scope/mineru_manifest.json \
  --output-dir /path/to/scope/png-view
```

這個節點只讀原始 MinerU 資產，會保存 source／output SHA-256、轉檔版本與
`asset_conversion_report.json`，不修改原始 JPG／PNG。後續 `e2e` 使用
`png-view/source_manifest.json` 與 `png-view/mineru_manifest.json`；送往 LiteLLM
的每張圖片才會嚴格符合 `data:image/png;base64,...`。

### Correction feedback／guardrail outbox

ReviewUI 真正保存人工修正時，會追加 `question_review_events` 與不可變的 `question_correction_feedback_events`；三證據自動修正只有帶三個獨立 evidence family 才能進 outbox。n8n 後續可讀取 `/api/correction-feedback?status=pending`，再把 bounded `ai_task` 交給 GLM。回傳的 `question_guardrail_candidate_v1` 只停在 `proposed`／`no_generalization`／`ai_failed` 等狀態，必須 owner approval、negative controls 與 gold regression 後才可另開版本實作；沒有任何直接寫 Skill、規則或題目檔案的節點。

## MacBook Qwen MLX 測試節點

這個 staging 不在 AI395 下載或啟動 Qwen；模型跑在 MacBook 原生 Ollama，AI395 worker 只在 owner 完成測試後透過 Tailscale Serve 呼叫：

```bash
python3 scripts/setup_qwen_mlx_tailscale_serve.sh --plan
# 確認 ollama list 的實際 tag 後，在 MacBook 設定 QWEN_MLX_MODEL
python3 scripts/probe_qwen_mlx_tailscale.py \
  --base-url https://<macbook>.<tailnet>.ts.net/v1 \
  --model "$QWEN_MLX_MODEL"
```

`--plan` 與 probe 不會改變服務；`--apply` 才會執行 `tailscale serve --bg --https=443 http://127.0.0.1:11434`。這會代理整個 Ollama API（不只 Qwen），同一個 tailnet 中符合 ACL 的裝置都可以使用，不綁定 AI395。不要使用 public Funnel、不要直接轉發 11434、不要把 endpoint 寫成 HTTP 公網 URL；若要限制使用者，請在 Tailscale ACL 管理，而不是在 Ollama API 裡放秘密。

完成 `/v1/models` read-only probe、一次 text live probe、一次 vision pixel probe、延遲／OOM／重啟與 200 題 gold comparison 前，`QWEN_MLX_ENABLED` 必須維持 `0`，profile 只能是 shadow/test。

## 從其他 tailnet 裝置使用通用 API

Serve 成功後，`tailscale serve status` 會顯示這台 MacBook 的 MagicDNS HTTPS 網址。把它替換成下列 `<macbook-tailnet-url>`，同一 tailnet 且 ACL 允許的其他裝置即可查詢所有本機 Ollama model：

```bash
export OLLAMA_TAILNET_URL='https://<macbook>.<tailnet>.ts.net'
curl --fail "$OLLAMA_TAILNET_URL/v1/models"

curl --fail "$OLLAMA_TAILNET_URL/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer ollama' \
  -d '{
    "model": "qwen3.8:27b-mlx",
    "messages": [{"role": "user", "content": "請只回覆：API_OK"}],
    "stream": false
  }'
```

`Authorization: Bearer ollama` 是 Ollama OpenAI-compatible adapter 的 placeholder，不是要提交的秘密。這個入口不是只提供 Qwen；只要模型已在 MacBook 的 `ollama list` 中，就能透過同一 API 使用。若未來要縮小為只有 AI395 或特定裝置，請改 Tailscale ACL，不要把 API 暴露到公網。

## 在 AI395 staging 明確啟用 MacBook Qwen

預設仍是 mock；只有 owner 在 staging `.env` 明確開啟時，worker 才會連到 MacBook。`QWEN_MLX_BASE_URL` 保留 `/v1`，供 model probe 使用；實際審核 adapter 會在同一個 root 呼叫 Ollama 原生 `/api/chat`，因為目前 Qwen MLX 需要原生介面的 `think:false` 與 `format:json` 才能穩定取得 JSON。

先在 AI395（或同一個 pipeline-worker network namespace）做 tailnet read-only 檢查：

```bash
export QWEN_MLX_ROOT='https://<macbook>.<tailnet>.ts.net'
export QWEN_MLX_BASE_URL="$QWEN_MLX_ROOT/v1"
export QWEN_MLX_MODEL='qwen3.8:27b-mlx'
curl --fail "$QWEN_MLX_BASE_URL/models"
curl --fail "$QWEN_MLX_ROOT/api/tags"
```

在 `deploy/ai395-review-staging/.env` 只對 staging 設定：

```dotenv
QWEN_MLX_ENABLED=1
QWEN_MLX_BASE_URL=https://<macbook>.<tailnet>.ts.net/v1
QWEN_MLX_MODEL=qwen3.8:27b-mlx
AI395_STAGING_ALLOW_LIVE_LLM=1
AI395_STAGING_MODEL_MODE=local_qwen_mlx
AI395_STAGING_LLM_LANE_POLICY=residual
AI395_STAGING_LLM_MAX_CALLS=20
AI395_STAGING_MODEL_TIMEOUT=120
AI395_STAGING_MODEL_MAX_TOKENS=512
```

重建 staging worker 後，以 n8n 或 HTTP bridge 啟動；這個請求仍然只產生 staging SQL、advisory findings 與 dry-run：

```bash
docker compose --env-file deploy/ai395-review-staging/.env \
  -f deploy/ai395-review-staging/compose.yaml up -d --build pipeline-worker
curl --fail -X POST http://127.0.0.1:58080/runs/e2e \
  -H 'Content-Type: application/json' \
  -d '{"fixture":"mini20","run_id":"ai395-mini20-qwen-local","model_mode":"local_qwen_mlx"}'
curl --fail http://127.0.0.1:58080/runs/ai395-mini20-qwen-local
```

若 AI395 不是 Tailscale tailnet member 或 ACL 不允許，worker 應保留 provider exception；不要把 11434 直接開到 LAN／公網。模型回覆、endpoint、transport、prompt version 與錯誤都會留在 staging lane result；不能自動寫入人工 accept、block、group confirm 或正式題目 revision。mini20 的 SVG fixture 刻意不會送給 vision model，會進 `pixels_unavailable` 例外；接上真實 MinerU PNG/JPEG/WebP 產物後，才測視覺 pixel lane。
