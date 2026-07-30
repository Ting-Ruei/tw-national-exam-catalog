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
  "work_lane": "human_text",
  "findings": [
    {
      "issue_family": "notation_markup",
      "location": "stem",
      "observed": "α 1",
      "suggested": "α1",
      "confidence": 0.86,
      "correction_applicable": true,
      "correction_omission_reason": null
    }
  ],
  "correction_coverage": "complete",
  "uncorrected_findings": [],
  "suggested_correction": {"stem": "...α1..."},
  "suggested_changes": ["題幹：α 1 → α<sub>1</sub>"],
  "model": "gpt-5.6-luna",
  "prompt_version": "codex_gpt56_luna_question_audit_v3"
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
- `work_lane`: one of `none`, `propose_rule`, `human_text`, `human_pdf`, `manual_visual`, `parser_repair`, `answer_review`；`pass` 必須為 `none`。
- `findings`: zero to three individually located findings. Correction-first question audit requires
  `issue_family`, `location`, `observed`, `suggested`, `confidence`, `correction_applicable`, and
  `correction_omission_reason` on every finding.
- `correction_coverage`: `complete`, `partial`, or `none`.
- `uncorrected_findings`: itemized reasons for every intentionally omitted correction.
- `suggested_correction`: null or a safe question-stage patch.
- `suggested_changes`: correction-first runs有 patch 時必填的繁體中文簡短變更清單，供 Review UI 顯示在一鍵校正按鈕旁。
- `model`, `prompt_version`.

## Consistency Rules

- `pass` requires an empty `issue_families` list and `recommended_action: "none"`.
- `needs_review` means source or human judgment is needed.
- `block` requires concrete evidence that the current stage is unsafe.
- Do not include an answer issue in question-stage output.
- Do not repeat a previous AI finding when `signals.previous_ai.superseded` is true unless current task evidence independently proves it still exists.
- Suggested correction fields are limited to `stem`, `options`, `group_ref`, and `group_sequence_no`.
- A `stem` correction is the corrected complete stem. An `options` correction is a JSON list of
  `{"key":"A","text":"corrected complete option"}` rows; an object map is invalid.
- `codex_gpt56_luna_question_audit_v3` 採 correction-first：只要是可由目前題文安全確定的
  OCR 字形、簡繁／異體字、上下標、單位或局部格式修正，就必須提供完整 patch，不得只留下
  `human_review_text` warning。
- 若 `suggested_correction` 含 `options`，必須依原順序回傳原題全部選項；即使只改 B，也要
  回傳完整 A-D（或原題實際全部選項），且未修改的選項文字原樣保留。Review UI 會以整個
  options 陣列套用，局部選項陣列會造成其他選項消失，因此 validator 必須拒絕。
- 有任何 correction 時，`suggested_changes` 必須是非空陣列；沒有 correction 時不得虛列
  suggested changes。
- Every finding with `correction_applicable: true` must have a non-empty `suggested` value and must
  be covered by `suggested_correction`.
- When two or more safe findings are reported, one patch must include all of them and
  `correction_coverage` must be `complete`.
- `partial` or `none` is only valid when each omitted finding is listed in
  `uncorrected_findings` with a concrete reason. A generic “needs human review” is not sufficient.
- A pass row requires empty `findings` and `uncorrected_findings`, `correction_coverage: "none"`,
  and `suggested_correction: null`.
- Never include human review actions in model output.
- `semantic_disfluency` is for an obvious transcription-caused grammatical or semantic break, not a disputed professional answer or a stylistic preference.

## Examples

```json
{"candidate_key":"moex:example:q114","stage":"question","status":"block","issue_families":["non_question_header"],"confidence":0.99,"reason":"題號等於年度，題幹含考試時間、類科與座號且有 320 個選項，為考卷表頭誤切。","evidence":[{"field":"signals.exam_header_false_question","value":true},{"field":"content.option_count","value":320}],"recommended_action":"fix_parser","work_lane":"parser_repair","findings":[{"issue_family":"non_question_header","location":"stem","observed":"考試時間、類科與座號","suggested":null,"confidence":0.99,"correction_applicable":false,"correction_omission_reason":"這是候選切分錯誤，應由 parser 排除，不能以文字 patch 修正。"}],"correction_coverage":"none","uncorrected_findings":[{"finding_index":1,"reason":"候選切分錯誤，需修 parser。"}],"suggested_correction":null,"suggested_changes":[],"model":"gpt-5.6-luna","prompt_version":"codex_gpt56_luna_question_audit_v3"}
```

```json
{"candidate_key":"moex:example:q020","stage":"question","status":"pass","issue_families":[],"confidence":0.9,"reason":"題幹與 A-D 選項完整，未見本關卡結構或文字異常。","evidence":[],"recommended_action":"none","work_lane":"none","findings":[],"correction_coverage":"none","uncorrected_findings":[],"suggested_correction":null,"suggested_changes":[],"model":"gpt-5.6-luna","prompt_version":"codex_gpt56_luna_question_audit_v3"}
```
