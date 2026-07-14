# Field Lessons From Human Review

These are observed failure modes, not hypothetical style preferences.

## Parser Lessons

### Exam header swallowed the whole paper

Symptom:

- candidate question number equals the ROC year;
- stem contains exam title, category, subject, time, seat number, and instructions;
- hundreds of repeated A-D options appear.

Root cause:

- MinerU emitted a modern-year paper with bare-number questions such as `1 題幹`;
- the strict modern parser only accepted `1.` and treated the year/header as one question.

Repair:

- reject the year/header candidate;
- only when header swallowing or no modern starts is proven, retry legacy bare-number parsing;
- require at least two option markers per recovered question;
- verify first/last numbers, duplicates, option counts, and expected document count;
- never delete the header candidate before confirming recovered questions exist.

### Absolute path chosen inside Docker

Historical pair indexes contain both `/Users/...` and project-relative paths. Always prefer the relative catalog path. Absolute host paths are fallback only.

### Mixed historical punctuation

Do not apply one question-number punctuation rule to every year. Some post-106 MinerU outputs still resemble the earlier `1 題幹` format.

### Pharmacy scientific typography and Traditional Chinese

The pharmacy family (`藥師`, `藥師(一)`, `藥師(二)`) repeatedly exposed
MinerU text artifacts that are safe to repair without medical inference:
`\\%`, numeric LaTex spacing, `^{®}`, ordinal superscripts, and the simplified
characters `须`, `剂`, `镁`, and `匀`. Normalize them at parser time and render
registered marks as semantic superscript markup. For existing blocked or
needs-review candidates, append a same-state repair event with the original
human note; do not silently convert them to accepted.

## AI Event Lessons

### Question AI and visual AI were mixed

The same event table historically contains question-format and visual semantic audits. Select the latest event per stage, not the latest event overall. Detect legacy visual events from `audit_json.visual_status`, `stage=image`, visual prompt version, or visual model name.

### Newer human decision wins

An older AI warning must become historical after a newer human terminal action. Preserve it for audit history but remove it from active filters, list badges, and the main panel.

### Newer AI may still be stale

Timestamp order alone is insufficient if AI audited raw parser text after a human correction. Work packets must merge the effective correction before model review. If the cited source character no longer exists, the finding is stale.

### Cosmetic findings are not human work

Mixed full-/half-width parentheses around an English term can be normalized by parser rules. Do not send an already accepted, otherwise correct question back to humans solely for this cosmetic issue.

Meaning-bearing OCR replacements such as `麸 -> 麩` or `氢 -> 氫` require a corrected candidate and quick human recheck.

## Parse Issue Lessons

`question_parse_issues` is append/history data. Active UI queries must use `resolved_at IS NULL`. Once a visible correction resolves an issue, set `resolved_at`; do not delete the historical issue row.

Answer-only issues never affect question-stage status.

## Review UI Lessons

### Focus item outside a filter

Keeping the current item during a mode/filter change is useful, but it must be labeled `目前題（篩選外）`. Count zero with `??`, not JavaScript `||`, or zero will incorrectly become one.

### Duplicate visual display

The image belongs in the question preview. The lower panel should manage paste/upload and placement, not render the same asset a second time.

### Prompt overload

- Hide AI pass panels and badges.
- Show only active, actionable AI findings.
- Keep the question-list status order fixed as system, AI, then human. Use explicit labels such as `系統提醒`, `AI 有疑點`, and `人工待看`; do not repeat the ambiguous label `保留疑問` for multiple owners.
- Keep long AI explanations in the detail pane. The left list is for triage and should normally show no more than three decision signals.
- Do not prefill the same historical note into review, correction, and image-upload fields.
- Collapse correction, symbol, and source tools when they are not the primary task.
- In group tables, hide AI pass and already-resolved visual badges.
- In image mode, present the human outcome (`有圖正確` / `圖片錯要改` / `沒有圖`) rather than making the reviewer endorse Python or AI provenance labels.

### Empty answer filter was slow

Do not run the same aggregate CTE a second time merely to obtain counts when no sheet matches. Return a count sentinel and selected rows from one SQL query.

### Formal row existence is not usability

Formal tables retain withdrawn question rows for lineage. Treat a question as formally usable only when the row is `accepted` and has a formal answer. Keep `review ready`, `pending formal sync`, and `formal usable` as separate states in SQL, UI, and package export.

## Data Repair Safety

Before a deterministic bulk repair:

1. Dry-run and write before/after artifacts under `tmp/`.
2. Count existing recovered keys and reviewed keys.
3. Abort if reviewed content would change unless an explicit per-question reset strategy exists.
4. Apply in one transaction.
5. Verify the old defect count is zero and sample a recovered question through Review UI.
