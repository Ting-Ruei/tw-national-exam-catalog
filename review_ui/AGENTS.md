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
  legacy.html                `/legacy` 的舊審題頁；由 server 讀取，僅保留相容性
  mobile-sw.js / mobile.webmanifest / mobile-icon.png.b64
```

## 路由（`scripts/serve_question_review_ui.py`）

| 路由 | 檔案 | 狀態 |
|---|---|---|
| `/v2` | `v2.html` | **基準線** |
| `/mobile` | `v1-reference/mobile.html` | 參考 |
| `/workflow`、`/mobile/workflow` | `v1-reference/workflow.html` | 參考 |
| `/legacy` | `v1-reference/legacy.html` | 參考 |

v1 仍然服務，**不是因為它還被維護**，而是因為既有書籤、已安裝的 PWA、以及會送
`mobile_defer` / `mobile_resume` 事件的消費者都是契約 —— 一個更好的介面沒有權利因為它更好
就把它們弄壞。刪掉 v1 也會讓「新的比舊的好」這個主張失去對照組。

**v1 只修「讓它繼續能動」的問題，不加功能。**

## 改完必須推上常駐機，否則等於沒改（2026-09-23 補）

**審題介面跑在常駐機 `192.168.10.70`，不是在筆電上。** 你現在編輯的檔案在筆電的工作樹；
使用者打開的是**常駐機的容器**。兩者之間沒有人自動搬運：

```text
筆電工作樹  ──(要你自己下指令)──>  192.168.10.70:8765
```

**所以「我改好了」與「使用者看得到」是兩件事。** 2026-09-23 就是這樣出錯的：介面與題目改了
一整天，常駐機的檔案停在 6 小時前，使用者打開 `192.168.10.70:8765/v2` 什麼都沒看到。
不是部署壞了——是**沒有人要求要部署**。

改完 `review_ui/`、`scripts/serve_question_review_ui.py`、`qbr/src/qbr/review_ui/` 或任何伺服器
會讀到的東西之後：

```sh
scripts/deploy_station.sh --restart     # 同步程式碼，並重建容器讓新程式真的生效
```

- `--restart` **不能省**。rsync 只換檔案；跑著的 Python 不會自己重讀已載入的模組。
  少了它，部署「成功」而服務仍是舊行為——這是這條路徑最會騙人的失敗。
- **不要手打 `rsync`。** 它會靜默漏掉三件事：`code/國考題資料夾` 掛載點、`record-deploy.sh`
  要的來源 revision，以及最要緊的——審核紀錄要排除在 `--delete` 之外。
- 改完介面**用瀏覽器打開 `http://192.168.10.70:8765/v2` 看一次**，再對照
  `shasum -a 256 review_ui/v2.html` 與服務中的位元組。截圖與 hash 都對才算完成。
- 反向（常駐機 → 筆電）只有審核紀錄：`scripts/pull_station_reviews.sh`。

「部署成功」不是證據。`up.sh` 自己會比對服務題數與 `wc -l candidates.jsonl`；數字不符它以非零
結束。要更強的證據就用 `~/qbr-review/DEPLOYED.json`——那裡記著站上每個檔案的 sha256。

## 伺服器結構（2026-09-23 拆分）

`scripts/serve_question_review_ui.py` 曾經是 **10,713 行**。它現在是 **347 行的 composition
root**：只做 `parse_args`、`main`，以及把實作再匯出。實作在
[`../qbr/src/qbr/review_ui/`](../qbr/src/qbr/review_ui/)：

| 檔案 | 內容 |
|---|---|
| `constants.py` | 共用常數、分類濾鏡、SQL 片段、prompt 版本 |
| `paths.py` | 路徑安全、content-type、專案路徑 rebind |
| `events.py` | append-only 事件流的讀取（六條流） |
| `ai_audit.py` | AI 稽核的判讀、範圍切分、修正建議 |
| `queue_view.py` | 佇列／卷的投影、討論區分類 |
| `legacy_assets.py` | v1 頁面與資產回應 |
| `review_state.py` | `ReviewState`：審核引擎本體 |
| `handlers.py` | `Handler` / `MobileHandler`：HTTP 進入點 |

**為什麼要拆**：一個 10,713 行的檔案讓「改一條規則」變成「在 10,713 行裡找那條規則」，
而 `ReviewState` 一個 class 就佔 7,239 行、132 個方法。拆完之後，改 AI 判讀只開
`ai_audit.py`。

**拆分的鐵則：`serve_question_review_ui` 仍是唯一的公開名字。** 約 50 個測試檔用
`importlib.util.spec_from_file_location(...)` 直接載入這個路徑，然後呼叫
`module.split_ai_audit_scopes(...)`。所以 composition root 會把每個模組的符號再匯出一次
（`from qbr.review_ui.x import (...)` 那一段）。**那段不是裝飾，是契約**：把東西移出
`qbr/review_ui/` 時，要把它加進再匯出清單，否則測試會在它沒改過的地方壞掉。

**測試要斷言「實作在原始碼裡的樣子」時，讀 `tests/review_ui_source.py::server_source()`**，
不要直接讀 `scripts/serve_question_review_ui.py`——實作已經不在那個檔案裡，直接讀會找不到
而誤報「規則不見了」。

**要 patch 模組層級的全域變數**（例如呼叫 `safe_file_path` 前改 `PROJECT_ROOT`）時，
要 patch **擁有它的那個模組**（`serve_question_review_ui.paths.PROJECT_ROOT`）。
patch composition root 上的同名屬性只會改到再匯出的那份副本，函式讀到的仍是自己模組的值。

**拆分的正確性用「逐定義比對」證明，不是用感覺**：229 個頂層定義中，227 個的原始碼與拆分前
**逐位元組相同**，另外 2 個（`main`、`parse_args`）留在 composition root 且同樣逐位元組相同。

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
11. **每個按鈕都要真的按過才交付。** 用 `scripts/test_v2_ui_audit.mjs`。一個沒接 handler 的按鈕
    在截圖上與成功的按鈕一模一樣；只有真的按下去、看狀態有沒有變才測得出來。
12. **錯題討論區的版面與題目審核區同一個算式**（`238px | 1fr | 1fr`）。改一格就要改另一格。
13. **討論區的 ③ 原題是讀的，⑤ 手動修改是改的。** 兩者不可混成一個框。
14. **寫入型／判決型控制項的驗法不同。** 寫入型的按**空**的驗守門；判決型的**不按**（那是
    append-only 的人工紀錄，`GOV-05`／G4）。詳見 skill 的「每一個按鈕都要真的按過」。
15. **討論區自己有篩選，而且四個下拉共用題目區的那一組判準。** 篩選在**伺服器端**（卡住的題散在
    整個佇列，瀏覽器只看得到上限那一頁）；每一層的 `onchange` 只寫自己那一層；全部類科是一個
    **合併過的** bucket，不是 `undefined`（否則下面三層是空的）。
16. **逐題註解只有「原本沒有的規則」才進基本原則，而且要指得出來源。** 判讀是人讀的（見
    `qbr/reports/comment_to_principles.md`），寫入是 `qbr/scripts/curate_principles_from_comments.py`
    （dry-run 預設、去重、來源要對帳、reviewer 不得是 `local` 或人的名字）。**機器自己產生的
    修復說明（例如 reset_review 的 `notes` 被預填進註解框）不是人的原則**——它在多題上一字不差。

## 驗證

```sh
# 導覽：抽真的 <script>，用最小 DOM，跑真的 rebuildRows/visibleRows/go/next
node scripts/test_v2_navigation.mjs review_ui/v2.html <workdir>/review-ui/candidates.jsonl

# 每一個按鈕都真的按一遍（使用者指定的驗收標準；四區 218 個控制項，見 repair-review-ui-v2）
node scripts/test_v2_ui_audit.mjs http://127.0.0.1:8897 --json /tmp/audit.json

# 註解與佇列的關係（自己開隔離的 server，不碰 live 事件檔）
node scripts/test_v2_note_keeps_question.mjs

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
