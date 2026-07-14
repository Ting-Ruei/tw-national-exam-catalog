# Database And Review UI Runbook

## Source Of Truth

- Review staging: `exam.question_candidates`, `exam.question_parse_issues`, review event tables.
- Human question state: latest non-group/non-visual row in `exam.question_review_events`.
- Human answer state: latest row in `exam.answer_review_events`.
- AI state: latest non-reset row in `exam.question_ai_review_events`, active only when newer than the relevant human decision.
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
- Non-question with high-confidence source evidence: exclude through an auditable repair workflow.
- Semantic uncertainty: advisory `needs_review`; no automatic human event.
- UI-only problem: fix UI/query behavior; do not mutate candidate content.
