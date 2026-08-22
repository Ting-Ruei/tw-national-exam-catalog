# ReviewUI workflow console

這是新的題目審核工作台。它從既有的 MinerU/parser 產物開始，不負責下載 PDF 或重新執行 MinerU；這兩個 upstream node 仍由 contract 與 disabled flag 控制。ReviewUI 只讀取 staging／JSONL／三證據產物，人工決策才會透過明確的 review API 寫入 append-only review log。

## 啟動隔離 staging ReviewUI

以下命令使用 real staging bundle；路徑請依實際 run 替換。它不連線 AI395 PostgreSQL，也不會把 Qwen 的 advisory 直接寫成正式審核結果。

```bash
ASSET_ROOT='/Users/tim/AI workspace/ai_learning_platform/tw-national-exam-catalog/國考題資料夾' \
REVIEW_UI_BACKEND=jsonl REVIEW_UI_ALLOW_PROJECT_FILES=0 \
python3 scripts/serve_question_review_ui.py \
  --candidate-jsonl /private/tmp/<ui-bundle>/candidates.jsonl \
  --issue-csv /private/tmp/<ui-bundle>/issues.csv \
  --review-log /private/tmp/<ui-bundle>/question_review_events.jsonl \
  --run-summary /private/tmp/<artifact-run>/summary.json \
  --three-source-analysis /private/tmp/<three-source>/analysis.json \
  --three-source-packets /private/tmp/<three-source>/blind-packets.jsonl \
  --repair-log /private/tmp/<artifact-run>/repair_log.md \
  --host 127.0.0.1 --port 8767 --mobile-port 8768 --review-backend jsonl
```

桌面入口是 `http://127.0.0.1:8767/`，新版手機工作台是 `http://127.0.0.1:8768/` 或 `/mobile/workflow/`。既有快速分流頁保留在 `/mobile/` 作相容入口；桌面上的「手機快審」會導向新版工作台。舊版桌面只作相容對照，入口為 `/legacy/`，不應再作為主要審核畫面。

## 新 UI 的資料流

左側 queue 是互斥的 primary queue，同一題只會出現在一個主 queue，避免人工重複處理。排序優先序為：

1. `revision`：版本修訂或跨 lane 失效，需要重新確認。
2. `source`：parser／來源或三證據差異。
3. `answer`：答案、MOD、送分或多答案異常。
4. `vision`：有無圖片、圖片分類、裁切範圍與前後差異。
5. `group`：題組邊界或題組歸屬。
6. `notation`：上下標、特殊字元、數值與化學／醫學記號。
7. `text`：文字抽取 residual。
8. `sample`：clean 題，只作抽樣品質監控。

選取題目後，右側會同時顯示：

- 題目目前 revision 與 parser 狀態。
- 五條 lane 的 deterministic 結果、Qwen advisory、latency 與 context guard telemetry。
- 三證據 extractor 對照、consensus、差異分類與 blind packet。
- MinerU crop/contact sheet，以及官方 PDF page viewer。
- 修訂／失效歷史與人工 review events。

## 人工操作邊界

Qwen 只能產生 finding、證據請求或 proposal；UI 不會因為模型輸出而自動 accept、block 或 materialize 題目。人工按下決策時：

- `accept`：代表人工接受目前題目狀態；建議留下 reviewer notes，方便追蹤。
- `needs_review`：必須填寫原因，代表題目退回 PDF／修正 queue。
- `block`：必須填寫原因，代表題目暫停入庫。
- `exclude`：必須填寫原因，代表這筆不是可入庫題目。

圖片題的人工審核仍保留三類：誤判有圖、裁切正確、裁切錯誤。裁切錯誤時，UI 應先呈現官方 PDF／crop 前後證據，再由腳本或人工決定是否建立新 revision；不能只依模型說「座標應該在這裡」就覆蓋原始資產。

## Qwen context 與 agent 邊界

MacBook 的 `qwen3.8:27b-mlx` 以 OpenAI-compatible endpoint 接入 staging。每個 lane 只收到 allow-list packet，不把完整 raw block、秘密路徑或整份 PDF 送入模型。現行實測設定為：

- hard context limit：`196608` tokens（192K，仍低於模型宣稱的 256K）。
- safety margin：`8192` tokens。
- output：`256` tokens。
- slow threshold：`30` 秒；慢回應後下一 lane 自動改用 compact context。
- evidence tool turns：每 lane 最多 `1` 回。

所以這套流程可以讓模型提出額外證據需求，再由程式收集有限證據回傳；它是 bounded agent，不是可以任意讀寫檔案的 autonomous agent。所有工具請維持 allow-list，並把每次 tool turn 寫進 lane telemetry。

## 正確的 real existing-artifact staging 命令

注意：同時提供 `--source-manifest` 與 `--mineru-manifest` 時，runner 會優先使用這對既有產物；只有未提供 manifest 時才使用預設的 `mini20` fixture。這避免把 real run 靜默降級成 fixture。

```bash
QWEN_MLX_ENABLED=1 \
QWEN_MLX_BASE_URL='https://<macbook-tailnet-host>/v1' \
QWEN_MLX_MODEL='qwen3.8:27b-mlx' \
python3 scripts/ai395_review_staging.py e2e \
  --source-manifest /private/tmp/<scope>/source_manifest.json \
  --mineru-manifest /private/tmp/<scope>/mineru_manifest.json \
  --database-url sqlite:///private/tmp/<run>.sqlite3 \
  --artifact-dir /private/tmp/<run>-artifacts \
  --run-id <unique-run-id> \
  --source-mode existing_artifact \
  --mineru-mode existing_artifact \
  --model-mode local_qwen_mlx \
  --allow-live-provider \
  --llm-lane-policy residual \
  --context-limit-tokens 196608 \
  --context-safety-margin-tokens 8192 \
  --model-max-tokens 256 \
  --max-tool-turns 1
```

完成後先看 `summary.json`、`run_manifest.json`、`formal_dry_run.json`，再把同一 run 的 candidates 與 evidence bundle 接到 ReviewUI。若看到 `context_budget_exceeded`、持續 slow、`provider_error` 或大量 `pixels_unavailable`，先修 adapter／fixture／asset contract，再擴大批次。

## 驗證

```bash
python3 -m unittest discover -s tests -q
python3 scripts/validate_agent_governance.py
git diff --check
```

生產部署仍需走 AI395 release／review 流程；本文件的 localhost staging 不代表已取得 AI395 production writer 權限。
