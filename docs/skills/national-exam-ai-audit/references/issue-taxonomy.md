# Review Issue Taxonomy

## One Problem, One Owner

Every finding has exactly one owner stage. Do not display the same warning in multiple panels.

| Stage | Owns | Does not own |
| --- | --- | --- |
| question | non-question headers, boundaries, stem/options, OCR characters, notation markup | answer correctness, image crop quality, final group range |
| image | missing asset, wrong crop, wrong placement, confirmed no-image | text OCR unless the crop omitted visible text |
| group | missing group, wrong range/order, shared stem, confirmed non-group | per-question text and answers |
| answer | ANS/MOD source, answer mapping, multi-answer representation | question parsing and medical explanation |
| UI | stale/duplicate prompts, wrong mode, navigation reset, latency, persistence failure | exam-content correctness |

## Allowed Issue Families

Question:

- `non_question_header`
- `boundary_merge`
- `boundary_missing`
- `empty_stem`
- `option_structure`
- `ocr_character`
- `notation_markup`
- `visual_dependency` (route to image; do not decide crop correctness)
- `group_dependency` (route to group; do not decide final range)

Image:

- `visual_missing`
- `visual_wrong_crop`
- `visual_wrong_placement`
- `visual_not_required`

Group:

- `group_missing`
- `group_range_wrong`
- `shared_stem_missing`
- `not_group`

Answer:

- `answer_missing`
- `answer_source_mismatch`
- `mod_precedence`
- `multi_answer_representation`

UI:

- `stale_ai_prompt`
- `duplicate_prompt`
- `wrong_stage_prompt`
- `navigation_reset`
- `action_latency`
- `action_no_persist`
- `source_view_mismatch`

## Deduplication Rules

1. Keep at most three issue families per candidate and stage.
2. Prefer the root cause. For a whole paper merged under a year header, report `non_question_header`, not hundreds of option errors.
3. Answer issues are invisible in question status and are shown only in answer review.
4. Once image review says `visual_asset_ok` or `no_visual_required`, older visual AI labels are historical.
5. Once a terminal human review is newer than AI, the old AI finding is historical.
6. Human notes are evidence for repair, not a new automatic label. A later human accept means the old note has been handled.

## Severity

- `pass`: no material issue in this stage.
- `needs_review`: source comparison or human judgment is still needed.
- `block`: the current candidate cannot be safely used, such as a non-question header, merged questions, missing essential stem/options, or missing required visual.

Do not use `block` for uncertainty alone.
