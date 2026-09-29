---
name: review-ui-v2
description: Operate and maintain the question Review UI v2 — the linear single-question review console at /v2, its keyboard map, its filter/navigation contract, how to run it against a queue, and what must never be forked. Use when reviewing a question bank, when a filter or W/S navigation behaves unexpectedly, when adding a panel or a decision to the review console, when the reviewer's records must survive a rebuild, or when deciding whether to change v1.
---

# Review UI v2

`tw-national-exam-catalog/review_ui/v2.html`, served at `/v2`. **v2 is the baseline.**
v1 (`mobile.html`, `workflow.html`) is **reference-only, no longer maintained** and lives in
`review_ui/v1-reference/`. Fix v1 only to keep it *working*; never add a feature to it.

The rule behind everything here: **improve by replacing, never by forking.** A second console that
shows the same questions is a second place a reviewer's records can be lost.

> **This skill is how to *operate* the console. To *repair* it — a wrong render, a picker that moves
> another picker, a dead button, an empty or self-emptying 錯題討論區 — read
> [`repair-review-ui-v2`](../repair-review-ui-v2/SKILL.md).** It holds the measurement discipline, the
> three verification harnesses, the catalogue of the ten defects already found and fixed, and the
> open items a next round can optimise.

## Run it

```sh
cd tw-national-exam-catalog
scripts/review_run.sh <workdir> 8774      # workdir = a built review queue
```

Under the hood (this is what a run actually is, and the flags matter):

```sh
python3 scripts/serve_question_review_ui.py \
    --candidate-jsonl  <workdir>/review-ui/candidates.jsonl \
    --issue-csv        <workdir>/review-ui/issues.csv \
    --review-log       <workdir>/review-ui/question_review_events.jsonl \
    --review-backend jsonl --host 127.0.0.1 --port 8774
```

Routes:

| Route | Serves | Status |
|---|---|---|
| `/v2` | `v2.html` | **baseline** |
| `/mobile` | `v1-reference/mobile.html` | reference-only |
| `/workflow`, `/mobile/workflow` | `v1-reference/workflow.html` | reference-only |

`/v2` is served `no-store` — **editing `v2.html` goes live without restarting the server**, and the
review log is opened append-only (`"a"`), never truncated.

The current v2 decision controls and `/api/review` record human actions. The interim QBR permission
allows separate AI-owned workflow status and result revisions, but this skill does not define or
implement that writer. Never route an AI status through a human control or human review event; a data
path for AI status must be defined separately during the planned workflow redesign.

## The five areas (首頁 / 題目審核區 / 答案審核區 / 錯題討論區 / 原則區)

v2 is **one page with a mode switch**, not five pages. Five pages would mean five loaders of the same
198 MB `candidates.jsonl`, and so five chances for them to disagree about what is in the queue. The
topbar switches the pane; the mode is in the hash so each area is linkable and a reload reopens it.

| Area | Hash prefix | Does | Writes |
|---|---|---|---|
| 題目審核區 | none, or `#審題/…` (bare `#類科/年/次/科目`) | reads the paper beside the extracted text | `/api/review` |
| 首頁 | `#首頁/…` | counts only; every number comes from the endpoints below | **nothing** |
| 答案審核區 | `#答案/…` | reads the **answer sheet** beside the extracted answers, one sheet at a time | `/api/answer-review-batch` |
| 錯題討論區 | `#錯題/…` | the stuck questions — what a detector measured, what a model read off the page, and what the text should say | `/api/review` (the reviewer's own save) |
| 原則區 | `#原則/…` | the standing 基本原則 (what the model must always honour) and the agent's 反問 that are waiting for an answer | `/api/principles`, `/api/repair-question` |

**一區一檔，載入序＝檔名前綴**（`v2.html` 以 `<script src>` 串起；伺服器由檔名供應 `v2/*.js`，見
`legacy_assets.py::_v2_script_response`）。所以「新增一區」＝新增一個檔＋一個 `<script src>`＋
一筆 `AREA_BY_NAME`，沒有第二份路由清單可以忘記——2026-09-24 就是忘了那一份，
兩個新檔在瀏覽器裡 404，原則是**整區空白卻沒有任何錯誤訊息**。

`03-areas.js` 是外殼：區表（`AREA_BY_NAME`／`AREA_PREFIX`／`PREFIX_AREA`／`AREA_LABEL`）、
`showArea`、`renderArea`、`invalidateAreas`、`afterWrite`、`renderHome`。每一區的畫面與寫入在
自己的檔案裡（`02-area-question.js`、`03-area-answer.js`、`03-area-principles.js`、
`04-area-discuss.js`）。

### 寫入之後只有一條路：`afterWrite()`（2026-09-24）

使用者說得很精確：「後面覺得我做了，前面覺得後面都沒做」。原因是每一區各自記著**自己的**快照
（題目區的 `S.verdict`／`S.notes`、討論區的投影、答案區的草稿），而一筆寫入的真相在伺服器上。
以前四區各自寫「`invalidateAreas()` ＋ 重畫自己」，於是任何一區寫入之後，**其他區手上的舊答案沒有
人知道**。

現在：任何一區寫入後呼叫 `afterWrite()`（＝`invalidateAreas()` ＋ 重畫當下這一區）。而
`invalidateAreas()` 在**別區**寫入時立起 `A.questionStale`，題目區下次被切回來時
（`showArea('question')`）會走 `refreshScopeRows()`：**重讀這個範圍的列，游標留在同一題**。

驗收（實機，2026-09-24）：在題目區叫 `invalidateAreas()` → 0 次 `/api/candidates`（負對照：自己
寫入不需要重讀）；切到討論區再叫 → `A.questionStale` 為 true，切回題目區發出 **1** 次
`/api/candidates`，游標位置不變、`A.questionStale` 歸零。

### 題目審核區：紙本表格用截圖顯示（2026-09-24，owner 的決定）

owner 原話：「叫你這種文字型表格要用截圖來顯示，聽不懂嗎」。文字型表格（紙本排成表格、抽取器
壓平成一串字，例如 `劑型  給藥途徑  劑量（mg）  AUC (μg．h/mL) 錠劑  口服  100  40…`）人得自己
數字數才看得出哪個數字屬於哪一欄。

**誰做什麼**：表格長什麼樣子是**讀出來的意義**（判讀在 `transcription.table_lines` 裡引述它讀到的
那幾行），紙本上那幾條線在哪裡是**量出來的性質**（`crop_run_figures.py --queue` 把那些行在頁面上
**量到的格子**聯成一個框切一張圖，`asset_role: figure-crop`、`label: paper-table`）。
owner 否決了「腳本自己猜哪裡是表格」：「表格題不是應該AI讀完定位之後進行切割嗎，你一直擴充腳本又會
overfitting」——沒有幾何猜測，就沒有下一條為了修正猜測而加的規則。

**畫法**（`02-area-question.js` 的 `questionTextHtml`，一個渲染器，原則區的 `prefix` 重畫走同一支）：
題幹散文照原本畫，**表格那一段改畫截圖**，抽取到的原字收在截圖底下的
`<details class="paper-table-text" data-view="table-text">`（原樣、`pre-wrap`、不重排——它仍然是這一題
的證據，只是不再是第一眼看到的東西）。`data-view` 說的是元素是什麼（與範圍 chips 同一個約定），
**程式不看摘要上寫的字**。下方的 `figureHtml(candidate, skip)` 收下表格裁切當 skip，所以同一張圖
不會畫兩次。

**四種形狀都量過**（`node scripts/test_v2_table_browser.mjs <base>`，真 Chrome）：
沒有裁切的題（站上 q065 今天的樣子）→ 題幹一字不差；有裁切但讀法引的行在題幹裡找不到 → 整段照畫、
截圖補在後面、不畫空的「文字版」；定位得到 → 題幹只剩散文、截圖在那一格、文字版逐字（且必須是
那一列 stem 的**後綴**）；題幹整段就是那張表 → 題幹留空、截圖與文字版帶著全部內容。
**負對照**：把 `git show HEAD:review_ui/v2/02-area-question.js` 蓋回去跑同一個 URL → **7 項 BAD**
（含「表格的資料列不再是題幹文字」「表格的位置上畫的是那張截圖 0 張」）；換回新版 → 全部符合。

### 答案審核區（`review_ui/v2/03-area-answer.js`）

這一區存在的理由就是「右邊要有答案卷讓我對照」，所以右欄是**答案卷 PDF**（不是題目卷），
而卡片是**一張答案卡**（一份答案卷一個 `sheet_key`），不是一題一頁。三欄：卡片清單、
答案對照（題號／抽出的答案／審核狀態），右欄 PDF。`renderAnswerPdf()` 的檔案鏈：

```
source_files[kind]  →  只有 official_pdf 才退到 metadata.answer_pdf_relative
                    →  answer_pdf_primary_relative  →  answer_pdf_primary
```

**為什麼一定要退到 metadata**：2026-09-24 量到線上 `/api/answer-candidates`（113 張卡）的
`source_files` 四欄**全是 null**，答案卷的路徑只存在 `metadata.answer_pdf_relative`。只讀
`source_files` 的版本在真實資料上右欄永遠是空的——而那一欄是這一區的全部理由。
**不要自己推 `_layout`／`_origin` 的兄弟檔名**：那是伺服器的規則（`sibling_pdf`），客戶端再推
一次就是同一條規則的第二份實作。沒有路徑時要**說出來**（「這一張答案卡沒有答案卷 PDF 路徑，
右欄無法對照」），不要留一個安靜的空 iframe。

- **每題的「阻擋」是開關**：`answerToggleBlock()` 讀 `row.answer_review.action === 'block'` 決定這次
  是 `block` 還是 `unreviewed`，單題一個請求，寫入後 `afterWrite()`。實測（scratch log，
  `POST /api/answer-review-batch`）：`block` → `answer_review.status='reviewed'/action='block'`；
  再送 `unreviewed` → 回到 `unreviewed`。取消是**真事件**，所以按鈕不會停在紅的。
- **ABCD 是草稿，不是寫入**：點字母只改 `A.answerDraft`，零個請求；四種寫法（單選／複選＋／
  任一／任一＋複選）是同一組字母的**另一種寫法**（`parseAnswerSelection`／`formatAnswerSelection`），
  再點同一字母＝從草稿拿掉。寫入發生在「儲存答案修正」（`correct`）或整份那一排。
- **`reviewed_answer` 一定要送**：它是「紙本上是什麼」（`row.answer`），不是草稿。省略它會存成
  `{"answer": null}`，把這一題拿來對照的答案悄悄清掉。
- **沒有已記錄的審核時，「取消」不送出**：只清草稿並說出來。送一筆沒有內容的取消是假的事件。
- 伺服器會把**新的** `correct` 在沒有既有決定時改寫成 `reviewed`
  （`_reaffirm_standing_action`），所以事件檔斷言要看 `corrected_answer` 而不是 `action`；
  畫面顯示「已審」是對的（`answer_review.correction` 是**字串**，例如 `A+C`）。
- 驗收：`node scripts/test_v2_areas_browser.mjs`（自起 scratch 伺服器；76 檢查）。
  負對照（實測，2026-09-24）：把 `03-area-answer.js` 還原成舊行為 → 「再點同一個字母不會把
  `A` 從草稿拿掉」「答案卷路徑只在 metadata 時右欄開不起來」兩條變紅，其餘全綠。

### 原則區（`review_ui/v2/03-area-principles.js`）

**是獨立的一區**，不是討論區裡的一塊：導覽列第五顆按鈕（`data-area="principles"`）、
`#areaPrinciples`、hash 前綴 `#原則/…`。它管的是「模型要永遠遵守的話」與「代理問人的話」——
基本原則 5 條、反問 13 題待答（2026-09-24 本機量到），兩份資料與首頁第四張卡讀的是**同一個**
`GET /api/discuss` 回應裡的兩個區塊，所以首頁與這一頁不可能各說一個數字。

每一則反問還多一顆 **`去看這一題`**（`gotoQuestionArea`，2026-09-25）：它離**這一區**，切到題目
審核區並開在那一題上。`叫出原題` 只把紙本叫到右邊的窗格，人還停在原則區——看不到那一題的判讀
狀態，也不能在那裡判它，而 owner 的原話是「我**找不到**……那些題目」。走的是既有欄位
（`S.scope` ＋ `S.openQuestion`，也就是貼一個 `#類別/年/次/科目/qNNN` 網址時走的那條路），五個
欄位取自伺服器的身分投影（`identityOf`，**不從 key 猜**）；欄位不齊或那一卷不在類別樹裡就說出來，
不亂跳，落地後還會核對游標真的在那一題上（題目區原本的規則是「找不到就回到第一題還沒審的」，
在這裡那會是一條**安靜的錯路**）。驗收：`test_v2_principles_browser.mjs` 按下它並量題目區的
`#where`／`#viewStem`，負向控制＝整段沒有任何 POST（看不等於寫）。

每一條原則／每一題反問都給得出**原題紙本**：`叫出原題` 以 `focusKey=<candidate_key>` 問
`/api/candidates`（**不是** `candidate_key=`——那個參數只有 `/workflow` 認，對 `/api/candidates`
是無效的，它會回預設的 500 列而你以為那 500 列裡沒有這一題；實測 `focusKey` 回 `focus_injected:true`
且那一列排第一），取 `source_files.official_pdf` 後開在 `#principlePdf`。寫入走
`POST /api/principles`（附 `candidate_key`，所以原則帶著它的例子題）與 `POST /api/repair-question`
（回答反問），寫完 `afterWrite()`——原則區不自己發明收尾順序。

**右欄是兩塊，跟著同一個 `focusKey`**（owner 2026-09-24：「那個原則區的 修理代理的反問 你好歹
右邊上面三分之一顯示UI的題目畫面，右邊下面顯示PDF，不然我真的很難跟你對話」）：上面三分之一是
那一題的**題目畫面**，下面三分之二是同一題的官方**題目**紙本（不是答案卷：只讀
`source_files.official_pdf || metadata.question_pdf_relative`）。上面那塊呼叫**題目區自己的**
`questionTextHtml`（`review_ui/v2/02-area-question.js`，`renderTextSide` 也用它畫 `#textSide`），
所以兩區顯示的是同一份文字、同一組答案標記、同一組圖片規則——抄一份會漂移，漂移的那一天人在
原則區讀到的就不是題目區那一題。傳進去的開關都只有一個意思：**「這是別的區，所以題目區專屬的
東西不要畫」**：

- `prefix: 'pq_'`：同一頁已經有題目區的 `viewStem`／`viewOpts`，節點 id 不能同名。
- `finding: false`：模型意見卡在同一頁左欄（原則區的證據卡）已經有一張一樣的（同一張截圖、
  同一個「哪裡」、同一組逐欄的兩邊），而這塊只有三分之一高——畫進去，題目就被擠到捲軸下面。
- `chrome: false`：題目區那條抬頭（`side-head`「抽出文字」＋`qnum`「第 N 題」）這一格自己已經有
  了（`.pdf-head` 寫出科目、卷號、第 N 題與 key）。這 51px 就是「第四個選項掉到捲軸下面」的差。
- `editor`／`apply` 預設關：編輯框與「帶入修正」按鈕寫的是題目區的 DOM（`#editStem`、委派綁在
  `#textSide`），畫在別區只會是死掉的控制項。

比例寫在 `review_ui/v2.html`：`.principle-pdf .qview { flex:1 }`（可捲）與
`.principle-frame { flex:2 }`。同一題按第二次**不會**再問伺服器（`P.rows` 一份 key → row）。
實測（2026-09-24，`scripts/test_v2_principles_browser.mjs`，1700×957 的視窗）：上 312px／下 568px
→ 0.355（框線與內距算在內；內容區是精確的 1:2），題幹與**四個選項全部**在第一眼（不捲動）就看得到
（同一條斷言在畫回那條抬頭時會失敗——那正是負向控制）。


### 錯題討論區 (the stuck-questions area)

The area exists for the questions **that are stuck**, and nothing else: a question a person rejected
(`block`) or one the pipeline returned for a fresh look (`repair_pending`, `accepted_reaudit`,
`reset_review`). It deliberately does **not** list the queue — measured: the first build pulled all
**79,090** rows and the stuck ones became unfindable. The filter is `reviewStatus=discuss`, defined
**once** server-side as `DISCUSS_BUCKETS` and used by the JSONL path *and* both SQL paths, so the
three backends cannot disagree about what is stuck.

The pane has three columns, and the layout is **the same arithmetic as 題目審核區**: `238px` of list,
the rest split in two (`.compare` is `1fr 1fr`). So the paper pane is the same width the question
area gives the same document. Measured before: `214px | 1fr | 330px` — the paper was about a quarter
of the question area's, while this area needs it *more* (a stuck question is the one you have to read
against the page). **Change the two grids together; never one alone.**

The middle column is a reading order, and each block has one author:

| Block | Shows | Author |
|---|---|---|
| 爲什麼在這 | the reset reason and the previous action | `review_projection` |
| ① 機器偵測 | `disputes` — what the deterministic layers measured | `disputes.py` |
| ② AI 意見 | the model's note, **with the screenshot it was shown** and the mechanical diff | `question_ai_findings.jsonl` (advisory) |
| ③ 原題 | the extracted question, **read** (not an editor) | the parser |
| ④ 擷圖／抽換 | paste (⌘V) / drop / file, placement, replace | the reviewer |
| ⑤ 手動修改 | the stem and each option, on top of the effective (corrected) text | the reviewer |
| ⑥ 註解 | what the reviewer changed and why — **for the AI to read** | the reviewer |
| 右 | the question sheet, decoupled | — |
| 左 | 統計 ＋ 四層篩選 ＋ the stuck list | — |

**⑦ 基本原則 and ⑧ 反問 used to be blocks in this pane and are not any more** (2026-09-24, at the
reviewer's request): they are a page of their own (原則區). They were the only two blocks in this
area that are not about *this question*, so keeping them here meant the reviewer scrolled past
another question's material to do a job that is not about a question at all. The panes were **moved,
not copied** — deleting them here and adding them there is the whole change, and
`scripts/test_v2_areas_browser.mjs` asserts this pane no longer contains them.

- **③ 原題 is drawn with `richText()`, not `esc()`.** The paper's inline markup is rendered (so
  `<sub>` is a real subscript); a stuck question must not be shown with *less* of itself than a normal
  one. It is deliberately **not** a textarea: 原題 is what the paper says, ⑤ is what you intend to
  change it to, and mixing them loses the only thing this area accumulates. Pinned by
  `tests/test_review_ui_discuss.py`.
- **The font control is one CSS variable (`--reading-size`), not eight `font-size`s.** The extracted
  text and the PDF must each be zoomable (the browser's PDF viewer has its own 52%), so they cannot
  share one. `setDiscussFont` writes the variable and **does not re-render** — a re-render would throw
  away the draft and the cursor. Persisted to `localStorage`; base `15px` is the question area's stem.
- **The screenshot is what makes the note evidence.** 「沒看過的證據不算證據」: a finding's crop is
  its evidence, so a missing crop is drawn as a failure (「這一筆意見沒有截圖，無法核對。」), never
  silently skipped. The crop must survive both a rebuild (`_adopt_finding_crops`) and a push
  (`push_referenced_crops`) — see the AGENTS.md rules.
- **④ writes through the existing `/api/manual-asset`,** with `placement` (`stem`/`option`/`table`/
  `group`), `target_option`, `replace_existing`, `caption`, `notes`. No new write path: that endpoint
  already writes the file and embeds the `asset_ref` into the correction, and a correction is an
  append-only event. An empty save is refused before any request is made.
- **⑥ 註解 is a `comment` event, the same kind the question area's 「只加註記 C」 writes,** so the two
  areas see each other's notes (both read `review.notes` from the one append-only stream). It is **not
  a verdict**: `_reaffirm_standing_action` keeps whatever decision it is attached to.
- **A note must not lift a question out of this area.** This was a real defect: the area's membership
  is "the latest state is a pending reset", and `_reaffirm_standing_action` only re-states actions in
  `STANDING_ACTIONS` — `reset_review` is not one. So a note on a stuck question popped the reset and
  the question left the stuck list: **the more you explained, the more it vanished.** The rule now
  lives in **one** function, `_note_annotates_pending_reset`, used by every fold (JSONL load, SQL load,
  and both in-memory update paths); the note merges into the pending reset with the person's words in
  `notes` and the repair marker preserved in `reset_notes` (`review_projection` reads `reset_notes`
  first). A real verdict still clears the reset. Pinned by `tests/test_review_ui_note.py` and
  `scripts/test_v2_note_keeps_question.mjs`.
- **「帶入」 fills the editor; it never writes.** `applyFindingChange` copies a mechanical diff into
  the textarea so the reviewer can accept part of it. Pressing it writes nothing; only 儲存修正 does,
  and that is the reviewer's own decision (the same `/api/review` path as the question area).
- **基本原則 (basic principles) is a field the reviewer adds to, and its only consumer is the
  prompt.** A sentence a person writes is pasted **verbatim** into the next repair round's prompt —
  not compiled into rules. That is this project's measured lesson (rules → scripts → new problems →
  more rules is a treadmill), and it is why the principle lives in an append-only stream
  (`question_review_principles.jsonl`) with its folding rule in `qbr/src/qbr/discuss.py`, which the
  server imports rather than re-implementing. A removal is an append-only `remove` event, not a
  deletion.
- **This area has its own four-level filter, and it shares the question area's contract.** It sends
  `category`/`year`/`ordinal`/`subject` to `/api/discuss` — the **same names** the question area uses —
  and, like `buildScope`, each `onchange` writes **only its own level**. The user's complaint was
  「每次都跳來跳去」, so "the other three selects did not move" is the property that is measured (in a
  real browser, in `scripts/test_v2_ui_audit.mjs`). Two things are easy to get wrong and both are
  pinned: the filter is **server-side** (the stuck rows are scattered across the whole queue and the
  client only sees the capped window, so filtering in the browser would filter a sample), and
  **全部類科 is a merged bucket, not `undefined`** (`mergedBucket`) — without it the year/sitting/
  subject pickers come out empty and only the top level works. Each level is then reconciled with the
  question area's own `resolveLevel`, so a value that stops existing falls back to 全部 rather than
  staying in the request and filtering the list to nothing.
- **The tree the pickers offer is the stuck set's own tree** (`ReviewState.discuss_taxonomy`,
  built over the **unfiltered** stuck rows through the one `review_queue.taxonomy_of`), not the whole
  queue's — a reviewer must not be able to aim a stuck-question filter at a category with no stuck
  questions in it. It comes back on **every** response, so choosing one level can never collapse the
  options of another.
- **The agent asks the reviewer back instead of guessing.** When a page read fails, or the page
  agrees while a person still blocked it, `confirm_dispute.py --escalate` appends one `ask` to
  `question_repair_questions.jsonl` (idempotent per reading) rather than asking the same model twice;
  the reviewer answers it in this pane, and the answer reaches the next prompt. **The agent never
  impersonates a reviewer**: its evidence stays in the finding stream and the human's decisions stay
  in the event stream.
- **A per-question comment only becomes a principle if it is a rule nobody had.** The reviewer's rule
  is 「逐題 comment 自己讀，是原本沒有的規則才加入」. That reading is a person's job, recorded in
  `qbr/reports/comment_to_principles.md` (9 comments over 7 questions were read; **one** was a rule
  that did not already exist). The write half is
  `qbr/scripts/curate_principles_from_comments.py`: dry-run by default, dedup by text, every added
  principle must point at a `comment` event that really exists (**ungrounded principles are refused,
  not written**), and `reviewer` may not be `local` or a person's name (governance: an agent must not
  impersonate a reviewer). The trap this guards: `reset_review`'s machine-generated note was
  **prefilled into the 註解 box**, so a comment event can carry a machine sentence verbatim — the
  same sentence on three different questions is the tell, and it is not a principle.

Contract rules (all pinned by `tests/test_review_ui_areas.py` + `scripts/test_v2_areas_browser.mjs`):

- **An empty hash is the question area, never 首頁.** The bare `#類科/年/次/科目/qNNN` spelling is a
  bookmark contract; the question area strips any area prefix back off, so old links keep working.
- **Switching areas hides, never empties.** The answer pane keeps the sheet and the half-typed note;
  a pane is rendered once (`renderArea` checks `A.rendered[area]`) and `invalidateAreas()` is called
  only by something that actually changed the data (a question decision, an answer write).
- **A pane reads its numbers from the server.** 首頁 calls `/api/candidates`,
  `/api/answer-candidates` and `/api/correction-feedback` and prints what they say; it computes
  nothing of its own, because a dashboard that disagrees with the page behind it is worse than none.
- **首頁 is a count, not a list: it sends `_count=1`.** The cards read `total_count` and
  `reviewed_count` and draw no row, so the request must not fetch one. The server honours the switch
  in **both** backends by forcing `limit = 0` — the *same* filter loop then counts and never appends
  (`if len(payloads) < limit` is never true), so the numbers cannot come from a different rule than
  the rows do. Measured on the live queue (gzip): `limit=1000` = 358.4 KB / 5.27 MB raw / 1,000
  rows; `_count=1` = 1.5 KB / 0 rows. `_count` was **never read** before this — the home page had
  been shipping a thousand full candidate payloads for two integers.
- **首頁 is rendered once, like the other three areas.** It goes through `renderArea`, not a direct
  `renderHome()` call from `showArea` (which bypassed `A.rendered` and re-fetched all three endpoints
  on every switch back). A second entry fires **0** `/api` calls. Pinned by
  `scripts/test_v2_areas_browser.mjs`.
- **The answer area does not decide eligibility.** `/api/answer-candidates` returns questions whose
  question review is `accept`/`unblock`, and `/api/answer-review-batch` refuses the rest — mirror
  that, don't re-implement it. The batch endpoint takes **one** `action` for the whole request
  (`action = payload.get("action")` then every event is stamped with it), so `answerSheetAction`
  groups rows by the action they each earned; sending one batch with a per-row action would write 79
  decisions nobody made.
- **Clicking an answer option drafts; it does not save.** A stray click on a 4-row table must not
  rewrite an answer. The draft (`A.answerDraft`) is sent only by the buttons under the table.
- **The discussion area writes only the reviewer's own words.** Two of them now: a ⑥ 註解 and a
  ⑤ 手動修改 (the ⑥ 註解 is the same `comment` event the question area's 「只加註記」 writes — one
  stream, not two). A 基本原則 sentence and an answer to the agent's question are the same kind of
  thing but are written from **原則區**. All of them are append-only streams with no SQL mirror
  (the area reads the whole file; a table would be a second representation with nothing reading it
  back). It does **not** learn anything by itself — the `change_class` that the old discussion area
  showed is still recorded by `_record_question_correction_feedback` when a person saves a
  correction. **Do not build a second store for it.**
- **The question shortcuts (`W`/`S`/`A`/`R`/`B`/`E`/`C`) fire only in the question area.** `w`, `a`,
  `b` are ordinary letters in a discussion or an answer note; an unguarded handler writes a review
  event from a keystroke the reviewer meant as text.

## Latency — what actually costs time

`question_ai_findings.jsonl` is the one big stream (measured: **482 MB / 73,688 records** on the
served queue) and it is *append-only by contract* — `qbr/src/qbr/ai_findings.py::append` is a single
`open(path, "a")`, the push helper appends with `cat >>`, and a rebuild writes a **new directory**.

- **The findings store reads only the tail.** `QbrAiFindingsStore` remembers its file position and
  re-reads only what was appended, because re-reading the whole file for each new line cost
  **1.4 s per request** while the corpus sweep was running (the sweep appends every second, so every
  request saw a changed signature and reloaded). Measured after: **0.07–1.6 ms** for an append and
  **0.1 ms** with nothing new. This is the fix for "UI 慢" — not JSONL as a format.
- **The result must be identical, not merely fast.** `test_the_tail_loader_equals_the_whole_file_loader`
  compares the tail store against `load_qbr_ai_findings` record by record (last-record-wins, the
  compact whitelist, appended `crop`/`changes`). Two engines, one answer.
- **A rewrite must not be read as an append.** Three guards, all about bytes (not `mtime` — an append
  moves that too): file identity (`st_dev`,`st_ino`; a deploy or rebuild swaps the file in), a
  short size (truncation), and two fixed-length 64-byte windows (head and just-before-offset). A
  same-inode, same-size patch of the *middle* is deliberately **not** claimed to be detected — no
  writer does that, and the promise lives in the writers, not the reader.
- **`refresh` is copy-on-write.** The old whole-file loader assigned a fresh dict each time (an
  atomic reference swap), so a request mid-answer held a consistent snapshot; mutating in place
  would let it see half an update. A 73k-record copy is 0.35 ms and only happens when there is
  something new.
- **The whole-file loader reads bytes, not text.** Text-mode iteration raised `UnicodeDecodeError`
  on a truncated file or a half-written multi-byte character — and that loader is the rewrite
  fallback, so one bad line made **every** request fail. A line that does not decode is now skipped
  like one that does not parse.

## 五個區的驗收

Verify the five areas end to end (needs a queue built by `build_review_queue.py`):

```sh
REVIEW_UI_ADDITIONAL_ASSET_ROOTS=<queue> python3 scripts/serve_question_review_ui.py \
    --candidate-jsonl <queue>/review-ui/candidates.jsonl \
    --issue-csv <queue>/review-ui/issues.csv \
    --review-log <queue>/review-ui/question_review_events.jsonl \
    --review-backend jsonl --host 127.0.0.1 --port 8897 &
node scripts/test_v2_areas_browser.mjs http://127.0.0.1:8897
python3 -m unittest tests.test_review_ui_areas
```

### 每一個按鈕都要真的按過（使用者的驗收標準）

「UI 做完要用 browse use / computer use 檢查——每一個按鈕都要測過才能交付。」那不是建議，
是驗收條件。這幾個缺陷的形狀完全一樣：控制項畫出來了、看起來可以按，卻沒有任何 handler 接上去
（圖片擷圖的四個位置、字體的三個鍵、儲存補圖、儲存註解全都曾是死的）。**截圖看不出來**，因為
一個死按鈕與一個成功按鈕長得一模一樣；只有真的按下去、再看狀態有沒有變才測得出來。

```sh
node scripts/test_v2_ui_audit.mjs http://127.0.0.1:8897 --json /tmp/audit.json
```

三種控制項，三種驗法——不要用同一種驗法驗這三種：

| 種類 | 例子 | 怎麼驗 |
|---|---|---|
| 唯讀／純前端 | 字體、切區、走清單、聚焦、篩選 | **真按**，量一個具體變化（CSS 變數、可見區 id、游標位置）。沒變化就是 BAD |
| 有寫入但可安全觸發 | 儲存補圖、儲存註解、新增原則 | 按**空的**，斷言守門出來且**沒送出任何東西**（守門本身就是要測的行為） |
| 會改動審核紀錄 | 確認正常／阻擋／退回未審／儲存修正 | 只驗「有 handler、沒 disabled」，**不按**——那是寫 append-only 的人工紀錄（`GOV-05`／G4） |

腳本也驗「打的字＝送出的字」：把 `fetch` 換成只做紀錄的替身，在框裡打字、按「儲存修正」，
檢查送出的 payload 帶著那些字。替身不發請求，所以這條驗收**一筆紀錄都不會寫**。最後一輪走訪
每一區，斷言每個可見動作控制項不是有 handler 就是有真的 href（實測數字見 `review_ui/AGENTS.md`），並且驗「每一區都真的
抓到夠多控制項」，否則空畫面會假裝通過。

註解與佇列的關係另有一支端到端瀏覽器驗收（自己在隔離的候選與事件檔上開一個 server）：

```sh
node scripts/test_v2_note_keeps_question.mjs   # 寫完註解，那一題必須還在錯題討論區
```

## Keyboard — all left hand

| Key | Action |
|---|---|
| `W` | previous question |
| `S` | next question |
| `A` | 確認正常 (accept) |
| `R` | 需重看 (needs review) |
| `B` | 阻擋 (block) |
| `E` | edit / save correction |

`W`/`S` step **one position in the drawn list**。清單就是紙本順序（同一卷之內），所以那一步就是紙本上的
前一題／下一題；跨卷時仍是照「先按卷、再按題號」的排序（`applyScope()` 的排序），不是紙本翻頁的順序。

### 「全部」= 紙本順序（2026-09-24：重排版當天就被退回）

使用者第一版的要求是「當我點『全部』的時候，優先顯示還沒有審核的，不然我如果照順序審核，會刷不到
應該看的」。第一版把它做成**重排**（未審段＋已判段），當天就被退回，理由是它同時弄壞了兩件事：

> 「你是直接顯示還沒看的題目，但你不是跟我保證說不會干擾原本的排序嗎……結果你現在只給我顯示還沒看的
> 題目，原本的順序不見了，我想要往上一題參考也沒有了」

* `W` 在未審段的最上面一列沒有上一列——紙本上的前一題（通常已判過）被搬到清單另一端；
* 相鄰兩列不再是相鄰兩題，`題組` 的共用題幹與前一題的線索都對不上。

現在：**`S.rows` 就是紙本順序**（與 `S.view` 同一個來源、同一種排序），`W`／`S` 是紙本上的前一題／
下一題。「優先」由兩件不改變順序的事提供：開範圍時游標放在第一題還沒審的（`firstOpen`），以及
`未看` 這個籤只留還沒審的題目——**要「只看還沒審的」是一個篩選，不是一種排序。**

### 重新載入時，未審的要開在你面前（2026-09-24 第二次修正）

使用者把同一件事講得更精確：「整體順序不變，但如果有還沒審的題目，在 F5 刷新的情況下，優先顯示在
面前，但因為整體順序不變，我不論是往上還是往下，都可以自由調整，這才是我想要的狀態，但是你現在
只是退回原本的樣子」。

**「退回原本的樣子」是真的**：`firstOpen` 早就寫好了，但它在真實操作裡**從來沒生效過**——因為
`scopeToHash()` 把題目區的當下游標也寫進網址（`#類科/年/次/科目/q41`），`buildScope()` 重新載入時把
它讀成 `S.openQuestion`，`applyScope()` 就照它開。F5 永遠回到你離開時那一題，而那一題通常已經判完。

修法是把**位置**與**範圍**分開：題目區只把範圍寫進網址，位置不寫。（明講的連結不受影響——
`scopeFromHash()` 照樣讀 `/qNNN`，所以貼上的 `#類科/年/次/科目/q41` 仍然開在 q41；討論區的 `qNNN`
也照寫，那是它自己的契約。）於是 F5 → 網址只有範圍 → 游標開在**第一題還沒審的**，而清單仍是紙本
順序，往上（已判過的）往下都走得動。

順手修掉同一條路上的第二個缺陷：`scopeFromHash()` 用 `rest.join('/')` 當科目，所以
`#藥師(一)/115/2/藥學(三)` 讀到的科目是整串，不在科目清單裡就被 `resolveLevel()` 換成「全部科目」
——連結指名的科目靜默消失（畫面還開得起來，只是多出一卷）。科目是**第四段以後**（`tail.join('/')`）。

驗收（`scripts/test_v2_navigation.mjs` check 3c）：題目區的網址只寫範圍、重新載入時沒有具名的題目
（所以 `firstOpen` 輪得到）、明講的連結仍讀得出 q41、負對照是「舊行為寫出的網址會具名一題，重新載入
就回到那一題」，以及「舊的 `rest.join('/')` 讀出的科目是整串」。

驗收（`scripts/test_v2_navigation.mjs`）：「整份清單按卷、再按題號」「判一題不會移動任何一列」
「判一題後游標仍在同一列」「剛判完那一題的上一題是紙本上的前一題（就算它早就判過了）」。
負對照：把判過的那一題搬到最後，後兩條就必須不成立（同一份資料，只改順序）。

## The filter contract (this was a real defect)

**`S.rows` is the single array that is both drawn and walked.**

- `S.view` = the whole scope. It exists for the counts above the chips and the taxonomy crumbs —
  statements about the scope, not about the filter. A chip count that changed when another chip was
  selected would be unreadable.
- `S.rows` = `visibleRows()`, rebuilt by `rebuildRows(anchor)` on every filter change.
- `go()`, `renderList()`, `renderTextSide()`, `renderPaperSide()`, `decide()`, `saveCorrection()`,
  `scopeToHash()` all read `S.rows`. `data-pos` is an index into `S.rows`.

This used to be two different sets: chips narrowed what was **drawn** while `W`/`S` walked the whole
scope, defended by a comment saying "a filter narrows what is drawn, not what is walked". Measured on
the real queue with 題組 selected and a group at 78–80: pressing `S` on 80 jumped to **question 1 of
the next paper**, because paper order said 80 → next paper while the list said 80 → next group.
Under 有圖 a reviewer stepping through the pictures was silently teleported out of the filter and
could not tell which questions they had seen.

**The fix, in two parts:**

1. One array for both (charter: 導覽與內容必須來自同一個來源).
2. On a decision that removes the row from the active filter, **keep the cursor's position** and
   read the row that slid in — do not step past it. The `next()` logic:

```js
const survived = was >= 0 && (S.rows[was] || {}).candidate_key === currentKey;
const target = survived ? was + 1 : S.index;
```

Stepping past in both cases skips exactly one question per decision while 未看 + accept is active.

Filters: 題組 (exclusive, about structure), 未看, `block`, `AI已修改`, `AI無法判斷`, 爭議, and the 有圖
toggle. The toggle is part of the counted set, so the numbers follow it.

### The chip vocabulary (2026-09-24, at the reviewer's request)

| `data-view` | 標籤 | 判準 | 軸 |
|---|---|---|---|
| `flagged` | **block** | `stateOf(item) === 'flagged'`（`block` 或 `needs_review`） | 人做了什麼 |
| `returned` | **AI已修改** | `stateOf(item) === 'returned'`（最新一筆是 `reset_review`） | 人做了什麼（但作者是機器） |
| `aicannot` | **AI無法判斷** | `aiCannotTell(item)` | **模型**給不給得出結論 |

**標籤是契約的一部分，`data-view` 才是鍵。** 2026-09-24 使用者把「我擋的・需重看」改成「block」、
「AI・管線退回」改成「AI已解決」，`test_v2_returned_chip_browser.mjs` 當時因為寫死字串而變紅——所以
那條檢查改成認 `data-view`。改標籤不必改測試。（2026-09-25 使用者再把「AI已解決」改成「AI已修改」：
「已解決」把「有人動過它」讀成「它沒問題了」，而機器把自己改錯的字收回（`withdrawn`）的那一題也不是
修改——它不再屬於這一籤。）

**`AI無法判斷` 是唯一不屬於 `stateOf` 的籤**：它同時問兩件事——**人擋過的**題目裡，**模型**有沒有下結論。
判準寫在 `02-area-question.js::aiCannotTell()`，第一行就是 `stateOf(item) !== 'flagged' → false`：

| 情況 | 算不算 |
|---|---|
| 人擋過（`block`／`needs_review`）＋ `NOT_EXTRACTION`（模型說「不是抽取造成的」＝要人判）、`error`、沒有 verdict | **算** |
| 人擋過 ＋ **機器已經試滿三次、每一次都被打回**（`review.exhausted`，2026-09-25） | **算**——機器停手了，這一題回到人手上 |
| 人擋過 ＋ `OK`／`DEFECT`（模型有結論） | 不算 |
| 人擋過 ＋ 沒有 `qbr_ai_finding`（模型還沒看） | 不算——沒讀過 ≠ 讀了不知道 |
| 人已經放行（`done`） | 不算——沒有待辦事項 |
| **人還沒看過（`unseen`）** | **不算**——人還沒表達意見，機器說不出所以然不是人的待辦事項 |
| 機器已經動過（`returned`＝修復後待複核） | 不算——那一題在「AI已修改」等的是「新文字對不對」 |

**`exhausted` 那一列是業主 2026-09-25 的循環收尾**：「機器改 → 人打回 → 機器再讀一次並依註解改 →
第三次之後才進『AI無法判斷』」。上限是三次（`withdrawals.MAX_ATTEMPTS`），只數人的 `block`
（`needs_review` 回答的是另一個問題：這一欄能不能整欄改寫），人 `accept` 或 `unblock` 會把門重新打開。
伺服器只送事實（`review.attempts`／`review.exhausted`），句子在瀏覽器
（`findingHtml` 的「機器已經停手：這一題試過 N 次、每一次都被你打回…」）——與 `machineAppliedLabel`
同一個分工。

也就是說：**這一格是 `block` 的子集**。兩個回報各自砍掉一條：

1. 2026-09-24 上午：「你的 AI無法判斷裡面，有我已經審核的問題啊，我都通過了你是要判斷什麼」→ 排除
   `done`。
2. 同日下午：「你把人工看過的放行，怎麼把人工未看過也框入……我才說後面的 agent 循環是僅針對 block
   去看」→ 排除 `unseen` 與 `returned`，只剩人真的擋下來的。

這一格的存在理由就是人機分工：**人擋的＝要修的工作**（agent 迴圈只做這一批），機器看紙本、能修就修
（修過的落在那題的「AI已修改」），修不了或說不出所以然的才回到人手上下註解。模型的話在每一題的右欄
仍然看得到（`findingHtml` 沒有改），這一格只決定要不要催人。

負對照（`test_v2_navigation.mjs` 的 check 4c）：「只看模型、不看人」會在已放行／未看過／機器退回三列上
不符（那正是第一版的行為）；「有 reading 就算」在五列上不符；「沒讀過也算」在一列上不符。實測站上：
藥師(一) 115 第2次，`block` 7、`AI無法判斷` 4（子集關係看得出來）。

`qbr_ai_finding` 是**伺服器送出時 join 的**（`review_state.py:176` 從 `--review-log` 旁邊的
`question_ai_findings.jsonl` 讀），所以原始 `candidates.jsonl` 裡沒有這個欄位——harness 讀原始檔，
因此它另外塞三列（都是 `block`）來驗篩選接線。

## Verify a navigation change

Do not test this by clicking. There is a harness that extracts the **real** `<script>` from
`v2.html`, stubs a minimal DOM, and drives the real `rebuildRows` / `visibleRows` / `go` / `next`
over the **real** candidates file:

```sh
node scripts/test_v2_navigation.mjs review_ui/v2.html <workdir>/review-ui/candidates.jsonl
# env: QBR_TEST_SITTING / QBR_TEST_PAPER
```

It asserts, for 醫事檢驗師 / 藥師(一) / 藥師(二): 題組 steps stay inside the filtered list
(8 steps); a cross-paper sitting walks 6 consecutive positions; 未看 judged 1→2→3 with no skips.
**Its negative control is the point**: the old behaviour walked **72** non-group questions while the
list showed 8 (474 vs 6 across a sitting). A navigation test without that control proves nothing.

## Review records must survive a rebuild

Human decisions live in `question_review_events.jsonl`, **append-only**. When a queue is rebuilt:

- **Carrying is automatic** (no flag to forget).
- **Deduplicate by whole record, not by `candidate_key`** — one question legitimately carries
  several events (`block` → `accept`); keying by question silently drops later decisions.
- **Keep and count orphans**; never drop them.
- If a rebuild would carry 0 records while records exist nearby, it **refuses to start (exit 2)**,
  checked *before* writing.

Two defects found here: `--carry-from` only worked for in-place rebuilds (the sf7→sf8→sf9 pattern
carried 0), and the dedup key included `_carried_from`, so every rebuild re-added the same record
(measured 392 → 629). Full detail: `qbr/reports/review_record_safety.md`.

> **Unsolved, do not paper over:** rebuilding into the directory the server is serving leaves the
> list and the candidate file inconsistent for a moment. **Pause the service during a rebuild.**

## Changing the console

- **One source for navigation and content** (`S.rows`). Adding a filter means adding it to
  `visibleRows()` — nothing else.
- **Do not fork.** Extend `v2.html`.
- **Every check ships with a negative control** — the case that must fail on the old behaviour.
- `null` and `''` are different states. A field that is absent (`null` = "not computed") is not the
  same as empty (`''` = "computed, nothing there").
- The list's primary key is the **paper**, the question numbers restart per paper — so any
  monotonicity assertion must be per-paper.
- Left panel: the extracted text. Right panel: **the question sheet only, never the answer sheet**,
  and it is **completely decoupled** from the question list.
- Disputes render **above the stem** — a reviewer must see the machine's uncertainty before reading
  the question, not after deciding.
- If a dispute looks wrong, it is a measurement to check, not an opinion to argue with:
  `qbr/src/qbr/disputes.py`. `null` means "none computed"; `[]` never occurs.
- **A correction is text, so the disputes must be re-measured after it is applied.** `disputes` is
  derived from the text *at the time it was measured*, and the queue's `candidates.jsonl` text is
  **never** rewritten by a repair (a correction is an event overlay, so the original reading
  survives). Left alone, a repaired question shows the dispute the repair just fixed, right above
  the fixed text — the two panels contradict each other while looking perfectly plausible.
  Measured 2026-09-23: **204 of 304** repaired questions still carried the stale dispute. The fix is
  in `candidate_payload`: when a `correction` is applied, re-run the **same**
  `review_queue.disputes_for_paper` — one rule, a different *moment* — and mark the payload
  `disputes_recomputed` so a reader can tell a dispute that survived the repair from one nobody
  re-checked. Pinned by `tests/test_review_ui_repaired_text.py`.

## 註記（只加註記，C）—— 一個「不是決定」的事件

**這個功能原本是壞的，而且壞得看不出來。** `reasonBox` 一直在 DOM 裡，但 `.on` 這個 class
在整個檔案裡**只被移除、從來沒有被加上**（`classList` 只有三處，全是 remove），所以那個 textarea
永遠 `display:none`——`reasonText.value` 在每一次決定時都是空的，**每一則打進去的註記都被靜默丟掉**。
這就是「註記為了速度被省略」的真相：不是選擇，是 class 從來沒被打開。

所以註記現在是**刻意打開的**（按鈕 `只加註記` 或 `C`），而不是每題都攤在畫面上：快速走過去的人
不會被一個框吸走注意力。儲存時送 `comment` 事件。

**加這個功能時最容易做錯的地方，是它會把決定吃掉。** 事件日誌以**最新事件**當作題目的狀態，
所以在 `確認正常` 之後存一則註記，會把那個接受**撤掉**（`comment` 不在 `QUESTION_READY_ACTIONS` 裡），
題目就從正式題庫掉出去——而沒有任何人決定任何事。`correct` 一直都有做「重申底下那個決定」這件事，
註記必須做同一件事；現在兩者走同一個 `_reaffirm_standing_action`，四個重複的 `correct`-only 區塊
都刪了（**一條規則，一個地方**）。在**還沒決定**的題目上寫註記，它就只是一則註記——它不會
把任何東西升級，因為只有 `accept`/`unblock` 會讓題目變成 ready。

前端把 `comment` **故意排除在 verdict map 之外**：在快速走過去的時候加註記，不可以把題目標成已過目
而讓動線跳過它。註記顯示在題目旁邊（`noteShown`）——看不到第二次的註記，就是會被寫第二次的註記。

**驗收：「框存在」和「審核者打得開」是兩個不同的主張。**

```bash
python3 -m unittest tests.test_review_ui_note          # 12 項，3 個負向對照會紅
node scripts/test_v2_note_browser.mjs http://127.0.0.1:<port>   # 真 Chrome，跑真佇列
```

後者用 CDP 驅動**頁面自己的** click 與 save 處理函式，檢查框真的開得起來、吃得到游標、存得下去、
重載還在，**而且不算已過目**。在真的 79,090 題佇列上跑：11 項全過。截圖上寫完一則註記後計數器
仍然是 `0 / 80 已過目`。

## Reporting a defect to the pipeline

The UI shows what the pipeline built. When a question looks wrong here, the fix usually belongs in
`qbr` — see the `build-exam-question-bank` skill. Bring back the **paper**, not the screenshot.

<!-- project-map:belongs-to -->
## 這一層在哪（回上層的路）

> **這是本子專屬技能**：只服務這個子專案。其他子專案要用同一件事時，先確認是不是該變成全域共通技能。

- 本層入口：[`../../../AGENTS.md`](../../../AGENTS.md)
- 不確定從哪開始：[`project_map`](../../../../project_map) 是整棵樹的可點擊地圖
- 卡住時的回溯路徑：技能 → 本層 `AGENTS.md` → `project_map` 入口文件鏈 → 傘層 → charter
<!-- /project-map:belongs-to -->
