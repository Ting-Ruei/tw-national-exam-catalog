#!/usr/bin/env bash
# Create a private, logical PostgreSQL + Git handoff snapshot.
# This script does not stop services and does not copy asset roots.

set -euo pipefail
umask 077

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
SNAPSHOT_PARENT="${1:-${MIGRATION_SNAPSHOT_ROOT:-$PROJECT_ROOT/migration_snapshots}}"
STAMP="$(date '+%Y%m%d-%H%M%S')"
SNAPSHOT_DIR="$SNAPSHOT_PARENT/postgres-git-$STAMP"

case "$SNAPSHOT_DIR" in
  /|"$HOME"|"$PROJECT_ROOT")
    printf 'Refusing unsafe snapshot directory: %s\n' "$SNAPSHOT_DIR" >&2
    exit 2
    ;;
esac

if ! command -v docker >/dev/null 2>&1; then
  printf 'docker was not found in PATH.\n' >&2
  exit 1
fi

mkdir -p "$SNAPSHOT_DIR"

if ! docker compose exec -T postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null; then
  printf 'PostgreSQL container is not ready.\n' >&2
  exit 1
fi

DB_DUMP="$SNAPSHOT_DIR/$POSTGRES_DB.dump"
SCHEMA_DUMP="$SNAPSHOT_DIR/$POSTGRES_DB.schema.sql"
GLOBALS_DUMP="$SNAPSHOT_DIR/postgres-globals-no-passwords.sql"

docker compose exec -T postgres pg_dump \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  -Fc \
  --no-owner \
  --no-acl \
  > "$DB_DUMP"

docker compose exec -T postgres pg_dump \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  --schema-only \
  --no-owner \
  --no-acl \
  > "$SCHEMA_DUMP"

docker compose exec -T postgres pg_dumpall \
  -U "$POSTGRES_USER" \
  --globals-only \
  --no-role-passwords \
  > "$GLOBALS_DUMP"

docker compose exec -T postgres pg_restore --list < "$DB_DUMP" > "$SNAPSHOT_DIR/restore-list.txt"

docker compose exec -T postgres psql \
  -X \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  -At \
  -F '|' \
  -c "SELECT (SELECT count(*) FROM exam.official_documents),(SELECT count(*) FROM exam.question_candidates),(SELECT count(*) FROM exam.question_review_events),(SELECT count(*) FROM exam.answer_review_events),(SELECT count(*) FROM exam.question_ai_review_events),(SELECT count(*) FROM exam.questions),(SELECT count(*) FROM exam.answers),(SELECT count(*) FROM exam.assets),(SELECT COALESCE(max(id),0) FROM exam.question_review_events),(SELECT COALESCE(max(id),0) FROM exam.answer_review_events),(SELECT COALESCE(max(id),0) FROM exam.question_ai_review_events)" \
  > "$SNAPSHOT_DIR/database-counts.psv"

git rev-parse HEAD > "$SNAPSHOT_DIR/git-head.txt"
git status --short --branch > "$SNAPSHOT_DIR/git-status.txt"
git diff --binary HEAD > "$SNAPSHOT_DIR/tracked-worktree.patch"
git ls-files --others --exclude-standard > "$SNAPSHOT_DIR/untracked-files.txt"
git bundle create "$SNAPSHOT_DIR/repository.bundle" --all

if [[ -s "$SNAPSHOT_DIR/untracked-files.txt" ]]; then
  tar -czf "$SNAPSHOT_DIR/untracked-files.tar.gz" -T "$SNAPSHOT_DIR/untracked-files.txt"
fi

{
  printf 'schema_version=tw_exam_postgres_git_snapshot_v1\n'
  printf 'created_at=%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')"
  printf 'hostname=%s\n' "$(hostname)"
  printf 'postgres_db=%s\n' "$POSTGRES_DB"
  printf 'postgres_user=%s\n' "$POSTGRES_USER"
  printf 'postgres_image=%s\n' "${POSTGRES_IMAGE:-pgvector/pgvector:0.8.2-pg18}"
  printf 'project_root=%s\n' "$PROJECT_ROOT"
} > "$SNAPSHOT_DIR/snapshot-metadata.txt"

: > "$SNAPSHOT_DIR/SHA256SUMS"
sha256_digest() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}
while IFS= read -r -d '' path; do
  relative="${path#"$SNAPSHOT_DIR/"}"
  digest="$(sha256_digest "$path")"
  printf '%s  %s\n' "$digest" "$relative" >> "$SNAPSHOT_DIR/SHA256SUMS"
done < <(find "$SNAPSHOT_DIR" -type f ! -name SHA256SUMS -print0)

printf 'Snapshot created: %s\n' "$SNAPSHOT_DIR"
if command -v sha256sum >/dev/null 2>&1; then
  printf 'Verify with: cd %q && sha256sum -c SHA256SUMS\n' "$SNAPSHOT_DIR"
else
  printf 'Verify with: cd %q && shasum -a 256 -c SHA256SUMS\n' "$SNAPSHOT_DIR"
fi
