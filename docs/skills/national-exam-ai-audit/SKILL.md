---
name: national-exam-ai-audit
description: Audit Taiwan national exam parsed question candidates for OCR, parser, table, image, option, answer-link, and structure issues. Use when Codex or another model is asked to scan a specific category, subject, year, or exam session and produce advisory labels for Review UI without changing human review status.
---

# National Exam AI Audit

## Purpose

Use this skill to scan `question_candidates__*.jsonl` records and produce advisory AI review labels. The audit is a triage layer: it helps humans review faster, but it must not accept, block, reset, or edit questions by itself.

## Rule Shape

Use a two-layer rule set:

1. Always apply `references/core-rules.md`.
2. Apply a subject override only when it matches the selected subject. Start with `references/subject-overrides.md`.

Most rules are shared across all subjects. Subject-specific rules should only cover symbols, image/table patterns, terminology, or historical formats that are genuinely different for that subject.

## Workflow

1. Identify the requested scope: category, subject, year, ordinal, and review status if provided.
2. Read candidate records from the latest relevant `國考題資料夾/30_normalized_items/question_candidates/*/question_candidates__*.jsonl`.
3. Ignore old human notes when the latest human event is pass/accept/unblock unless the user explicitly asks to re-audit accepted questions.
4. Inspect only candidate structure and visible text. Do not decide medical correctness or answer correctness unless the user asks for a separate answer audit.
5. Emit JSONL-compatible audit records using the schema in `references/output-schema.md`.
6. When a concrete OCR or formatting correction is safe to propose, include `suggested_correction` and `suggested_changes`; this is only a Review UI suggestion and still requires human approval.
7. Treat all output as advisory. Write to `question_ai_review_events.jsonl` only through project scripts or explicit user approval.

## Labeling Policy

Prefer small, actionable labels over long prose. Use:

- `pass_likely`
- `ocr_char_suspect`
- `amino_acid_translation_suspect`
- `science_notation_suspect`
- `option_parse_suspect`
- `table_or_image_suspect`
- `group_question_suspect`
- `answer_pair_suspect`
- `parser_boundary_suspect`
- `needs_human_review`
- `block_likely`

Use `pass_likely` only when no material issue is found. If any other label is present, set status to `needs_review` or `block`.

## Human Review Boundary

Do not change:

- `question_review_events.jsonl`
- `answer_review_events.jsonl`
- candidate JSONL content
- official PDF files
- MinerU raw outputs

`suggested_correction` is not a direct edit. It only gives Review UI a one-click draft correction. A human must still apply it and then press pass/accept.

If a parser fix changes candidate content, append per-question `reset_review` events later and preserve previous human notes. AI audit alone is never a reason to reset accepted questions.

## Quality Expectations

Be conservative. False positives are acceptable when they help humans find likely parser/OCR issues; false negatives are worse for ingestion safety. Keep reasons short and tied to observable candidate fields.
