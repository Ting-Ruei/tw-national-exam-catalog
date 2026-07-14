# Advisory Review Output Schema

Emit one JSON object per task and no Markdown.

```json
{
  "candidate_key": "moex:...",
  "stage": "question",
  "status": "needs_review",
  "issue_families": ["notation_markup"],
  "confidence": 0.86,
  "reason": "題幹中的 α 與 1 被分開，可能改變 α1 的顯示。",
  "evidence": [
    {"field": "content.stem", "before": "α 1", "after": "α1"}
  ],
  "recommended_action": "human_review_text",
  "suggested_correction": {"stem": "...α1..."},
  "model": "glm-5.2",
  "prompt_version": "national_exam_ai_audit_v1"
}
```

## Required Fields

- `candidate_key`: exact input key.
- `stage`: exact input stage; one of `question`, `image`, `group`, `answer`.
- `status`: `pass`, `needs_review`, or `block`.
- `issue_families`: zero to three values from the stage allowlist in `issue-taxonomy.md`.
- `confidence`: number from 0 to 1.
- `reason`: one short Traditional Chinese sentence.
- `evidence`: list of observable fields. Non-pass rows require at least one item.
- `recommended_action`: one of `none`, `human_review_text`, `human_review_pdf`, `fix_parser`, `add_manual_asset`, `review_group`, `review_answer`.
- `suggested_correction`: null or a safe question-stage patch.
- `model`, `prompt_version`.

## Consistency Rules

- `pass` requires an empty `issue_families` list and `recommended_action: "none"`.
- `needs_review` means source or human judgment is needed.
- `block` requires concrete evidence that the current stage is unsafe.
- Do not include an answer issue in question-stage output.
- Do not repeat a previous AI finding when `signals.previous_ai.superseded` is true unless current task evidence independently proves it still exists.
- Suggested correction fields are limited to `stem`, `options`, `group_ref`, and `group_sequence_no`.
- Never include human review actions in model output.

## Examples

```json
{"candidate_key":"moex:example:q114","stage":"question","status":"block","issue_families":["non_question_header"],"confidence":0.99,"reason":"題號等於年度，題幹含考試時間、類科與座號且有 320 個選項，為考卷表頭誤切。","evidence":[{"field":"signals.exam_header_false_question","value":true},{"field":"content.option_count","value":320}],"recommended_action":"fix_parser","suggested_correction":null,"model":"glm-5.2","prompt_version":"national_exam_ai_audit_v1"}
```

```json
{"candidate_key":"moex:example:q020","stage":"question","status":"pass","issue_families":[],"confidence":0.9,"reason":"題幹與 A-D 選項完整，未見本關卡結構或文字異常。","evidence":[],"recommended_action":"none","suggested_correction":null,"model":"glm-5.2","prompt_version":"national_exam_ai_audit_v1"}
```
