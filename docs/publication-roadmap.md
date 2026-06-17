# Publication Roadmap

This project should grow in layers.

## Hosting Strategy

The public repository should stay small and reproducible. Large official exam
artifacts should be published as versioned dataset packages, not committed into
git history.

Recommended hosting split:

- GitHub repository: code, schemas, docs, manifests, small samples, export scripts.
- GitHub Releases: versioned compressed dataset packages linked to repo tags.
- Hugging Face Datasets: primary programmatic dataset distribution for Parquet,
  JSONL, WebDataset, and image assets.
- Zenodo: archival DOI releases for important versions.
- Cloudflare R2 or compatible object storage: optional online image/object
  hosting for web apps and APIs.
- Neon or Supabase: small demo databases only; not the canonical full dataset.

Canonical source for sharing should be file-based exports:

```text
parquet/*.parquet
assets/images/...
manifest.json
checksums.sha256
sqlite/*.sqlite
```

Cloud databases should be treated as generated demo surfaces. They can be
recreated from the file-based dataset and may contain only subsets when free
quotas are too small.

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

Suggested package:

- `metadata-only`: catalog, source URLs, file hashes, document roles, pair index.

## Phase 3: OCR / MinerU Artifacts

Publish only after quality flags are added:

- markdown path
- image asset references
- parser version
- OCR/MinerU version
- extraction warnings
- source document hash

Suggested package:

- `full-official`: official question OCR text, answer OCR text, images, tables,
  layout references, and MinerU provenance.

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

Suggested packages:

- `text-lite`: structured question text, options, answers, and metadata without images.
- `full-official`: structured question text plus official exam images and tables.

AI-generated explanations should be clearly marked as generated content and
kept separate from official answers.

## Phase 5: Reviewed Practice Dataset

Publish community-reviewed data only when:

- source lineage is traceable
- duplicate and supersede relations are recorded
- image and group-question references are stable
- known answer alignment problems are flagged
