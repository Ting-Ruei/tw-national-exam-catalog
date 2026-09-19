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

## Reporting a defect to the pipeline

The UI shows what the pipeline built. When a question looks wrong here, the fix usually belongs in
`qbr` — see the `build-exam-question-bank` skill. Bring back the **paper**, not the screenshot.
