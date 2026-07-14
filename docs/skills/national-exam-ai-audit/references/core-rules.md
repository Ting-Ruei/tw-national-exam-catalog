# Core Question Audit Rules

## Scope

Question audit asks whether the parsed question is a faithful, usable representation of the source. It does not decide medical correctness, answer correctness, image crop quality, or the final group range.

## Required Fields

Inspect:

- `candidate_key`, question number, occurrence;
- stem and A-D options;
- raw block excerpt and option count;
- previous/current/next question context;
- parser issues;
- image/group routing signals;
- latest human state and whether prior AI is superseded;
- source paths when PDF comparison is needed.

## Decision Order

### 1. Non-question header

Use `non_question_header` when a candidate is an exam cover/header rather than a question. Strong evidence includes:

- question number equals the ROC exam year;
- stem contains several of `考試時間`, `類科名稱`, `科目名稱`, `座號`, `本試題`, `代號`;
- the candidate contains more than eight option markers because it swallowed the paper.

This is a root-cause finding. Do not also emit `option_structure` for the same header candidate.

### 2. Boundaries

Use `boundary_merge` when one candidate contains two or more independent questions or repeated A-D sets. Check the next question before deciding.

Use `boundary_missing` when a gap or duplicate indicates that another question was swallowed. Never fix only the visible duplicate options; recover the missing neighboring question from PDF/Markdown.

Historical numbering:

- ROC 105 and earlier may use `1 題幹` without a dot.
- ROC 106 and later usually use `1.` / `1、` / `1．`.

Do not require one punctuation style across all years.

### 3. Stem and options

Use `empty_stem` for an empty or unusable stem.

Use `option_structure` for duplicated keys, missing A-D option text, merged stem/option text, or abnormal option count in an ordinary multiple-choice question. Four A-D options are expected for this corpus unless source evidence proves otherwise.

### 4. OCR characters

Use `ocr_character` only for visible OCR damage. Known examples:

- `麸` -> `麩`
- simplified biomedical characters such as `氢`, `钠`, `钾`, `钙`, `镁` in Traditional Chinese text;
- bilingual amino-acid anchor mismatch, such as a damaged Chinese term next to `valine`, `glutamine`, or `tyrosine`.

`酶` is valid Traditional Chinese and must not be flagged.

### 5. Scientific notation and markup

Use `notation_markup` for meaning-bearing format damage:

- Greek letter split from index/term (`α 1`, `γ 麩胺醯`);
- missing or broken superscript/subscript;
- blood-group antigen notation flattened into ordinary text;
- raw LaTeX/HTML that the shared renderer cannot display;
- Celsius lost or left as broken markup. Canonical display should preserve `°C`/`℃` meaning.

Do not flag harmless spacing or ordinary Latin names.

Percentage escape safeguard:

- In candidate text, `\%`, `\\%`, and `\ %` are LaTeX/Markdown display escapes for `%`, not distinct content. Normalize all of them to `%`.
- Rebuild derived `stem_markup`/option markup after this normalization; do not edit `raw_candidate_json`, which remains source evidence.
- Celsius escapes such as `\\circC`, `\\circ C`, and `^{\\circ}C` normalize to `℃`; standalone angle markers such as `90^\\circ`, `90^{\\circ}`, and `165^{\\circ} F` normalize only to `90°`, `90°`, and `165° F` respectively.

Token-boundary safeguard:

- Never infer a missing digit, subscript, or formula from a substring inside an English word. `hypo`, `hypothyroidism`, and a standalone `PO` remain unchanged.
- Normalize `PO2` to `PO₂` only when the source already contains that independent token, or an explicit equivalent such as `PO₂` / `P_{O_2}`. Do not turn `hypo` into `PO₂`.
- If the proposed correction changes an English word into a scientific token, label it `needs_review` with the original/source evidence instead of applying it.

### 6. Route visual/group work

Use `visual_dependency` only to route a question whose text clearly depends on a figure/table but whose visual state still needs image review. Image review owns missing/wrong crop/placement.

Use `group_dependency` only to route likely shared context. Group review owns the final range, order, type, and shared stem.

Phrases such as `下列資料`, `以下資料`, or `依據下列資料` alone are not enough to declare a group. Stronger evidence includes an explicit range/count, `承上題`/`呈上題`, shared stem, or neighbor dependence.

## Answer Boundary

Missing, multi-valued, ANS/MOD, or malformed answers do not lower question-stage status. Route them to answer review without adding a question issue.

## Pass Criteria

Return `pass` when this stage has a readable stem, expected option structure, no visible OCR/notation damage, no boundary defect, and no unresolved route dependency.

Keep reason and evidence short. Cite observable fields, not medical assumptions.
