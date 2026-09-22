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
| 錯題討論區 | `#錯題/…` | shows each human fix and its `change_class` | **nothing** |

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
- **The discussion area learns nothing by itself and writes nothing.** The lesson is already
  recorded when a person presses 儲存修正 (`_record_question_correction_feedback`) or corrects an
  answer (`_record_answer_correction_feedback`), which build a `question_correction_feedback_events`
  row with a `diff` and a `change_class`. **`change_class` is measured, not chosen** —
  `ai395_feedback.classify_change` reads the changed field names and the diff shape — so a type on
  this page really is a type. **Do not build a second store for it.**
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
