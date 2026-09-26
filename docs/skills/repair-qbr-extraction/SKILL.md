---
name: repair-qbr-extraction
description: Find and fix a defect in the qbr PDF extraction/segmentation code — the measurement discipline, negative controls, how to tell a broken instrument from a broken product, and the catalogue of defects already found and fixed. Use when a paper reads the wrong number of questions, when shipped text looks corrupted (doubled characters, truncated options, a stem ending mid-sentence), when a question is blocked by count-mismatch or option-shape, or when adding a geometry rule to extract.py / repair.py.
---

# Repairing the qbr extraction code

The code is in `tw-national-exam-catalog/qbr/src/qbr/`: `extract.py` (two engines, span reading)
and `repair.py` (chrome mask, anchors, segmentation). This is **how to change them without
breaking a paper that already works**, and the record of what has already been found.

Read `build-exam-question-bank/SKILL.md` for the *build* procedure; this is the *repair* procedure.

## The one rule that governs everything here

> **A broken measuring instrument looks exactly like a broken product.**

Every real defect in this project was found twice: once as a phantom, once as itself. The repair is
never finished when the number moves — it is finished when you know **which instrument** produced
the number and that it measures what you think.

**Corollary:** when a fix makes an aggregate improve but the named case is untouched, the aggregate
is lying about the mechanism. Always pull the specific case out and look at its geometry.

## Procedure

1. **Reproduce as a named paper, not a percentage.** "`1031_醫師(二)_醫學(四)` reads 2 questions"
   is a defect. "count-mismatch is 2%" is a mood.
2. **Read the paper's own geometry for that case.** Spans, x-extents, y-overlap, font. Do not read
   the parse output — read the paper (`get_text("dict")`), and compare with a second engine.
3. **Ask which engine disagrees.** Native PyMuPDF `get_text("text")`, engine B (`extract_lines_b`,
   poppler), and our reader (`read_spans`). If two independent readers agree against ours, it is
   our defect. If all three differ, the paper is unusual — investigate before "fixing".
4. **Write the rule in terms of the paper, never in terms of the parse.** A rule whose input is the
   parser's output makes the parser and the check confirm each other. That is a circle.
5. **Guard it so it cannot delete a real character.** Prefer rules that only drop text **already
   present** elsewhere. **Deleting a real character is worse than the duplication it removes.**
6. **Add the positive test AND the negative control.** The negative control is a case the rule must
   NOT fire on (real repetition, a real header, a real successor number). Then run both tests on the
   **old code** — a new test that passes on old code proves nothing.
7. **Re-run more than your test.** `pytest tests/ -q`, and for an extract.py change, re-run the
   whole-corpus count sweep (below) to prove **no paper loses questions**.
8. **Rebuild the queue** — shipped text is the product, and it only changes when the pipeline is
   re-run (`reports/count_mismatch_two_causes.md` §重跑管線). Then `reset_review` any reviewed
   question whose text changed (append; never rewrite).

## The measurement tools that must exist

```sh
cd tw-national-exam-catalog/qbr

# Whole-corpus question counts, one entry per paper, for before/after comparison.
# (Used to prove +581 questions and 0 decreases across 3,516 papers.)
.venv/bin/python -c "..."   # see reports/count_mismatch_two_causes.md for the harness shape

# The acceptance standard: every shipped field must be found by the second engine.
.venv/bin/python scripts/verify_option_continuation.py 160 7

# Negative control for ANY extract.py change: stash the file and re-run.
git stash push qbr/src/qbr/extract.py && .venv/bin/python scripts/verify_option_continuation.py 160 7
git stash pop
```

**The count sweep is the guard against overfitting.** A rule that fixes one subject but breaks two
others shows up as a decrease somewhere. **Zero decreases is the bar.**

## Defects already found and fixed (read this before adding a rule)

| # | Symptom | Cause | Fix | Where |
|---|---|---|---|---|
| 1 | A character doubled (`血液液中`, `722`, `344`) | `read_spans` concatenated spans; the paper draws text twice (fake bold, ~0.24pt offset), so a boundary char appears in two spans | `_covered_prefix_length` + `_is_a_redraw` in `extract.py`; drop only text **already present** | commit `29f6532`, report `count_mismatch_two_causes.md` |
| 2 | A paper truncated from a question starting `NN 年…` | `is_year_line` matched `^NNN年`, so a **stem** was masked as a running head; `_is_year_running_head` existed but contributed **0** real heads and mis-blocked **29** stems | delete `_is_year_running_head`; tighten `is_year_line` to require the exam-title continuation (`第`/`專`) and NFKC-fold | commit `7adbec3` |
| 3 | An option stopped mid-sentence; its tail became part of the stem | `segment_questions` had a wrap test only for the **stem** anchor; an option had none | `_continues_an_option` in `repair.py` (same shape as the stem rule; **no length limit**) | commit `7d4e087`, report `option_continuation_fix.md` |
| 4 | `⻑` (U+2ED1) instead of `長` | CJK radical codepoint | NFKC fold at comparison, **stored text preserved** | commit `724a363` |
| 5 | A paper blocked as `count-mismatch:items=80 expected=79` | the gate read the question count off the **parsed table**, but a voided question (`#` + `一律給分`) has no letter and is dropped from it | read the sheet's own printed `題數：NN題` (every one of 4,833 sheets prints it); table kept as fallback | `golden_path.py`, report `count_mismatch_three_causes.md` |
| 6 | `segment_mixed` treated the `options` **dict** as a list | measurement-tool bug, not a product bug | third instance of "the instrument was broken" | — |

**Two of the six were measurement-tool defects.** Expect roughly that ratio.

## What is deliberately NOT done

- **No dispute kind for a defect that the fix removes.** `option-continuation-loss` was not added
  because detection *was* the fix — a kind that can never occur is dead code
  (`engine-disagreement` is the precedent: the kind exists, `engine_counts` is never passed,
  0 rows in the queue).
- **No rule that reads meaning.** "Read what this text means" is a prompt, not a script. Scripts
  keep the **properties of the paper** (geometry, ink, font, counts).
- **No `if category == ...`.** A condition keyed on a subject name is the definition of overfitting.
  State conditions as properties of the **print form**.
- **No threshold as a substitute for structure.** Thresholds **select**; they do not **cut**.

## Traps that cost the most time

- **A rule that deletes real characters is worse than the defect.** The guard `right[0] <= left[0]`
  exists because an overlay's spans march *left* as often as right; without it the fake-bold header
  `科目名稱：臨床血液學與血庫` read as `科目名稱科目名稱科目名稱科目名稱：臨床血液學與血庫庫學`.
  That exact case is a test.
- **The corpus glob can silently exclude the paper under investigation.** A glob for `醫師` never
  matched `醫師(一)`/`醫師(二)`; whole-corpus measurements excluded the very papers being fixed.
  Always assert the population size (`3,516` papers / `30` categories) before trusting a total.
- **A fix that only handles "one overlapping character" misses "a whole redrawn run".** The first
  Cause-A fix improved the total (6,389 → 4,887) and left all six target papers untouched.
- **"The questions read correctly" is not "the paper shipped".** `1001_醫師(二)_醫學(三)` read 80/80
  after the Cause-A/B fixes and still never entered the queue: the gate's `expected` came from the
  answer sheet and was 79. Only **rebuilding the queue and checking each paper's count** exposed
  it. Checking "how many questions did the extractor report" checks the number that was already
  right.
- **`--carry-from` points at the queue ROOT**, not its `review-ui/` subdir.
- **`/tmp` is wiped by reboot.** Persist measurements under `qbr/data/runs/` or `qbr/reports/`.

## When the fix will change shipped text (most extract.py / repair.py fixes do)

1. Re-run `batch_package.py` for the affected categories.
2. Re-run `build_review_queue.py` with `--carry-from <live queue root>`.
3. Diff the reviewed questions' text against the new queue. For each whose text changed, append a
   `reset_review` event (`scripts/append_reset_review_events.py`, dry-run by default) — **keep the
   old event, add a new one. Never rewrite a human decision.**
4. Swap the live queue (keep a dated backup), restart the service, and confirm the fix is visible in
   the UI. **"Packaged" is not "done"; "visible in the UI" is done.**
