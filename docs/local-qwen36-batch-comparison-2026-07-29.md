# 本地 Qwen 3.6 批次正式比較（2026-07-29）

## 測試範圍

- 模型：`qwen3.6:35b-mlx`、`qwen3.6:27b-mlx`
- 題本：115 年第 2 次醫事檢驗師，臨床血液學與血庫學，共 80 題 frozen candidates
- 單次 payload：16、24、40（半份）、80（全份）題
- 每個 payload 依序執行 `ocr_text` 與 `meaning`，`concurrency=1`
- `think=false`、`temperature=0`、`num_ctx=65536`
- 圖片題只路由人工圖片流程；本題本沒有題組候選
- 所有結果只產生 preview，沒有匯入 event 或執行 `reset_review`

先以 offset 0 比較單一 request 的容量，再用 `--offset` 讓每一種 batch size 都覆蓋完整
80 題。16 題為 5 chunks、24 題為 4 chunks、40 題為 2 chunks、80 題為 1 chunk。

## offset 0 單一 payload

| 模型 | 題數 | OCR | 題義 | 兩路合計 | prompt tokens | eval tokens | 結果 |
|---|---:|---:|---:|---:|---:|---:|---|
| 35B | 16 | 3.37 s | 2.70 s | 6.07 s | 4,642 | 338 | 完整 |
| 35B | 24 | 3.76 s | 3.17 s | 6.93 s | 6,587 | 336 | 完整 |
| 35B | 40 | 5.75 s | 4.49 s | 10.24 s | 10,510 | 416 | 完整 |
| 35B | 80 | 9.55 s | 10.81 s | 20.37 s | 21,073 | 702 | 完整 |
| 27B | 16 | 27.56 s | 21.75 s | 49.31 s | 4,642 | 557 | 完整 |
| 27B | 24 | 38.99 s | 25.59 s | 64.58 s | 6,587 | 568 | 完整 |
| 27B | 40 | 42.40 s | 35.77 s | 78.17 s | 10,510 | 567 | 完整 |
| 27B | 80 | 85.51 s | 58.57 s | 144.08 s | 21,073 | 1,226 | OCR 截斷，整批失敗 |

35B 是 `qwen3_5_moe`，27B 是 dense `qwen3_5`；模型總參數較少不代表本機推論較快。
兩者同為 `nvfp4`，但本機實測 35B 明顯較快。

27B 的 80 題 OCR 用滿 1,024 output tokens，`done_reason=length`，JSON 未閉合，因此
validator 正確阻止 Preview 物化，不能用增加 timeout 解決。

## 完整 80 題 chunk 比較

下表包含成功及失敗 chunks 的實際運算時間；任一 lane 未通過 validator，該 chunk 即不產生
Review UI preview。

| 模型 | batch size | chunks 完成 | 全卷時間 | prompt tokens | eval tokens | 結論 |
|---|---:|---:|---:|---:|---:|---|
| 35B | 16 | 4/5 | 42.87 s | 24,317 | 2,764 | 一批題義漏掉全部 `confidence` |
| 35B | 24 | 3/4 | 32.96 s | 23,500 | 2,101 | 一批題義 JSON 語法錯誤 |
| 35B | 40 | 2/2 | 24.36 s | 21,881 | 1,186 | 完整 |
| 35B | 80 | 1/1 | 20.37 s | 21,073 | 702 | 完整，但召回下降、題義誤報增加 |
| 27B | 16 | 5/5 | 180.48 s | 24,317 | 1,525 | 完整但最慢 |
| 27B | 24 | 3/4 | 195.24 s | 23,500 | 2,008 | 一批把長篇自我檢查塞入 `note`，JSON 無效 |
| 27B | 40 | 2/2 | 141.78 s | 21,881 | 880 | 完整 |
| 27B | 80 | 0/1 | 144.08 s | 21,073 | 1,226 | OCR 截斷 |

兩模型在這份題本的最佳單批大小都是 40 題。小批次不必然穩定，因為特定題目組合仍可能
誘發模型違反 schema；全份也不必然提升召回。

## 原卷證據與抓漏品質

| 題目 | candidate 問題 | 官方 PDF / MinerU 證據 | 35B | 27B | 分類 |
|---|---|---|---|---|---|
| q005 題幹 | `鎂細胞貧血症` | PDF=`鐮`；MinerU=`鎌`；parser=`鎂` | 四批皆漏 | 16/24/40 抓到，但建議錯改為 `鐡` | OCR 加 parser 規則錯誤 |
| q005 D | `纍胺酸` | PDF=`纈胺酸`；MinerU=`纍胺酸` | 四批皆漏 | 16/24/40 抓到；24/40 replacement 正確 | MinerU OCR 錯誤 |
| q039 | A–D 被解析成兩欄 | PDF、MinerU 都有完整 A–D；`B-cell` 被誤當 option B | 四批皆抓到 | 16/24/40 皆抓到 | parser 邊界錯誤 |
| q014 D | `nomoblast` | PDF、MinerU、candidate 都是 `nomoblast` | 四批皆抓到 | 24/40 抓到 | 官方原卷疑似錯字 |
| q025 D | `CARL` | PDF、MinerU、candidate 都是 `CARL` | 40 題抓到但報成 option C，evidence 無效 | 未抓到 | 官方原卷疑似錯字 |
| q035 | 中英文間空格遺失 | PDF 有空格，MinerU/candidate 遺失 | 只在 80 題抓到 | 未抓到 | 低嚴重度 OCR／排版 |
| q052 A | `Cᵃ²⁺` | PDF、MinerU markup=`Ca²⁺`；candidate markup 轉換錯誤 | 16/40 chunks 抓到 | 16/24/40 chunks 抓到 | parser markup 錯誤 |
| q061 D | `强化` | PDF=`強化`；MinerU/candidate=`强化` | 全部漏掉 | 全部漏掉，且曾錯指其他題的 `Miᵃ` | MinerU 簡體字 OCR |

共同前 16 題中的三個 OCR／parser gold（q005 題幹、q005 D、q039）：

- 35B 各批只抓到 q039，finding recall 為 1/3。
- 27B 的 16、24、40 題都抓到 3/3，但 q005 題幹 replacement 錯誤。
- 27B 24/40 題對 q005 D 的 replacement 正確。
- q014、q025 是官方原卷疑似錯字，需獨立標籤，不能混入 OCR 正確率。

較大批次沒有穩定提高品質：

- 35B 80 題仍漏 q005 兩處，並把 q014 的錯誤選項內容當成題義問題，以及出現 q034/q035
  題號混淆。
- 27B 80 題直接截斷。
- 27B 16 題把圖片選項題 q016 誤報為選項遺失；24 題時沒有此誤報。
- 35B 小批次多次違反「不要解題」指令，把 q040、q060、q067、q069、q072、q074、
  q076、q078、q080 的選項真假或醫學內容當成 OCR／題義問題。
- q072 的 `CDPA-1` 已存在於官方 PDF。即使模型提出 `CPDA-1` 看似合理，也必須標成
  `source_original_suspected_typo`，不能直接當 OCR correction。

## 是否能加入審核環節

現在只能加入 **advisory shadow mode**，尚不適合成為正式自動審核 gate：

1. 兩模型都以 40 題為單一 request。
2. 27B 作為主要 finding-only 讀者；它在本卷對 q005、q039、q052 的召回優於 35B。
3. 35B 只作為可選的快速第二讀者；它雖快，但漏掉 q005 且事實核對型誤報較多。
4. 兩模型均不得改人工狀態、不得自動接受、不得自動 `reset_review`。
5. 單模型 replacement 不應直接視為可信 correction。q005 的 `鎂 → 鐡` 已證明「局部字串
   可套用」不等於「修改正確」。
6. correction 按鈕至少需符合以下一項才物化：
   - 命中人工確認的錯字 list；
   - 兩個獨立模型給出完全相同的 observed、location、replacement；
   - 已由官方 PDF／可靠 source 比對確認。
7. `evidence_validated=false` 的 finding 可以顯示給人看，但不得產生 correction。
8. 來源必須分成 `candidate_mismatch`、`mineru_ocr_mismatch`、`parser_error` 與
   `source_original_suspected_typo`，避免把官方原卷疑似錯字誤當 OCR 錯誤。

40 題方案完整跑完 80 題時，27B 約 141.8 秒；兩模型順序跑完約 166.1 秒。這個延遲對
非同步初審合理，也不占用 LLM Share token。正式掛入 Review UI 前仍需完成 correction gate、
失敗 chunk 明確顯示、結果合併，以及至少多一份不同題本的 shadow validation。

## 已確認的上游問題

- `scripts/build_question_candidates_from_mineru.py` 目前含錯誤規則 `"鎌": "鎂"`；
  q005 不應在 candidate 階段被改成「鎂」。
- `INLINE_OPTION_RE` 會把 q039 option A 裡的 `B-cell` 誤認成新 option B。
- 這兩個 parser 修正會影響既有 reviewed candidate。依資料規則，修改 parser 後若重建題目，
  必須保存舊 review 並對受影響題目追加 `reset_review`；本次尚未修改 parser 或重建資料。

## 試跑產物

- `/private/tmp/qwen36-local-formal-35b-n16`
- `/private/tmp/qwen36-local-formal-35b-n24`
- `/private/tmp/qwen36-local-formal-35b-n40`
- `/private/tmp/qwen36-local-formal-35b-n80`
- `/private/tmp/qwen36-local-formal-27b-n16`
- `/private/tmp/qwen36-local-formal-27b-n24`
- `/private/tmp/qwen36-local-formal-27b-n40`
- `/private/tmp/qwen36-local-formal-27b-n80`
- 其餘完整覆蓋 chunks：`/private/tmp/qwen36-local-formal-{35b,27b}-bs{16,24,40}-o*`
