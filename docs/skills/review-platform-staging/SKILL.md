---
name: review-platform-staging
description: 審題平台目前已知缺陷與常駐代理的完整架構說明，逐段列出待 owner 決定的問題。當 owner 要逐一回應架構決策、或要理解「為什麼現在是這樣」時讀這份。這是階段性文件：每一項有決定後就搬進對應的正式 skill 或程式碼，本文件隨之刪減。
---

# 審題平台：現況架構與待決問題（階段性）

**這份文件的用途是「把架構邏輯講清楚，讓 owner 逐段回應」，不是操作程序。**
每一節的形狀是：**觀察到什麼（有證據）→ 為什麼會這樣（因果）→ 需要決定的問題**。

決定之後，該節的結論會搬到正式位置（`docs/skills/` 的對應 skill、`compose.yaml`、
程式碼或 `docs/preserved/`），**本文件隨之縮短**。它不該長期存在。

> **量測日期：2026-09-23。** 所有數字都是當天實測。程式碼在動，所以引用時要重驗。

---

## 0. 一句話總結

**你的審題平台現在有一個核心矛盾：多數寫入路徑是唯讀的，但使用者界面假裝它們可寫。**

這件事不是單一 bug，而是好幾個症狀的共同根因。以下從根因往外展開。

---

## 1. 容器只掛了一個可寫檔案（根因）

### 觀察到的

`deploy/qbr-review/compose.yaml:50-64` 的四條掛載：

| 主機路徑 | 容器路徑 | 旗標 |
|---|---|---|
| `/Users/tim/qbr-review/code` | `/workspace` | **ro** |
| `/Users/tim/qbr-review/assets/國考題資料夾` | `/workspace/國考題資料夾` | **ro** |
| `/Users/tim/qbr-review/queue` | `/queue` | **ro** |
| `.../review-ui/question_review_events.jsonl` | `/queue/review-ui/question_review_events.jsonl` | **rw** ← 唯一 |

我在容器內實測每一條寫入路徑：

```
可寫  題目審核        ← 唯一
唯讀  答案審核        OSError
唯讀  偏好設定        OSError
唯讀  討論原則        OSError
唯讀  修正回饋        OSError
唯讀  補圖            OSError
```

**站上實檔對照**（`/Users/tim/qbr-review/queue/review-ui/`）：

| 檔案 | 存在？ |
|---|---|
| `question_review_events.jsonl` | ✓ 1,040,980 bytes |
| `answer_review_events.jsonl` | **不存在** |
| `review_ui_preferences.json` | **不存在** |
| `question_review_principles.jsonl` | **不存在** |
| `question_repair_questions.jsonl` | **不存在** |
| `question_correction_feedback_events.jsonl` | **不存在** |

### 為什麼會這樣

`compose.yaml:56-64` 的註解說明原始設計意圖，而它是**對的**：

> 佇列（`candidates.jsonl`）是可重建的產物；審核紀錄（`question_review_events.jsonl`）
> 是無可取代的人類決定。把前者掛唯讀、後者留在可寫的目錄，讓「重建佇列」不可能弄壞
> 「人的紀錄」。

設計者的心智模型是「**只有審核紀錄是人類產物**」。但實際上後來的功能——
偏好設定、討論原則、反問回答、補圖——**全都是人類產物**，只是它們比這個註解晚出現，
沒有人回頭改掛載。所以這是一個**設計落後於功能**的問題，不是誰寫錯。

`constants.py:40` 把補圖路徑寫成 `ASSET_ROOT / "40_manual_assets"`，而 `ASSET_ROOT`
是唯讀語料，所以補圖**在設計上就不可能成功**——它從來沒有一個可寫的家。

### 這解釋了你的哪些回報

| 你的回報 | 因果 |
|---|---|
| (2) 「補圖失敗：Read-only file system」 | 直接命中 |
| (1) 「環境要固定」 | `review_ui_preferences.json` 寫不進去 → 偏好永遠存不住 |
| (5) 錯題討論區 | `question_review_principles.jsonl`（你寫的原則）與反問回答都寫不進去 |
| (4)(5) agent 沒有回饋資料 | `question_correction_feedback_events.jsonl` 不存在 |

### 已決定（2026-09-24，operator）：路線 A

**`/queue/review-ui/` 整包 rw，語料維持唯讀。** 理由：B 的失效模式已經發生過一次（逐檔開 5 條 bind，
而今天就是忘了 5 條），而 C 違反 charter 的資料邊界。佇列本身真的可重建——重建時從筆電推一份回去。

實作在 `deploy/qbr-review/compose.yaml:56`（第五條掛載改為 `review-ui` → `/queue/review-ui` rw，
外加 `manual-assets` 單獨一條 rw，因為它需要一個**可寫的家**）。`candidates.jsonl` 的保護不在
mount：server 對它從來只有讀，佇列是 `build_review_queue.py` 在容器外寫的產物。

**決定的完成條件不是「compose 改了」，是「跑著的容器換了掛載」。** 這兩件事分開，而且第一次
部署時只有前者成立：檔案同步了、compose 同步了，`docker inspect` 仍是舊的四條掛載，於是
「偏好存不住」原封不動。所以 `deploy_station.sh --restart` 現在會跑
`deploy/qbr-review/verify-mounts.sh`：量容器**當下**的掛載與五條 stream 的實際可寫性，不過就讓
整個部署回非零。

`manual-assets/` 同時是 `deploy_station.sh` 的 `PROTECTED_DIRS`：它只存在於常駐機（容器建出來的
掛載點），筆電的工作樹沒有它，所以帶 `--delete` 的 rsync 會嘗試刪它——`unlinkat: Permission
denied`（實測），rc=23，而部署繼續跑。保護它跟保護人類紀錄的理由相同：不是從題庫重建得出來的。

**前提已實測**：佇列可重建——`crops/` 由 `build_review_queue.py:207-269` 重建（站上實測 742 個目錄），
`_adopt_crops`／`_adopt_finding_crops` 把圖從 build run 複製進來，`:324` 印出 `crops: N copied, M missing`。

**A 方案唯一要接受的殘餘風險**：`:266-269` 的註解說 crops 是 finding 的證據（「A finding says 紙本這頁
印的是 長, and the crop is that」）。所以 crops 能被重建，但**重建出來的不一定與當初那筆 finding
同一張**（來源 run 不同時）。這個風險 operator 已知並接受；若日後認為不可接受，替代是 B（逐檔 bind），
代價是每加一條 stream 都要改 compose——而那正是已經失效過的模式。

---

## 2. 你的人為決策正在被覆寫（嚴重）

### 觀察到的

`question_review_events.jsonl`（5,059 筆）裡，**同一題有多筆事件**的情況：

```
不重複題目: 4720
有多筆事件的題目: 253
你的人為決策被後續 reset_review 覆蓋的題數: 72
目前 notes 顯示管線自動文字（不是你的字）的筆數: 231
```

一個具體例子 —— `moex:115020:305:0403:1:question:q062`：

```
2026-09-19T10:21:58  block          notes=''                      ← 你擋的
2026-09-19T19:57:59  reset_review   notes='上下標修復：…'          ← 你的 block 沒了
2026-09-22T01:13:01  comment        notes='上下標修復：…'          ← 管線的文字
2026-09-22T01:13:03  block          notes='上下標修復：…'          ← 你的新 block，但註解是管線的
```

還有一個可觀察的徵狀：你在 `q037` 上按了**兩次「下標」**，相隔 **6 秒**
（`14:35:56` 與 `14:36:02`）。兩次 `block` 內容不同（一次帶註解、一次只帶「下標」），
顯示**第一次的操作沒有如預期生效**，所以你按了第二次。

### 為什麼會這樣

`AGENTS.md` 寫著：

> Human review events are append-only. Do not rewrite existing
> `question_review_events.jsonl` … unless the user explicitly asks for a repair script.

**檔案確實是 append-only**（`review_state.py:2038-2047` 用 `open("a")`）。
問題不在寫入方式，在**讀取時的語意**：顯示「這一題現在的狀態」時，
系統採用**每題最新一筆事件**，所以後來的 `reset_review` 在視覺上消滅了你的 `block`。

而且管線寫的 `reset_review`／`comment` **會攜帶 `notes` 欄位**，該欄位同時是你寫註解的地方。
於是管線的樣板文字（「上下標修復：紙本印刷的英文上下標…」）**蓋在你的位置**上。
231 筆就是這樣來的。

**這是一個欄位共用的問題**：`notes` 既是「人的話」也是「管線的話」，
兩者用同一個欄位、卻沒有區分來源。

### 待決問題 2

| | 做法 | 代價 |
|---|---|---|
| A | `reset_review` 不再影響人為決策的顯示；狀態改由**事件序列**推導（例如「block 之後若無人工覆蓋，就仍是 block」） | 要改狀態推導邏輯，且要決定「管線 reset 後人該看到什麼」 |
| B | 只把管線的說明搬到新欄位（如 `pipeline_note`），`notes` 永遠只放人的字 | 小，但沒解決「你的 block 被 reset 蓋掉」 |
| C | 保留現狀，只做備份 | 你的審核歷史繼續失真 |

**我的建議是 A + B 一起**：`notes` 只給人用（B），狀態推導改成序列式（A）。
但**A 需要你定義一個語意**：當管線 `reset_review` 了一題你曾 block 的題，
你的 block 應該——

- (i) 完全保留，直到你親手改變它
- (ii) 保留但標示「管線已改動內容，建議重看」
- (iii) 由管線決定是否失效

我傾向 **(ii)**，因為它尊重你的判斷，同時不假裝題目沒被改過。
但這是**你的審題語意**，我不該替你決定。

---

## 3. 左側面板：統計與篩選混在一起

### 觀察到的

`review_ui/v2.html:527-551` 的左側欄實際內容：

| 元件 | 互動 | 來源 |
|---|---|---|
| 進度條 `doneCount`/`totalCount`/`doneBar` | **不可點** | JS 現算（`02-area-question.js:165-170`） |
| 4 個下拉：類科／年度／考次／科目 | 可選 | `/api/queue_index` 分類樹 |
| 麵包屑 `crumbs` + `scopeCount` | **不可點** | JS |
| `有圖` checkbox | 可選 | — |
| 6 個 chips：全部／題組／未看／我擋的・需重看／AI・管線退回／爭議 | **可選** | 每 chip 有 `<span class="n">` 計數 |

所以「統計」具體指 **進度條** 與 **`scopeCount`**——這兩個確實不可點，
而 6 個 chips **已經是**可選的篩選器（且各自帶計數）。

另一個關鍵事實：**6 個 chips 的計數是 JS 從 `S.view` 現算的**
（`02-area-question.js:26-170`），不是從 API 拿聚合數字。所以它們彼此一致，
但每次換 scope 都要重算。

### 為什麼會這樣

進度條與 `scopeCount` 是**最早的功能**，目的只是「讓你知道還剩多少」，
不具備篩選能力。後來加的 chips 才是有篩選能力的元件，但**沒有取代舊的**，
所以兩種並存，佔掉可觀的版面。

這正是 umbrella `AGENTS.md` 說的那件事：

> **改進要用取代，不是分岔。** 兩個做同一件事的東西，就是兩個可以不一致的地方。

### 待決問題 3

我上一則問得太窄（「要不要讓它可點」）。真正該問的是**這一欄要承擔什麼**：

| | 做法 | 結果 |
|---|---|---|
| A | 進度條／`scopeCount` 也變成可點（點「已過目 120」→ 篩出那 120 題） | 保留現有版面，新增第 7、8 個篩選器 |
| B | **整併**：把 4 下拉 + 6 chips + 進度條重組成「一個選範圍、一個篩狀態」 | 版面大幅縮減，但要重做整個左欄 |
| C | 只移除你認為無意義的部分 | 最小改動，但可能移除你其實需要的資訊 |

**我需要你先回答一個更前面的問題**：你審題時的**動線**是什麼？

- 你是「先選一個科目，然後一路 S 到底」？ → 那 4 個下拉很重要，chips 是輔助
- 還是「我今天只想處理所有『我擋的』題」？ → 那 chips 才是主軸，下拉只是範圍限制

**動線決定了哪個元件該是主角。** 我不猜，因為猜錯就是白做。

---

## 4. 重新載入會把你踢出討論區（機制已定位）

### 觀察到的

`review_ui/v2/05-boot.js:77`：

```js
boot().then(() => showArea(areaFromHash(), { push: false }));
```

看起來「從 hash 決定模式」是對的。但**執行順序**是問題：

```
boot()
  └─ buildScope(tree)                      05-boot.js:13-19
       └─ refreshScope()                    01-core.js:263-283
            └─ applyScope()                 01-core.js:329-331
                 └─ S.index = 0;            01-core.js:482-485
                    S.editing = false;
                    scopeToHash();          ← 這裡把 hash 覆寫成「題目 scope」
                                           01-core.js:228-232
  └─ showArea(areaFromHash())              05-boot.js:77
       ↑ 此時 hash 裡的 #錯題 已經沒了 → 回落到 'question'
```

而且 `scopeFromHash()`（`01-core.js:214-224`）把 `#錯題/...` 的**第一段當成類科**，
而不是先剝掉模式前綴，所以它認不出那個前綴。

**討論區的題號完全沒有持久化**：`D.index`（`04-area-discuss.js:13-20`）
只在記憶體裡。整個 v2 唯一的 `localStorage` 是**字級**
（`04-area-discuss.js:59-62, 675-683`）。

### 為什麼會這樣

兩個獨立的缺陷**疊在一起**：

1. **啟動順序**：scope 初始化（會寫 hash）跑在模式讀取（會讀 hash）之前。
2. **沒有持久化**：討論區的當前題目從來沒有被寫進任何地方。

所以就算修好 (1)，重新載入仍然會回到第 0 題——因為 (2) 沒做。

### 待決問題 4

「當下環境要固定」的**範圍**要定清楚：

| | 要持久化的東西 | 存哪 |
|---|---|---|
| 最小 | 模式（首頁／題目／答案／討論） | hash（已有機制，只要修順序） |
| 中等 | 模式 + 討論區當前題目 | hash 或 `localStorage` |
| 完整 | 上面 + 題目區當前題 + 篩選狀態 + scope | `localStorage` |

**我的建議是「中等」**，理由是：題目區的當前題**已經**寫在 hash 裡（`#類科/年/次/科目/qNNN`），
所以它其實已經會復原；真正缺的是**討論區的題號**。

**但這裡有一個你要決定的設計問題**：討論區的「當前題目」該不該進 hash？

- 進 hash → 可以分享連結、上一頁／下一頁可用，但 hash 會變得很長
- 存 `localStorage` → 乾淨，但**跨裝置不同步**（你在 A 電腦看到第 50 題，換 B 電腦回第 1 題）

因為你提到「刷新環境會導致回到第一題」，聽起來你是**在同一台裝置**刷新。
如果是這樣，`localStorage` 就夠。但你若會在多台裝置間接續工作，就要進 hash 或後端。

---

## 5. 常駐代理已經存在（這改變了整件事）

### 觀察到的

你以為要從頭打造，實際上 **`qbr/scripts/repair_daemon.sh` 就是你要的東西**：

```bash
INTERVAL=1800                                    # 30 分鐘 ✓
while true; do
  confirm_dispute.py --blocked-only --skip-confirmed --limit 5 --escalate
  sleep "${INTERVAL}"
done
```

**權限邊界已經做對了**：

| 疑慮 | 實際 |
|---|---|
| 會冒充人類審題者嗎？ | 不會。寫 `question_ai_findings.jsonl`，署名 `reviewer: "repair_agent"`；**不碰**人類的 `question_review_events.jsonl` |
| 會自動改題嗎？ | 不會。只寫 advisory；`--apply` 不在指令裡 |

**排程不存在**：repo 內沒有它的 cron／launchd。唯一找到的 launchd job
（`deploy/qbr-review/com.timsvms.qbr-review-ensure.plist`，每 300 秒）
是**顧 UI 容器**的 watchdog，與代理無關。

### 為什麼「細節沒做好」——三個具體的接點斷裂

**① 選題空轉**

`repair_daemon.sh:12-23` 自己記錄：原策略是「找沒有偵測器能解釋的 block」，
但三條偵測規則上線後，**304 題全部都有偵測器** → 每輪掃零題。

**② 空集合不設防（無人值守時最危險）**

`confirm_dispute.py:274`：

```python
if blocked and key not in blocked:   # blocked 為空集合時 → false → 不 continue
    continue                          # → 反而把「所有爭議題」當成可掃
```

設計契約是「只處理人已 block 的」，但**讀不到 block 時它會越權**。
`qbr/tests/test_confirm_dispute.py:285-305` 只測了集合非空的情況，沒覆蓋空集合。

**③ 你的回答沒有回流**

`repair_daemon.sh:55-59` 宣稱「人對 ask 的回答下一輪提示詞讀得到」，
而 `docs/skills/review-ui-v2/SKILL.md:148-152` 也這樣寫。
但 `confirm_dispute.py:506-507` **只讀 `PRINCIPLES_STREAM`**——
你回答的內容**從來沒有進入下一輪的提示詞**。

**這正是你說的「很多細節沒做好」的真正原因**：不是邊界沒定，
是**迴路的兩端沒接上**（選題端空轉、回饋端斷線）。

### 為什麼搬去 Mac Studio 是一次架構變更

代理目前的模型走 `qbr/src/qbr/engines.py:49-57`：

| 引擎 | 端點 | 站上活著嗎 |
|---|---|---|
| `splash` (預設，daemon 用) | `127.0.0.1:8088` | **沒有** |
| `mtplx-35b` | `127.0.0.1:18120` | **沒有** |

而你指定的 `192.168.10.90:8888`（DGX，`qwen3.8-flash-next`，context 524,288）
**repo 完全沒有引用**。

技能檔 `skills/operate-local-open-models/SKILL.md` 明文：

> **A remote inference host is not a fallback, configured default, or implicit dependency.**
> If a future task needs another engine, it must specify a new endpoint, model,
> authentication, data boundary, and rollback contract before use.

所以「用 DGX 模型」= 新增一個跨主機 AI 節點，charter 要求
「另經 owner-approved 方向、契約與驗證後加入」。

### 待決問題 5

**5.1 要做哪一條？**

| | 做法 |
|---|---|
| A | **啟動既有 daemon**，修上面三個接點，裝 launchd 到 Mac Studio |
| B | 走 `docs/preserved/review-feedback-agent.md` 那條「自我進化」路線，從頭做 |
| C | 兩者都要 |

我建議 **A**：東西已經存在且權限正確，缺的是修三個接點 + 常駐。
B 那條線的設計本身還沒定案（該文件列了 6 個未決問題，是 owner 專屬決定）。

**5.2 模型放哪？**

- 用 DGX（`192.168.10.90:8888`）→ 需要你核准跨主機節點與資料邊界
- 在 Mac Studio 上跑 `splash` → 站上目前沒有這個模型，要另外部署
- 代理留在筆電、只把結果推去站上 → **但這正是過去失敗的模式**
  （`qbr/reports/` 記錄「71 筆只留在筆電」的教訓）

**5.3 它的產出要走到哪裡？**

現在只有「寫 advisory，有疑問就反問你」。
但你的第 8 條註解說「**你同意修法但系統沒修**」——
所以真正的問題是：**要不要讓它產生「可一鍵套用的修法」？**

（這牽涉 G3：`apply_dispute_repairs.py --apply` 是 G3，需要你的人為核准。
所以「產生修法」可以是 G2，「套用」必須是 G3。）

**5.4 `question_ai_findings.jsonl` 已經 508 MB。**

這是「累積沒有消費者」的證據（站上該檔 11:40 後未再變動）。
要保留、歸檔、還是清掉？

---

## 6. 你的 11 類註解：它們是同一件事的四個面向

從 5,059 筆事件挑出你手寫的指示，共 **11 種**：

| # | 你的話 | 題數 |
|---|---|---|
| 1 | `Ae-αt＋Be-βt 並沒有改到` | 7 |
| 2 | `這類是圖片或特殊字型被放入選項，PO2、PCO2 數字下標` | 10 |
| 3 | `⁻0.23t 是上標` | 1 |
| 4 | `表格應該用截圖的` | 1 |
| 5 | `mineral oil 560g…這堆文字事實上是表格，用文字讀就失去意義` | 1 |
| 6 | `應該是 酶，早期試卷有大量這類問題` | 2 |
| 7 | `下標` | 2 |
| 8 | `你自己的註解說要改成 GABA_B，這我同意但是你沒有修` | 1 |
| 9 | `你應該自行截圖確認` | 1 |
| 10 | `亂碼` | 1 |
| 11 | `答案沒進去` | 1 |

**歸類後只有四個面向**：

- **上下標**（1、3、7、8）：英文上下標被讀成平排
- **表格**（4、5）：PDF 裡是文字，但語意上是表格
- **圖片／特殊字型**（2、9、10）：選項裡是圖或非標準字型
- **缺答案**（11）

而且第 6 條（`酶`）透露一個重要事實：**「早期試卷有大量這類問題」**——
所以這不是零星缺陷，是**成批的、可規則化的**。

第 8 條最關鍵：

> **「你自己的註解是：怎麼修：將『GABAB』改為『GABA_B』…這我同意但是你沒有修」**

**系統會產生建議，但沒有執行建議的迴路。** 這正是
`docs/preserved/review-feedback-agent.md` 診斷的核心缺口。

### 待決問題 6

| | 做法 |
|---|---|
| A | 腳本把你的 notes 當**指令**，產生 `repair_proposal`，**你按一次套用**才改題 |
| B | 讓 agent 直接改題（**不合 `AGENTS.md`**：AI 不得冒充人類審題者） |
| C | 只修有明確機械解的（如上下標），其餘留人工 |

**我建議 A**，理由：它符合既有契約（「只能提出候選，不得直接修改題目」
是 `review_feedback.py:build_ai_task()` 已經寫好的約束），而且
**第 8 條的訴求正是「我同意了，請去執行」**——一鍵套用就滿足它，且不越權。

**但這裡有一個你要決定的**：這 11 類要修的是**已發布的題目**還是**佇列裡的候選**？
（如果是已發布 package，就牽涉 package 版本與 outbox proposal 回流 catalog 的流程。）

---

## 7. 我建議的施工順序（有依賴關係）

```
第 0 步  備份 5,059 筆紀錄                    ← 動任何東西之前
   │
   ├─► 決策 1/2/4（都是「寫入與紀錄」）        ← 可並行，但都要先備份
   │
   ├─► (2) 補圖          ← 最急，且純設定
   ├─► (1) F5            ← 每次刷新都受影響
   │
   └─► 決策 3（左側面板）  ← 需要你先回答動線
        │
        └─► 決策 5（agent 常駐）  ← 最大工程，且依賴 6（修法產出）
```

**為什麼備份是第 0 步**：決策 4 會改變紀錄的**解釋方式**。
如果推導邏輯寫錯了，你損失的是**不可重建的 2 個月審題歷史**。

---

## 8. 需要你回覆的問題清單（逐項，不摺疊）

1. **佇列可重建嗎？**（決策 1 的前提）那些 744 個 crops 是從 PDF 現切、還是原始素材？
2. **可寫範圍**：A（整包 queue rw）／B（逐檔 bind）／C（語料也開）
3. **`reset_review` 後你的 block 該如何**：(i) 全保留 (ii) 保留但標示需重看 (iii) 由管線決定
4. **管線的說明要不要搬到新欄位**，讓 `notes` 只放你的字？
5. **你審題的動線**是「選科目一路走」還是「專攻某類狀態」？（決定左欄誰是主角）
6. **左欄要不要整併**：A（統計變可選）／B（整併重做）／C（只移除）
7. **要持久化到什麼程度**：模式／模式+討論題號／全部狀態
8. **討論區題號進 hash 還是 localStorage**（跨裝置嗎？）
9. **agent 走哪條**：A（啟動既有 + 修三接點）／B（重做自我進化）／C（都要）
10. **模型用 DGX 還是站上自架**（DGX 需你核准跨主機節點）
11. **agent 要不要產生「一鍵套用的修法」**（G2 產出 / G3 套用）
12. **508 MB findings 怎麼處理**
13. **11 類註解修的是「已發布題目」還是「佇列候選」**
14. **權限確認**：改 compose／v2 JS／`review_state.py`／裝 launchd／建目錄／呼叫 DGX／**先備份**

---

### 相關入口

- 審核迴圈操作程序：[`../run-question-review-loop/SKILL.md`](../run-question-review-loop/SKILL.md)
- 修理代理權限邊界：[`../../../qbr/AGENTS.md`](../../../qbr/AGENTS.md)
- 尚未完成的回饋代理設計：[`../../preserved/review-feedback-agent.md`](../../preserved/review-feedback-agent.md)

<!-- project-map:belongs-to -->
## 這一層在哪（回上層的路）

> **這是本子專屬技能**：只服務這個子專案。其他子專案要用同一件事時，先確認是不是該變成全域共通技能。

- 本層入口：[`../../../AGENTS.md`](../../../AGENTS.md)
- 不確定從哪開始：[`project_map`](../../../../project_map) 是整棵樹的可點擊地圖
- 卡住時的回溯路徑：技能 → 本層 `AGENTS.md` → `project_map` 入口文件鏈 → 傘層 → charter
<!-- /project-map:belongs-to -->
