# Post-MinerU exact-text audit lane

The input is already-extracted MinerU candidate text. Do not run OCR, transcribe a PDF,
or request page images. Inspect only the supplied stem/options for residual character,
simplified/traditional, boundary, and meaning-bearing notation defects.

Priorities:

1. Active exact rules already supplied in the packet.
2. Visible simplified or damaged characters.
3. Broken subscript, superscript, Greek letter, unit, or canonical markup.
4. Obvious option-boundary damage; route it to parser instead of rewriting options.

Do not flag valid canonical `<sub>/<sup>`, harmless spacing, official wording variants, or a
professional term merely because another spelling is more common. A new replacement without an active rule
must route to `propose_rule` or `human_pdf`.

For Latin genus/species text, preserve the supplied name unless the packet shows an
exact local character/transposition defect and the `note` names that defect. Do not
turn a remembered scientific spelling into a correction. A one-click patch is only
allowed for an active exact rule; otherwise use `human_pdf` with the original and
candidate text in the evidence.

Do not flag `承上題`/`呈上題`/`上題`/`前述` as wording defects. They are group-continuation
markers; emit them only on the `group` lane as a `group_ref` dependency with no replacement.

Preserve valid abbreviated binomials such as `B. cereus`, `C. difficile`, `S. aureus`,
and `P. aeruginosa`. A capital letter followed by a period and a lowercase epithet is
not a punctuation error and must not be expanded from model memory.
