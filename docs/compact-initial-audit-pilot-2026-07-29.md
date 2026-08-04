# 四路 compact 初審 pilot（2026-07-29）

## 結論

舊 verbose v2 不適合文字抓漏。新 `compact_initial_question_audit_v1` 已改為四個獨立通道：

- `ocr_text`：OCR 字形、簡體字、錯別字、符號。
- `meaning`：轉寫造成的句意斷裂、漏段與選項黏合。
- `visual`：圖片依賴、缺圖、裁切及配錯。
- `group`：題組範圍、共同題幹與承接關係。

模型正常題不逐題輸出，只回 `batch_id / checked_count / issues`。本機 validator 負責索引、
evidence、replacement 與 finish reason；只有安全 OCR 局部替換可由本機 materialize 成
Review UI 的 `suggested_correction`。所有結果仍是 advisory，不會改動人工標記。

## 本地 `qwen3.6:35b-mlx`

測試資料為 frozen 115-2 醫事檢驗師 0501 前 8 題。最終安全 run：

| 通道 | 適用題數 | 延遲 | TTFT | prompt tokens | eval tokens | 結果 |
|---|---:|---:|---:|---:|---:|---|
| OCR 文字 | 8 | 2.176 s | 0.253 s | 1,292 | 209 | 結構完成，2 findings |
| 題義 | 8 | 1.999 s | 0.758 s | 1,350 | 136 | 結構完成，1 finding |
| 圖片題 | 1 | 未呼叫 | — | — | — | vision transport probe 失敗，明確轉人工 |
| 題組題 | 0 | 未呼叫 | — | — | — | 本批無適用候選 |

OCR 與題義均為 `done_reason=stop`、`thinking_bytes=0`，沒有截斷。文字延遲沒有過度異常。

本地模型抓到：

- q39 選項 B–D 黏合，但模型的 `observed` 不是原欄位的連續字串，因此只保留 finding，
  不產生 correction。
- q5 題幹「鎂細胞貧血症」疑似 OCR 問題；模型提出的 replacement 不是安全局部 patch，
  validator 已移除 replacement。

圖片 probe 顯示 Ollama 雖宣告模型具 `vision` capability，實際模型只收到 `[img-0]`
placeholder，無法讀取同一張長圖中的 A–D 分圖。因此本模型不可執行圖片內容審查；不得把其
錯誤圖片判斷匯入 Review UI。

最終安全 preview：

`/private/tmp/qwen36-compact-four-lane-8-final/review_ui_results_preview.jsonl`

## LLM Share 純文字 pilot

LLM Share connector 不能附圖片，因此只傳同一批 8 題的 OCR 與題義文字。六模型依序、
concurrency 1，每個模型僅一個 request。

| 模型 | 約略 wall time | completion tokens | 結構 | 已知缺陷表現 | gate |
|---|---:|---:|---|---|---|
| `gemma4:31b` | 8.3 s | 305 | 有效 JSON | 抓到 q5 option D `纍→纈`、q39 選項黏合 | 可進下一輪 |
| `minimax-m2.7` | 21.8 s | 646 | 有效 JSON | 0 finding，已知缺陷全漏 | recall 不合格 |
| `deepseek-v4-flash` | 11.7 s | 1,200 | 無 final JSON | 生成思考文字後截斷 | 協定不合格 |
| `qwen3.5:397b` | 27.6 s | 1,755 | 有效 JSON | 抓到 q5 兩處與 q39；q 為字串且 patch 不安全 | 僅可搭 validator |
| `gpt-oss:120b` | 23.9 s | 1,200 | 無 final JSON | 生成思考文字後截斷 | 協定不合格 |
| `minimax-m3` | 25.2 s | 1,200 | 無 final JSON | 生成思考文字後截斷 | 協定不合格 |

`gemma4:31b` 顯示精簡協定已把可見 completion 降到合理量級。另一方面，三個 reasoning
模型仍會在 connector 中把 token 用於思考，證明「縮短可見 JSON」不保證所有模型都能關閉
hidden reasoning。未取得 final JSON 的 request 不立即重試。

Gemma 的有效 JSON 已通過同一個本地 validator，並物化為純預覽：

`/private/tmp/llmshare-gemma4-compact-8-preview-v2/review_ui_results_preview.jsonl`

q5 option D 的 `纍胺酸 → 纈胺酸` 已形成完整選項 correction，選項的 `text`、
`markup.plain` 與 `markup` 三者同步；q39 選項黏合只保留 finding，不自動改寫。
此步沒有匯入任何 AI review event。

## Review UI 與資料安全

- 新 runner 只產生 preview，不包含 event import 或 `reset_review` 寫入。
- Review UI event 內保存 `checks.ocr_text / meaning / visual / group` 四路狀態。
- 圖片模型不可用時顯示 `unavailable`，並路由原圖片人工流程，不會偽裝為 pass。
- 已人工審過的題目仍鎖住「套用 AI 建議校正」，必須先由使用者批准精確抓漏範圍。
- 題義、圖片與題組通道永不產生文字 correction。

## 下一個 gate

1. 先由人工核對本批 q39、q5 與 q16，建立小型 known-defect gold。
2. 下一輪優先測 `gemma4:31b`，`qwen3.5:397b` 僅保留為有 validator 的候選。
3. `minimax-m2.7` 先排除；另外三個 reasoning 模型在 gateway 可控制 reasoning 前不進全量。
4. 另找實際可接收 pixels 的 vision 模型，再測圖片內容；目前僅做 deterministic 圖片路由。
5. 找至少一份真實題組卷驗證 group 通道，不能以本批「無題組」宣稱題組功能已通過。

## 本地批次後續比較

`qwen3.6:35b-mlx` 與 `qwen3.6:27b-mlx` 的 16、24、40、80 題正式比較、官方 PDF
逐題比對及加入審核環節的 gate，另見
[`local-qwen36-batch-comparison-2026-07-29.md`](local-qwen36-batch-comparison-2026-07-29.md)。

後續擴充至九個本地模型的完整矩陣、`gpt-oss:20b` Ollama 相容性診斷及模型別建議批次，
另見
[`local-model-nine-model-comparison-2026-07-29.md`](local-model-nine-model-comparison-2026-07-29.md)。
