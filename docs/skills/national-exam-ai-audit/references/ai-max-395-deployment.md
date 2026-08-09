# AI MAX 395 常駐部署

> 2026-08-09 14:47 +08:00 起 AI395 已是唯一 production Review UI/PostgreSQL writer。
> 隔離 restore drill、模型 shadow run 與 production 權限仍必須分離；以下 artifact-only
> AI audit 邊界繼續有效。

## 角色

AI MAX 是 production 與預設執行主機，但模型 worker 仍不持有 production DB 寫入權。
production Review UI 與人工事件權威在 AI395 production stack；restore drill 只寫隔離 volume，
Mac Studio 是停止狀態的 rollback standby。

```text
AI395 production read-only task snapshot
  -> frozen task + manifest + profile
  -> AI MAX inbox
  -> local deterministic preflight
  -> Ollama sparse batches
  -> raw response + results + token journal
  -> AI395 production-side validator
  -> correction preview / human review
```

不要將 PostgreSQL owner password、Review UI credential 或人工事件檔放進模型 task packet。

## 目錄約定

```text
/srv/national-exam-audit/
  inbox/<run_id>/
  running/<run_id>/
  completed/<run_id>/
  failed/<run_id>/
  assets/
  models/
```

每個 run 保留 task hash、packet hash、profile、prompt version、raw response、results、
validation report 與 token journal。重新執行時建立新 run id，不覆寫完成品。

## 執行

1. 使用 `compile_agent_packets.py` 產生 Ollama profile 的 run directory。
   初始候選為 `profiles/ai-max-qwen3.6-27b.yaml`；部署後先以 `ollama list`
   確認實際 tag，不能沿用 macOS MLX tag。
2. 將整個 run directory 同步至 AI MAX inbox。
3. 設定 `OLLAMA_BASE_URL`，預設為 `http://127.0.0.1:11434`。
4. 執行：

```bash
python3 scripts/run_ollama_shadow_batches.py \
  --run-dir /srv/national-exam-audit/running/<run_id> \
  --resume
```

5. 將 results、raw 與 journal 回傳權威端。
6. 在權威端執行 `validate_sparse_results.py`；失敗 batch 不得匯入或 materialize。

## 穩定性

- 初期每模型 concurrency 為 1；以實測顯存、context 與 timeout 再調整。
- 固定 model digest、prompt version、profile 與 batch size。
- runner 只接受 `transport=ollama` 的 request manifest。
- 每完成一批即原子更新 results 與 journal；中斷後用 `--resume` 跳過已完成 batch。
- 保留 Ollama `prompt_eval_count`、`eval_count` 與 duration，作為真實 token/效能指標。
- context overflow、invalid JSON、batch coverage 不符皆停止，保留 raw response。
- visual lane 必須確認模型支援圖片且 pixels 已同步；只有 metadata 時只能 route。

## 權限與回傳

- AI MAX outbound 只需模型下載與受控 artifact transfer。
- 不對外暴露 Ollama；若必須跨主機，使用內網防火牆或 SSH tunnel。
- 只回傳 immutable artifacts，不直接呼叫 Review UI POST API。
- 規則提案回傳為 `observed/proposed`；promotion 仍在權威端進行。
