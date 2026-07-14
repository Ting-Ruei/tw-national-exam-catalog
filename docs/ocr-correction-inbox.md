# OCR 修正待確認清單

這是提供人工發現 OCR 問題的 Markdown 輸入區。你可以直接在表格新增一列，之後告訴 Codex：

> 請讀取 `docs/ocr-correction-inbox.md`，核對新增項目並處理。

Codex 會先對照官方 PDF，不會只根據字面猜測而套用規則。確認後才會：

1. 將規則寫入 `configs/text_normalization_rules.json`。
2. 對符合範圍的 SQL 題目做安全修復。
3. 保留原本的 `accept`、`block`、`needs_review` 或 `unreviewed` 狀態。
4. 追加修復事件，讓每次修改可以追蹤。

## 填寫格式

| status | source | target | scope | category | subject | evidence | note |
|---|---|---|---|---|---|---|---|
| `new` | `錯誤文字` | `正確文字` | `category` / `subject` / `global` | `藥師` |  | `113-2 第 40 題官方 PDF` | 看到的上下文或疑問 |

欄位規則：

- `status`：新發現填 `new`；已確認填 `confirmed`；不要套用填 `rejected`。
- `source`：MinerU 或網頁目前顯示的錯誤文字，必須精確貼上。
- `target`：你認為正確的文字；若不確定可以先留空，改在 `note` 描述疑問。
- `scope`：`global`、`category` 或 `subject`。不確定時先填 `category`，避免誤傷其他科目。
- `category`：可填一個或多個類科，使用逗號分隔，例如 `藥師,藥師(一),藥師(二)`。
- `subject`：只有確定是特定科目時才填。
- `evidence`：盡量填年份、考次、題號，方便回看官方 PDF。
- 一個錯誤詞一列；不要把多個不相關錯字合併在同一列。

## 目前已處理範例

| status | source | target | scope | category | subject | evidence | note |
|---|---|---|---|---|---|---|---|
| `confirmed` | `檠師` | `藥師` | `category` | `藥師,藥師(一),藥師(二)` |  | 藥師官方 PDF | 類科名稱 OCR 誤辨 |
| `confirmed` | `藁事法` | `藥事法` | `category` | `藥師,藥師(一),藥師(二)` |  | 藥師官方 PDF | 藥事法首字 OCR 誤辨 |
| `confirmed` | `罂粟` | `罌粟` | `category` | `藥師,藥師(一),藥師(二)` |  | 藥師官方 PDF | 簡體 OCR 誤辨 |
| `confirmed` | `藁局` | `藥局` | `global` |  |  | 多類科官方 PDF | 完整詞組中的藥字被 OCR 誤辨；不延伸替換單獨的藁 |
| `confirmed` | `藁用` | `藥用` | `global` |  |  | 多類科官方 PDF | 例如藁用酒精；藁本是合法藥材名，不套用單字替換 |
| `confirmed` | `藁名` | `藥名` | `global` |  |  | 多類科官方 PDF | 藥名字形 OCR 誤辨 |
| `confirmed` | `藁商` | `藥商` | `global` |  |  | 多類科官方 PDF | 藥商字形 OCR 誤辨 |
| `confirmed` | `藁害` | `藥害` | `global` |  |  | 多類科官方 PDF | 藥害字形 OCR 誤辨 |
| `confirmed` | `Cmax` / `tmax` | `C<sub>max</sub>` / `t<sub>max</sub>` | `global` |  | 藥動學 | 113-2 藥師人工註記 | `max` 為下標；完整 token 才套用 |
| `confirmed` | `Cp` / `Ccr` / `Css` | `C<sub>p</sub>` / `C<sub>cr</sub>` / `C<sub>SS</sub>` | `global` |  | 藥動學 | 藥師人工註記 | 藥物濃度符號的下標 |
| `confirmed` | `fe` / `fu` / `D0` / `DL` / `Rin` | `f<sub>e</sub>` / `f<sub>u</sub>` / `D₀` / `D<sub>L</sub>` / `R<sub>in</sub>` | `global` |  | 藥動學 | 藥師人工註記 | 排泄分率、劑量與輸注速率符號的下標 |
| `confirmed` | `MW dextrose` / `Ksp` / `Du∞` | `MW<sub>dextrose</sub>` / `K<sub>sp</sub>` / `D<sub>u</sub>∞` | `global` |  | 藥動學／分析化學 | 藥師人工註記 | 只在完整 token 邊界修正，不改普通英文 |
| `confirmed` | `Co` | `C₀` | `subject` |  | 藥動學 | 111-1 藥師第 49 題 | 只有同題出現初濃度或 initial concentration 語境才套用；避免誤改元素 Co |
| `confirmed` | `Eo` / `E0` | `E⁰` | `subject` |  | 電化學 | 110-2 藥師第 6 題 | 只有還原電位或 reduction potential 語境才套用 |

## 快速範例

```text
我在 108-2 藥師／藥理學第 12 題看到「錯誤詞」，PDF 是「正確詞」。
```

也可以直接在對話中貼同樣資訊；Markdown 的好處是能留下完整、可共享的發現紀錄。
