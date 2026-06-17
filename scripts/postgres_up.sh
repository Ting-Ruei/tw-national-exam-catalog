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
POSTGRES_IMAGE="${POSTGRES_IMAGE:-pgvector/pgvector:0.8.2-pg18}"

if [[ "${POSTGRES_PREPULL_IMAGE:-1}" == "1" ]]; then
  DOCKER_PULL_CONFIG="${DOCKER_PULL_CONFIG:-/private/tmp/tw-national-exam-catalog-docker-config}"
  mkdir -p "$DOCKER_PULL_CONFIG"
  if [[ ! -f "$DOCKER_PULL_CONFIG/config.json" ]]; then
    printf '{}\n' > "$DOCKER_PULL_CONFIG/config.json"
  fi
  docker --config "$DOCKER_PULL_CONFIG" pull "$POSTGRES_IMAGE"
fi

docker compose up -d postgres

for _ in $(seq 1 60); do
  if docker compose exec -T postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
    echo "PostgreSQL is ready: ${POSTGRES_USER}@${POSTGRES_DB}"
    exit 0
  fi
  sleep 1
done

echo "PostgreSQL did not become ready within 60 seconds." >&2
exit 1
