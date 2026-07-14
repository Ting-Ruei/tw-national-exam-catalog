# Core Classification Rules

## Source gate

- Classify only formal `exam.questions` rows whose `review_status` is `accepted`.
- Read the effective stem, every option, the accepted answer when available, and the shared group stem when applicable.
- Use images only when supplied to the model. If an unseen image is required, return `needs_human_review` with low confidence.
- Treat category, subject, year, ordinal, and question number as metadata, not topic evidence.

## Classification unit

Assign three layers:

1. **Subject**: Copy the official or normalized subject; never predict it.
2. **Domain**: Select exactly one broad course component within the composite subject.
3. **Chapter**: Select one primary curriculum chapter and zero to two secondary chapters.

The primary chapter is the knowledge needed to answer the question, not merely a word appearing in an option. A wrong distractor alone is not enough to create a secondary chapter.

## Small-model decision procedure

Follow these steps mechanically:

1. Identify the question target: organism, cell lineage, specimen, organ/function, lesion, test principle, transfusion task, or molecular method.
2. Prefer the most specific target rule in the selected subject taxonomy.
3. Apply the boundary rules before keyword cues. For example, a viral test remains virology when it tests knowledge of a named virus; an assay-principle question without a virus target belongs to serologic/immunologic methods.
4. Choose the domain.
5. Compare only chapters within that domain.
6. Choose a primary chapter only if its definition matches the tested knowledge.
7. Add a secondary chapter only when independently necessary to solve the question.
8. If two primary chapters remain equally plausible, select the best one but set `confidence = low` and `review_status = needs_human_review`.

## Confidence rubric

- `high`: One domain and one chapter are directly supported; no boundary rule competes.
- `medium`: The domain is clear, but two nearby chapters overlap; the primary is still more strongly supported.
- `low`: The item is cross-domain, lacks required image/context, uses unfamiliar content, or two labels are equally plausible.

`ai_suggested` is permitted only for high or medium confidence. Low confidence must use `needs_human_review`.

## Evidence rules

- Copy one to five short terms from the stem or options.
- Keep each term short; do not reproduce an entire question.
- Do not cite the subject name, question number, expected paper position, or answer letter as evidence.
- Give a one-sentence reason that names the tested knowledge and explains the selected chapter.

## Distribution rules

- Never infer domain from question position.
- Never force a 40-question paper into equal halves.
- Never rebalance results to match an expected quota.
- A whole paper may legitimately contain an uneven domain or chapter distribution.

## Advisory safety

- AI output must not modify `question_review_events`, `answer_review_events`, or formal questions.
- Unknown content maps to human review, not to a fabricated `other` code.
- Taxonomy changes create a new version and a new classification run. Preserve previous results.
