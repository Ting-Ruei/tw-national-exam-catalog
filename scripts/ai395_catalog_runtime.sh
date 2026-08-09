#!/usr/bin/env bash
set -euo pipefail

SSH_ALIAS="${CATALOG_RUNTIME_SSH_ALIAS:-ai395}"
PROJECT_ROOT="${CATALOG_RUNTIME_PROJECT_ROOT:-/home/tim/src/tw-national-exam-catalog}"
CONTROL="${CATALOG_RUNTIME_CONTROL:-/srv/ai395/stacks/tw-national-exam-catalog/production/ai395_catalog_production.sh}"
LOCAL_DESKTOP_PORT="${CATALOG_RUNTIME_DESKTOP_PORT:-8875}"
LOCAL_MOBILE_PORT="${CATALOG_RUNTIME_MOBILE_PORT:-8876}"
LOCAL_DB_PORT="${CATALOG_RUNTIME_LOCAL_DB_PORT:-54330}"
REMOTE_DB_PORT="${CATALOG_RUNTIME_DB_PORT:-54329}"

usage() {
  printf '%s\n' \
    "Usage: $0 {checkout|status|preflight|verify|tunnel|urls|local-status}" \
    "" \
    "All exposed remote service actions are read-only. Restore/enable-writes commands are intentionally absent."
}

case "${1:-}" in
  checkout)
    ssh "$SSH_ALIAS" -- git -C "$PROJECT_ROOT" status --short --branch
    ssh "$SSH_ALIAS" -- git -C "$PROJECT_ROOT" rev-parse HEAD
    ;;
  status|preflight|verify)
    ssh "$SSH_ALIAS" -- "$CONTROL" "$1"
    ;;
  tunnel)
    exec ssh -N \
      -L "${LOCAL_DESKTOP_PORT}:192.168.10.90:8765" \
      -L "${LOCAL_MOBILE_PORT}:192.168.10.90:8766" \
      -L "${LOCAL_DB_PORT}:127.0.0.1:${REMOTE_DB_PORT}" \
      "$SSH_ALIAS"
    ;;
  urls)
    printf '%s\n' \
      "desktop=http://127.0.0.1:${LOCAL_DESKTOP_PORT}/" \
      "mobile=http://127.0.0.1:${LOCAL_MOBILE_PORT}/mobile/" \
      "postgres=127.0.0.1:${LOCAL_DB_PORT}" \
      "write_authority=ai395_production"
    ;;
  local-status)
    docker compose ps
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
