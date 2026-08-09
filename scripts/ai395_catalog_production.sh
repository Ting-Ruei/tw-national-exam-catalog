#!/usr/bin/env bash

set -euo pipefail
umask 077

action="${1:-status}"
stack_dir="/srv/ai395/stacks/tw-national-exam-catalog/production"
env_file="/etc/ai395/tw-national-exam-catalog/production.env"
compose_file="${stack_dir}/compose.production.yaml"
state_dir="/srv/ai395/state/tw-national-exam-catalog/production"

if [[ ! -r "${env_file}" ]]; then
  printf 'Missing production environment: %s\n' "${env_file}" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "${env_file}"
set +a

compose=(
  docker compose
  -p tw-exam-production
  --env-file "${env_file}"
  -f "${compose_file}"
)

require_non_default_secret() {
  local name="$1" value="${!1:-}"
  if [[ ${#value} -lt 24 || "${value}" == *CHANGE_ME* || "${value}" == *example* ]]; then
    printf '%s is missing or is not a production secret.\n' "${name}" >&2
    exit 1
  fi
}

verify_snapshot_file() {
  local target="$1" name expected actual
  name="$(basename "${target}")"
  expected="$(awk -v name="${name}" '$2 == name {print $1}' "${MIGRATION_SNAPSHOT_ROOT}/SHA256SUMS")"
  if [[ -z "${expected}" ]]; then
    printf 'No checksum entry for %s\n' "${name}" >&2
    exit 1
  fi
  actual="$(sha256sum "${target}" | awk '{print $1}')"
  if [[ "${actual}" != "${expected}" ]]; then
    printf 'Checksum mismatch for %s\n' "${target}" >&2
    exit 1
  fi
  printf '%s: OK\n' "${name}"
}

wait_for_postgres() {
  local attempt
  for attempt in $(seq 1 60); do
    if "${compose[@]}" exec -T postgres pg_isready \
      -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  printf 'Production PostgreSQL did not become ready.\n' >&2
  return 1
}

wait_for_review_ui() {
  local attempt
  for attempt in $(seq 1 90); do
    if "${compose[@]}" ps --status running --services | grep -qx review-ui; then
      if python3 - "${REVIEW_UI_BIND}" "${REVIEW_UI_PORT}" 2>/dev/null <<'PY'
import socket
import sys
with socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=3):
    pass
PY
      then
        return 0
      fi
    fi
    sleep 2
  done
  "${compose[@]}" logs --tail 100 review-ui >&2 || true
  printf 'Production Review UI did not become ready.\n' >&2
  return 1
}

curl_config() {
  local config="$1"
  {
    printf 'silent\n'
    printf 'show-error\n'
    printf 'user = "%s:%s"\n' "${REVIEW_UI_BASIC_AUTH_USERNAME}" "${REVIEW_UI_BASIC_AUTH_PASSWORD}"
  } > "${config}"
  chmod 600 "${config}"
}

query_counts() {
  "${compose[@]}" exec -T postgres psql \
    -X -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -At -F '|' \
    -c "SELECT (SELECT count(*) FROM exam.official_documents),(SELECT count(*) FROM exam.question_candidates),(SELECT count(*) FROM exam.question_review_events),(SELECT count(*) FROM exam.answer_review_events),(SELECT count(*) FROM exam.question_ai_review_events),(SELECT count(*) FROM exam.questions),(SELECT count(*) FROM exam.answers),(SELECT count(*) FROM exam.assets),(SELECT COALESCE(max(id),0) FROM exam.question_review_events),(SELECT COALESCE(max(id),0) FROM exam.answer_review_events),(SELECT COALESCE(max(id),0) FROM exam.question_ai_review_events)"
}

configure_application_role() {
  "${compose[@]}" exec -T postgres psql \
    -X -v ON_ERROR_STOP=1 \
    -v app_user="${REVIEW_UI_DB_USER}" \
    -v app_password="${REVIEW_UI_DB_PASSWORD}" \
    -v db_name="${POSTGRES_DB}" \
    -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')
\gexec
ALTER ROLE :"app_user" NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'app_password';
ALTER SCHEMA exam OWNER TO :"app_user";
SELECT format('ALTER TABLE %I.%I OWNER TO %I', n.nspname, c.relname, :'app_user')
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'exam' AND c.relkind IN ('r', 'p')
\gexec
SELECT format('ALTER SEQUENCE %I.%I OWNER TO %I', n.nspname, c.relname, :'app_user')
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'exam' AND c.relkind = 'S'
\gexec
GRANT CONNECT ON DATABASE :"db_name" TO :"app_user";
GRANT USAGE, CREATE ON SCHEMA exam TO :"app_user";
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON ALL TABLES IN SCHEMA exam TO :"app_user";
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA exam TO :"app_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"app_user" IN SCHEMA exam
  GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLES TO :"app_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"app_user" IN SCHEMA exam
  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO :"app_user";
SQL
}

rebind_linux_filename_assets() {
  local mapping_count postgres_container rebound_count
  mapping_count="$(awk 'END {print NR - 1}' "${CATALOG_LINUX_FILENAME_MAP}")"
  if [[ ! "${mapping_count}" =~ ^[1-9][0-9]*$ ]]; then
    printf 'Linux filename mapping is unexpectedly empty: %s\n' "${CATALOG_LINUX_FILENAME_MAP}" >&2
    exit 1
  fi
  postgres_container="$("${compose[@]}" ps -q postgres)"
  docker cp "${CATALOG_LINUX_FILENAME_MAP}" "${postgres_container}:/tmp/ai395-linux-filename-map.tsv" >/dev/null
  "${compose[@]}" exec -T postgres psql \
    -X -v ON_ERROR_STOP=1 -v expected_mapping_count="${mapping_count}" \
    -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" <<'SQL'
BEGIN;
CREATE TEMP TABLE ai395_linux_filename_map (
  root_label TEXT NOT NULL,
  source_relative_path TEXT PRIMARY KEY,
  storage_relative_path TEXT NOT NULL,
  bytes BIGINT NOT NULL,
  sha256 TEXT NOT NULL
);
\copy ai395_linux_filename_map FROM '/tmp/ai395-linux-filename-map.tsv' WITH (FORMAT csv, HEADER true, DELIMITER E'\t')
SELECT 1 / CASE WHEN count(*) = :'expected_mapping_count'::integer THEN 1 ELSE 0 END
FROM ai395_linux_filename_map WHERE root_label = 'macstudio-main';
UPDATE exam.assets AS a
SET asset_path = m.storage_relative_path,
    relative_asset_path = m.storage_relative_path
FROM ai395_linux_filename_map AS m
WHERE m.root_label = 'macstudio-main'
  AND COALESCE(NULLIF(a.relative_asset_path, ''), a.asset_path) = m.source_relative_path;
COMMIT;
SQL
  rebound_count="$("${compose[@]}" exec -T postgres psql \
    -X -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -At \
    -c "SELECT count(*) FROM exam.assets WHERE relative_asset_path LIKE '.linux-name-map/objects/%'")"
  if [[ "${rebound_count}" != "${mapping_count}" ]]; then
    printf 'Linux filename rebind count mismatch: expected=%s actual=%s\n' \
      "${mapping_count}" "${rebound_count}" >&2
    exit 1
  fi
}

case "${action}" in
  preflight)
    require_non_default_secret POSTGRES_PASSWORD
    require_non_default_secret REVIEW_UI_DB_PASSWORD
    require_non_default_secret REVIEW_UI_BASIC_AUTH_PASSWORD
    test "$(stat -c '%a' "${env_file}")" = "600"
    test -r "${compose_file}"
    test -r "${CATALOG_RELEASE_ROOT}/scripts/serve_question_review_ui.py"
    test -r "${CATALOG_FINALIZATION_REPORT}"
    test -r "${CATALOG_FINAL_PHYSICAL_MANIFEST}"
    test -r "${CATALOG_LINUX_FILENAME_MAP}"
    test -d "${CATALOG_ASSET_ROOT}"
    test -d "${CATALOG_MANUAL_ASSET_ROOT}"
    python3 - "${CATALOG_FINALIZATION_REPORT}" <<'PY'
import json
import sys
report = json.load(open(sys.argv[1], encoding="utf-8"))
if report.get("state") != "candidate_finalized" or report.get("ready_for_promotion") is not True:
    raise SystemExit("canonical finalization report has not passed the promotion gate")
PY
    test "$(sha256sum "${CATALOG_FINAL_PHYSICAL_MANIFEST}" | awk '{print $1}')" = "${CATALOG_FINAL_PHYSICAL_MANIFEST_SHA256}"
    "${compose[@]}" config --quiet
    printf 'AI395 Catalog production preflight passed.\n'
    ;;
  build-image)
    docker build \
      --file "${CATALOG_RELEASE_ROOT}/docker/review-ui/Dockerfile" \
      --tag "${REVIEW_UI_IMAGE}" \
      "${CATALOG_RELEASE_ROOT}"
    docker image inspect "${REVIEW_UI_IMAGE}" --format '{{.Id}}'
    ;;
  prepare-manual-assets)
    if [[ -e "${state_dir}/manual-assets-initialized" ]]; then
      printf 'Manual assets were already initialized; refusing to overwrite the live writable layer.\n' >&2
      exit 1
    fi
    mkdir -p "${state_dir}" "${CATALOG_MANUAL_ASSET_ROOT}"
    rsync -a "${CATALOG_ASSET_ROOT}/40_manual_assets/" "${CATALOG_MANUAL_ASSET_ROOT}/"
    chown -R "${CATALOG_RUNTIME_UID}:${CATALOG_RUNTIME_GID}" "${CATALOG_MANUAL_ASSET_ROOT}"
    touch "${state_dir}/manual-assets-initialized"
    printf 'Manual asset writable layer initialized without deleting source data.\n'
    ;;
  restore)
    if [[ "${CATALOG_PRODUCTION_RESTORE_APPROVED:-0}" != "1" ]]; then
      printf 'Set CATALOG_PRODUCTION_RESTORE_APPROVED=1 for the explicit production restore.\n' >&2
      exit 1
    fi
    if "${compose[@]}" ps --status running --services | grep -qx review-ui; then
      printf 'Production Review UI is running; refusing database restore.\n' >&2
      exit 1
    fi
    verify_snapshot_file "${MIGRATION_SNAPSHOT_ROOT}/tw_national_exam_dev.dump"
    verify_snapshot_file "${MIGRATION_SNAPSHOT_ROOT}/database-counts.psv"
    "${compose[@]}" up -d postgres
    wait_for_postgres
    "${compose[@]}" exec -T postgres pg_restore \
      -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
      --clean --if-exists --no-owner --no-acl \
      < "${MIGRATION_SNAPSHOT_ROOT}/tw_national_exam_dev.dump"
    rebind_linux_filename_assets
    configure_application_role
    expected_counts="$(tr -d '\r\n' < "${MIGRATION_SNAPSHOT_ROOT}/database-counts.psv")"
    actual_counts="$(query_counts | tr -d '\r\n')"
    test "${actual_counts}" = "${expected_counts}"
    printf 'Production database restore and exact count verification passed.\n'
    ;;
  start-read-only)
    test "${REVIEW_UI_READ_ONLY}" = "1"
    "${compose[@]}" up -d review-ui
    wait_for_review_ui
    auth_config="$(mktemp)"
    trap 'rm -f "${auth_config}"' EXIT
    curl_config "${auth_config}"
    test "$(curl --silent --output /dev/null --write-out '%{http_code}' "http://${REVIEW_UI_BIND}:${REVIEW_UI_PORT}/")" = "401"
    test "$(curl --config "${auth_config}" --output /dev/null --write-out '%{http_code}' "http://${REVIEW_UI_BIND}:${REVIEW_UI_PORT}/")" = "200"
    test "$(curl --config "${auth_config}" --header 'Content-Type: application/json' --data '{}' --output /dev/null --write-out '%{http_code}' "http://${REVIEW_UI_BIND}:${REVIEW_UI_PORT}/api/preferences")" = "503"
    printf 'Authenticated read-only UI smoke passed; writes remain blocked.\n'
    ;;
  enable-writes)
    if [[ "${CATALOG_CUTOVER_APPROVED:-0}" != "1" ]]; then
      printf 'Set CATALOG_CUTOVER_APPROVED=1 for the explicit single-writer cutover.\n' >&2
      exit 1
    fi
    test "${REVIEW_UI_READ_ONLY}" = "1"
    env_backup="${env_file}.pre-writes-$(date '+%Y%m%d-%H%M%S')"
    cp -p "${env_file}" "${env_backup}"
    env_tmp="${env_file}.tmp.$$"
    awk '
      BEGIN { found=0 }
      /^REVIEW_UI_READ_ONLY=/ { print "REVIEW_UI_READ_ONLY=0"; found=1; next }
      { print }
      END { if (!found) print "REVIEW_UI_READ_ONLY=0" }
    ' "${env_file}" > "${env_tmp}"
    chmod 600 "${env_tmp}"
    mv "${env_tmp}" "${env_file}"
    set -a
    # shellcheck disable=SC1090
    . "${env_file}"
    set +a
    "${compose[@]}" up -d --force-recreate review-ui
    wait_for_review_ui
    auth_config="$(mktemp)"
    trap 'rm -f "${auth_config}"' EXIT
    curl_config "${auth_config}"
    test "$(curl --config "${auth_config}" --header 'Content-Type: application/json' --data '{}' --output /dev/null --write-out '%{http_code}' "http://${REVIEW_UI_BIND}:${REVIEW_UI_PORT}/api/preferences")" = "400"
    printf 'AI395 is now the enabled Review UI writer; env backup=%s\n' "${env_backup}"
    ;;
  verify)
    wait_for_postgres
    expected_counts="$(tr -d '\r\n' < "${MIGRATION_SNAPSHOT_ROOT}/database-counts.psv")"
    actual_counts="$(query_counts | tr -d '\r\n')"
    printf 'expected=%s\n' "${expected_counts}"
    printf 'actual=%s\n' "${actual_counts}"
    test "${actual_counts}" = "${expected_counts}"
    "${compose[@]}" exec -T postgres psql \
      -X -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -At \
      -c "SELECT current_setting('server_version'), extversion FROM pg_extension WHERE extname = 'vector'"
    "${compose[@]}" exec -T postgres psql \
      -X -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -At \
      -c "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = '${REVIEW_UI_DB_USER}'"
    ;;
  status)
    "${compose[@]}" ps
    ;;
  stop-ui)
    "${compose[@]}" stop review-ui
    printf 'Production Review UI stopped; database and volumes retained.\n'
    ;;
  *)
    printf 'Usage: %s {preflight|build-image|prepare-manual-assets|restore|start-read-only|enable-writes|verify|status|stop-ui}\n' "$0" >&2
    exit 2
    ;;
esac
