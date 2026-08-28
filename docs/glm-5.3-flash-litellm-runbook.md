# GLM-5.3-Flash × LiteLLM 審核工作流操作手冊

更新日期：2026-08-28（Asia/Taipei）
適用範圍：AI395 staging／既有 MinerU-parser 產物；不代表 AI395 production 已開放模型寫入。

## 目前決策

目前五條審核 lane 的 primary 都是 `glm-5.3-flash-litellm`：

```text
text_evidence / notation / group / vision / answer
                         ↓
              LiteLLM OpenAI-compatible API
                         ↓
                 glm-5.3-flash
```

Qwen 3.8-27B MLX 保留為 MacBook shadow／測試 provider；舊的 DeepSeek、Gemma、Kimi route 設定不再是目前主流程的 primary 或 challenger。`mock` 仍保留給離線 unit test 與不帶 secret 的 walking skeleton。

模型輸出永遠是 advisory：不能直接接受、封鎖、排除、reset、改題目、改答案、覆蓋圖片或寫入正式人工 review event。只有人工 ReviewUI 決策或明確的 deterministic rule 才能改變正式狀態。

## 1. LiteLLM 測試環境設定

`tw-national-exam-catalog-v1` 是 key 的識別名稱，不是要提交到版本庫的 secret。實際 key 只放在目前 shell、AI395 的 mode-600 EnvironmentFile 或 secret manager。

```bash
export LITELLM_BASE_URL='https://<internal-litellm-host>/v1'
export LITELLM_MODEL='glm-5.3-flash'
export LITELLM_GLM_ENABLED=1
export LITELLM_REASONING_EFFORT='low'
export LITELLM_SEND_REASONING_EFFORT=0
export LITELLM_SEND_THINKING=0
export LITELLM_CLEAR_THINKING=0

# 互動式輸入，不要把 secret 寫進 command history 或聊天內容。
read -r 'LITELLM_API_KEY?LiteLLM test key: '
export LITELLM_API_KEY
```

`LITELLM_BASE_URL` 應指向 LiteLLM 的 `/v1` base URL。遠端服務必須使用 HTTPS；只有同機 `localhost`／`127.0.0.1` 可以使用 HTTP。若測試 gateway 確實只有私有網段 HTTP，必須在探針及 runner 各自明確加上 insecure opt-in，不能由設定默默放行。

檢查 shell 是否有 key，但不顯示 key：

```bash
test -n "${LITELLM_API_KEY:-}" && echo 'LITELLM_API_KEY is configured' || echo 'LITELLM_API_KEY is missing'
```

## 2. 先做 transport probe

只看 `/models`，不會產生模型內容：

```bash
python3 scripts/probe_litellm_glm.py
```

確認文字與 JSON contract：

```bash
python3 scripts/probe_litellm_glm.py --live
```

確認真正有傳入一張 raster pixels：

```bash
python3 scripts/probe_litellm_glm.py --live --image /path/to/small-test.png
```

GLM vision 的圖片 transport contract 是嚴格的：輸入檔必須是真實 PNG bytes，
OpenAI-compatible request 的 URL 必須以 `data:image/png;base64,` 開頭。adapter
不會把 JPEG／WebP bytes 偽裝成 PNG；若 MinerU 產生其他格式，必須先增加可追蹤的
deterministic PNG conversion node，例如：

```bash
python3 scripts/normalize_vision_assets_to_png.py \
  --source-manifest /path/to/scope/source_manifest.json \
  --mineru-manifest /path/to/scope/mineru_manifest.json \
  --output-dir /path/to/scope/png-view
```

它不修改原始 MinerU JPG／PNG，會產生新的 candidate／manifest／PNG asset view
與 `asset_conversion_report.json`；之後 staging runner 應使用這組新 manifest。
若轉檔失敗，該題停在 `pixels_unavailable`／人工例外。

probe 通過只代表 endpoint、key、model alias、JSON response 與視覺 transport 可用；不代表五條 lane 已通過 gold certification。

## 3. Context 與模型參數

GLM 官方宣稱 context 1M、最大輸出 128K；本 workflow 不因此把整份 PDF 或整個考試批次送入模型。官方文件也指定圖片以 `messages[].content[]` 的 `image_url`／Base64 Data URL 傳入，thinking 僅能啟用，並建議 `clear_thinking=false`。[Z.AI GLM-5.3-Flash 文件](https://docs.z.ai/guides/vlm/glm-5.3-flash)

目前 admission policy：

| 項目 | 設定 |
|---|---:|
| 預設 hard limit | 262,144 tokens（256K） |
| safety margin | 8,192 tokens |
| 單次 extension | 393,216 tokens（384K） |
| 每 lane extension 次數 | 1 |
| output 預留 | `--model-max-tokens` |
| reasoning 設定 | 記錄為 `low`，但預設不送 `reasoning_effort` |
| thinking | 預設不送；只有 gateway 明確支援時才設 `LITELLM_SEND_THINKING=1` |

context guard 使用 UTF-8 JSON byte upper bound，在 transport 前 fail-closed。若第一次超過 256K，GLM lane 只自動重試一次、上限 384K；仍超過則進人工 exception，不會無限放大。可由 `pipeline.yaml`、provider profile 或 CLI 調整，但每次調整都必須留下新的 config SHA。

若 LiteLLM gateway 不接受 `thinking` 或 `reasoning_effort` 額外欄位，保持
`LITELLM_SEND_THINKING=0` 與 `LITELLM_SEND_REASONING_EFFORT=0`，讓 adapter 與
probe 都不送該欄位。只有 gateway 明確允許後才逐項開啟，再以 probe 驗證；不要
刪除 context guard 或繞過 structured output。

## 4. 以既有產物跑 staging

只有 probe 通過後才執行 live staging。以下從既有 source／MinerU manifest 開始，下載與 MinerU node 仍是 disabled，不會重跑上游：

```bash
python3 scripts/ai395_review_staging.py e2e \
  --source-manifest /private/tmp/<scope>/source_manifest.json \
  --mineru-manifest /private/tmp/<scope>/mineru_manifest.json \
  --database-url sqlite:///private/tmp/<run>.sqlite3 \
  --artifact-dir /private/tmp/<run>-artifacts \
  --run-id <unique-run-id> \
  --source-mode existing_artifact \
  --mineru-mode existing_artifact \
  --model-mode litellm_glm \
  --allow-live-provider \
  --llm-lane-policy residual \
  --context-limit-tokens 262144 \
  --context-safety-margin-tokens 8192 \
  --model-max-tokens 4096 \
  --max-tool-turns 1
```

想先讓每個 lane 都進行模型測試時才使用 `--llm-lane-policy all`；大批次第一次不要用 all。`--llm-max-calls` 是總 call budget，應先用小批次與低上限確認 latency、usage、JSON adherence、vision pixels 與 exception 比例。

GLM-5.3-Flash 可能先把 completion budget 用在 `reasoning_content`；實測 512
tokens 會以 `finish_reason=length` 結束而沒有 final JSON。因此 staging 預設為
4096，可依模型 profile 調整；若再次出現 JSON parse error，先檢查
`finish_reason`、`content` 長度與 `usage.completion_tokens`，不要直接放寬 context。

檢查：

```bash
cat /private/tmp/<run>-artifacts/<unique-run-id>/summary.json
cat /private/tmp/<run>-artifacts/<unique-run-id>/run_manifest.json
cat /private/tmp/<run>-artifacts/<unique-run-id>/formal_dry_run.json
```

必須看到 `model_provider=litellm_glm`、`model_name=glm-5.3-flash`、`production_write_count=0`，且 summary 不包含 API secret。`llm_context_extensions`、`llm_context_rejections`、`llm_slow_calls` 用來判斷是否需要修正 packet，而不是直接放寬上限。

## 5. ReviewUI 觀察順序

把同一個 staging run 的 candidates、issue CSV、summary、三證據與 repair log 接到新版 ReviewUI。先看：

1. model identity、lane status、latency、context guard。
2. `vision` 是否真的有 pixels，並比較 MinerU crop 與 PDF page。
3. GLM finding 是否仍是 advisory，沒有偽造人工事件。
4. revision 是否建立新版本，affected lanes 是否重新列管。
5. formal dry-run 是否仍為零 production writes。

若有 `provider_error`、`context_budget_exceeded`、`context_extension_retry` 後仍失敗、`pixels_unavailable` 或大量 JSON parse error，先停批次並保存 artifact；修正 adapter、profile、prompt 或 fixture 後，用新 run／新 config SHA 重跑。

## 6. 模型切換點

所有節點的模型接入都由下列版本化檔案控制：

```text
configs/ai395_review_pipeline/provider_registry.yaml
configs/ai395_review_pipeline/route_registry.yaml
configs/ai395_review_pipeline/pipeline.yaml
docs/skills/national-exam-ai-audit/profiles/glm-5.3-flash-litellm.yaml
scripts/ai395_llm_adapter.py
```

要換模型時只改 profile、provider env、route registry，先 probe，再做該 lane gold／shadow；不要在 n8n node 內硬編碼模型名稱。`scripts/probe_litellm_glm.py` 只驗證 LiteLLM/GLM contract；正式品質仍以三證據、PDF pixels、人工 gold 與 ReviewUI 結果為準。

## 7. 離線驗證與治理

```bash
python3 -m unittest discover -s tests -q
python3 scripts/validate_agent_governance.py
git diff --check
```

GLM 目前是 staging primary candidate，不是「模型說正確就自動入庫」。通過 transport probe、gold metrics、人工抽樣與 release review 後，才可由 owner 另外批准 production 路由。
