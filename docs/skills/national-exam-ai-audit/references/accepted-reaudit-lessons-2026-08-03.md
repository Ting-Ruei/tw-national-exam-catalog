# Accepted Re-audit Lessons — 2026-08-03

This record captures the first evidence-gated retrospective pass over human-accepted
questions. It is a rule-learning artifact, not a human decision log.

## Scope and outcome

- Frozen accepted scope: 17,914 questions — 醫事檢驗師 15,360; 藥師 879;
  藥師(一) 925; 藥師(二) 750.
- LUNA coverage: 17,914/17,914. The model read MinerU-extracted text and task
  structure; it did not run OCR again.
- Non-pass advisory rows: 675.
- Evidence-gated reset proposal: 402. Every reset candidate still had the
  observed text or structural signal in the current SQL-first task snapshot and
  confidence at least 0.80.
- Formal review events and AI events are separate append-only layers. The prior
  human action, notes, reviewer, time, and correction were copied into each
  reset event.

## Rules strengthened by this pass

1. Accepted re-audit is a separate `accepted_reaudit` worklist policy. It must
   never be mixed with the normal unreviewed queue.
2. A model result alone cannot reset a human decision. The join requires current
   visible evidence, the expected human action, and an unchanged content hash;
   a user approval reference is required for the append operation.
3. `group_dependency`, `visual_dependency`, parser/option structure, and
   source-unverified semantic wording stay manual/PDF routes. They can explain
   a suspected issue, but must not claim a one-click patch.
4. Safe one-click corrections are field-scoped. A full option patch is reduced
   to `{key, text}` objects so parser display metadata cannot leak into the
   correction contract.
5. A correction finding is marked `correction_applicable=false` whenever the
   safety gate withholds a patch; the proposed wording remains visible as an
   advisory for the human reviewer.

## Regression gates

- Sparse and per-candidate LUNA results must cover every frozen task key.
- Advisory output must pass `validate_review_results.py` with zero errors before
  import.
- Reset apply must be dry-run first, recheck current human action/content hash,
  and report skipped keys.
- After apply, the UI must show `reset_review`/unreviewed and the advisory must
  remain active; formal sync must drain its queue.

## Follow-up failure analysis — semantic/name guard (2026-08-03)

The first accepted re-audit exposed three linked defects:

1. The sparse v4 issue contract allowed a missing `note`, and the accepted-
   re-audit flattener dropped the model's local explanation. The importer then
   fell back to the row-level string `v4 sparse 稽核發現…`, so the UI could not
   tell a reviewer why a bacterial name was flagged.
2. The one-click materializer treated `propose_rule + candidate_mismatch +
   confidence >= .95` as sufficient. That is not source evidence. It produced
   `氩 → 氬` for `氩鍵（hydrogen bonds）`, following a generic glyph mapping and
   ignoring the English semantic anchor.
3. A Latin binomial was allowed to be corrected from model memory. A valid
   scientific name and an OCR typo are indistinguishable without a local
   character difference plus source/PDF evidence.

The repair adds a required per-issue `note`, preserves it as finding
`message`/`reason`/`evidence.reason`, blocks generic argon-glyph conversion when
`hydrogen bond(s)` is present, and withholds one-click patches for Latin
scientific names unless an active exact rule or official-source mismatch is
attached. The production repair was appended as model run 22 for 393 still-open
reset-review candidates; nine human-closed keys were skipped. Prior events were
not rewritten. The hydrogen candidate was already human-corrected to `氫鍵` and
its AI event is now superseded by that human decision.
