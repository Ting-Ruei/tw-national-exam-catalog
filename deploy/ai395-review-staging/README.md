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

## MacBook Qwen MLX 測試節點

這個 staging 不在 AI395 下載或啟動 Qwen；模型跑在 MacBook 原生 Ollama，AI395 worker 只在 owner 完成測試後透過 Tailscale Serve 呼叫：

```bash
python3 scripts/setup_qwen_mlx_tailscale_serve.sh --plan
# 確認 ollama list 的實際 tag 後，在 MacBook 設定 QWEN_MLX_MODEL
python3 scripts/probe_qwen_mlx_tailscale.py \
  --base-url https://<macbook>.<tailnet>.ts.net/v1 \
  --model "$QWEN_MLX_MODEL"
```

`--plan` 與 probe 不會改變服務；`--apply` 才會執行 `tailscale serve --bg --https=443 http://127.0.0.1:11434`。Serve 是 tailnet 內的私有入口；不要使用 public Funnel、不要直接轉發 11434、不要把 endpoint 寫成 HTTP 公網 URL。Tailscale ACL 也必須限制只有 AI395 節點能存取該主機。

完成 `/v1/models` read-only probe、一次 text live probe、一次 vision pixel probe、延遲／OOM／重啟與 200 題 gold comparison 前，`QWEN_MLX_ENABLED` 必須維持 `0`，profile 只能是 shadow/test。
