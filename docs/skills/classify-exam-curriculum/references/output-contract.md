# Output Contract

Return one compact JSON object per input question, in the same order as the tasks.

```json
{
  "question_key": "copied exactly from input",
  "input_hash": "copied exactly from input",
  "taxonomy_version": "medtech-curriculum-v1",
  "category": "醫事檢驗師",
  "subject": "copied exactly from input",
  "domain_code": "MTH_HEM.RBC",
  "primary_chapter_code": "MTH_HEM.RBC.ANEMIA",
  "secondary_chapter_codes": [],
  "confidence": "high",
  "review_status": "ai_suggested",
  "evidence_terms": ["MCV", "小球性貧血"],
  "reason": "題目以紅血球指數判讀貧血類型，主要考查貧血分類。"
}
```

## Required constraints

- Copy `question_key`, `input_hash`, `category`, and `subject` exactly.
- Set `taxonomy_version` to the version supplied in the task or prompt.
- Use only domain and chapter codes supplied for that subject.
- Put the primary chapter under the selected domain.
- Use zero to two unique secondary chapter codes; do not repeat the primary code.
- Use only `high`, `medium`, or `low` for confidence.
- Use only `ai_suggested` or `needs_human_review` for review status.
- Low confidence always requires `needs_human_review`.
- Include one to five non-empty evidence terms and one non-empty reason.
- Do not add prose outside the JSON object.
