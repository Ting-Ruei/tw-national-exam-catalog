#!/usr/bin/env bash
set -euo pipefail

SSH_ALIAS="${CATALOG_RUNTIME_SSH_ALIAS:-ai395}"
PROJECT_ROOT="${CATALOG_RUNTIME_PROJECT_ROOT:-/home/tim/src/tw-national-exam-catalog}"
CONTROL="${CATALOG_RUNTIME_CONTROL:-/home/tim/ai395-catalog-restore-drill.sh}"
LOCAL_DESKTOP_PORT="${CATALOG_RUNTIME_DESKTOP_PORT:-8875}"
LOCAL_MOBILE_PORT="${CATALOG_RUNTIME_MOBILE_PORT:-8876}"
REMOTE_DB_PORT="${CATALOG_RUNTIME_DB_PORT:-54330}"

usage() {
  printf '%s\n' \
    "Usage: $0 {checkout|status|preflight|verify|tunnel|urls|local-status}" \
    "" \
    "All remote service actions are read-only. Restore/start/production commands are intentionally absent."
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
      -L "${LOCAL_DESKTOP_PORT}:127.0.0.1:8875" \
      -L "${LOCAL_MOBILE_PORT}:127.0.0.1:8876" \
      -L "${REMOTE_DB_PORT}:127.0.0.1:54330" \
      "$SSH_ALIAS"
    ;;
  urls)
    printf '%s\n' \
      "desktop=http://127.0.0.1:${LOCAL_DESKTOP_PORT}/" \
      "mobile=http://127.0.0.1:${LOCAL_MOBILE_PORT}/mobile/" \
      "postgres=127.0.0.1:${REMOTE_DB_PORT}" \
      "write_authority=disabled_on_ai395_restore_drill"
    ;;
  local-status)
    docker compose ps
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
