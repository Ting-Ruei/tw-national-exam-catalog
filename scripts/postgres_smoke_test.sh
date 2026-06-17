#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

POSTGRES_DB="${POSTGRES_DB:-tw_national_exam_dev}"
POSTGRES_USER="${POSTGRES_USER:-national_exam}"

docker compose exec -T postgres psql \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  -v ON_ERROR_STOP=1 <<'SQL'
SELECT current_database() AS database_name, current_user AS database_user;

SELECT table_schema, count(*) AS table_count
FROM information_schema.tables
WHERE table_schema = 'exam'
GROUP BY table_schema;

SELECT extname, extversion
FROM pg_extension
WHERE extname = 'vector';

BEGIN;

CREATE TEMP TABLE smoke_embeddings (
    id BIGSERIAL PRIMARY KEY,
    embedding vector(3)
);

INSERT INTO smoke_embeddings (embedding)
VALUES ('[0.1,0.2,0.3]'), ('[0.2,0.2,0.2]');

SELECT id
FROM smoke_embeddings
ORDER BY embedding <-> '[0.1,0.2,0.25]'
LIMIT 1;

INSERT INTO exam.source_systems (code, name, base_url, notes)
VALUES ('smoke_moex', 'Smoke Test MOEX', 'https://wwwc.moex.gov.tw/', 'rollback-only smoke test');

INSERT INTO exam.exam_sessions (
    source_system_id,
    exam_code,
    roc_year,
    exam_ordinal,
    exam_label,
    source_url
)
SELECT id, 'smoke_115001', 115, 1, 'Smoke Test Session', 'https://example.invalid/smoke'
FROM exam.source_systems
WHERE code = 'smoke_moex';

INSERT INTO exam.categories (
    category_code,
    official_category_name,
    normalized_category_name,
    group_name,
    is_locked27,
    notes
)
VALUES ('smoke_category', '測試類科', '測試類科', '測試類科', true, 'rollback-only smoke test');

INSERT INTO exam.subjects (
    category_id,
    subject_code,
    official_subject_name,
    normalized_subject_name,
    canonical_subject_name,
    notes
)
SELECT id, 'smoke_subject', '測試科目', '測試科目', '測試科目', 'rollback-only smoke test'
FROM exam.categories
WHERE category_code = 'smoke_category';

INSERT INTO exam.official_documents (
    registry_key,
    exam_session_id,
    category_id,
    subject_id,
    question_set,
    document_role,
    source_url,
    official_category_name,
    official_subject_name
)
SELECT
    'smoke:115001:smoke_category:smoke_subject:1:question',
    s.id,
    c.id,
    subj.id,
    '1',
    'question',
    'https://example.invalid/smoke.pdf',
    c.official_category_name,
    subj.official_subject_name
FROM exam.exam_sessions s
CROSS JOIN exam.categories c
CROSS JOIN exam.subjects subj
WHERE s.exam_code = 'smoke_115001'
  AND c.category_code = 'smoke_category'
  AND subj.subject_code = 'smoke_subject';

SELECT count(*) AS smoke_document_count
FROM exam.official_documents
WHERE registry_key = 'smoke:115001:smoke_category:smoke_subject:1:question';

ROLLBACK;
SQL
