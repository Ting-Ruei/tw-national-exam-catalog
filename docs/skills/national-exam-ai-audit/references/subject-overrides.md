# Subject Override Rules

## Default Policy

Use core rules for all subjects first. Add subject-specific rules only when they reduce false positives or catch recurring parser failures for that subject.

Subject overrides should not duplicate core checks.

## 醫事檢驗師 / 生物化學與臨床生化學

Common high-risk patterns:

- Greek letters and enzyme/protein names: `α`, `β`, `γ`, `δ`, `α1`, `β2`, `γ-GT`, `γ麩胺醯`.
- Bilingual amino-acid names where English is a useful OCR anchor, such as `纈胺酸（valine）`, `麩醯胺（glutamine）`, `酪胺酸（tyrosine）`, `苯丙胺酸（phenylalanine）`.
- Units and lab values: `mg/dL`, `μg/dL`, `mmol/L`, `%`, `IU/L`, `U/L`, `pH`.
- Tables of specimen results, peer group mean, SD, lower/upper limits.
- Multi-panel figures or electrophoresis/chromatography-like images.

Extra checks:

- Flag `science_notation_suspect` when Greek letters are separated from adjacent numbers or terms in a way that changes biomedical notation.
- Flag `table_or_image_suspect` when a lab-value table is represented only by broken text or when a manual screenshot is likely needed.
- Do not flag `酶`.
- Flag `麸` as `ocr_char_suspect` because it likely should be `麩`.
- When an English amino-acid anchor is present, verify that the nearby Chinese translation matches the expected biomedical term. If the Chinese text appears OCR-damaged, use `amino_acid_translation_suspect` and provide a concrete `suggested_correction` when safe.

## 醫事檢驗師 / 微生物學與臨床微生物學

Common high-risk patterns:

- Latin genus/species names and italic-like terms.
- Parenthetical scope changes in historical subject names.
- Tables comparing bacteria, fungi, culture conditions, or tests.

Extra checks:

- Do not flag Latin words merely because they are English.
- Flag `option_parse_suspect` when organism names are split across options.
- Flag `science_notation_suspect` only for notation damage, not ordinary Latin binomials.

## 醫事檢驗師 / 臨床血液學與血庫學

Common high-risk patterns:

- Blood group antigen symbols often require superscript letters: `Fyᵃ/Fyᵇ`,
  `Jkᵃ/Jkᵇ`, `Leᵃ/Leᵇ`, `Luᵃ/Luᵇ`, `Diᵃ/Diᵇ`, `Miᵃ`, `Kpᵃ/Kpᵇ`,
  and similar antibody names such as `anti-Fyᵃ`, `Anti-Jkᵇ`.
- ABO subgroup and Bombay phenotype notation may require subscripts:
  `A₁`, `A₂`, `Oₕ`.
- Coagulation formula notation may include superscripted terms, such as
  `ISI` in an INR formula.
- Hemoglobin or globin-chain notation may use Greek letters or subscripts; do
  not flatten these into ordinary baseline text when it changes meaning.

Extra checks:

- Flag `blood_group_symbol_suspect` and `science_notation_suspect` when a blood
  group antigen letter is separated as ordinary text, such as `Fy a`, `Jk b`,
  `Le a`, `Lu a`, `Mi a`, `Di a`, or `Anti-Fy a`.
- Flag `science_notation_suspect` when `O_h`, `A 1`, `A1`, `A 2`, or `A2`
  appears in a blood-group context and the candidate does not preserve subscript
  styling.
- When the correction is a direct typography-only change, include
  `suggested_correction` and `suggested_changes`, but keep the audit advisory:
  human review must still apply and pass the item.

## Generic Image-Heavy Clinical Subjects

Use for radiology, pathology, clinical microscopy, parasitology, physiology traces, and similar subjects until a more specific override exists.

Extra checks:

- Prefer `table_or_image_suspect` when a question likely depends on a visual finding.
- If the candidate contains image references but the stem/options do not indicate which image belongs to which option, use `needs_review`.
- If image assets are duplicated in both stem and option fields, flag the duplicated placement explicitly.

## Adding New Subject Rules

When repeated human review notes reveal a subject-specific pattern:

1. Add the smallest possible override here.
2. Keep the core rule unchanged unless the pattern affects many subjects.
3. Re-audit only affected candidates.
4. Do not reset accepted questions unless candidate content actually changes.
