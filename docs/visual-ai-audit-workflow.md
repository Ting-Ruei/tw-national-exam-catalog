# 圖片審核流程

圖片審核只保留三個人工結論：

- `有圖`：目前圖片、表格截圖或人工補圖正確，寫入 `visual_review=visual_asset_ok`。
- `錯圖待改`：圖片缺漏、裁切錯誤、綁錯位置或需要人工補圖，寫入 `visual_review=visual_asset_problem`。
- `沒有圖`：題目不需要圖片或表格資產，寫入 `visual_review=no_visual_required`。

`待處理` 不是資料庫結論，而是 Review UI 用來找出尚未人工判斷、且可能與圖片或表格有關的題目。

`待處理` 主清單只放需要人工看的題目：

- 已有 MinerU 圖片、表格或其他圖片資產，需要確認裁切與綁定是否正確。
- AI 檢查為可能需圖或不確定。
- 尚未 AI 檢查，但 Python / SQL 寬篩抓到明確圖片或表格線索。

如果 AI 已判斷「可能不需要圖」，且題目本身沒有圖片或表格資產，則不進入人工圖片待審主清單。AI 只是在這裡幫忙排除低價值人工審核量，不會自動寫入 `visual_review`。

## AI 的角色

AI 只做檢查與導流，不做人工背書。

允許 AI 或其他模型輸出圖片 advisory，例如：

- 這題可能真的需要看圖。
- 這題只是文字提到影像、心電圖或箭頭概念，可能不需要圖。
- 這題不確定，需要人工看 PDF。

這些結果只追加到 `exam.question_ai_review_events`，不會寫入 `visual_review`，也不會讓題目自動通過、阻擋或標成沒有圖。Review UI 主流程不再提供 `AI 建議需圖`、`AI 建議不需圖`、`AI 不確定` 等篩選，也沒有 `接受本頁 AI 不需圖` 按鈕。

## 候選來源

圖片頁的 `待處理` 主要來自：

- 題目已有 MinerU 圖片、表格截圖或人工補圖。
- 題幹或原始區塊出現明確圖片/表格線索。
- parser 偵測到結構化表格，但資料庫預覽不應直接依賴不完整文字表格。
- AI advisory 或批次掃描提示這題值得人工看 PDF。

這些來源只決定題目要不要進入圖片頁，不代表審核結果。

## 匯出 AI 檢查任務

若要用 Codex、ChatGPT MCP、OpenAI API、本地模型或其他模型重新篩候選，可用：

```bash
python3 scripts/export_visual_ai_audit_batch.py \
  --category 醫事檢驗師 \
  --subject 臨床生理學與病理學 \
  --source all \
  --chunk-size 100
```

腳本只輸出 task JSONL，不呼叫模型、不寫資料庫。輸出位置：

```text
國考題資料夾/30_normalized_items/visual_ai_audit_tasks/
```

## AI 輸出格式

任何模型都可以使用，只要每行輸出 JSON：

```json
{"candidate_key":"...","visual_status":"visual_required_likely","confidence":0.86,"reason":"題幹寫明心電圖如下，需要對照圖。","evidence":"緊急心電圖如下","recommended_human_action":"review_pdf_visual"}
```

允許的 `visual_status`：

- `visual_required_likely`
- `visual_not_required_likely`
- `visual_uncertain`

這些值只是 advisory，不能直接當人工審核結論。

## 匯入 AI Advisory

```bash
python3 scripts/import_visual_ai_audit_results.py \
  國考題資料夾/30_normalized_items/visual_ai_audit_tasks/<run>/chunks
```

匯入只追加 `exam.question_ai_review_events`：

- `labels` 保留模型判斷。
- `visual_status` 保留模型判斷。
- 不寫 `exam.question_review_events`。
- 不寫 `visual_review`。

## Review UI 操作

進入 `圖片` 模式後只看四種篩選：

- `待處理`
- `有圖`
- `錯圖待改`
- `沒有圖`

每題只按三種人工按鈕：

- `有圖正確`
- `圖片錯要改`
- `沒有圖`

若圖片錯誤，可以在同一題使用補圖區把截圖貼到 `題幹`、`A`、`B`、`C`、`D`、`表格` 或 `題組共用`。人工補圖本身會被視為圖片資產，但題目能否正式入庫仍由審題與答案核對決定。
