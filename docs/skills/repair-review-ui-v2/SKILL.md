---
name: repair-review-ui-v2
description: Find and fix a defect in the question Review UI v2 (/v2) — the measurement discipline, the three verification harnesses, the catalogue of defects already found and fixed, and the open items a next round can optimise. Use when the console shows the wrong thing (a literal `<sub>`, a picker that moves another picker, a pane the wrong size, a control that does nothing), when the discuss area is empty or loses a question, or when adding a panel or a decision to the review console.
---

# Repairing the Review UI v2

The console is `tw-national-exam-catalog/review_ui/v2.html` + `review_ui/v2/*.js`
(`01-core.js`, `02-area-question.js`, `03-areas.js`, `04-area-discuss.js`, `05-boot.js`), served at
`/v2` by `scripts/serve_question_review_ui.py`. Read `review-ui-v2/SKILL.md` for the *operating*
contract (keyboard, filter rule, four areas); this is the *repair* procedure and the record of what
has already been found — the same relationship `repair-qbr-extraction` has to
`build-exam-question-bank`.

> **`/v2` is served `no-store`**: a JS or HTML edit is live on reload, no restart. **A Python edit
> needs the server restarted.** Restarting is a mutation of a running service — minimise it, and say
> when you did it.

## The one rule that governs everything here

> **A dead control looks exactly like a live one.**

Every real defect in this campaign was invisible in a screenshot. 「圖片擷圖與抽換」的 four placement
buttons, the three font buttons, 儲存補圖 and 儲存註解 were all **drawn and never wired** — they
rendered perfectly and did nothing. 「看圖驗收」 cannot see that. The only way to see it is to press
it and measure whether something changed.

The user stated the acceptance standard in exactly these words:

> **「UI 做完要用 browse use / computer use 檢查，否則太多 bug 會出貨，每一個按鈕在交付前都要測過。」**

That is a condition of delivery, not advice. `scripts/test_v2_ui_audit.mjs` is that condition made
executable.

**Corollary (from the charter):** a green suite can prove a wrong rule. A test that re-implements
the walk proves something about the test. Extract the *real* script and drive the *real* function.

**Second corollary:** the instrument can be the broken thing. This campaign found **two**
measurement-tool defects (the audit script's `CONTROL_PROBE` never ran; `test_v2_navigation.mjs` had
been crashing on HEAD since the v2 split). Expect roughly that ratio and check the tool first.

## Procedure

1. **Reproduce as a named thing, not a mood.** 「q53 的 `<sub>2</sub>` 顯示成字面標籤」 is a defect.
   「上下標有點問題」 is not. Name the paper, the question number, the pane, the control.
2. **Measure the *state*, not the pixels.** A button works iff a specific value changed:
   `--reading-size` went `15px → 17px`, the visible pane id changed, the cursor is in the box. "It
   looked right" is not a measurement.
3. **Write the rule in terms of the paper/data, never in terms of the parse.** A rule whose input
   is the parser's output makes the parser and the check confirm each other (charter: rules keep
   only the properties; *reading what the text means* is a prompt). This is why `<sub>` rendering is
   a whitelist regex (a property of the string) and why "is this comment a new rule" is a **human
   reading**, not a script.
4. **One rule, one place.** The discuss predicate lives in one constant (`SQL_DISCUSS_PREDICATE`)
   used by both CTEs *and* the JSONL path; the note rule lives in one function
   (`_note_annotates_pending_reset`) used by every fold. Two copies of a predicate is how a picker
   and a list come to disagree.
5. **Add the positive test AND the negative control.** The negative control must **fail on the old
   behaviour**. Then *prove it bites*: revert the fix (or inject a dead control) and watch the check
   go red. A test that passes on old code proves nothing.
6. **Press every button in a real browser.** `node scripts/test_v2_ui_audit.mjs <url>` — see the
   three control kinds below.
7. **Prove zero drift on the live corpus.** Any change to a fold/projection must not move a row
   between buckets. Before/after counts must be identical (measured this campaign: **4494
   `reviewed`, 205 `repair_pending`, 21 `accepted_reaudit`**, and **79,090 rows** on the SQL sweep).
8. **Run more than your test.** catalog `python3 -m pytest tests/ -q` and qbr
   `cd qbr && .venv/bin/python -m pytest tests/ -q`. Both must be green before delivery.
9. **Commit the checkpoint** (G1/G2: branch + PR; never push `main`). Ledger stays append-only.

## The three verification instruments that must exist

```sh
# 1. Every button, really pressed, in real Chrome (CDP). The user's acceptance standard.
node scripts/test_v2_ui_audit.mjs http://127.0.0.1:8897 --json /tmp/audit.json

# 2. Navigation: what is drawn == what W/S walks. Drives the REAL functions over the REAL queue.
node scripts/test_v2_navigation.mjs review_ui/v2.html <workdir>/review-ui/candidates.jsonl

# 3. End-to-end append-only: a note written in the browser must NOT evict the question from the
#    錯題討論區. Spins its own server on an isolated temp ledger, so the live ledger is untouched.
node scripts/test_v2_note_keeps_question.mjs
```

Plus the unit contracts:

```sh
python3 -m pytest tests/test_review_ui_rich_text.py tests/test_review_ui_v2_scope.py \
                  tests/test_review_ui_discuss.py tests/test_review_ui_note.py \
                  tests/test_review_ui_areas.py -q
cd qbr && .venv/bin/python -m pytest tests/test_principles_curation.py -q
```

### The three kinds of control, and why they need three verifications

| Kind | Examples | How to verify |
|---|---|---|
| Read-only / pure front-end | 字體, 切區, 走清單, 聚焦, 篩選, 展開 | **Really press it**, measure a concrete change (`--reading-size`, visible pane id, cursor). No change = BAD |
| Writes, but safe to trigger | 儲存補圖, 儲存註解, 送出回答, 新增原則 | Press it **empty**; assert the guardrail appears **and nothing was sent** (the guardrail *is* the behaviour under test) |
| Changes the review record | 確認正常 / 阻擋 / 退回未審 / 儲存修正 | Verify handler + not-disabled + label reads; **do not press**. That writes an append-only human record (`GOV-05` / G4) — an audit script pressing it is the script impersonating a reviewer |

The audit also proves 「打的字＝送出的字」 with a `fetch` stub (types in the box, presses 儲存修正,
asserts the payload carries the words). The stub sends nothing, so this writes **no record**.

**Coverage guard:** the last pass visits all four areas and asserts every visible action control has
a handler or a real href — measured **218 controls: home 3 / question 93 / answer 100 / discuss 22**
— *and* asserts each area found enough controls, so an empty pane cannot pass by being empty. Verify
this bites by injecting a dead button (done: a `deadProbe` button turned the check red).

## Defects already found and fixed (read this before changing the console)

| # | Symptom | Cause | Fix | Where |
|---|---|---|---|---|
| 1 | The paper's `<sub>`/`<sup>` shows as **literal text** `&lt;sub&gt;` (6,652 corpus rows carry inline markup; `<sub>2</sub>` 2,563×, `<sup>99m</sup>` 1,594×) | the stem/options were emitted with `esc()`, which escapes the markup | `richText()` in `01-core.js`: escape **first**, then un-escape only a whitelist (`sub,sup,u,b,i`, bare `<br>`), never attributes; unmatched opener → closer appended, unmatched closer → re-escaped. Used in `02-area-question.js` (stem, options, shared group stem) and `03-areas.js` (answer-sheet preview); the list preview strips tags | `e0342d4`, `tests/test_review_ui_rich_text.py` |
| 2 | Choosing 項目 X **moves the other pickers** (「每次都跳來跳去」) | `category.onchange` nulled year/sitting/subject, then `resolveLevel` re-picked the *first offered* value for each. Measured: setting 考次 back to 全部考次 moved 科目 `''` → `藥學(一)(包括藥理學與藥物化學)` in the same event | each `onchange` writes **only its own level**; `resolveLevel` keeps a still-offered value, falls back to `''` (全部) when not, and only `null` ("never chosen") takes the first value | `e0342d4`, `tests/test_review_ui_v2_scope.py` |
| 3 | The 錯題討論區's right pane was a quarter of the 題目審核區's, though this area needs the paper *more* | `.discuss { grid-template-columns:214px minmax(0,1fr) 330px; }` vs the question area's `238px / 1fr / 1fr` | same arithmetic: `238px minmax(0,1fr) minmax(0,1fr)`. Measured 1680px window: PDF pane **330px → 721px**, equal to the question area's 721px. **Change the two grids together.** | `aa209f4` |
| 4 | The discuss area had no 原題 / 擷圖抽換 / 字體 / 註解; and its four placement buttons + three font buttons were **dead** | the controls were drawn but never bound | ③ 原題 (read-only, via `richText`), ④ 擷圖／抽換 (reuses `/api/manual-asset`), font via **one** CSS var `--reading-size`, ⑥ 註解 as a `comment` event; stats moved to the left column | `aa209f4`, `tests/test_review_ui_discuss.py`, `scripts/test_v2_ui_audit.mjs` |
| 5 | **A note on a stuck question evicted it from the 錯題討論區.** 「你愈解釋，它愈消失」 | membership is "latest state is a pending reset"; a `comment` became the latest event and cleared the reset (`_reaffirm_standing_action` only re-states `STANDING_ACTIONS`, and `reset_review` is not one) | `_note_annotates_pending_reset` (the **one** place the condition lives) + `_merge_note_into_reset`, applied in every fold (`load_review_events`, `load_latest_events`, `_sql_question_review_maps`, `_sql_latest_event_maps`) and both in-memory paths (`append_review`, `append_reviews_batch`). The note merges in with the person's words in `notes` and the repair marker kept in `reset_notes` | `aa209f4`, `tests/test_review_ui_note.py`, `scripts/test_v2_note_keeps_question.mjs` |
| 6 | **The SQL-backed 錯題討論區 would be empty**, while JSONL worked | the discuss predicate lived only in the **light** CTE (`_sql_light_candidate_filter_parts`, unreachable — `_sql_can_use_light_candidate_query` excludes discuss); the **full** CTE (`_sql_candidate_filter_parts`) had no discuss branch → fell through to `review_action = 'discuss'`, matching nothing | add the `elif review_status == "discuss"` branch to the full CTE; both CTEs now share the one `SQL_DISCUSS_PREDICATE`. **Zero drift on 79,090 rows** | `d1bb541`, `tests/test_review_ui_discuss.py` |
| 7 | The discuss pickers could offer a 類科/科目 with no stuck questions, or collapse when one level was chosen | the tree would have been built from the whole queue / the current filter | `ReviewState.discuss_taxonomy()` builds the tree over the **unfiltered stuck set** via the single `review_queue.taxonomy_of`; membership uses `is_discuss_bucket`. Measured 0.107 s, 495 stuck rows, 7 categories, 13,244 bytes, cached 0 s | `2dbfe3f`, `tests/test_review_ui_discuss.py` (`DiscussTaxonomyTests`) |
| 8 | With 類科 = 全部, only the **top** filter worked; the other three pickers were empty | the shared helpers (`availableSittings`/`availableSubjects`/`countPapers`/`countQuestions`) take **one** category's bucket, and the discuss area defaults to 全部 → `bucket` was `undefined`. Measured `optCounts=[8,1,1,1]` | `mergedBucket(tree)` merges every category's bucket (same shape; counts added, subject `papers` concatenated) so the shared pure helpers walk it unchanged; plus `resolveLevel` reconciliation for all four levels. Measured `[8,16,3,37]`; picking 年度 115 narrowed 495 → 24 rows, other three selects untouched | `bccc099`, `tests/test_review_ui_discuss.py` (`DiscussScopeFilterTests`) |
| 9 | `scripts/test_v2_navigation.mjs` **crashed on HEAD** (`TypeError: Cannot read properties of null`) — so the only proof that "drawn == walked" **was not running** | `f9d932c` split `v2.html`'s single `<script>` into `v2/*.js`, and updated five python tests but missed this one; `html.match(/<script>(...)/)` is now `null` | reassemble in the HTML's own `<script src>` order (the browser's order) instead of a second hard-coded list | `244f764` |
| 10 | Not a UI defect, but the campaign's last item: a per-question comment should only become a principle if it is a rule nobody had | — | reading recorded in `qbr/reports/comment_to_principles.md`; write half `qbr/scripts/curate_principles_from_comments.py` (dry-run default, dedup by text, **every principle must point at a real `comment`** or it is refused, `reviewer` may not be `local`/a human name). 9 comments over 7 keys read; **only q071's 表格→截圖 is new** | `b65ed92`, `qbr/tests/test_principles_curation.py` |

## What is deliberately NOT done

- **No auto-accept / auto-block.** AI is advisory (`GOV-05`); an agent never presses a decision
  button and never impersonates a human reviewer. The audit verifies the decision buttons exist and
  are enabled — it does not press them.
- **No script that reads the meaning of a comment.** 「哪一句是新的規則」 is *reading what the text
  means*, so the output is one sentence for the model, not a new `if`. The curation script does only
  mechanical things (append, dedup, ground-in-evidence).
- **No second store and no second rule.** Share helper functions (`resolveLevel`, `availableSittings`,
  `availableSubjects`, `countPapers`, `countQuestions` are used by *both* the question and discuss
  areas), one predicate, one `taxonomy_of`. The duplication worth avoiding is **logic**, not DOM
  wiring.
- **v1 is still served and is not tested.** `review_ui/v1-reference/` (`mobile.html`,
  `workflow.html`) answers on its old routes because a bookmark and an installed PWA are contracts.
  Fix it only to keep it working; never add a feature to it, and never restore a backend/writer/
  deployment from it.
- **No `if category == ...` in a rule.** A condition keyed on a subject name is overfitting.

## Traps that cost the most time

- **The audit script's own bug hid a whole probe.** A backtick inside a comment terminated the
  `CONTROL_PROBE` template literal, so the probe read `".onchange is not a function"` and the check
  silently proved nothing. When a probe reports something absurd, suspect the probe.
- **The audit did not exercise the new selects** until a scope check was added *to the audit*.
  Adding a feature does not extend the acceptance test; you must extend the acceptance test.
- **`全部` is a value, not an absence.** `null` ("not computed/not chosen") and `''` ("deliberately
  all") are different states (charter rule 7). Every level is reconciled with `resolveLevel` so a
  stale value falls back to 全部 instead of filtering the list to nothing.
- **A capped list that does not say it was capped** is the truncation the question area already had
  to fix. The discuss area shows `returned / filtered` rather than silently dropping the tail.
- **The machine's sentence can end up in a human's field.** `reset_review`'s note
  (「上下標修復：…請重新確認。」) was **prefilled into the 註解 box**, so a `comment` event's `notes`
  carries a machine announcement — measured **verbatim-identical on q059 / q071 / q062**. A person
  does not write the same sentence on three questions; that is the tell, and it is not a principle.
- **The corpus glob / the sample size.** Before trusting a total, assert the population (495 stuck
  rows, 7 categories, 116 papers, 79,090 queue rows).
- **`/tmp` is wiped by reboot.** Persist measurements under `qbr/data/runs/` or `qbr/reports/`.

## Delivered scope (this campaign)

The user's report and the eight items it became. Every item is done, measured in a real browser, and
committed.

| # | The user said | What was done | Commit |
|---|---|---|---|
| P1 | 上下標顯示成 `<sup></sup>` | `richText()` whitelist; stem/options/原題/answer preview render real tags | `e0342d4` |
| P2 | 下拉選單不可以互相干擾 | each level writes only itself; `resolveLevel` | `e0342d4` |
| P3 | 錯題討論區版面要跟題目審核區一致 | `238px / 1fr / 1fr`; PDF pane 330→721px | `aa209f4` |
| P4 | 要看得到原題／擷圖抽換／字體／註解 | ③ 原題, ④ 擷圖／抽換, font via `--reading-size`, ⑥ 註解, stats to left column | `aa209f4` |
| P5 | 每個按鈕都要真的按過 | `test_v2_ui_audit.mjs` (218 controls) + `test_v2_note_keeps_question.mjs` | `aa209f4` |
| P5b | (found by P5) 寫註解把題目踢出討論區 | `_note_annotates_pending_reset` + `_merge_note_into_reset`; zero drift | `aa209f4` |
| P6 | 文件 | `review-ui-v2/SKILL.md`, `review_ui/AGENTS.md` 鐵則 11–16, `docs/ROUTE_HISTORY.md` | `aa209f4`, `b65ed92` |
| P7 | 「要有篩選，比較好審核」 | discuss-only taxonomy (`2dbfe3f`), client picks the four levels sharing the question area's helpers (`bccc099`) | `2dbfe3f`, `bccc099` |
| P8 | 逐題 comment 自己讀，只有新的規則才加入 | 9 read / 1 added (`p1`, q071); curation script + negative controls | `b65ed92` |

Measured at delivery: catalog **449 passed**; qbr **392 passed / 17 skipped**;
`test_review_ui_discuss.py` **38 passed**; audit **40/40 checks, 0 failures**; navigation and
note-keeps-question all green; the append-only ledger **untouched** (5001 lines); principle stream
**1 line**.

## 未決 — the open items a next round can optimise

These are measured facts, not guesses. They are listed here so the next change is aimed, not guessed.

1. **Discuss list rows show only 「第 N 題」, with no paper label.** The list's primary key is the
   paper (charter rule 8), and several papers share question numbers. Narrowing to one subject with
   two papers produces duplicate-looking row numbers. Whether to label/group rows by paper is a UX
   decision (possible scope creep) — **not yet done**.
2. **The curated principle's provenance is `reviewer:"principle_curator"`, not the human.** This is
   deliberate (governance forbids impersonation), but if the 基本原則 list should visibly credit the
   owner, that is an **owner decision**.
3. **q071's principle wording.** Recorded as 「表格要以紙本圖（截圖／figure）為準，不要用抽取出來
   的數字把它拼成文字表格」. Whether it should be scoped (e.g. 藥師 subjects only) is open.
4. **`S.scope` (question area) and `D.scope` (discuss area) are two parallel state objects** that
   share the helpers but not the state. They could not drift *today* (same helpers, same semantics),
   but they are two places a scope can be written.
5. **註解 history is read from `candidate.review.notes`** (the server sends it). If a comment's
   history should also surface the `reset_notes` marker, that is a reading decision.
6. **The v1/v2 fork discipline is held by discipline alone.** No automated check stops someone adding
   a feature to v1; v1 has no tests. It answers on old routes and could break unnoticed.
7. **Rebuilding into the directory the server serves leaves the list and the candidate file
   momentarily inconsistent.** Pause the service during a rebuild. Detail:
   `qbr/reports/review_record_safety.md`.

## Reporting a UI defect back to the pipeline

The UI shows what the pipeline built. When a question looks wrong here, the fix usually belongs in
`qbr` — see the `repair-qbr-extraction` skill. Bring back the **paper**, not the screenshot.

<!-- project-map:belongs-to -->
## 這一層在哪（回上層的路）

> **這是本子專屬技能**：只服務這個子專案。其他子專案要用同一件事時，先確認是不是該變成全域共通技能。

- 本層入口：[`../../../AGENTS.md`](../../../AGENTS.md)
- 不確定從哪開始：[`project_map`](../../../../project_map) 是整棵樹的可點擊地圖
- 卡住時的回溯路徑：技能 → 本層 `AGENTS.md` → `project_map` 入口文件鏈 → 傘層 → charter
<!-- /project-map:belongs-to -->
