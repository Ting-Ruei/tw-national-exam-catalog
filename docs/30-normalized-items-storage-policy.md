# 30 Normalized Items Storage Policy

`國考題資料夾/30_normalized_items/` is the local derived-data root for parsed,
reviewable, and pre-ingestion artifacts. It is not an official source archive.
Official PDFs stay under `10_official_pdf/`; MinerU raw outputs stay under
`20_mineru_output/`.

## Current Size Drivers

As of 2026-06-24, `30_normalized_items/` is about 17 GB.

- `question_candidates/`: about 17 GB.
- `question_candidates/20260620-213413/`: about 13 GB.
- `question_candidates/20260620-213413/_repair_backups/`: about 12 GB.
- The active `question_candidates__20260620-213413.jsonl` is about 699 MB.
- Each full repair backup has historically copied the full candidate JSONL plus
  issue CSV, so one backup is roughly 700-790 MB.

The large size is therefore mostly historical full snapshots, not active review
state.

Cleanup update on 2026-06-25:

- Removed 5 `.DS_Store` files.
- Removed one imported AI task chunk folder:
  `question_candidates/codex_audit_tasks/20260624-092445/chunks` (255 MB).
- Compressed all directories under
  `question_candidates/20260620-213413/_repair_backups/` to `.tar.zst`
  archives, verifying each archive before removing the original directory.
- `30_normalized_items/` shrank from about 17 GB to about 4.8 GB.
- `_repair_backups/` shrank from about 12 GB to about 468 MB.
- The cleanup manifest is stored at
  `國考題資料夾/30_normalized_items/cleanup_logs/cleanup_manifest__20260625-094904.txt`.

## Directory Roles

### `question_candidates/<run>/`

This is a generated candidate run from MinerU outputs and parser rules.

Important files:

- `question_candidates__<run>.jsonl`
  - Main candidate records used by Review UI.
  - Contains parsed stems, options, answer payload hints, source metadata, image
    refs, and quality status.
  - Large file. Do not commit.
- `question_parse_issues__<run>.csv`
  - Parser/system issue flags for candidates.
  - Recomputable from candidates and parser issue logic.
  - Large file. Do not commit.
- `question_candidate_summary__<run>.json`
  - Summary of the parser run.
  - Useful for audit, but not needed for live Review UI.
- `question_review_events.jsonl`
  - Human review event log.
  - Append-only. Do not rewrite unless running an explicit repair script.
  - This is much smaller than candidate JSONL and is high-value.
- `answer_review_events.jsonl`
  - Answer review event log.
  - Append-only.
- `question_ai_review_events.jsonl`
  - Advisory AI audit log.
  - Append-only. Never auto-accept or auto-block from this alone.
- `review_ui_preferences.json`
  - Local UI preferences and filters.
  - Small and disposable if necessary, but useful for continuity.

### `question_candidates/<run>/_repair_backups/`

Historical snapshots created before repair scripts changed the candidate JSONL or
issue CSV.

These are useful for emergency rollback, but they are not part of the live review
path. Most size growth comes from full JSONL snapshots here.

Recommended policy:

- Keep the latest 1-2 full backups locally during active repair work.
- Compress older backups with `zstd` or `gzip`, or move them to an external
  archive location.
- Prefer future repair manifests over full-file backup snapshots.
- Never delete backups during active review unless the user explicitly approves a
  concrete cleanup plan.

### `manual_assets/`

Manual assets added during human review, such as hand-cropped table screenshots.

Rules:

- Do not modify MinerU raw output to add manual screenshots.
- Store manual assets under `manual_assets/<candidate_key>/`.
- Include a `manifest.json` describing source PDF, page, crop context, and why
  the asset was added.
- Attach the asset through review event correction fields such as `stem_image` or
  `image_refs`.

### AI Audit Task Folders

Examples:

- `codex_audit_tasks/`
- `subject_codex_audit_tasks/`
- `chatgpt_mcp_audit_tasks/`
- `ai_audit_runs/`

These are temporary task/result exchange folders for advisory AI review. They are
not the source of truth. The durable output is the append-only
`question_ai_review_events.jsonl`.

Recommended policy:

- Keep current in-flight task runs.
- Archive or delete completed task chunks after results have been imported and
  verified.
- Do not delete `question_ai_review_events.jsonl` when cleaning task folders.

## Live Review Data Contract

Review UI should treat files this way:

1. Load `question_candidates__*.jsonl` and `question_parse_issues__*.csv` at
   startup.
2. Auto-refresh only small append-only event logs during review:
   `question_review_events.jsonl`, `answer_review_events.jsonl`, and
   `question_ai_review_events.jsonl`.
3. Do not auto-reload the large candidate JSONL on every request by default.
   Candidate reload can briefly double memory usage and may kill the Docker
   container.
4. If repair scripts change candidate JSONL or issue CSV while Review UI is
   running, Review UI should show a stale-data hint and require an explicit
   reload or service restart.

## Future Repair Strategy

Full-file backups are safe but expensive. Future repair scripts should create a
small repair manifest:

- `repair_id`
- script name and version
- timestamp
- changed `candidate_key`
- field-level before/after values
- added candidate keys
- removed candidate keys
- related human review notes
- whether human re-review is required

For high-risk repairs, one full backup can still be kept. For ordinary repairs,
the manifest should be enough to inspect and reverse the change.

## SQL Review Layer vs JSONL Review Layer

The project now has enough candidates and review events that SQL should be the
primary working layer for review and correction. JSONL should remain the durable
exchange/export layer and emergency fallback.

Recommended split:

- Use SQL for day-to-day review UI, filters, answer review, group review, bulk
  status queries, and manual correction workflows.
- Keep append-only JSONL review logs as an audit trail until the SQL event model
  has been exercised for long enough.
- Export SQL snapshots or append-only events back to JSONL before major parser
  rewrites, public sharing, or cross-machine sync.
- Do not edit historical JSONL logs directly unless running an explicit repair
  script with a manifest.

Benefits of SQL for this project:

- Fast filtering across category, subject, year, exam ordinal, status, human
  review, and AI advisory labels once the right indexes exist.
- Safer concurrent review from multiple browsers or devices.
- Easier "current state" queries without repeatedly replaying large event logs.
- Better support for normalized structures such as questions, answer rows,
  group stems, image assets, parse issues, review events, and final usable-bank
  status.
- More reliable partial updates: a single corrected question can be updated
  without rewriting a large candidate JSONL.

Costs of SQL:

- Requires schema migrations and backup discipline.
- Requires indexes and query plans to avoid slow Review UI pages.
- Harder to inspect with a plain text editor.
- Needs import/export scripts so the local data root remains portable.

Benefits of JSONL:

- Simple, portable, easy to diff in small samples, and easy to regenerate.
- Good as append-only evidence for human review and AI advisory events.
- Good for cross-machine transfer when SQL is not available.

Costs of JSONL at current scale:

- Large candidate files are expensive to reload and rewrite.
- Filtering depends on application code scanning or cached indexes.
- Corrections are awkward because safe updates require full-file backups or
  repair manifests.
- UI state can become stale unless the server reload logic is very careful.

Practical decision:

- Continue using SQL as the active review staging layer.
- Keep JSONL as append-only source/export logs during this transition.
- After the SQL layer is stable, stop creating full candidate JSONL backups for
  ordinary parser fixes; create small repair manifests instead.
- For large parser changes, run a preflight that reports exactly which
  candidate keys changed and only resets human review for those keys.

## Cleanup Candidates

Safe to consider after explicit approval:

- Compress old `_repair_backups/*`.
- Move old `_repair_backups/*` to an external archive.
- Remove imported AI task chunks.
- Remove superseded early candidate runs if the active run and review logs are
  verified.

Not safe to delete casually:

- Active `question_candidates__20260620-213413.jsonl`.
- Active `question_parse_issues__20260620-213413.csv`.
- `question_review_events.jsonl`.
- `answer_review_events.jsonl`.
- `question_ai_review_events.jsonl`.
- `manual_assets/`.
