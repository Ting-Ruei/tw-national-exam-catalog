# Output Schema

Emit one JSON object per candidate. Keep it JSONL-compatible.

```json
{
  "candidate_key": "moex:...",
  "status": "pass",
  "labels": ["pass_likely"],
  "confidence": 0.82,
  "reason": "題幹與 A-D 選項完整，未見圖表或 OCR 疑點。",
  "evidence": [
    {
      "field": "options",
      "value": "A-D present"
    }
  ],
  "recommended_action": "human_can_quick_accept",
  "suggested_correction": null,
  "suggested_changes": [],
  "model": "codex-or-selected-model",
  "prompt_version": "national_exam_ai_audit_v0.1"
}
```

## Field Rules

`candidate_key` must exactly match the input.

`status` must be one of:

- `pass`
- `needs_review`
- `block`

`labels` must contain one or more of:

- `pass_likely`
- `ocr_char_suspect`
- `amino_acid_translation_suspect`
- `science_notation_suspect`
- `blood_group_symbol_suspect`
- `option_parse_suspect`
- `table_or_image_suspect`
- `group_question_suspect`
- `answer_pair_suspect`
- `parser_boundary_suspect`
- `needs_human_review`
- `block_likely`

`confidence` is a number from 0 to 1. Use lower confidence when the issue requires PDF visual confirmation.

`reason` should be one short Traditional Chinese sentence.

`evidence` should cite candidate fields, not assumptions.

`recommended_action` should be one of:

- `human_can_quick_accept`
- `human_review_text`
- `human_review_pdf_visual`
- `fix_parser_rule`
- `add_manual_asset`
- `defer_to_answer_audit`

When the only concern is the parsed answer value or answer source, use `status: "pass"` with `recommended_action: "defer_to_answer_audit"` if the question stem/options/images/group structure is otherwise safe. For example, `answer: "A|C|AC"` is a multi-answer normalization issue for the answer-audit page, not a reason to mark the question candidate `needs_review`.

`suggested_correction` is optional. Use it only when the model can propose a concrete, low-risk OCR or formatting correction that a human can apply in Review UI. It must be an object matching the Review UI correction shape:

```json
{
  "stem": "corrected stem text",
  "options": [
    {"key": "A", "text": "corrected option text"}
  ],
  "answer": "A",
  "group_ref": "optional group ref"
}
```

Only include fields that should change. Do not use `suggested_correction` for uncertain subject-matter claims, answer correctness, answer-format normalization, or changes requiring PDF visual confirmation. In those cases, leave it null and use `recommended_action`.

`suggested_changes` is optional but recommended when `suggested_correction` is present. It should be a short list of human-readable changes, for example `["選項 B: 麸胺 -> 麩胺"]`.

## Examples

```json
{"candidate_key":"moex:example:q007","status":"block","labels":["table_or_image_suspect","block_likely"],"confidence":0.9,"reason":"題幹提到下表，但 image_refs/stem_image 均為空，且表格文字不完整。","evidence":[{"field":"stem","value":"下表"},{"field":"image_refs","value":[]}],"recommended_action":"add_manual_asset","suggested_correction":null,"suggested_changes":[],"model":"codex","prompt_version":"national_exam_ai_audit_v0.1"}
```

```json
{"candidate_key":"moex:example:q012","status":"needs_review","labels":["science_notation_suspect"],"confidence":0.76,"reason":"出現 α 與數字分離，可能影響 α1 這類符號顯示。","evidence":[{"field":"stem","value":"α 1"}],"recommended_action":"human_review_text","suggested_correction":{"stem":"... α1 ..."},"suggested_changes":["題幹: α 1 -> α1"],"model":"codex","prompt_version":"national_exam_ai_audit_v0.1"}
```

```json
{"candidate_key":"moex:example:q020","status":"pass","labels":["pass_likely"],"confidence":0.84,"reason":"題號、題幹與 A-D 選項完整，未見圖表或 OCR 疑點。","evidence":[{"field":"options","value":"4 options"}],"recommended_action":"human_can_quick_accept","suggested_correction":null,"suggested_changes":[],"model":"codex","prompt_version":"national_exam_ai_audit_v0.1"}
```

```json
{"candidate_key":"moex:example:q031","status":"pass","labels":["pass_likely","answer_pair_suspect"],"confidence":0.78,"reason":"題目結構完整；答案欄 A|C|AC 留待答案核對關卡正規化。","evidence":[{"field":"answer","value":"A|C|AC"}],"recommended_action":"defer_to_answer_audit","suggested_correction":null,"suggested_changes":[],"model":"codex","prompt_version":"national_exam_ai_audit_v0.1"}
```
