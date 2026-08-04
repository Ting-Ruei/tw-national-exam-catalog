# Semantic transcription lane

Find only transcription-caused meaning damage.

Use bilingual anchors, repeated terminology, grammar breaks, or source alignment. Do not judge whether an
answer choice is scientifically true. If the official source itself contains the wording, label it
`source_original` or `source_original_suspected_typo` and do not materialize it.

An English anchor has priority over a generic character conversion. For example,
`hydrogen bond(s)` conflicts with `氩鍵`/`氬鍵`: 氬 is argon, while the semantic
term is `氫鍵`. Report the conflict explicitly in `note`, propose `氫`/`氫鍵`
only as a source-checked advisory, and never silently convert `氩` to `氬`.

Do not flag a bacterial or other Latin scientific name merely because it is
unfamiliar or differs from model memory. A name finding must state the exact
observed character difference and its local evidence; absent an official PDF or
active exact rule, route it to `human_pdf` and set `after` to null when no safe
replacement is established.

Safe output is usually a short `human_pdf` or `propose_rule` issue. Use parser route for merged/missing
questions or option sets.

`承上題`、`呈上題`、`上題`、`前述` belong to the group layer. Do not correct or rewrite
these markers in a stem; route a `group_dependency` with `field=group_ref` to `group`.

Valid abbreviated scientific names (`B. cereus`, `C. difficile`, `S. aureus`, `P. aeruginosa`)
must be preserved. Do not expand a genus initial or call the period an OCR error without
an exact official-source mismatch.
