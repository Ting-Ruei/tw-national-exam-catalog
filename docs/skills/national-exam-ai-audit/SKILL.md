---
name: national-exam-ai-audit
description: SQL-first human-perspective audit of Taiwan national exam questions and the Review UI. Use when Codex or another model must pre-review question text, options, images, groups, answers, parser boundaries, stale/duplicated prompts, or review-page behavior; build model worklists; inspect PostgreSQL and source PDFs; or emit advisory labels without changing human review decisions.
---

# National Exam AI Audit

## Mission

以真人審題者的視角，系統性預審國考題，找出題目內容、圖片、題組、答案與介面流程的潛在問題，並把判斷轉成低智能模型也能穩定執行的工作框架。

## Non-Negotiable Boundaries

- Treat PostgreSQL review staging as the current truth. JSONL is legacy backup, not the primary review state.
- Treat official PDF as source truth. Use human correction, MinerU layout, MinerU Markdown, then parsed candidate in that order.
- AI output is advisory. Never insert human `accept`, `block`, `exclude`, or reset events.
- A newer terminal human decision supersedes an older AI finding. Keep the AI event as history but do not show or filter it as active work.
- Audit one stage at a time. A question-stage run must not block on answer formatting; route it to answer review.
- Never overwrite a human note. Parser repairs preserve the note and reset only candidates whose visible content changed.

## Required Reading

1. Read `references/issue-taxonomy.md` for stage ownership and deduplication.
2. Read `references/core-rules.md` for question-content checks.
3. Read `references/subject-overrides.md` only for the selected subject.
4. For database or browser work, read `references/database-ui-runbook.md`.
5. Read `references/field-lessons.md` before changing parser or UI logic; it records failures already observed in production review.
6. For GLM-5.2 or another low-capability model, read `references/glm-5-2-contract.md` and `references/output-schema.md`.

## Workflow

1. **Inventory before judging.** Run the SQL worklist builder and record totals for parser risk, active AI risk, stale AI, human-open items, and low-risk unreviewed items.
2. **Fix deterministic defects first.** Repair parser/header/boundary rules when evidence is mechanical. Do not spend model tokens repeatedly describing a deterministic bug.
3. **Review as a human operator.** In Review UI, sample every active mode and risk stratum. Compare the center candidate with the right-side official or MinerU layout PDF. Check whether navigation, persistence, prompts, and status transitions behave as the labels claim.
4. **Use neighbor context.** Boundary and group decisions require previous/current/next questions from the same exam session. Never judge these from one isolated stem.
5. **Emit one owner issue.** Assign each problem to question, image, group, answer, or UI. Other stages may include a route note but must not repeat the warning.
6. **Validate model output.** Reject unknown labels, evidence-free findings, duplicate keys, unsafe corrections, and cross-stage findings before import.
7. **Import advisory events only.** Human state changes happen in Review UI or an explicitly approved human workflow.

## Commands

Run inside the Docker service so the script uses the same SQL connection as Review UI:

```bash
docker compose exec -T review-ui python3 \
  docs/skills/national-exam-ai-audit/scripts/build_sql_review_worklist.py \
  --stage question --policy risk --chunk-size 50
```

Build a full inventory without materializing question text:

```bash
docker compose exec -T review-ui python3 \
  docs/skills/national-exam-ai-audit/scripts/build_sql_review_worklist.py \
  --stage question --policy all --inventory-only
```

Validate a model result batch:

```bash
docker compose exec -T review-ui python3 \
  docs/skills/national-exam-ai-audit/scripts/validate_review_results.py \
  --tasks tmp/national_exam_ai_audit/RUN/tasks \
  --results tmp/national_exam_ai_audit/RUN/results.jsonl
```

Dry-run the append-only advisory import after validation:

```bash
docker compose exec -T review-ui python3 \
  docs/skills/national-exam-ai-audit/scripts/import_advisory_results.py \
  --results tmp/national_exam_ai_audit/RUN/results.jsonl
```

Add `--apply` only after the validation report is successful and the batch scope is correct.

## Completion Criteria

- Inventory covers the requested SQL scope.
- Deterministic defects have a parser rule and regression test.
- Semantic findings cite visible evidence and have exactly one owner stage.
- Newer human decisions suppress older AI prompts.
- Review UI actions persist after refresh and do not unexpectedly move the PDF, list, or selected filters.
- Skill result validation passes before any advisory SQL import.
