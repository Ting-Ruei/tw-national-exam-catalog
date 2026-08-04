# Review UI System Audit — 2026-07-29

## Scope

This audit covers the Mac Studio Review UI at `192.168.10.70:8765`, its SQL
review events, deferred formal synchronization, pipeline statistics, and the
question-bank release boundary.

The intended human workflow is:

1. review question content;
2. review the official answer;
3. add or revise group and visual labels independently;
4. synchronize accepted question/answer content plus the latest labels into
   formal SQL;
5. pass deterministic validators;
6. export an immutable package;
7. dry-run and explicitly approve the platform production import.

Group and visual labels do not approve, block, or reset the question decision.
They may be completed after question and answer review.

## Findings and repairs

### Independent label state

The old visual-review page sent a normal `correct` action. The backend then
reused the previous question action, or defaulted to `reviewed`, so a visual
click could accidentally change an unreviewed question to accepted.

Repair:

- visual clicks now write `human_review_pdf_visual`;
- visual events do not update `question_candidates.review_status`;
- visual status is replayed into formal `question_json`;
- old visual-only accidental question decisions are detected by
  `scripts/repair_visual_label_question_state.py` and repaired with append-only
  `reset_review` events.

### Group and visual formal refresh

Group review events and independent visual events were not reliably enqueued
for formal refresh. A group confirmed before its formal questions existed
could leave the review event accepted while the formal rows remained unlinked.

Repair:

- all question, answer, group, and visual changes enqueue formal refresh;
- the formal worker replays the latest group decision after promotion;
- a confirmed group updates `question_group_id`, `group_sequence_no`, and
  `question_json.group_ref`;
- pending, reset, and confirmed-not-group labels keep formal questions usable
  but ungrouped;
- a queue claim is finalized only when its `requested_at` version still
  matches, preventing a concurrent newer review event from being marked
  processed by an older worker run.

### Pipeline page latency and wording

The pipeline endpoint previously ran several repeated `DISTINCT ON` scans over
large review-event tables on every click. Production measurements were about
4.4 seconds per request.

Repair:

- one consolidated SQL query materializes each latest-event set once;
- statistics are warmed at process startup and refreshed in the background;
- the endpoint returns cached statistics immediately and refreshes at most
  every 30 seconds;
- the page displays the statistics timestamp and browser load time;
- layer names now state exactly what the count means, including:
  `候選題目（已列入題目審核區）`,
  `答案核對（已列入答案審核區）`, and
  `正式題庫（已列入正式題庫）`;
- the formal count is the number of actual accepted formal rows with answers,
  not merely the number eligible for promotion;
- the page explicitly states that formal SQL is not the same as publication to
  the external question website.

### Error handling and regression coverage

- pipeline API and UI failures now return/display an explicit error;
- group and batch-review handlers return structured JSON errors;
- duplicate unreachable exception handling was removed;
- regression tests cover visual-label independence, deferred group replay, and
  pipeline count semantics;
- Python and embedded JavaScript syntax checks are part of the deployment
  verification.

## Data impact discovered

The audit found:

- 23 questions in the 115-2 medical technologist batch whose only question
  acceptance came from the old visual-page behavior;
- 3 medical technologist formal questions whose latest confirmed group label
  was not fully reflected in formal SQL;
- no pending or errored formal-sync queue rows at the time of the audit.

The 23 visual-only question decisions must be reset append-only and explicitly
reviewed as questions before the new medical technologist package is released.
Their visual labels remain preserved.

## Release boundary

The Review UI does not publish directly to the learning website. After all
exceptions are resolved:

1. synchronize group labels;
2. validate formal SQL;
3. export and validate the immutable package;
4. run the platform importer in dry-run mode;
5. inspect identity, subject, duplicate, group, asset, and checksum results;
6. back up the platform production database and assets;
7. obtain explicit production-import approval;
8. apply and smoke-test practice, exams, wrong notes, statistics, images, and
   old-session replay.
