# Review UI 權責契約

Review UI 的分頁不是同一個審核佇列的不同顯示，而是不同資料責任邊界。AI 事件可以共用儲存表，但送到畫面前必須依責任投影；一個疑點只能有一個主要 owner。

| 介面 | 負責判定 | 不負責判定 | AI 輸出路由 |
| --- | --- | --- | --- |
| 題目 | OCR 字形、繁簡/字型、語意轉錄、選項與題幹文字、題號/欄位 | 題組範圍與承接關係、圖片像素、答案表答案 | `question`；可在有 deterministic rule + source evidence 時提供一鍵建議 |
| 題組 | 共同題幹、題組起訖、承接順序、是否同組、`group_ref` | 題目內文的單字修正、答案值、圖片內容 | `group` / `group_dependency`；不得產生文字 patch |
| 圖片 | 是否需要圖片、資產存在性、裁切/版面/像素可讀性 | 純文字字形與題組範圍 | `visual`；交由圖片審核或人工資產處理 |
| 答案 | ANS、MOD、多答案與答案表對應 | 題幹文字、題組範圍、圖片 | `answer`；只寫答案審核事件 |

## 承上題類疑點

`承上題`、`呈上題`、`上題`、`前述` 是來源中的結構線索，不是題目文字錯誤。即使模型把它記錄在舊事件的 `field=stem`，Review UI 也必須在投影時移到題組頁：

- 題目頁顯示 `pass`，不顯示疑點或一鍵修正。
- 題組頁顯示 `needs_review` / `review_group`，欄位為 `group_ref`，沒有文字 patch。
- 混合事件只把非題組 findings 留在題目頁；題組 findings 另存於 `group_ai_review`。

這是介面投影修正，不會改寫既有 append-only AI 或人工事件。模型若要提出「是否為題組」只能提出候選範圍，最後仍由題組頁確認。

## 變更檢查

任何新增 issue family 或 UI 欄位，至少要同步：

1. `rules/` 中的 owner/route 與 negative control。
2. 對應 lane prompt 的禁止越權條款。
3. `serve_question_review_ui.py` 的 scope projection。
4. 題目、題組、圖片、答案四個模式各一個回歸測試。
