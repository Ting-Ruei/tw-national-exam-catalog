# review_ui — Agent Instructions

審題介面。**v2 是基準線；v1 僅供參考，不再維護。**

工作程序見 skill：[`../docs/skills/review-ui-v2/SKILL.md`](../docs/skills/review-ui-v2/SKILL.md)。
路線對照見 [`../docs/ROUTE_HISTORY.md`](../docs/ROUTE_HISTORY.md)。

---

## 檔案

```text
v2.html                    **基準線**。線性審題：一份清單、一題、四個決定。
v1-reference/              僅供參考，不再維護
  mobile.html                舊的手機快速分流介面
  workflow.html              舊的八線道工作台
  mobile-sw.js / mobile.webmanifest / mobile-icon.png.b64
```

## 路由（`scripts/serve_question_review_ui.py`）

| 路由 | 檔案 | 狀態 |
|---|---|---|
| `/v2` | `v2.html` | **基準線** |
| `/mobile` | `v1-reference/mobile.html` | 參考 |
| `/workflow`、`/mobile/workflow` | `v1-reference/workflow.html` | 參考 |

v1 仍然服務，**不是因為它還被維護**，而是因為既有書籤、已安裝的 PWA、以及會送
`mobile_defer` / `mobile_resume` 事件的消費者都是契約 —— 一個更好的介面沒有權利因為它更好
就把它們弄壞。刪掉 v1 也會讓「新的比舊的好」這個主張失去對照組。

**v1 只修「讓它繼續能動」的問題，不加功能。**

## 鐵則

1. **導覽與內容必須來自同一個來源。** `S.rows` 同時是被畫出來的陣列與被走過的陣列。
   濾鏡只改「畫什麼」不改「走什麼」是**錯的**（見 `docs/ROUTE_HISTORY.md`）。
2. **改進要用取代，不是分岔。** 兩個顯示同一批題目的介面，就是兩個可以弄丟審核紀錄的地方。
3. **人類的決定是 append-only。** 不得改寫 `question_review_events.jsonl`。
   重建時承接是自動的，去重以**整筆紀錄**為單位。
4. **不得自動接受或阻擋。** AI 一律 advisory，不得冒充人類審核者。
5. **右側 PDF 只放題目卷。** 不放答案卷；右側與左側清單完全脫鉤。
6. **鍵盤全部左手**：`W` 上一題、`S` 下一題、`A` 確認正常、`R` 需重看、`B` 阻擋、`E` 修正/儲存。
7. **`null` 與 `''` 是不同的狀態。** 「沒算」與「算了，是空的」不是同一件事。
8. **清單的主鍵是卷，不是題號。** 題號每卷重數；任何單調性檢查必須以卷為單位。
9. **爭議畫在題幹之上。** 審題者必須在讀題**之前**看到機器的不確定性。
10. **每個檢查都要有負對照** —— 必須在舊行為上失敗的那個案例。

## 驗證

```sh
# 導覽：抽真的 <script>，用最小 DOM，跑真的 rebuildRows/visibleRows/go/next
node scripts/test_v2_navigation.mjs review_ui/v2.html <workdir>/review-ui/candidates.jsonl

# 伺服器路由
python3 -c "import ast;ast.parse(open('scripts/serve_question_review_ui.py',encoding='utf-8').read())"

# 跑一個不動正式服務的測試實例
python3 scripts/serve_question_review_ui.py --candidate-jsonl <...> --issue-csv <...> \
    --review-log <...> --review-backend jsonl --host 127.0.0.1 --port 8899
```

`/v2` 是 `no-store`：改 `v2.html` 不需要重啟伺服器，改完就是活的。
review log 以 `"a"` 開啟，永不截斷。

## 重建佇列時

**重建進「正在服務」的目錄時，要先暫停服務。** 重建期間列表與候選檔會短暫不一致。
（已知未解，見 `qbr/reports/review_record_safety.md`。）

審核紀錄所在位置就是佇列旁邊的那個檔案 —— 它是「佇列旁邊的一個檔」，不是資料庫交易。
這件事的風險與三個未解項目記在同一份報告裡。
