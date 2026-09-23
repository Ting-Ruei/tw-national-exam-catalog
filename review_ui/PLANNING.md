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

- [ ] P0 建立驗收工具：`scripts/test_v2_ui_audit.mjs`（CDP，逐顆按鈕點、每區截圖）
- [ ] P1 上下標：新增 `richText()`（白名單 markup → 真標籤），題幹／選項用 `innerHTML` 輸出
      - 負對照：`<script>`／`<img onerror>` 必須被跳脫
- [ ] P2 下拉選單：每個 level 只改自己那一格，不清上層；改選上層才把下層選「全部」
      - 由 `resolveLevel` 的 `''`＝全部語意支撐
- [ ] P3 討論區版面：右欄改 `minmax(360px,0.9fr)` 全高，跟題目區一致；左欄加 scope 過濾器
- [ ] P4 討論區加入：圖片擷圖／抽換（沿用 `/api/manual-asset`）、字體調整、給 AI 的註解（comment 事件）
- [ ] P5 全部按鈕真瀏覽器測過（含 `/legacy` 對照）
- [ ] P6 文件：`docs/skills/review-ui-v2/SKILL.md`、`review_ui/AGENTS.md`、`docs/ROUTE_HISTORY.md`

## 不能違反的規則

- 改進用取代，不是分岔；`S.rows` 是唯一被畫與被走的來源。
- 人類決定 append-only；AI advisory；agent 不得冒充人類審核者。
- 每個檢查要有負對照。
- 相對路徑；`<sub>`/`<sup>` 只有白名單，其餘一律跳脫。
- 這是 G1/G2 工作：branch + PR，不直接推 main。
