# Publication Roadmap

This project should grow in layers.

## Phase 1: Catalog Metadata

Status: started.

Publish:

- official exam/category/subject catalog
- official PDF URL availability
- known parser issues
- manual override tables
- schemas for future parsed data

Do not publish yet:

- mirrored PDF files
- OCR markdown
- parsed official question text
- database dumps

## Phase 2: Source Document Registry

Publish:

- official source URLs
- downloaded filename
- SHA-256 hash
- file size
- document role
- processing status

Large files should be distributed through GitHub Releases, Hugging Face
Datasets, Zenodo, or object storage.

## Phase 3: OCR / MinerU Artifacts

Publish only after quality flags are added:

- markdown path
- image asset references
- parser version
- OCR/MinerU version
- extraction warnings
- source document hash

## Phase 4: Structured Question Candidates

Publish JSONL or Parquet candidate data with:

- stem
- options
- answer
- source registry key
- image references
- group references
- quality flags
- review status

AI-generated explanations should be clearly marked as generated content and
kept separate from official answers.

## Phase 5: Reviewed Practice Dataset

Publish community-reviewed data only when:

- source lineage is traceable
- duplicate and supersede relations are recorded
- image and group-question references are stable
- known answer alignment problems are flagged

