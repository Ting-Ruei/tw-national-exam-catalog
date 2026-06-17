# Database Schema Drafts

This directory contains database schema drafts for downstream ingestion.

Current status:

- `postgresql_schema.sql` is a design draft.
- It can be applied to a local Docker PostgreSQL development database.
- PDF and image binaries should remain in the asset folder or object storage; database rows store paths and hashes.

## Local PostgreSQL smoke test

This repository includes a Docker Compose development database for schema testing.
It is intended for local validation before real ingestion. The default image is
PostgreSQL 18 with pgvector installed.

```bash
cp .env.example .env
bash scripts/postgres_up.sh
bash scripts/postgres_apply_schema.sh
bash scripts/postgres_smoke_test.sh
```

Default connection:

```text
postgresql://national_exam:national_exam_dev_password@localhost:54329/tw_national_exam_dev
```

The smoke test verifies the `exam` schema, confirms that `vector` is enabled,
creates a temporary vector table, and inserts a minimal set of linked rows inside
a transaction. It rolls the transaction back, so it verifies writeability without
keeping fake data.
