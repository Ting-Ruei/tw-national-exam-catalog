# Database And Review UI Runbook

## Source Of Truth

- Review staging: `exam.question_candidates`, `exam.question_parse_issues`, review event tables.
- Effective candidate content: raw candidate overlaid by the latest historical
  `corrected_candidate_json`, regardless of whether a newer `reset_review`
  reopened the question state. Manual assets and unlinks remain effective.
- Human question state: latest non-group/non-visual row in `exam.question_review_events`.
- Human answer state: latest row in `exam.answer_review_events`.
- AI state: latest non-reset row in `exam.question_ai_review_events`, active only when newer than the relevant human decision.
- AI audit feedback: latest rating per candidate/scope/reviewer in
  `exam.question_ai_feedback_events`, tied to the exact `ai_review_ref`. It is
  append-only training evidence and never a question decision.
- AI learning selections: latest human-selected example per candidate/scope/reviewer
  in `exam.question_ai_learning_events`, tied to the exact `ai_review_ref` and
  carrying an effective candidate snapshot for later curation. It is independent
  from thumbs up/down and never triggers automatic training or a question decision.
- Answer AI state: latest row in `exam.answer_ai_review_events`; it is advisory and never substitutes for `answer_review_events`.
- Formal readiness: question and answer latest actions are both `accept` or `unblock`.
- Formal usable state: the background sync has also produced `exam.questions.review_status='accepted'` and at least one `exam.answers` row. A retained non-accepted question row is history, not a usable question.

## SQL Checks

For every audit scope, count:

- all non-excluded candidates;
- accepted, human-open, and unreviewed questions;
- parser warning/error candidates excluding answer-only issues;
- active AI findings;
- AI findings superseded by newer human decisions;
- candidates whose question number equals the ROC year and whose stem contains exam-header fields;
- abnormal option counts, especially more than eight;
- formal drift.

Use `scripts/build_sql_review_worklist.py` rather than copying ad hoc SQL into model prompts.
The worklist must merge `latest_content_correction`, not merely the correction
column on the latest question-state event.

For a retrospective pass over questions that are already human `accept`/`unblock`,
use `--policy accepted_reaudit` and freeze the resulting task files before model
dispatch. This lane is intentionally separate from the normal unreviewed queue:
it never overwrites the prior human event. A non-pass advisory must first pass
`scripts/build_accepted_reaudit_proposal.py`'s current-text evidence join; only
an explicitly approved proposal may be applied with
`scripts/apply_ai_reset_proposal.py`.

## Browser Sampling

Open `http://127.0.0.1:8765/` and sample:

1. Question mode: one parser pass, one parser risk, one active AI risk, one corrected/accepted item.
2. Image mode: one existing image, one suspected missing image, one no-image decision.
3. Group mode: one confirmed group, one confirmed non-group, one manual-range case.
4. Answer mode: one ANS sheet and one MOD/multi-answer sheet.

For each action, verify all of the following:

- the button reacts promptly;
- the database changes immediately;
- refresh preserves the action and selected filters;
- automatic next-item navigation occurs only where intended;
- the right-side PDF remains on the useful position when possible;
- stale AI or old notes do not reappear as active work;
- the same issue is not repeated in system, AI, and human panels.

## Source Comparison

Use this order:

1. Official PDF.
2. Human-corrected candidate/manual asset.
3. MinerU layout PDF with detected regions.
4. MinerU Markdown and extracted images.
5. Parsed SQL candidate.

If official PDF and MinerU disagree, fix parser/candidate or add a manual asset. Do not edit the official PDF.

## Repair Boundaries

- Mechanical parser bug: add a narrow rule and regression test.
- Candidate content changes: reset only affected candidates and preserve old notes.
- `reset_review`: change only the question-state projection. Never remove or
  hide an earlier human correction/manual asset; see
  `review-layer-architecture.md`.
- Non-question with high-confidence source evidence: exclude through an auditable repair workflow.
- Semantic uncertainty: advisory `needs_review`; no automatic human event.
- UI-only problem: fix UI/query behavior; do not mutate candidate content.

## Advisory Import Gate

Before `import_advisory_results.py --apply`:

- require successful sparse validation for every run and successful per-candidate export validation;
- require every `pass` row to use `recommended_action=no_action`;
- reject any `needs_review` row with `recommended_action=no_action`;
- dry-run the exact file and record its SHA-256;
- verify the candidate scope is still human-unreviewed immediately before import.

After import, check the new `model_run_id` in SQL and the production Review UI API. Event count,
distinct candidate count, `needs_review`, and `pass` must match the export report. If an import
mapping is wrong, do not rewrite or delete old AI events: fix the exporter, append a repair model
run, and verify that the latest events produce the correct UI filters.
