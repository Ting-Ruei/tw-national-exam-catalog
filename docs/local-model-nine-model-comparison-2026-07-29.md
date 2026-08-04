# 本地九模型文字初審比較（2026-07-29）

## 結論

固定 80 題的正式比較已完成。半份或全份送入並沒有普遍提高糾錯效果；大批次反而常使
JSON 結構失效、輸出截斷或召回下降。

目前建議只讓三個模型進入下一階段的 advisory shadow mode：

1. `gemma4:31b-mlx`：每批 16 題，作為精準型 OCR／parser 抓漏模型。
2. `qwen3.6:27b-mlx`：每批 40 題，作為召回型 finding-only 模型；不得單獨產生可信
   correction。
3. `qwen3.6:35b-mlx`：每批 40 題，作為快速第二讀者；需抑制醫學事實核對型誤報。

`gemma4:12b-mlx` 與 `qwen3.5:9b-mlx` 雖有完整批次，但誤報過多；其餘四個模型沒有任何
完整覆蓋 80 題的批次方案。`gpt-oss:20b` 另有 Ollama structured-output 相容性問題。

所有輸出都只寫入 `/private/tmp` preview，沒有修改人工標記、匯入 AI review event、
執行 `reset_review`，或把題目丟回未審。

## 測試方式

- 題本：115 年第 2 次醫事檢驗師，臨床血液學與血庫學，80 題 frozen candidates。
- 批次：16 題（5 chunks）、24 題（4 chunks）、40 題（2 chunks）、80 題（1 chunk）。
- 每批依序執行 `ocr_text`、`meaning`；`concurrency=1`。
- `temperature=0`、`num_ctx=65536`、`timeout=1200` 秒。
- 圖片題只明確路由原有人工圖片流程；本卷沒有題組候選。
- 任一 lane 未通過 JSON、欄位、證據或 `done_reason` 驗證，整個 chunk 即失敗，不產生
  Review UI preview。
- 完整時間是跑遍 80 題全部 chunks 的兩個文字 lane 合計，不是單一 request 的時間。

## 九模型結構穩定性

| 模型 | 16 題 | 24 題 | 40 題 | 80 題 | 最佳完整方案 |
|---|---:|---:|---:|---:|---|
| `qwen3.6:35b-mlx` | 4/5 | 3/4 | 2/2 | 1/1 | 40 題 |
| `qwen3.6:27b-mlx` | 5/5 | 3/4 | 2/2 | 0/1 | 40 題 |
| `gemma4:31b-mlx` | 5/5 | 4/4 | 1/2 | 0/1 | 16 題 |
| `gemma4:12b-mlx` | 5/5 | 3/4 | 1/2 | 0/1 | 16 題 |
| `qwen3.5:9b-mlx` | 4/5 | 4/4 | 2/2 | 0/1 | 40 題 |
| `gemma4:26b-mlx` | 2/5 | 0/4 | 1/2 | 0/1 | 無 |
| `medgemma:27b` | 1/5 | 0/4 | 0/2 | 0/1 | 無 |
| `medgemma:4b` | 2/5 | 0/4 | 0/2 | 0/1 | 無 |
| `gpt-oss:20b` | 0/5 | 0/4 | 0/2 | 0/1 | 無 |

分母是覆蓋完整 80 題所需 chunks，分子是 OCR 與題義兩路均完整的 chunks。

## 最佳方案的時間與抓漏品質

目前人工確認的六個 candidate／pipeline 缺陷為 q005 題幹、q005 D、q039、q035、q052、
q061。這只是小型 known-defect set，不是完整 gold，因此下表只報「命中數」，不把它包裝成
正式正確率。

| 模型 | 批次 | 全卷時間 | 文字 findings | 已知缺陷命中 | 品質判斷 |
|---|---:|---:|---:|---:|---|
| `gemma4:31b-mlx` | 16 | 163.29 s | 5 | 3/6 | 本輪精準度最佳；可進 shadow |
| `qwen3.6:27b-mlx` | 40 | 141.78 s | 8 | 4/6 | 召回最佳；replacement 曾嚴重出錯 |
| `qwen3.6:35b-mlx` | 40 | 24.36 s | 12 | 2/6 | 極快，但醫學事實型誤報多 |
| `qwen3.5:9b-mlx` | 40 | 49.83 s | 11 | 1/6 | 快，但 lane 偏移與誤報多 |
| `gemma4:12b-mlx` | 16 | 63.57 s | 15 | 2/6 | 低精準度，不建議加入 |

沒有完整方案的模型不列入品質排名。完整時間包含模型首次載入及所有 chunks，適合評估實際
非同步工作流，而不是只看單題生成速度。

## `gemma4:31b-mlx`

16 題方案 5/5 完整，五個 finding 都值得人工核對：

- q005 D：抓到 `纍胺酸 → 纈胺酸`，已能安全物化選項 correction。
- q039：抓到選項 B–D 被 parser 黏合，只保留 finding。
- q052：抓到 `Cᵃ²⁺ → Ca²⁺` markup 轉換錯誤，已能物化 correction。
- q014：抓到官方原卷疑似錯字 `nomoblast`，但證據未逐字定位，因此不產生 correction。
- q072：抓到 `CDPA-1 → CPDA-1`；此字串原本就存在官方 PDF，必須標為
  `source_original_suspected_typo`，不能當 OCR 錯誤自動套用。

它漏掉 q005 題幹 `鎂細胞貧血症`、q035 空格及 q061 `强化`。24 題雖然 4/4 完整，卻新增
`HLA class Ⅱ → II`、`thromboxane A₂ → A2`、`Anti-Miᵃ → Anti-Mii` 等低價值或錯誤
建議，因此選 16 題，不選 24 題。

40 題第二批出現多餘 JSON；80 題的題義欄位無效。增加 timeout 不會修正這些問題。

## `qwen3.5:9b-mlx`

40 題方案 2/2 完整且只需 49.83 秒，但大部分 finding 不是文字糾錯：

- 正確抓到 q039 選項黏合。
- 把 q010、q018、q025、q031、q037 等題的醫學內容判成缺段或邏輯斷裂。
- 對 `PT`、`CPDA-1`、`FXIII` 等正確字串仍輸出 issue，甚至在 note 中說「無需修正」。
- 將可渲染的 HTML 表格同時判成 OCR 格式異常及題義缺漏。

24 題雖 4/4 完整且更快，但只在第一批回報三個題義 finding，OCR 全卷為零；這是低召回，
不是高精準。80 題兩路都跑滿 1,024 tokens 並截斷。

## 其餘四個新測模型

### `gemma4:26b-mlx`

16、24、40、80 題都沒有完整覆蓋全卷的方案。錯誤包含 invalid JSON、invalid confidence
與 invalid code，先排除。

### `gemma4:12b-mlx`

16 題 5/5 完整，但 15 個 finding 中大量把正常醫學字串與符號當 OCR 問題，例如
`chromosome 5q deletion`、`vitamin B₁`、`Fe³⁺`、`fresh frozen plasma`。它抓到 q005
題幹及 q039，但漏 q005 D 與 q061；不值得增加 Review UI 人工負擔。

### `medgemma:27b` 與 `medgemma:4b`

兩者常跑滿 1,024 output tokens，並接近逐題輸出 issue。27B 的 16 題方案只有 1/5
完整；4B 只有 2/5，其餘批次均無完整方案。醫療領域模型不等於適合 OCR 抓漏，兩者排除。

### `gpt-oss:20b`

Ollama 一般 prompt 可正常回 JSON，但傳入 JSON Schema 或 `format: "json"` 時會以
HTTP 200 回空字串。新增 `--ollama-format prompt-only` 後，模型可開始生成，但
`think=false` 仍將每個 lane 的 1,024 tokens 全耗在 thinking，`response` 保持空白，
所有批次仍為 0 完整。

這與 LLM Share 的 `gpt-oss:120b` 行為一致：可見 JSON 很短不代表 hidden reasoning 已關閉。
不應只為它提高 output token 上限，否則會重新引入大量無用生成。

## 半份與全份是否比較準

本輪答案是否定的：

- 35B 全份雖結構成功，但已知缺陷召回下降且題義誤報增加。
- 27B 全份 OCR 截斷。
- Gemma 31B 半份只有 1/2 完整、全份失敗。
- Gemma 12B 半份與全份均無完整方案。
- Qwen 9B 半份完整但精準度低，全份兩路截斷。
- 其餘模型沒有可用的大批次。

較大的上下文可讓模型看到更多題，但不會自動讓它更專注於 OCR；題目越多，越容易觸發解題、
長篇自我檢查、欄位錯誤或輸出截斷。批次大小必須依模型個別設定。

## Review UI 納入 gate

下一階段仍只做 advisory shadow：

1. 不修改既有人工標記，不自動接受、不自動封鎖、不自動 `reset_review`。
2. 模型結果先顯示建議；使用者核准抓漏範圍後，才可把精確題目丟回未審。
3. `evidence_validated=false` 只顯示 finding，不提供 correction。
4. correction 至少要命中人工錯字 list、兩模型同意，或已有官方 PDF／MinerU 證據。
5. source provenance 必須區分 `candidate_mismatch`、`mineru_ocr_mismatch`、
   `parser_error`、`source_original_suspected_typo`。
6. q005 parser 規則與 q039 option 邊界應先從 MinerU／parser 上游修正；重建已審題目時，
   保存舊 review 並依規則追加 per-question `reset_review`，不能直接覆寫。
7. deterministic Python 規則先處理簡體字、已確認錯字與可重現 parser pattern，再把殘餘題目
   交給本地模型。

## 可重現命令與產物

正式 runner：

```bash
python3 scripts/run_local_question_audit_benchmark.py \
  --tasks /private/tmp/tw-national-exam-local-pilot-0501/question__part0001.jsonl \
  --output-root /private/tmp/local-model-formal-benchmark-20260729 \
  --model gemma4:31b-mlx
```

runner 預設 sequential、可續跑，並支援 `--batch-sizes` 與
`--ollama-format schema|json|prompt-only`。新增模型可直接套用同一個 16／24／40／80
矩陣；不同 transport mode 應使用不同 output root，避免把結果混在一起。

主要產物：

- `/private/tmp/local-model-formal-benchmark-20260729`
- `/private/tmp/local-model-formal-benchmark-20260729-prompt-only`
- 兩個 Qwen 3.6 的早期完整產物列於
  [`local-qwen36-batch-comparison-2026-07-29.md`](local-qwen36-batch-comparison-2026-07-29.md)。
