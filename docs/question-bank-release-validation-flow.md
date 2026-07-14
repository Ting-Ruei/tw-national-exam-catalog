# Question Bank Release Validation Flow

This is the standard release flow for exporting a reviewed national-exam question
bank package to an external platform.

The source of truth is the formal SQL layer. Review JSONL files and Review UI
events remain audit/history inputs, but the package exporter must only read the
formal tables.

## Release Gate Order

Run these gates in order:

```bash
python3 scripts/sync_formal_group_links_from_reviews.py --category 醫事檢驗師 --dry-run
python3 scripts/sync_formal_group_links_from_reviews.py --category 醫事檢驗師

python3 scripts/validate_formal_question_bank.py --category 醫事檢驗師

python3 scripts/export_question_bank_package_from_postgres.py \
  --category 醫事檢驗師 \
  --slug medtech \
  --package-version tw-national-exam-medtech-vYYYY.MM.DD \
  --force

python3 scripts/validate_question_bank_package.py \
  國考題資料夾/40_exports/question_bank_packages/tw-national-exam-medtech-vYYYY.MM.DD
```

Do not publish or hand off a package if either validator exits non-zero.

## Formal SQL Validator

`scripts/validate_formal_question_bank.py` checks the formal database before
export.

Blocking errors include:

- accepted questions missing stem, options, or answer
- duplicate option keys left inside `question_json.options`
- latest group review events not reflected in `exam.questions.question_group_id`
- stale formal group links after `confirm_not_group` or `reset_group_review`
- non-contiguous `group_sequence_no`
- group keys attached to a different source registry
- missing `canonical_subject_name`
- invalid asset role, missing asset files, or asset checksum mismatch

Warnings include:

- missing stored formal asset SHA256
- duplicate same-question/same-path/same-role asset links in formal SQL
- missing subject mapping notes
- duplicate content hashes that should be recorded as reused/duplicate lineage

Warnings do not block export by default, but they must be read before release.
For the current medtech package, package export computes and verifies package
asset checksums even when formal `exam.assets.sha256` is empty. Formal SHA256
backfill and asset-link deduplication are still recommended cleanup tasks for
stronger source lineage.

## Package Validator

`scripts/validate_question_bank_package.py` checks the versioned delivery
package after export.

Blocking errors include:

- missing required package files
- manifest counts that do not match package contents
- exporter warnings present in `manifest.counts.warnings`
- duplicate `source_question_key`
- required question fields missing
- duplicate option keys
- unsupported `question_type`
- missing top-level `visual_profile` or `feature_tags`
- local absolute paths in package JSON
- `group_ref` not matching `groups.jsonl`
- group members not matching `questions.jsonl`
- missing asset files or SHA256 mismatches
- missing canonical subject mapping

Warnings include:

- visual dependency markers without packaged assets
- duplicate package asset paths
- duplicate content hashes
- missing subject mapping notes

## AI Role

AI may help interpret reports, identify likely causes, and write repair scripts.
AI must not be the release gate.

The release gate is the Python validator output and exit code.

## Platform Handoff

The external platform should consume only:

- the versioned package directory
- `manifest.json`
- `subjects.json`
- `questions.jsonl`
- `groups.jsonl`
- `asset_manifest.jsonl`
- files under `assets/`

It should not depend on this repository's Review UI schema, candidate JSONL
layout, local absolute paths, or working PostgreSQL schema details.
