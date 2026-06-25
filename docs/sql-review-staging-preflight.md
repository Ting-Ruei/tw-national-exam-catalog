# SQL Review Staging Preflight

This note records the migration direction from JSONL-heavy review toward SQL review staging, and the checks that should run before a full candidate import.

## Direction

The long-term review pipeline is:

```text
題目審核層
  ↓
題組審核層
  ↓
答案審核層
  ↓
可使用狀態
```

`question_candidates` and `question_parse_issues` should move into PostgreSQL review staging for browsing, filtering, and cross-device review. Append-only JSONL logs remain the source of recovery while the workflow is still evolving, but the operating surface should become SQL-first.

The existing importer already supports full candidate import when `--category` is omitted:

```bash
bash scripts/postgres_apply_schema.sh
python3 scripts/ingest_question_candidates_to_postgres.py
python3 scripts/ingest_review_events_to_postgres.py
docker compose up -d review-ui
```

For safer rollout, keep using `--category ...` while parser rules are still changing heavily. Full import should happen after the preflight scans below are stable.

## Current Medtech Findings

As of the SQL review staging check on 2026-06-24, the active unresolved medtech review states were concentrated in:

- `臨床血液學與血庫學`: 61 `block`, 1 `needs_review`
- `臨床生理學與病理學`: 28 `block`, 12 `needs_review`
- `生物化學與臨床生化學`: 13 `block`, 20 `needs_review`
- `微生物學與臨床微生物學` historical/current names combined: 62 `needs_review`

Common parser issue codes among unresolved medtech items:

- `too_few_options`
- `empty_option`
- `duplicate_option_label`
- `option_order_unusual`
- `markup_needs_review`
- `amino_acid_translation_suspect`
- `duplicate_question_number`
- `empty_stem`

Many unresolved records are already repaired but intentionally kept as `block` or `needs_review` until human review passes them. Do not treat old notes as current defects when the latest event is `accept`, `unblock`, or a later manual correction.

## Universal Pre-SQL Scans

Run these checks on all categories before broad SQL staging import or before promotion to formal tables:

- Option structure: detect too few options, empty options, duplicate option labels, and merged same-line A-D options.
- Question boundary: detect duplicate question numbers, empty stems, extra PDF headers parsed as questions, and old-format numbering such as `1 題幹`.
- Scientific notation: normalize or flag Greek letters, subscript/superscript, chemical formulas, ion charges, Celsius, percent, and units.
- Table/image dependency: flag questions that mention tables, figures, traces, stains, charts, or lab values but have no usable asset binding.
- Asset placement: flag duplicated image assets attached both to stem and options, or answer-side figures incorrectly attached to question fields.
- Group candidates: flag shared scenarios, repeated long paragraphs, references such as `上題` / `此病人` / `下列資料`, and candidates likely requiring `group_ref`.
- Answer-only problems: missing, multi-valued, or `MOD`/`ANS` answer questions should be deferred to the answer review layer unless they expose a question-structure problem.

## Subject Overrides

Subject-specific rules should stay small and only catch repeated patterns:

- Biochemistry: amino-acid Chinese/English anchor checks, enzyme/protein Greek notation, lab-value tables, formula images.
- Microbiology: same-line option splitting, Latin names, organism names split across options, `E. coli`-like abbreviations, temperature and gas notation.
- Hematology/blood bank: blood group superscripts/subscripts such as `Fyᵃ`, `Jkᵇ`, `Leᵃ`, `A₁`, `A₂`, `Oₕ`, and antibody names.
- Image-heavy clinical subjects: figure-to-option binding, incomplete visual assets, and duplicated image placement.

Canonical AI audit wording lives in:

- `docs/skills/national-exam-ai-audit/references/core-rules.md`
- `docs/skills/national-exam-ai-audit/references/subject-overrides.md`

When a new repeated pattern appears, update those files first, then re-audit only affected candidates.

## SQL Optimization Plan

The first SQL migration step can still use append-only review events. For speed, the next optimization should add a derived review state layer:

- latest human question review per candidate
- latest AI advisory per candidate
- latest answer review per candidate
- effective question gate status
- effective answer gate status
- final usable status

This can start as SQL views. If Review UI filtering remains slow after full import, promote the derived state to a refreshable cache table updated after review-event writes and bulk event ingestion.

## AI Suggestion UX

`套用 AI 建議校正` must not jump to the next question. It should:

- write the suggested correction as a review correction;
- preserve the current human gate as `needs_review`, `block`, or `exclude`;
- keep the reviewer on the same candidate for visual confirmation;
- require a separate human `通過` action before the candidate can enter the answer review layer.
