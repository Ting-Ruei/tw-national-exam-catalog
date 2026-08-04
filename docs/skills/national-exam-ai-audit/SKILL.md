---
name: national-exam-ai-audit
description: Audit and repair Taiwan national-exam OCR candidates from source-aligned parsing through review-ready data with deterministic checks, low-token model lanes, sparse advisory output, safe patch materialization, rule proposals, regression gates, and SQL-first human review. Use when Codex or another agent must inventory or pre-review question text, semantic transcription, parser boundaries, images, tables, groups, answers, stale AI findings, model profiles, rule registries, or Review UI behavior without changing human decisions.
---

# National Exam AI Audit

Build faithful, review-ready candidates while spending model tokens only on residual ambiguity.

## Non-negotiable boundaries

- Treat Mac Studio PostgreSQL review staging as current review truth and official PDF as source truth.
- Preserve raw PDF, MinerU, candidate, human corrections, and append-only events as separate lineage layers.
- Compute effective content independently from review state: `reset_review`
  reopens only the question decision and never invalidates a prior human text,
  unlink, or manual-asset correction.
- Never let model output insert human `accept`, `block`, `exclude`, or `reset_review`.
  An explicitly user-approved, evidence-verified catch proposal may use the
  separate `scripts/apply_ai_reset_proposal.py` repair gate to append
  `reset_review`; the proposal itself is never approval.
- Never auto-materialize a model replacement unless an active deterministic rule and exact unique match authorize it.
- Let a newer human decision suppress older AI work. Preserve history without reactivating stale prompts.
- Assign each issue to exactly one owner stage: parser, question text, image, group, answer, or UI.

## Orchestration

1. Inventory the requested SQL scope before judging.
2. Run deterministic source, numbering, option, asset, and active OCR rules first.
3. Select exactly one residual lane:
   - `ocr_text`: visible OCR, simplified characters, and notation;
   - `semantic_transcription`: obvious transcription-caused meaning damage, never answer solving;
   - `group`: candidate range, shared stem, and continuation routing;
   - `visual`: image/table dependency routing and vision-capable inspection.
4. Read only the matching prompt and rule files. Do not load every subject or model profile.
5. For a pilot, freeze a stratified scope with `scripts/build_shadow_pilot_scope.py`, then compile
   inferred group controls with `scripts/build_group_shadow_controls.py`.
6. For a scaled Review UI re-audit, freeze non-truncated API shards with
   `scripts/fetch_review_ui_backlog.py`, prepare answer-free compact tasks, and build an
   exactly-one-route coverage ledger with `scripts/build_residual_audit_scope.py`. Treat
   `terminal_prior_codex54_pass` as carry-forward evidence, never as an independent LUNA review
   or deterministic proof.
7. If the requested completion definition requires independent LUNA review of prior AI passes,
   promote that frozen terminal set with `scripts/build_prior_pass_recheck_scope.py`. Require an
   exact key and source-fingerprint join; do not count the rows as complete until their sparse
   results validate.
8. Compile only model-lane residuals with `scripts/compile_agent_packets.py`, then create
   resumable 20-batch work units with `scripts/plan_agent_segments.py`. Do not overwrite a
   compiled run or treat partial result files as complete segments.
9. Use the selected model profile and adapter. Models emit sparse batch envelopes; normal
   questions do not emit per-question pass objects.
10. Validate each segment against its immutable packets, assemble every lane in manifest order,
   and require `scripts/validate_sparse_results.py` to report zero missing batches and zero
   integrity errors.
   If a provider quota pauses a scaled run, freeze only whole segments with matching successful
   validation reports using `scripts/build_validated_partial_run.py`. Exclude any segment with
   cross-run exposure or unresolved double-review disagreement; never count a read-but-unwritten
   segment as complete.
   For a multi-lane completion claim, prove the frozen scope, completed independent runs, prior
   carry-forward run, and remaining parser queue form a disjoint union with
   `scripts/build_coverage_proof.py`.
11. Materialize only active-rule exact replacements with `scripts/materialize_sparse_corrections.py`.
12. Import advisory results only after validation and a current human-unreviewed gate. Human
   state changes remain in Review UI or an explicitly approved repair workflow.
   When sparse findings include a safe, complete field patch, expand them into
   `suggested_correction` with `scripts/build_review_ui_advisory_results.py`. Review UI may show
   a human-clicked one-step correction, but the saved event remains `needs_review`; structural,
   source-original, PDF-dependent, and ambiguous findings keep the manual/PDF route.
13. Cluster recurring model-only corrections as `observed` rule candidates. Label their evidence
   `model_observation`; they cannot become `proposed`, materialized, or active until independent
   source or human evidence is attached.
14. After reviewed batches, propose reusable rules and validate them with
   `scripts/validate_rule_proposals.py`. Promote them through
   `observed → proposed → shadow → verified → active`; never skip regression and source evidence.
   Export operator feedback with `scripts/export_ai_feedback.py --rating down
   --latest-only`; export explicitly selected training examples with
   `scripts/export_ai_learning.py --latest-only`. Cluster by model/prompt/rule
   from the embedded AI context, but keep both streams as evidence rather than
   automatic rule activation or automatic model training.
15. For a retrospective catch-up of already accepted questions, build the SQL
   worklist with `--policy accepted_reaudit`. Run the current sparse LUNA lanes
   over that frozen accepted scope, then join results with
   `scripts/build_accepted_reaudit_proposal.py`. Only findings whose observed
   text/structure is still present and whose confidence clears the proposal
   gate may be included as `requires_reset`; a user approval reference is still
   required before `scripts/apply_ai_reset_proposal.py` appends `reset_review`.
   The old human event, notes, correction, model evidence, and proposal hash
   remain in the append-only event.

## Read on demand

- Read `references/automation-strategy.md` for the end-to-end agents, repair loop, gates, and token budget.
- Read `references/issue-taxonomy.md` for stage ownership and allowed issue families.
- Read `references/core-rules.md` for question text and source fidelity.
- Read `references/subject-overrides.md` only for the selected subject.
- Read `references/field-lessons.md` before changing parser, UI, or repair behavior.
- Read `references/database-ui-runbook.md` for production SQL or Review UI inspection.
- Read `references/review-layer-architecture.md` before changing reset behavior,
  candidate projection, parser rebuilds, manual assets, formal gates, or Agent
  return paths. It defines the eight logical layers and their precedence.
- Read `references/ui-ownership.md` before changing Review UI routing, labels, or
  audit fields; it is the interface-level ownership contract.
- Read `references/pdf-second-source.md` when an official PDF must be used as the
  second reference source. Run `scripts/build_pdf_reference_source.py` into a
  non-Git `tmp/`/registry artifact directory; it is read-only and never replaces
  the MinerU candidate or writes review events.
- Read `references/accepted-reaudit-lessons-2026-08-03.md` when auditing questions
  that already have a human terminal action; it records the evidence gate and
  the failure modes found in the first retrospective pass.
- Read `references/ai-max-395-deployment.md` when moving inference to the always-on AI MAX worker.
- Read `contracts/` when emitting or validating v4 tasks, sparse results, or rule proposals.
- Read only the chosen lane file in `prompts/` and the chosen model file in `profiles/`.
- Query `rules/` by lane and candidate signals; do not paste whole registries into a model prompt.
- For accepted-question catch-up, load `rules/accepted-reaudit.json` in addition
  to the selected text/group/visual lane rule. It is a policy registry, not a
  replacement dictionary.
- For any Latin genus/species or bilingual semantic finding, also load
  `rules/scientific-names.json` and `rules/semantic-anchors.json`. A valid-looking
  scientific name is source/PDF-owned unless an active exact rule proves the
  replacement; every finding must carry an observable reason in `note`.

## Agent responsibilities

- Deterministic agents detect, repair, materialize, validate, and measure.
- Text models audit already-extracted MinerU text for residual character or
  semantic-transcription issues; they do not run OCR. The internal compatibility
  key `ocr_text` means `post_mineru_text_audit`.
- Group models only route and propose ranges; they do not approve groups.
- `承上題`/`呈上題`/`上題`/`前述` are group-owned continuation markers. They are
  projected to `group_ref`/`group` and must not appear as question-text findings
  or one-click patches in the question UI.
- Vision models inspect pixels; text-only models only route likely visual dependencies.
- Retrospective agents propose rules from accepted/rejected evidence; promotion gates activate them.
- No agent both proposes and activates its own rule.

## Completion gates

- Every input key is covered by the local ledger even when the model returns no issue.
- Sparse output passes schema, batch, stage, source-class, route, and exact-field validation.
- Deterministic patches reference active rules, match one location, preserve complete options, and pass regression.
- Source-original wording, parser boundaries, images, and groups remain non-materializable by text models.
- Token, retry, latency, new-catch yield, clean false alerts, unsafe edits, and route accuracy are recorded.
- Production-scale automation remains blocked until the versioned gold corpus meets the thresholds in `references/automation-strategy.md`.

## Existing SQL tools

Build SQL worklists with `scripts/build_sql_review_worklist.py` (use
`--policy accepted_reaudit` for the retrospective accepted-question lane),
validate legacy v3 results with `scripts/validate_review_results.py`, and dry-run
advisory imports with `scripts/import_advisory_results.py`. Use
`scripts/build_accepted_reaudit_proposal.py` and the explicit
`scripts/apply_ai_reset_proposal.py` gate for approved catch-up resets. Preserve
these paths for backward compatibility while v4 sparse agents run in shadow
mode.
