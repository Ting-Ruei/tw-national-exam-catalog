# 題庫八層資料與退回契約

這份文件是 Review UI、LUNA worklist 與未來 Agent 的共同資料契約。核心原則是：

> 內容版本與審核狀態是兩條獨立軸線。`reset_review` 只能移動審核狀態，不能降低內容來源優先序。

## 八個邏輯層

| 層 | 名稱 | 目前載體 | 權責 | 可否被後層覆蓋 |
| --- | --- | --- | --- | --- |
| L0 | 官方來源 | 考選部 PDF、來源 registry、checksum | 最終文字、版面與圖片的來源真相；只新增版本，不修改原檔 | 不可 |
| L1 | OCR／版面證據 | MinerU layout、Markdown、抽圖、PDF 文字第二參考源 | 保存模型辨識結果與座標，供追溯及重建；不是人工校正後的顯示真相 | 可由 L3 在顯示上覆蓋，但不可刪除 |
| L2 | Parser 基底 candidate | `exam.question_candidates.raw_candidate_json`、parse issues | 將 L1 切成題幹、選項、題號、圖片引用與 metadata；可重建 | 可由 L3 覆蓋 |
| L3 | 人工內容校正 | `question_review_events.corrected_candidate_json`、`40_manual_assets/` | 保存人工文字修正、去連結、補圖、替圖與圖片審核結果；append-only | 只有更新的人工校正可覆蓋；reset 不可覆蓋 |
| L4 | 有效內容投影 | Review UI／worklist 查詢時計算 | `effective_candidate = L2 raw + 最新 L3 correction`；同時保留 parser original | 不直接寫入事件 |
| L5 | 分域人工審核狀態 | question/group/visual/answer review events | 分別回答「是否通過／待審／阻擋」；不保存或撤銷其他層的內容權威 | 各分域只由自己的新事件推進 |
| L6 | AI 建議與人類回饋 | `question_ai_review_events`、`question_ai_feedback_events`、`question_ai_learning_events` | AI 疑點、一鍵建議、模型版本、讚／倒讚，以及人工選入 AI 學習的案例；全是 advisory | 不可直接更改 L3、L5 或正式庫 |
| L7 | 正式可用題庫 | `exam.questions`、options、assets、answers、不可變匯出包 | 僅接收完成規定人工 gate 的 L4 有效內容 | 新版本重建，不回寫上游 |

L0 是「判定對錯」的最高證據；Review UI 實際顯示則由 L4 投影。L1、L2 即使時間較新，也不能蓋掉 L3 已保存的人工校正。

## 內容投影與狀態投影必須分開

每題至少同時計算兩個值：

```text
effective_content = raw_candidate(L2) + latest_content_correction(L3)
question_state    = latest_question_state_event(L5)
```

`latest_content_correction` 是最後一筆含 `corrected_candidate_json` 的有效內容事件；
`latest_question_state_event` 是最後一筆題目審核事件。兩者可以來自不同 event id。

例如：

```text
accept + 手動正圖  → L3 保存正圖，L5=accept
reset_review       → L3 仍保存正圖，L5=unreviewed
重新人工通過       → L3 仍保存正圖，L5=accept
```

禁止把最後一筆 `reset_review` 沒有 correction 解讀成「沒有人工校正」。

## 問題應在哪一層修

| 問題 | 根因修復層 | 立即可用修復 | 審核路徑 |
| --- | --- | --- | --- |
| 官方 PDF 本身版本錯誤／換版 | L0 新增來源版本 | 暫停該來源 | 重建 L1–L4，人工決議是否重審 |
| MinerU 讀錯字、裁錯圖、多層 PDF 抽取異常 | L1 OCR／抽取流程 | L3 人工文字或 manual asset 覆蓋 | 只退回受影響的題目或圖片分域 |
| 題號、選項、題界切錯 | L2 parser 規則 | L3 可先修正可見內容 | parser regression 後，僅可見內容真的改變才 reset question |
| 人工曾修正文字／去舊圖／補正圖 | L3 | 追加新的 correction | 不因 reset 或 parser rebuild 失效 |
| Review UI 顯示回到舊 MinerU | L4 投影程式 | 修查詢／投影，不寫資料修復事件 | 不改人工狀態 |
| `呈上題`／`承上題` | L5 group state | 題組頁確認範圍 | 不得進 question text 修正 |
| 圖片需要補、換或去連結 | L3 asset correction + L5 visual state | manual asset／unlink | 不改題目文字或答案狀態 |
| 答案值或 MOD 對應 | L5 answer state；必要時 answer correction | 答案頁處理 | 不改 question/group state |
| AI 誤報、理由差、漏一鍵修正 | L6 feedback／規則候選 | 倒讚並寫原因；修 prompt/rule 後 append 新 AI audit | 不 reset human state |
| AI 找到已通過題目的真異常 | L6 先產 proposal | 人工核准後只 append L5 `reset_review` | L3 既有內容繼續生效 |

修復原則是「根因盡量往低層修，營運不中斷用 L3 覆蓋」，但任何重建都不得抹除 append-only 人工證據。

## 退回動作的精確目的地

- `reset_review`：只把 L5 question state 退回未審；L3 文字、圖片、去連結與註記保留。
- `reset_group_review`：只把 L5 group state 退回未審；題目文字、圖片、答案與 question state 不變。
- 圖片待重審：追加 visual-owned event；manual asset 仍是有效內容，除非人類另存新的圖片 correction。
- 答案待重審：只追加 answer reset/review event；question state 不變。
- `reset_ai_review`：只撤回目前 L6 AI 投影；AI 歷史與人類 feedback 保留。
- parser rebuild：寫 L2 新基底或修復事件；如果 L3 覆蓋後的 L4 畫面沒有改變，不得僅因 raw 改動就 reset 人工審核。
- 正式庫撤回：由 L5 gate drift 觸發 L7 同步；不能以刪除 L3 校正來達成。

## Agent 執行順序

1. 讀 L0/L1/L2 lineage 與 L3 是否存在，不先做 OCR。
2. 建立 L4 effective candidate，所有 text/group/visual worklist 都只能讀 L4。
3. 依 owner 分派到 question、group、visual、answer；一個疑點只有一個 owner。
4. deterministic active exact rule 才能產生可套用 patch；模型只能產 L6 advisory。
5. 人工評分綁定 `ai_review_ref`。新 AI event 出現後，舊評分仍保留但不投影成新事件的評分。
6. 新規則先 observed/proposed/shadow/verified，通過 regression 才 active；提出規則的 Agent 不得自行啟用。
7. 只有人工決議可推進 L5；只有完成規定 gate 的 L4 內容可進 L7。

## 上線與回歸檢查

每次改動 projection、reset 或 parser 規則，至少驗證：

1. 一題只有 raw MinerU。
2. 一題有人工文字 correction 後 reset。
3. 一題有 option manual asset 後 reset。
4. 一題先去舊圖再補正圖後 reset。
5. LUNA worklist 與 Review UI API 對同一 candidate 產生相同 L4 內容。
6. 最新人工狀態仍是 unreviewed，但 `review.has_correction=true` 且顯示人工資產。
7. 新 AI audit 不繼承上一版的讚／倒讚；舊 feedback 仍可查詢。

## 待人工決議

目前正式 readiness 的既有實作以 question 與 answer 同時 `accept/unblock` 為主。下列政策應由產品決議後再升級為 hard gate：

- 有視覺依賴的題目是否必須 `visual_asset_ok` 才能進正式庫。
- 已判定為題組的題目是否必須 `confirm_group` 且 range 完整才可進正式庫。
- L3 correction 在官方 PDF 換版後，是自動標成待核對，還是只在 fingerprint drift 時退回。

在決議前，Agent 只能報告 gate 缺口，不能自行擴大自動阻擋範圍。
