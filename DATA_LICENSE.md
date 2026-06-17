# Data License and Source Policy

This repository is intended to make Taiwan national examination metadata and
derived open datasets easier to inspect, verify, and reuse.

## Important Distinctions

The project separates four layers:

1. Official catalog metadata extracted from public MOEX pages.
2. Official exam source documents, such as question, answer, and correction PDF
   files.
3. Machine-produced derived artifacts, such as OCR markdown and image crops.
4. Community-reviewed structured datasets, such as JSONL question candidates.

Each layer should preserve source attribution and processing lineage.

## Current Public Data

The current repository includes metadata catalogs only. It does not include PDF
files or parsed question text.

## Attribution

When reusing this catalog, please cite:

- Source platform: 考選部考畢試題查詢平臺
- Source URL: `https://wwwq.moex.gov.tw/exam/wFrmExamQandASearch.aspx`
- This project as the community-maintained catalog and processing layer

## Legal Caution

Taiwan copyright law includes a rule that examination questions held according
to law are not copyright subject matter. However, before publishing bulk PDF
mirrors, parsed question text, answer keys, images, or enriched datasets, this
project should maintain conservative attribution and source tracking.

Official raw data should not be represented as if it were authored by this
project. Community corrections and derived parsing results should be clearly
marked as derived data.

## Suggested License Policy

- Code: MIT License.
- Catalog metadata produced by this project: CC0-1.0 or CC BY 4.0, pending final
  maintainer decision.
- Derived parsed datasets: publish with explicit provenance fields and a clear
  dataset license after legal review.
- Official PDFs: do not commit directly to git; publish only with source URL,
  SHA-256, and attribution policy.

