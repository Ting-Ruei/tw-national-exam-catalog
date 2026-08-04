# Sparse audit base contract

Prompt version: `national_exam_sparse_audit_v4`

Review every task in the supplied batch. Return one JSON object and no Markdown:

```json
{"batch_id":"...","checked_count":16,"issues":[],"model":"...","prompt_version":"national_exam_sparse_audit_v4"}
```

Normal tasks produce no per-question row. Put only material issues in `issues`, using
`contracts/sparse-result.schema.json`.

Rules:

- Preserve official/source-original wording, including likely official typos.
- Do not solve the question or repair a deliberately wrong answer choice.
- Use one owner route: deterministic, propose_rule, human_text, human_pdf, parser, group, or visual.
- Use `deterministic` only when the packet supplies an active `rule_id` whose exact source and target match.
- Never infer missing source text, numbers, laterality, formulas, group ranges, or images.
- Keep `before` and `after` to the smallest exact local replacement. Use null when no safe replacement exists.
- Every issue must include a short `note` (max 160 characters) that states the
  observable text difference and the local evidence for it. For semantic or
  terminology findings, say which bilingual anchor, grammar break, or source
  mismatch supports the finding. Never use a bare verdict such as “疑似錯字”.
- A valid-looking Latin scientific name is not evidence of an error. Do not
  replace a genus/species from model memory or a preferred taxonomy. Without
  official-source evidence or an active exact rule, route it to `human_pdf` and
  explain that the name may be valid.
- Emit at most three issues per candidate. Deduplicate the same exact `before` → `after`
  replacement across fields; keep one representative example and prioritize distinct,
  higher-risk issues.
- Never emit human accept, block, reset, or database actions.
- `承上題`、`呈上題`、`上題`、`前述` 是題組連續脈絡線索，不是錯字。
  這些文字只能由 `group` lane 回報（`field=group_ref`、`route=group`、
  `after=null`），不得在 OCR/semantic 題目欄位提出文字替換或一鍵修正。
