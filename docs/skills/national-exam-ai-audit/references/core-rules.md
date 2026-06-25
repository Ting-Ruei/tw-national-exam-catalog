# Core AI Audit Rules

## Scope

Apply these rules to every Taiwan national exam question candidate regardless of category or subject.

The audit answers: "Does the parsed candidate look structurally safe enough for human review and later ingestion?" It does not answer: "Is the exam answer correct?" or "Is the medical/scientific statement true?"

Question audit and answer audit are separate gates. During question audit, do not lower a candidate's status only because the parsed `answer` or `answer_payload` is unusual, multi-valued, missing, or needs MOD/ANS confirmation. Record those as answer-audit notes only when useful, and keep the question status based on stem/options/images/group structure.

## Candidate Fields To Inspect

- `candidate_key`
- `question_number`
- `question_number_occurrence`
- `stem`
- `options`
- `answer`
- `answer_payload`
- `group_ref`
- `image_refs`
- `stem_image`
- `stem_markup`
- `metadata.raw_block`
- `metadata.year`
- `metadata.exam_ordinal`
- `metadata.normalized_category_name`
- `metadata.normalized_subject_name`
- `metadata.question_pdf_relative`
- `metadata.answer_pdf_primary_relative`
- `issue_count`
- `quality_status`

## Status Decision

Use:

- `pass` when the candidate appears structurally complete.
- `needs_review` when a human should inspect but ingestion may still be possible after quick confirmation.
- `block` only when the parsed candidate is likely unsafe for ingestion without correction.

Recommended mapping:

- `pass_likely` -> `pass`
- one or more suspect labels -> usually `needs_review`
- missing stem, missing options for multiple choice, severe boundary mix, or image/table missing from a question that depends on it -> `block`

## Universal Labels

### `ocr_char_suspect`

Flag visible OCR character problems, especially simplified/variant characters inside Traditional Chinese exam text. Known examples:

- `麸` likely should be `麩`
- `黄` likely should be `黃`
- `氢` likely should be `氫`
- `脱` likely should be `脫`
- `铵` likely should be `銨`
- `巯` likely should be `巰`
- `羟` likely should be `羥`
- `钠` / `钾` / `钙` / `镁` likely should be `鈉` / `鉀` / `鈣` / `鎂`

Do not flag `酶`; it is valid Traditional Chinese in Taiwan biomedical terminology.

### `amino_acid_translation_suspect`

When a question includes an English amino-acid name in parentheses or nearby text, use it as an anchor to check the preceding Chinese translation. This is especially useful in biochemistry, where MinerU may turn one Chinese character into a visually similar but wrong character.

Use `needs_review` when:

- the English anchor is clear, such as `valine`, `glutamine`, `phenylalanine`, or `tyrosine`;
- nearby Chinese text looks like an amino-acid translation; and
- the expected Chinese term is missing or visibly damaged.

Common anchors:

- `glycine` -> `甘胺酸`
- `alanine` -> `丙胺酸`
- `valine` -> `纈胺酸`
- `leucine` -> `白胺酸` / `亮胺酸`
- `isoleucine` -> `異白胺酸` / `異亮胺酸`
- `serine` -> `絲胺酸`
- `threonine` -> `蘇胺酸`
- `cysteine` -> `半胱胺酸`
- `methionine` -> `甲硫胺酸`
- `aspartate` / `aspartic acid` -> `天門冬胺酸`
- `glutamate` / `glutamic acid` -> `麩胺酸` / `穀胺酸` / `谷胺酸`
- `asparagine` -> `天門冬醯胺`
- `glutamine` -> `麩醯胺` / `麩胺醯胺`
- `lysine` -> `離胺酸` / `賴胺酸`
- `arginine` -> `精胺酸`
- `histidine` -> `組胺酸`
- `phenylalanine` -> `苯丙胺酸`
- `tyrosine` -> `酪胺酸`
- `tryptophan` -> `色胺酸`
- `proline` -> `脯胺酸`

If the correction is obvious, include a `suggested_correction` for the exact field and a short `suggested_changes` entry. Do not auto-accept the question after applying this suggestion; it must still pass human review.

### `science_notation_suspect`

Flag likely broken scientific notation or biomedical symbols:

- Greek letters split from numbers or words, such as `α 1` when the source likely has `α1`.
- Chemical formulas with missing subscript/superscript meaning.
- Units or ranges broken by OCR, such as `mg / dL`, `10 - 3`, or separated `%`.
- Celsius temperature markup left in LaTeX-like form, such as `65^{\circ} C`, `65^\circ C`, or `65° C`; it should display as `65℃`.
- HTML/LaTeX-like fragments that would display poorly.

Do not require perfect LaTeX at this stage. The goal is to catch display-risk candidates.

### `option_parse_suspect`

Flag option structure issues:

- multiple-choice candidate has fewer or more than 4 options unless clearly not A-D format.
- duplicated option keys.
- option text appears merged into stem.
- stem appears split into option A.
- option text is empty while raw block has visible option content.

### `parser_boundary_suspect`

Flag likely wrong question boundaries:

- stem contains another question number.
- raw block appears to include multiple independent questions.
- candidate has very short stem but long unrelated option text.
- historical format has `1 題幹` rather than `1.` and parser may have mis-split.

Year guidance:

- For `105` and earlier, tolerate legacy question numbering like `1 題幹`.
- For `106` and later, expect stricter `1.` / `1、` / `1．` style, but do not fail solely on punctuation.

### `table_or_image_suspect`

Flag visual dependency issues:

- stem says `下表`, `下圖`, `圖示`, `附圖`, `如圖`, `依下列資料`, `下列檢驗結果`, `following table`, or similar but `image_refs`, `stem_image`, and table/markup fields are empty.
- options are images but candidate stores the same image set under both stem and options.
- answer/explanation area contains images that were incorrectly attached to question stem.
- MinerU table markup exists but is unreadable or incomplete; prefer manual asset review.

If a table is essential for solving the question and only garbled text remains, use `block`.

### `group_question_suspect`

Flag likely題組 problems:

- stem begins with shared scenario language but `group_ref` is empty.
- consecutive candidates repeat a long shared paragraph.
- question refers to "上題", "前述", "下列資料", "此病人", or "此案例" without a clear group binding.

### `answer_pair_suspect`

Use this only as an advisory note that should be deferred to answer audit. It should not by itself make a question `needs_review` or `block`.

- candidate has no answer source path even though metadata suggests an answer PDF exists.
- answer value is missing, multi-valued, malformed, or outside expected format, such as `A|C|AC`.
- `MOD` / correction precedence appears inconsistent in metadata.

For pure answer-format concerns, keep `status` as `pass` if the question text, options, images, and group binding are otherwise structurally safe. Use `recommended_action: "defer_to_answer_audit"` and include evidence, but do not provide `suggested_correction` for `answer`.

Do not mark a whole question blocked or needs_review merely because an answer PDF is missing or an answer value is unusual. Full answer correctness and answer-format normalization belong to answer audit.

## Pass Criteria

Use `pass` when all are true:

- question number is present.
- stem is readable and non-empty.
- A-D options are present for multiple-choice questions.
- no obvious image/table dependency is missing.
- no obvious OCR or notation damage that may change meaning.
- candidate does not appear to contain multiple questions.

## Reason Style

Write one concise reason in Traditional Chinese. Mention field evidence, for example:

- `題幹提到「下表」，但 image_refs/stem_image 均為空。`
- `選項只有 3 個，raw_block 看起來仍有 D 選項。`
- `出現「麸」，疑似 OCR 將「麩」轉成簡體。`
