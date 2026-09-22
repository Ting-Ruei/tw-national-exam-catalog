---
name: run-question-review-loop
description: 人工審核完一批題目之後，在 tw-national-exam-catalog/ 這一層打開 pi 指揮的工作線 —— 把常駐機的人類決定拉回來、找出沒有偵測器解釋的 block、用當次核准的本機模型問出原因、把確認的類別寫成規則、全庫重掃，並把改過文字的題目標成「需重看」。當使用者說「跑審核迴圈」「跑 AI 審題」「建新規則並重掃」「這批審完了」時讀這份。
---

# 跑審核迴圈（在 catalog 層指揮）

**這一節的起點是 `tw-national-exam-catalog/`，不是傘層 `ai_learning_platform/`。**
使用者審完一批題目之後，會在這一層打開 pi，然後說「跑審核迴圈」。

工作範圍就是**題目匯入、審核、優化這一條線**。要改 `platform-app/` 或其他子專案，
那是在傘層開的 session，不是這一條。

> **現況與量測的權威在 [`qbr-pipeline-status/SKILL.md`](../qbr-pipeline-status/SKILL.md) §7.5。**
> 本文件是**操作程序**（每一步打什麼、判準是什麼）；§7.5 是**已完成量測的紀錄**。
> 若兩者衝突，先信 §7.5 的數字，再把本文件改對。

## 這條線的形狀（單向，不能倒著跑）

```
人標 block ─► 既有偵測器能解釋嗎？
                ├─ 能 ─► 不是缺口（是偵測結果沒在畫面上被看到）
                └─ 不能 ─► 這才是要建規則的地方
                            ├─ 地端模型問出「哪裡錯、怎麼修」（寫進 append-only 紀錄）
                            ─► 人確認這是一個類別 ─► 新規則進 disputes.py ─► 全庫重掃
                                  ─► 有人判斷過的 → reset_review（附加，不覆寫）
                                      沒人判斷過的 → 不動
```

**不能反向跑**：人的沉默不是核准；`needs_review`（我無法判斷）**不是輸入**——
無法從「我不確定」長出一條規則。

## 每一步打什麼

```bash
cd tw-national-exam-catalog                              # 這一層，不是傘層

# 0. 把常駐機（審題的家）上的人類決定拉回筆電。**順序是刻意的：先拉，後推。**
bash scripts/pull_station_reviews.sh

# 1+2. 收集 block、找出沒有偵測器解釋的那些（分考卷／考科列出，看是一因還是多因）
cd qbr
.venv/bin/python scripts/repair_loop.py --queue data/review-queues/live

# 3. 把「沒有被解釋的 block」送給地端模型問原因（會寫成 append-only 紀錄）
.venv/bin/python scripts/ask_about_blocks.py --queue data/review-queues/live --model splash

# 3b. 問「整庫每一題」一遍（使用者的規則；約 12h、可中斷續跑）
.venv/bin/python scripts/ask_about_blocks.py --queue data/review-queues/live \
    --all --concurrency 4 --resume

# 3c. 讀回模型說了什麼，類別與個案分開看
.venv/bin/python scripts/ask_about_blocks.py --queue data/review-queues/live --report

# 4. 新規則寫進 qbr/src/qbr/disputes.py 之後，全庫重掃
.venv/bin/python scripts/repair_loop.py --queue data/review-queues/live --rescan

# 5. 把「已被判斷過、但新規則說那次判斷看的是錯的」重開（dry-run 預設；--apply 才寫）
.venv/bin/python scripts/repair_loop.py --queue data/review-queues/live \
    --rescan --propose /tmp/reset.json
.venv/bin/python scripts/append_reset_review_events.py \
    --proposal /tmp/reset.json --events data/review-queues/live/review-ui/question_review_events.jsonl --apply

# 6. 把新的 AI 紀錄推回家（append-only；守衛會拒絕「站上有筆電解釋不了的事件」的情形）
cd .. && bash scripts/push_reviews_to_station.sh
```

## 模型：只用當次核准的本機引擎

| 引擎 | 端點 | 什麼時候用 | 關鍵開關 |
|---|---|---|---|
| **`splash`** | `127.0.0.1:8088` | 缺陷判讀、**看截圖** | `reasoning_effort:"none"`（思考要關，見下）|
| **`mtplx-35b`** | `127.0.0.1:18120` | 當次任務核准的文字／圖像工作 | `chat_template_kwargs.enable_thinking:false` |

- **`ask_about_blocks.py` 的預設引擎就是 splash**（`--model splash`）。它由量測選出，不是由大小
  選出：在 `108030:305 q076` 上它報 `FIGURE_MISSING`，而小引擎說 NONE；在 `q049` 上它指出康熙
  部首——那正是決定性掃描**明確判定無害**的東西。**一個引擎回答全部**也有紀錄上的理由：兩個引擎
  回答同一題，就是兩份會互相矛盾的筆記，沒有規則說哪一份對。
- **思考必須關**：Splash 用 `reasoning_effort:"none"`；MTPLX 用
  `chat_template_kwargs.enable_thinking`（它**靜默忽略** `reasoning_effort`）。開關是**引擎的屬性**，
  所以存在 `ENDPOINTS` 裡跟引擎一起。詳細與本機引擎見
  [`operate-local-open-models/SKILL.md`](../../../../skills/operate-local-open-models/SKILL.md)。
- 未經當次任務核准的模型 provider 不得使用。

## 判準：一條規則看 **block 率**，不是命中數

「它抓到幾題」不是判準。判準是「它抓到的題目裡，人真的認為有問題的比例」。基準線是這個題庫
的 block 率（**5.4%**）。

| 規則 | 候選 | 已判 | block | block 率 | 決定 |
|---|---|---|---|---|---|
| `substituted-script` | 164 | 4 | 4 | **100%** | 建 |
| `flattened-offset` | 174 | 6 | 6 | **100%** | 建 |
| Kangxi radical 未折疊 | 2,293 | 71 | 3 | 4.2% | **拒絕（低於基準線）** |
| 提到圖但圖不在自己身上 | 887 | 7 | 1 | 14.3% | **擱著（樣本太小，且翻掉 6 題已接受）** |

**不是「不確定所以不做」，是量完之後決定不做。** 決定性的一條是：一條新規則若把**已經被人類
接受**的題目翻成「需重看」，它要有對等的理由，否則只是把審題者拉回來做白工。

## 五個一定要記住的陷阱

1. **`reset_review` 是附加，不是改寫。** 人的決定是關於「當時眼前那份讀法」的陳述；讀法變了，
   舊決定不是錯的，只是關於不同的文字。所以新事件說明這件事、保留舊事件。
   `load_review_events` 會保留 `correction`，所以人自己的修正與手動圖片不會被修復弄丟。
2. **推回前一定要先拉。** 站上是審題的**家**，筆電是消費者。順序錯了守衛會拒絕（那是**真訊號**，
   不是 bug：站上有筆電解釋不了的決定）。驗法是「筆電有、站上沒有 = 0」。
3. **AI 輸出是 advisory。** 它寫 `question_ai_findings.jsonl`（append-only，沒有 `action`、沒有
   `reviewer`），**永遠不寫人類審核事件**。只有人能把題目變成 ready。
4. **`--learned` 要帶上這批已知的事。** 提示詞是量測的一部分（`prompt_version`）；把上一批
   學到的方向用 `--learned CODE=意義` 傳給下一批，下一批才不是從零開始。**但 `--learned` 只陳述
   已知／已排除的事，不能寫「去找 X」**——那會把模型推向編造。
5. **重建佇列要暫停服務，而且輸出要放在真佇列之間。** 見
   [`qbr-pipeline-status/SKILL.md`](../qbr-pipeline-status/SKILL.md) §7 末「重建佇列：兩個陷阱」。

## 驗收

**「完成」= 審題者在畫面上看得到。** 一條規則跑完全庫、block 率夠高，但新爭議沒有出現在
ReviewUI 上，就還沒完成。

```bash
cd tw-national-exam-catalog/qbr && .venv/bin/python -m pytest tests/ -q     # 管線
cd tw-national-exam-catalog && python3 -m unittest discover -s tests        # catalog
python3 scripts/validate_agent_governance.py
```

新規則一定要有**負對照**（新測試必須在舊行為上失敗）。**驗收標準是兩個引擎在同一張紙上一致，
不是測試全綠**——一個綠的測試可能只證明了一個錯的規則。


## 2026-09-22 本輪的量測與改動（實測，寫入當次 run 的 validation report）

**上下標的兩種拼法（同一個事實，第三種是量測形）**——規則是「一種拼法一个表徵式」，
不是兩套樣式；`qbr.extract` 負責把雨者互相可換（`html_sup_sub` ⑄→markup、`plain_sup_sub` ⑤→ASCII），
`disputes._texts` 先折回 ASCII 再比對（否則 `⑨` 的 regex 收斂不到 `<sub>` 包住的形）。
（實測 3.14：`'Ⅰ'.isalnum()` 為 True、`'⁻'.isalnum()` 為 False，故新類判讀同此。）

| 類（`disputes.KINDS`） | 判準（話） | 命中 n | 已判 | 精確率（對人已判） |
|---|---|---|---|---|
| `flat-offset`（`⑨` 新） | 單位與冪次同列、行內無序號、雨側有標點 | 178 | 6 | 100% |
| `punctuation-only-option`（`⑩` 新） | 选项全為標點、字串非空 | 35 | 12 | 29.4%→（實测 5/17） |
| `table-flattened`（`�22` 新） | 行首=數字 且 至多 2 個列 | 23 | 19 | 17.4% |
（對照基準＝`qbr-pipeline-status/SKILL.md` §7.5。block 率基準 5.4%。此為三筆「35/23/178」與先前
「34/17/22」不同——以列 22 次為準，因新行内無重扫；表 23 次為最終一次。）

**为何 `medium` 而非 `high`（本機 27B，thinking ON）**：`reasoning_effort` 只收命名檔位
（整數 1500 回 HTTP 400）。`high` 的思考把 budget 吃光（`completion_tokens` 頂满、`content` 空）；
`medium` 在 4000 內同時留下 reasoning 與 content（實測 reasoning≈1102/834，總 1295/920）。
故腳本本體 `("medium", 4000)`，空 content 時降 `("low", 3200)` 重試一次（思考模式重試會翻倍代價）。
拼错開關（如 `reasoning=true`）表現在 `usage`：`reasoning_tokens` 有=有 thought；`null`=思空。
（實測此 vLLM 把 `reasoning_tokens` 放進 `completion_tokens_details`，非 `reasoning`。）

**30 分鐘定時（續跑）**：`repair_agent.py --every N --interval 1800`（同 session 輪迴，`call_omo_parse_result`
取到 id 一致）；单無 `--every` 為 1 輪（預設）後自動退出。空內容＝該次達 `max_tokens` 上限（用 `medium` 應可避）。
**未解（本輪 4 項極大 table，`medium`/4000 仍空）**：記於 `experience.json.open_cases`，人工對照 PDF。

**佇列刷新（Q9 幂等）**：`refresh_queue_text.py --queue <root>` 冪等（重複 call 同 sha256）。
**QA 對照**：本輪 4 項（12/10/12/12 全過）。**否決**: `question_typology` 的 26 鍵之「number」
為 3 個 distinct（實測），非 1；`_split_block` 前缀 `PAPER[0-9]+` 非 `PAPER[0-9]+$`（3.14 之 `str.endswith`）。
**limit 上限**：本機（3.14）核 5；station（builtin）核 4（`PAPER[0-9]+` 同）。）
（實測此 3 個並發端點為本機 4 位埠號、且本機 `python3` 的 `json.load` 可用。）

<!-- project-map:belongs-to -->
## 這一層在哪（回上層的路）

> **這是本子專屬技能**：只服務這個子專案。其他子專案要用同一件事時，先確認是不是該變成全域共通技能。

- 本層入口：[`../../../AGENTS.md`](../../../AGENTS.md)
- 不確定從哪開始：[`project_map`](../../../../project_map) 是整棵樹的可點擊地圖
- 卡住時的回溯路徑：技能 → 本層 `AGENTS.md` → `project_map` 入口文件鏈 → 傘層 → charter
<!-- /project-map:belongs-to -->
