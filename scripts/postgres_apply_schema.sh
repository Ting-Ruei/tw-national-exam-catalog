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
SCHEMA_PATH="${1:-schemas/database/postgresql_schema.sql}"
EXTENSION_PATH="${POSTGRES_EXTENSION_SCHEMA:-schemas/database/postgresql_extensions.sql}"

docker compose exec -T postgres psql \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  -v ON_ERROR_STOP=1 \
  < "$EXTENSION_PATH"

docker compose exec -T postgres psql \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  -v ON_ERROR_STOP=1 \
  < "$SCHEMA_PATH"
