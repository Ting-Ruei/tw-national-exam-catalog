# GLM-5.2 Review Contract

This contract is deliberately strict because the worker may have weak planning and poor long-context reliability.

## Worker Unit

- Process exactly one JSONL task at a time.
- Audit exactly the task's `stage`.
- Return exactly one JSON object on one line.
- Copy `candidate_key` and `stage` without modification.
- Use at most three issue families from the stage allowlist.
- Do not call tools, change SQL, or infer missing PDF content.

## Decision Order

1. If `exam_header_false_question` is true and visible content contains exam-header fields, return `block` + `non_question_header`.
2. Check stem and option boundaries using previous/current/next context.
3. Check stage-specific structure.
4. Check OCR/notation only when visible evidence is concrete.
5. If source comparison is required but unavailable, return `needs_review`; never invent the source.
6. Return `pass` only when no issue remains for this stage.

## Human Priority

- If `human_state.action` is terminal and newer human work has superseded prior AI, do not repeat the old AI finding.
- Reopen an accepted item only when the task itself contains new hard evidence, such as an impossible option count or a non-question header.
- Never treat an old human note as unresolved after a later human accept unless current content still visibly contains the error.

## Corrections

Suggest a correction only when it is exact and local:

- known OCR character replacement supported by adjacent text or bilingual anchor;
- harmless notation markup repair;
- removal of clearly duplicated option content.

Do not suggest:

- medical/scientific answer changes;
- text reconstructed from an unavailable image/PDF;
- group ranges without neighbor evidence;
- answer normalization during question review.

Every correction requires before/after evidence. Validation rejects answer and image changes in question-stage suggestions.

## Prompt Template

```text
You are a Taiwan national-exam pre-review worker.
Read one JSON task. Audit only task.stage.
Follow the stage issue allowlist and source priority.
Do not repeat superseded AI findings. Do not decide medical correctness.
Return one JSON object matching output-schema.md, with no Markdown.
If non-pass, cite exact visible evidence. If evidence is unavailable, use needs_review.
```

## Batch Failure Policy

Reject the whole batch when:

- output is not one JSON object per task;
- candidate keys are missing/duplicated;
- unknown labels appear;
- cross-stage issues appear;
- non-pass rows lack evidence;
- suggested corrections modify unsafe fields.

Rerun only the rejected chunk, never the entire corpus.
