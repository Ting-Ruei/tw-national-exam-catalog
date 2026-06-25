# ChatGPT / LLM 協作通道規劃

本專案把「ChatGPT 協助 Codex 開發」和「Review UI 呼叫 LLM 協助審題」分成兩條通道，避免權限、成本與審核責任混在一起。

## 通道一：ChatGPT 連入本機專案

參考 `Waishnav/devspace` 的做法：在本機啟動一個 MCP server，讓 ChatGPT 透過受控工具讀取、搜尋、編輯專案並執行命令。DevSpace 的 README 描述它是 self-hosted MCP server，讓 ChatGPT 操作本機允許的專案資料夾；啟動後通常透過 `/mcp` endpoint 連入，並用 owner password 授權。

建議用途：

- 讓 ChatGPT 讀 repo、幫忙規劃 parser 或 UI 修改。
- 把大型討論或架構設計交給 ChatGPT，減少 Codex 主線 thread 的 token 消耗。
- 讓 ChatGPT 產生草案，再由 Codex 在本機實作、測試與 commit。

安全原則：

- 只把必要的 workspace root 加進允許清單。
- 不讓 ChatGPT 直接碰 API key、資料庫密碼或未公開的課本資料。
- 任何程式碼修改仍以 Git diff、測試與人工確認為準。

## 通道二：Review UI 呼叫 LLM 做格式稽核

Review UI 新增 `AI 格式稽核`，它只檢查 candidate 的格式與字形疑點，不判斷學科答案正確性，也不會自動改變人工審核狀態。

目前稽核範圍：

- 疑似 OCR 字形錯誤或簡繁混用。
- 希臘字母、上下標、LaTeX / HTML markup 是否需要人工確認。
- 選項數量、選項代號重複、括號不平衡。
- 題文提到圖表但 candidate 沒有圖片或表格資產。
- MinerU table markup 是否適合改掛 manual asset。

輸出位置：

- `question_ai_review_events.jsonl`：目前 Review UI 寫入的 append-only AI 稽核紀錄。
- `exam.question_ai_review_events`：未來入庫用 PostgreSQL schema。
- `exam.model_runs`：未來記錄模型 provider、model、prompt version、token 與成本。

## 啟用 OpenAI API

若沒有設定 `OPENAI_API_KEY`，Review UI 會使用本機規則稽核，適合零成本測試流程。

若要讓它呼叫 OpenAI：

```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_REVIEW_MODEL="gpt-4.1-mini"
docker compose up -d --force-recreate review-ui
```

也可以把不含引號的變數放在專案 `.env`：

```dotenv
OPENAI_API_KEY=sk-...
OPENAI_REVIEW_MODEL=gpt-4.1-mini
OPENAI_REVIEW_TIMEOUT=45
```

API key 不應 commit 到 GitHub。

## 與人工審核的關係

AI 稽核結果只是一個輔助 flag：

- `pass`：AI / 本機規則未發現明顯格式疑點，不等於正式通過。
- `needs_review`：建議人工看一下，但不會自動 block。
- `block`：AI 認為格式風險高，但仍需人工決定是否阻擋入庫。

人工審核仍是唯一能讓題目進入下一關的依據。已經人工 `accept` 的題目，不會因 AI 舊註記被自動退回；除非 parser 或人工校正真的改變該題入庫欄位，才用 `reset_review` 單題退回。

## 後續延伸

同一個 `model_runs` 架構可以延伸到：

- 題目相似題關聯。
- RAG 詳解草稿。
- 詳解驗證與 citation 檢查。
- 概念圖規格產生。
- 記憶圖 / 教學圖生成前的 prompt 與 provenance 記錄。

原則是先存候選結果與來源，不直接覆蓋正式題庫；任何公開內容都不得包含課本原文或受版權限制的教材內容。
