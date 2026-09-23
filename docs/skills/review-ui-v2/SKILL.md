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

## The four areas (首頁 / 題目審核區 / 答案審核區 / 錯題討論區)

v2 is **one page with a mode switch**, not four pages. Four pages would mean four loaders of the same
198 MB `candidates.jsonl`, and so four chances for them to disagree about what is in the queue. The
topbar switches the pane; the mode is in the hash so each area is linkable and a reload reopens it.

| Area | Hash prefix | Does | Writes |
|---|---|---|---|
| 題目審核區 | none (bare `#類科/年/次/科目`) | reads the paper beside the extracted text | `/api/review` |
| 首頁 | `#首頁/…` | counts only; every number comes from the three endpoints below | **nothing** |
| 答案審核區 | `#答案/…` | reads the answer sheet, one sheet at a time | `/api/answer-review-batch` |
| 錯題討論區 | `#錯題/…` | the stuck questions — what a detector measured, what a model read off the page, and what the text should say | `/api/review` (the reviewer's own save), `/api/principles`, `/api/repair-question` |

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
| ⑦ 基本原則 | a sentence the reviewer adds to, pasted into the next prompt | the reviewer |
| ⑧ 反問 | the agent's open questions | `question_repair_questions.jsonl` |
| 右 | the question sheet, decoupled | — |
| 左 | 統計 ＋ 四層篩選 ＋ the stuck list | — |

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
- **The discussion area writes only the reviewer's own words.** Three of them: a 基本原則 sentence,
  an answer to the agent's question, and a ⑥ 註解 (the same `comment` event the question area's
  「只加註記」 writes — one stream, not two). The two agent streams are append-only with no SQL mirror
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

## 四個區的驗收

Verify the four areas end to end (needs a queue built by `build_review_queue.py`):

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
四個區，斷言每個可見動作控制項不是有 handler 就是有真的 href（實測 213 個：home 3 / question 93 /
answer 100 / discuss 17），並且驗「四個區都真的抓到夠多控制項」，否則空畫面會假裝通過。

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

`W`/`S` step **one position in the drawn list**. They are not "previous/next in the paper".

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

Filters: 題組 (exclusive, about structure), 未看, 需重看・阻擋, 爭議 (what the *pipeline* could not
settle — a separate axis from what the reviewer decided), and the 有圖 toggle. The toggle is part of
the counted set, so the numbers follow it.

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
