# 國考題審核自動化與低人工介入策略

本文件定義 SQL-first 審核系統的長期流程。目標不是取消人工控制，而是讓人只處理高風險、模型不一致與新型態案例；可重複驗證的工作交給 parser、SQL validator 與批次 AI advisory。

## 單一資料流

```text
官方 PDF / 答案 PDF
  -> MinerU 可追溯輸出
  -> parser candidate
  -> deterministic QA
  -> AI advisory
  -> 題目審核
  -> 答案核對
  -> 題組 / 圖片結構標籤
  -> formal sync queue
  -> 正式題庫與 release validator
```

- PostgreSQL 是審核狀態的唯一主庫。
- `question_review_events`、`answer_review_events` 與 AI events 保持 append-only。
- JSONL 是匯入交換格式與歷史快照，不再是 Review UI 的即時寫入主路徑。
- 題目與答案通過後先提交 SQL 審核事件，再由 `formal_sync_queue` 在背景同步正式表；UI 不等待 promotion 完成。
- 題組與圖片是獨立結構標籤。它們不改寫題目/答案的人工狀態，但 release validator 可依發布目的決定是否阻擋缺圖或題組不完整的資料包。

## 三層自動判斷

### 1. Deterministic QA

Python / SQL 必須處理可以明確判定的條件：

- PDF、MinerU 輸出與 answer pair 是否存在，MOD 是否優先。
- 每份題數、題號連續性、重複或缺漏題號。
- A-D 選項完整性、重複標籤、空選項與跨題合併。
- 資產路徑、檔案存在、圖片尺寸、重複綁定與無效關聯。
- 可安全正規化的希臘字母、攝氏、上下標與常見 OCR 字形。
- parser 版本、來源雜湊與每次轉換的 lineage。

這一層只處理規則確定的事；不可用大量模糊 regex 取代語意判斷。

### 2. AI Advisory

模型處理需要語意或視覺理解的問題：

- 中文 OCR 是否與後方英文原文矛盾。
- 題幹是否被截斷、選項是否語意上屬於下一題。
- 是否真正依賴圖片/表格，以及目前圖片是否截取完整。
- 題組範圍、共同題幹與「承上題」關係。
- parser pass 題目的快速格式複核。

每個模型都輸出同一份結構：`status`、`confidence`、`labels`、`findings`、`suggested_correction`、`evidence`、`model`、`prompt_version`。模型只提供 advisory，不可單獨把題目改成人工通過或阻擋。

### 3. Risk Routing

Review UI 不應平均展示所有題目，而應依風險分流：

- 高風險：deterministic error、AI block、AI/規則互相矛盾、MOD 特殊答案、缺圖、題號缺漏。逐題人工處理。
- 中風險：公式、題組候選、圖片裁切、AI needs_review。以題組或整份試卷為單位人工確認。
- 低風險：結構規則全通過、AI 無 finding、來源完整。先抽樣，再使用「本頁批次通過」。

AI 不能取代人工 gate，但可以讓人工從「每題都看」縮小成「只看風險題 + 抽樣」。

## 建議抽樣規則

- 每個新 parser 版本、考別、科目、年份格式至少抽查 20 題或 5%，取較高者。
- 抽樣錯誤率低於 0.5%，其餘低風險題可由人一次批次通過。
- 抽樣錯誤率介於 0.5%-2%，擴大到 20%。
- 超過 2% 或發現系統性錯誤，停止批次通過，修 parser 後只 reset 實際變動題目。
- 每次批次通過保存 filter、parser version、AI run、抽樣批次與 reviewer，讓結果可重現。

## 各關卡的人工作量

### 題目

人工只看 deterministic/AI 非 pass、抽樣題與已有人工註記的題目。系統 pass 且 AI pass 的題目應集中成可批次接受的低風險隊列。

### 答案

ANS 單選且 1-N 題號完整、答案值都在 A-D、來源 PDF 唯一時，以整份答案表一次確認。MOD、`#`、多答案、送分或答案表缺題才逐列處理。答案關卡不重複回報題目格式問題。

### 題組

先由規則找明示範圍，再由 AI 判斷共同脈絡。人工主要處理 AI 不確定、沒有候選但人工發現的新型態，以及範圍調整。確認後直接寫入 `question_groups` 與各題 sequence。

### 圖片

規則只做寬篩；AI 比對題意、PDF 區域與已綁資產。人工只確認已有圖是否正確、補錯圖，以及 AI 不確定案例。人工補圖視為可信資產，但仍保留來源與操作人。

## UI 精簡原則

- 上層固定四個工作模式：`審題`、`答案`、`題組`、`圖片`。
- 每個模式只顯示該關卡的狀態與主要動作；AI 是證據，不另成一套人工狀態。
- SQL 模式不顯示 JSONL 重載按鈕。
- 審核後先在前端本地移到下一筆，背景更新清單與正式庫，不讓慢查詢阻塞按鈕。
- 搜尋需 debounce；切換模式優先顯示短期快取，再背景更新。
- API 每次只回傳一個可操作批次，總數另外計算，避免傳回數 MB 的候選資料。

## 不可省略的人工或制度關卡

- 新考卷版型、新科目名稱變革與新型態題組的首次確認。
- AI 與 PDF 不一致、模型彼此不一致或信心不足。
- 人工補圖、MOD 特殊答案與送分題。
- 每個 parser/model 版本的抽樣驗證。
- 正式資料包發布前的 deterministic validator；validator 失敗不得發布。

## 成效指標

每個考別/科目持續記錄：人工逐題率、批次通過率、AI 攔截命中率、抽樣錯誤率、正式庫退回率、每份答案表處理時間、圖片/題組漏抓率。只有抽樣錯誤率與退回率維持低檔，才能進一步降低人工比例。
