# OCR 文字修正規則

`configs/text_normalization_rules.json` 是可以逐步補齊的「發現清單／輸入區」。
每一筆規則都必須是從官方 PDF 核對過的精確詞組，不能只因為看起來像簡體字就全域替換。

人工發現建議先填入 [OCR 修正待確認清單](/Users/tim/tw-national-exam-catalog/docs/ocr-correction-inbox.md)，確認後再轉入 JSON active 規則。

Parser 與醫事檢驗師的既有規則總覽見 [parser-rule-inventory.md](/Users/tim/tw-national-exam-catalog/docs/parser-rule-inventory.md)。

## 確定規則與 AI 建議的差異

已由官方 PDF 核對並登錄為 active 的規則（例如 `内` → `內`、`藁事法` →
`藥事法`、`藁局` → `藥局`、`罂粟` → `罌粟`）屬於 deterministic parser 規則。它們會在 parser
或明確執行 SQL repair 時直接套用，但仍保留原本的 `block` / `needs_review`
狀態，等待人工最後按通過；不需要每次再按「套用 AI 建議校正」。如果 deterministic
規則實際改動了已 `accept` / `unblock` / `reviewed` 題目，SQL 會另外追加
`reset_review`，保留原註記與校正內容，讓人工重新確認。

AI 新發現的字形或語意推測仍然只會顯示為 advisory，只有在目前可見文字確實
不同時才顯示「套用 AI 建議校正」。若 deterministic 修復已經把文字改成建議
內容，舊 AI 事件保留在歷史紀錄，但 Review UI 不再顯示重複的套用按鈕。

## 通用與科目規則

- **通用規則**：`scope` 留空，所有題目適用。只放不會因學科而改變意思的明確 OCR 錯誤。
- **類科規則**：`scope.categories` 填類科，例如 `藥師`、`藥師(一)`、`藥師(二)`。
- **科目規則**：`scope.subjects` 填科目名稱；可再搭配 `categories` 限定範圍。
- `source`、`target` 都是精確字串；`target` 是 PDF 核對後的正確文字。
- 不確定的發現先不要加入 active 規則，改記在 `note` 或交給人工複核。

目前已登錄的科目群組規則：

| source | target | 範圍 | 狀態 |
|---|---|---|---|
| `檠師` | `藥師` | 藥師、藥師(一)、藥師(二) | active |
| `藁事法` | `藥事法` | 藥師、藥師(一)、藥師(二) | active |
| `罂粟` | `罌粟` | 藥師、藥師(一)、藥師(二) | active |
| `藁局` | `藥局` | 全域完整詞組 | active |
| `藁用` | `藥用` | 全域完整詞組 | active |
| `藁名` | `藥名` | 全域完整詞組 | active |
| `藁商` | `藥商` | 全域完整詞組 | active |
| `藁害` | `藥害` | 全域完整詞組 | active |

注意：`藁本` 是合法中藥材名稱，不能用單一 `藁` → `藥` 的規則替換。

藥師三類科近期人工註記確認的科學符號由 parser 負責完整 token 正規化，不使用
單字母全域替換：

- `KM`、`Vmax`、`Cmax`、`tmax`、`Cp`、`Ccr`、`Css`、`Clcr` → 對應的
  `<sub>` 標記。
- `fe`、`fu`、`D0`、`DL`、`Rin`、`MW dextrose`、`Ksp`、`Du∞` → 對應的
  排泄分率、劑量、輸注速率、溶解度積與尿中排泄量下標。
- `Co` 只有在同題鄰近「初濃度／initial concentration」時才轉成 `C₀`；
  還原電位語境的 `Eo/E0` 才轉成 `E⁰`，避免誤改元素 `Co`。
- `（VD） ss` 會保留括號並呈現為 `（V<sub>D</sub>）<sub>ss</sub>`；
  `VD,ss` 則呈現為 `V<sub>D,ss</sub>`。

這些規則只代表顯示/文字結構修復，答案仍由答案核對關卡處理；SQL 修復會保留
原本的人工註記。`block` / `needs_review` 仍維持原 action；已審核內容若被
實際改動，則追加 `reset_review`，不可沿用舊的 `accept`。

## 新增方式

直接編輯 JSON，或使用：

```bash
python3 scripts/add_text_normalization_rule.py \
  --source '錯誤詞' \
  --target '正確詞' \
  --category '藥師' \
  --note '已對照 109-1 官方 PDF 第 12 題'
```

`--category` 和 `--subject` 可以重複輸入。新增規則只會影響後續 parser 及明確執行的文字修復；修復時會保留原始 candidate、人工註記與修復歷史。`block` / `needs_review` 仍維持原 action；已審核內容若被實際改動，則追加 `reset_review`，不可沿用舊的 `accept`。

## 套用與檢查

先 dry-run：

```bash
docker compose exec -T review-ui python3 scripts/repair_reviewed_text_normalization.py \
  --include-unreviewed --include-reviewed \
  --category '藥師' --category '藥師(一)' --category '藥師(二)' \
  --match-text '錯誤詞' \
  --output-dir tmp/ocr-rule-audit
```

只要傳播已確認的上下標與科學符號，使用 `--notation-only`，避免把其他歷史
OCR/表格差異一起帶入：

```bash
docker compose exec -T review-ui python3 scripts/repair_reviewed_text_normalization.py \
  --category '藥師' --category '藥師(一)' --category '藥師(二)' \
  --notation-only --audit-all \
  --output-dir tmp/pharm-notation-audit
```

全庫 active `block` / `needs_review` 乾跑或套用：

```bash
docker compose exec -T review-ui python3 scripts/repair_reviewed_text_normalization.py \
  --all-categories --output-dir tmp/reviewed_text_normalization/all-blocks
```

加上 `--apply` 才會追加 SQL 修復事件；原始 candidate 不會被覆蓋，人工註記會被
保留。重複執行必須是冪等的，不應再次產生修復事件。若要讓已審核題目回到人工
確認，使用 `--reset-reviewed-repairs`；只有 parser 對既有 correction 產生新的
實際差異時才會追加 `reset_review`，不會因為重跑命令而無條件退回。Review UI
會保留 correction 並顯示為未審。

若只要傳播已確認的簡繁 OCR 字形規則，不碰公式、上下標或語意校正，使用：

```bash
docker compose exec -T review-ui python3 scripts/repair_confirmed_ocr_rules_sql.py \
  --all-categories --output-dir tmp/confirmed_ocr_rules/run
```

這個批次保留 raw source、更新 derived candidate，並為畫面真的改變的題目
追加同狀態 SQL event。`内` → `內` 會套用到所有類科；有 scope 的詞組仍遵守
`configs/text_normalization_rules.json` 的類科範圍。

確認 findings、manual conflicts 與 artifact 後，再加 `--apply`。這個流程不會把 AI 建議直接變成人工通過，也不會覆蓋既有人工校正；若現有文字已經不同於 parser 原文，會列為 conflict 等待人工處理。
