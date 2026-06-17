# Taiwan National Exam Catalog

An open, machine-readable catalog of Taiwan national examination metadata,
starting from MOEX historical question-and-answer pages.

This repository begins with official catalog metadata:

- exam year
- exam code
- exam title
- category code
- category name
- subject code
- subject name
- official question / answer / correction PDF URL availability
- stable registry keys for future download, parsing, and dataset work

It does not claim to be an official MOEX project.

## Current Scope

The first public scope is metadata only:

- ROC years 100-115
- source: MOEX exam question-and-answer search platform
- no PDF files included
- no parsed question text included yet
- no AI-generated explanations included

For the AI Learning Platform ingestion work, the current active subset is the
professional / technical license path for the locked 27 health-care related
categories. Public-service categories such as `公職醫事檢驗師` are detected but
excluded from the current ingestion scope.

## Repository Layout

```text
catalogs/
  moex_subject_catalog__y100-115.csv
  moex_subject_catalog__y100-115.md
  moex_subject_catalog_overrides.csv
  locked_27_canonical_category_names.csv
  other_professional_high_categories_excluding_locked27__y100-115.csv
docs/
  source-policy.md
  contribution-guide.md
  known-issues.md
  locked-27-category-name-stability.md
schemas/
  moex_catalog.schema.json
  question_candidate.schema.json
scripts/
  export_moex_subject_catalog.py
examples/
  sample-question-candidate.json
國考題資料夾/              # Local asset workspace; gitignored
```

## Local Asset Workspace

The local working folder for PDF downloads, MinerU output, review queues, and
ingestion-ready candidates is:

```text
./國考題資料夾
```

This folder is intentionally ignored by git. It keeps large, messy, and
intermediate exam assets next to the catalog project without publishing them in
the repository history.

## Registry Key

Subject-level key:

```text
moex:{exam_code}:{category_code}:{subject_code}:{question_set}
```

Document-level key:

```text
moex:{exam_code}:{category_code}:{subject_code}:{question_set}:{document_role}
```

Where `document_role` is one of:

- `question`
- `answer`
- `correction`

## Data Quality Notes

The catalog preserves official raw names. Normalized names should be added as
derived fields, not by overwriting official values.

Known examples:

- Full-width and half-width parentheses both appear in official category names.
- Some essay-only exams have no answer PDF link; this is not automatically a
  download failure.
- Some official pages contain orphan subject rows whose parent category label
  is not present in the HTML; these are handled through explicit overrides.

## Future Dataset Layers

Planned layers can be added progressively:

1. PDF asset manifests with SHA-256 hashes and official source URLs.
2. MinerU / OCR markdown outputs.
3. Structured question candidates in JSONL.
4. Reviewed datasets suitable for practice systems and research.
5. SQLite / Parquet / PostgreSQL export formats.

Large PDF, image, markdown, and database artifacts should be published through
GitHub Releases, Hugging Face Datasets, Zenodo, or object storage rather than
committed directly to git.

## License

See:

- `LICENSE`
- `DATA_LICENSE.md`

This project separates repository code, official metadata, official exam
materials, and community-derived parsed data because their legal status and
attribution requirements may differ.
