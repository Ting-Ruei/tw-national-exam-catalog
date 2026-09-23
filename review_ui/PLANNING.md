# v2 UI 修正計畫（2026-09-23，本次 session）

使用者回報（原文重點）：
1. **上下標顯示成 `<sup></sup>`** —— UI 有 bug，做完要真的用瀏覽器逐顆按鈕測過才交付。
2. **題目審核區的下拉選單**：選了某個科系，不可以動到其他選項，才能精準篩選（現在會跳來跳去）。
3. **錯題討論區 UI 不好**：
   - 整題介面要跟題目審核區一致（左欄資訊、右邊 PDF 要一樣大）
   - 「基本原則」上面要有**註解區**（說哪裡錯了／我改了哪裡）
   - 要像以前的 review UI 一樣**看得到原題**，有**圖片擷圖／抽換**區、**字體調整**區、
     **給 AI 參考的註解區**

## 已量到的證據（不是猜測）

| # | 現象 | 證據 |
|---|---|---|
| A1 | v2 的題幹／選項用 `esc()` 直接輸出，`<sub>`/`<sup>` 被跳脫成字面文字 | `02-area-question.js` `renderTextSide()`：`${esc(candidate.stem)}`、`${esc(option.text)}`；`esc()` 在 `01-core.js`。語料 6,652 行含 markup（`<sub>2</sub>` 2,563 次、`<sup>99m</sup>` 1,594 次…） |
| A2 | 舊版（legacy `/legacy`、v1 mobile）有 `renderText()`：把 markup 正規化成真的 `<sub>`/`<sup>` | `/tmp/legacy.html:853-876`、`mobile.html:592-722` |
| B1 | 選單會互相重設：`category.onchange` 清掉 year/sitting/subject；`refreshScope()` 每次都重建 `<option>` 並 `resolveLevel()` 覆寫 | `01-core.js:194-244` |
| C1 | 討論區右欄窄（330px、iframe `min-height:440px`），題目區右欄是 `1fr` 全高 | `v2.html` `.discuss { grid-template-columns:214px minmax(0,1fr) 330px; }` |
| C2 | 討論區沒有註解（comment）欄位；legacy 有「人工審核」欄位，v2 題目區有「只加註記 C」 | `04-area-discuss.js`；`/tmp/legacy-paste.png` |
| C3 | 討論區沒有圖片擷圖／抽換；`/api/manual-asset` 只在 legacy 用 | `serve_question_review_ui.py:10124` |
| C4 | 沒有字體大小調整 | 全樹無字級控制 |

## 工作項（每項做完 → 真 Chrome 量測 → 測試 → checkpoint commit）

- [x] P0 建立驗收工具：`scripts/test_v2_ui_audit.mjs`（CDP，逐顆按鈕點）
- [x] P1 上下標：新增 `richText()`（白名單 markup → 真標籤），題幹／選項／原題用 `innerHTML`
      - 負對照：`<script>`／`<img onerror>` 必須被跳脫（`tests/test_review_ui_rich_text.py`）
- [x] P2 下拉選單：每個 level 只改自己那一格；`resolveLevel` 的 `''`＝全部語意
      - 負對照：`tests/test_review_ui_v2_scope.py`，真 Chrome 重驗通過
- [x] P3 討論區版面：`238px | 1fr | 1fr`，與題目區同一個算式（實測 PDF 欄 330→721px）
- [x] P4 討論區加入：③ 原題（唯讀、走 `richText`）、④ 擷圖／抽換（沿用 `/api/manual-asset`）、
      字級控制（一個 CSS 變數）、⑥ 註解（`comment` 事件）、統計移到左欄
- [x] P5 全部按鈕真瀏覽器測過：`test_v2_ui_audit.mjs` 四區 213 個控制項 + `test_v2_note_keeps_question.mjs`
- [x] P5b 修掉由此暴露的真缺陷：寫註解會把題目踢出討論區（`_note_annotates_pending_reset`）
- [x] P6 文件：`docs/skills/review-ui-v2/SKILL.md`、`review_ui/AGENTS.md`、`docs/ROUTE_HISTORY.md`

### 使用者 2026-09-23 回答後的追加需求

| # | 使用者的話 | 判讀 |
|---|---|---|
| 1 | 「字體」是前者 | 閱讀字級（已完成），不是上下標標記 |
| 2 | **「要有篩選，比較好審核」** | **討論區要加 scope 篩選器（未做）** |
| 3 | 「類科，維持目前四層」 | 四層是 類科／年度／考次／科目 |
| 4 | 逐題 comment 自己讀，是原本沒有的規則才加入 | 讀完 10 筆註解 → 只有新規則才進基本原則（未做） |

- [x] P7 討論區 scope 篩選器：左欄加四層下拉，與題目區**共用** `resolveLevel`／`availableSittings`／
      `availableSubjects`／`countQuestions`（不重寫一份）。分類樹只含討論區的題
      （`discuss_taxonomy`，在未篩選的卡住集上算），預設全部
      - 契約：改一層不動別層（真 Chrome 量過）；負對照會咬（拿掉 `mergedBucket`／`resolveLevel` 各掛一條）
      - 實測：修正前後年度下拉選項 `[8,1,1,1]` → `[8,16,3,37]`；選年度 115 後清單 495 → 24 題
- [x] P8 逐題註解 → 基本原則：讀 9 筆 `comment`（7 個題號），只把**新的**規則寫成 `add`
      - 判讀：`qbr/reports/comment_to_principles.md`（9 筆逐一對照）；只有 q071「表格要用紙本圖」是新的
      - 寫入：`qbr/scripts/curate_principles_from_comments.py`（dry-run 預設、去重、來源對帳、
        reviewer 不得是 `local`）；負對照三條都會咬
      - 實測：寫入 1 條 `p1`；已驗它真的進到轉錄／審核提示詞，且在真 Chrome 畫面上看得到


## 不能違反的規則

- 改進用取代，不是分岔；`S.rows` 是唯一被畫與被走的來源。
- 人類決定 append-only；AI advisory；agent 不得冒充人類審核者。
- 每個檢查要有負對照。
- 相對路徑；`<sub>`/`<sup>` 只有白名單，其餘一律跳脫。
- 這是 G1/G2 工作：branch + PR，不直接推 main。
