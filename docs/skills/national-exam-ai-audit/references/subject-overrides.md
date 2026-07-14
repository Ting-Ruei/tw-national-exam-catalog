# Subject Override Rules

## Default Policy

Use core rules for all subjects first. Add subject-specific rules only when they reduce false positives or catch recurring parser failures for that subject.

Subject overrides should not duplicate core checks.

## 藥師制度群組

This group includes `藥師`, `藥師(一)`, and `藥師(二)`. They are historical
forms of one pharmacy examination system: the older one-stage examination and
the later staged examination must be cataloged separately, but use the same
question-text audit standard.

Common high-risk patterns:

- MinerU LaTex escapes embedded in ordinary clinical values, such as `30\\%`,
  `HCO_{3}^{-}=16\\ mEq/L`, `FEV_{1}`, and `PaCO_{2}`.
- Registered drug or product names extracted as `^{®}` or `$^{®}$`, including
  `Paxlovid`, `Oncotype Dx`, and inhaler brand names.
- English ordinal staging such as `$3^{rd}$ degree AV block`.
- Simplified characters inside otherwise Traditional Chinese text, especially
  `须`, `剂`, `镁`, and `匀`.
- Pharmacokinetic and renal-function abbreviations whose printed subscripts are
  easily lost: `ClCr`, `SCr`, `FE Na`, `ER H`, `Ctrough`, `CSS`, `Vd`, and
  `HbA1C`.

Extra checks:

- Normalize mechanically safe text before it becomes review work:
  `\\% -> %`, a numeric LaTex spacing escape such as `16\\ mEq/L -> 16 mEq/L`,
  `须 -> 須`, `剂 -> 劑`, `镁 -> 鎂`, and `匀 -> 勻`.
- Preserve meaning-bearing notation as semantic text or markup: `HCO₃⁻`,
  `PaCO₂`, `FEV₁`, `3ʳᵈ`, and `<sup>®</sup>`.
- Normalize the confirmed pharmacokinetic forms to display markup such as
  `CL<sub>Cr</sub>`, `S<sub>Cr</sub>`, `FE<sub>Na</sub>`,
  `C<sub>trough</sub>`, `V<sub>d</sub>`, `K<sub>M</sub>`,
  `V<sub>max</sub>`, `k<sub>a</sub>`, `k<sub>e</sub>`, `V<sub>p</sub>`,
  and `V<sub>D</sub>`. The forms `KM`, `K_M`, `K_{M}`, `Vmax`, `V_{max}`,
  `ka`, `k_a`, `ke`, `k_e`, `Vp`, `V_p`, `VD`, and `V_D` are equivalent OCR
  inputs for this narrow pharmacokinetic rule.
- Also normalize the confirmed complete tokens `Vss`, `V ss`, `V_{ss}`,
  `Vexp`, `V exp`, and `V_{exp}` to `V<sub>ss</sub>` and
  `V<sub>exp</sub>`. Do not infer subscripts from ordinary longer words.
- Remove a stray approximation/layout tilde before the printed unit `h⁻¹`
  only when it follows a numeric value, for example `0.16 ~h⁻¹` ->
  `0.16 h⁻¹`. The same narrow rule applies to `~hr` and `~L`. Do not remove
  a real approximation sign in ordinary prose.
- A registered mark is a display/markup concern, not an answer concern and
  not a reason to route an otherwise complete question to image review.
- When normalizing an already reviewed non-accepted candidate, append a repair
  event that keeps its current `block` or `needs_review` state and includes the
  previous human note. Never auto-accept it.
- Do not create separate parser rules solely because a paper is one-stage or
  staged. The source PDF and its own question numbering remain authoritative.

## 醫事檢驗師 / 生物化學與臨床生化學

Common high-risk patterns:

- Greek letters and enzyme/protein names: `α`, `β`, `γ`, `δ`, `α1`, `β2`, `γ-GT`, `γ麩胺醯`.
- Bilingual amino-acid names where English is a useful OCR anchor, such as `纈胺酸（valine）`, `麩醯胺（glutamine）`, `酪胺酸（tyrosine）`, `苯丙胺酸（phenylalanine）`.
- Units and lab values: `mg/dL`, `μg/dL`, `mmol/L`, `%`, `IU/L`, `U/L`, `pH`.
- Tables of specimen results, peer group mean, SD, lower/upper limits.
- Multi-panel figures or electrophoresis/chromatography-like images.

Extra checks:

- Flag `notation_markup` when Greek letters are separated from adjacent numbers or terms in a way that changes biomedical notation.
- Route broken or missing lab-value tables with `visual_dependency`; image review decides whether a manual screenshot is needed.
- Do not flag `酶`.
- Flag `麸` as `ocr_char_suspect` because it likely should be `麩`.
- When an English amino-acid anchor is present, verify that the nearby Chinese translation matches the expected biomedical term. If the Chinese text appears OCR-damaged, use `ocr_character` and provide a concrete `suggested_correction` when safe.

## 醫事檢驗師 / 微生物學與臨床微生物學

Common high-risk patterns:

- Latin genus/species names and italic-like terms.
- Parenthetical scope changes in historical subject names.
- Tables comparing bacteria, fungi, culture conditions, or tests.

Extra checks:

- Do not flag Latin words merely because they are English.
- Flag `option_structure` when organism names are split across options.
- Flag `notation_markup` only for notation damage, not ordinary Latin binomials.

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

- Flag `notation_markup` when a blood
  group antigen letter is separated as ordinary text, such as `Fy a`, `Jk b`,
  `Le a`, `Lu a`, `Mi a`, `Di a`, or `Anti-Fy a`.
- Flag `notation_markup` when `O_h`, `A 1`, `A1`, `A 2`, or `A2`
  appears in a blood-group context and the candidate does not preserve subscript
  styling.
- When the correction is a direct typography-only change, include
  `suggested_correction` and `suggested_changes`, but keep the audit advisory:
  human review must still apply and pass the item.

## Generic Image-Heavy Clinical Subjects

Use for radiology, pathology, clinical microscopy, parasitology, physiology traces, and similar subjects until a more specific override exists.

Extra checks:

- Prefer `visual_dependency` when a question likely depends on a visual finding.
- If the candidate contains image references but the stem/options do not indicate which image belongs to which option, use `needs_review`.
- If image assets are duplicated in both stem and option fields, flag the duplicated placement explicitly.

## Adding New Subject Rules

When repeated human review notes reveal a subject-specific pattern:

1. Add the smallest possible override here.
2. Keep the core rule unchanged unless the pattern affects many subjects.
3. Re-audit only affected candidates.
4. Do not reset accepted questions unless candidate content actually changes.
