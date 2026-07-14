---
name: classify-exam-curriculum
description: Classify human-approved Taiwan national-exam questions into course domains and curriculum chapters with a versioned taxonomy, constrained JSON output, evidence, and advisory human review. Use when assigning or auditing chapter labels for formal exam.questions records, preparing classification batches for small or large language models, validating classification JSONL, or extending the medical technologist curriculum taxonomy.
---

# Classify Exam Curriculum

Classify only formal, human-approved questions. Treat every AI label as advisory until a human confirms it. Never modify question, answer, or existing review-event records while classifying.

## Run the workflow

1. Confirm the source is `exam.questions` with `review_status = 'accepted'`. Do not classify candidate JSONL when a formal record exists.
2. Read [references/core-rules.md](references/core-rules.md).
3. Read only the selected subject section in [references/medtech-taxonomy-v1.md](references/medtech-taxonomy-v1.md). Use [references/taxonomy-v1.json](references/taxonomy-v1.json) as the machine-authoritative code list.
4. For a weak or small model, render a self-contained prompt before inference:

   ```bash
   python3 scripts/render_classifier_prompt.py \
     --subject '臨床血液學與血庫學' \
     --output tmp/hematology-classifier-prompt.md
   ```

5. Classify one question at a time. For small models, keep a request to at most 10 questions and require one JSON object per question.
6. Validate all output before import or human review:

   ```bash
   python3 scripts/validate_classification_results.py \
     --tasks tmp/classification/tasks.jsonl \
     --results tmp/classification/results.jsonl
   ```

7. Route `needs_human_review`, low-confidence, cross-domain, image-dependent, and unknown-label cases to a human. Do not auto-accept or auto-block from AI output alone.

## Build a SQL worklist

Use the bundled read-only exporter. It selects only accepted formal questions and includes the stem, options, answer, source metadata, and an input hash.

```bash
python3 scripts/build_sql_classification_worklist.py \
  --category 醫事檢驗師 \
  --subject 臨床血液學與血庫學 \
  --output-dir tmp/classification/hematology
```

The exporter requires `DATABASE_URL` and writes only under the requested output directory. It does not update PostgreSQL.

## Apply the decision order

Use this order without skipping steps:

1. Copy `question_key`, `input_hash`, category, and subject exactly.
2. Select exactly one subject-specific `domain_code`.
3. Select exactly one `primary_chapter_code` beneath that domain.
4. Add zero to two secondary chapters only when the question materially tests them.
5. Quote one to five short evidence terms visible in the question or options.
6. Assign `high`, `medium`, or `low` confidence using the core rules.
7. Use `needs_human_review` whenever the evidence does not uniquely support an allowed code.

Never invent a label, infer a chapter from question number, use expected 20/20 or 10/10 paper splits, or force a balanced distribution.

## Output exactly one JSON object

Follow [references/output-contract.md](references/output-contract.md). Output JSON only when the caller requests machine-readable results. Do not wrap JSONL in Markdown fences.

## Extend the taxonomy

To add or split a chapter:

1. Preserve all existing codes and meanings.
2. Add a new code, definition, positive cues, and exclusion/boundary rule to `taxonomy-v1.json`.
3. Mirror the human-readable rule in `medtech-taxonomy-v1.md`.
4. Bump `taxonomy_version`; do not silently change the meaning of an old version.
5. Add at least one synthetic positive example and one boundary example.
6. Re-render a small-model prompt and run the validator tests.
7. Reclassify old records only through a new advisory run; do not overwrite prior classification events.

If a new subject is unrelated to these four medical-technologist subjects, add a separate taxonomy reference rather than loading unrelated labels into every prompt.
