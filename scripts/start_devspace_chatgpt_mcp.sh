#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ -f .env.devspace ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env.devspace
  set +a
fi

PORT="${DEVSPACE_PORT:-7676}"
HOST="${DEVSPACE_HOST:-127.0.0.1}"
ALLOWED_ROOTS="${DEVSPACE_ALLOWED_ROOTS:-$PROJECT_ROOT}"
CONFIG_DIR="${DEVSPACE_CONFIG_DIR:-$HOME/.devspace-tw-national-exam-catalog}"
STATE_DIR="${DEVSPACE_STATE_DIR:-$HOME/.local/share/devspace-tw-national-exam-catalog}"
WORKTREE_ROOT="${DEVSPACE_WORKTREE_ROOT:-$HOME/.devspace-tw-national-exam-catalog/worktrees}"
TOKEN_FILE="${DEVSPACE_OWNER_TOKEN_FILE:-$CONFIG_DIR/owner_token}"

mkdir -p "$CONFIG_DIR" "$STATE_DIR" "$WORKTREE_ROOT"

if [[ -z "${DEVSPACE_OAUTH_OWNER_TOKEN:-}" ]]; then
  if [[ -f "$TOKEN_FILE" ]]; then
    DEVSPACE_OAUTH_OWNER_TOKEN="$(cat "$TOKEN_FILE")"
  else
    DEVSPACE_OAUTH_OWNER_TOKEN="$(openssl rand -base64 32)"
    umask 077
    printf '%s\n' "$DEVSPACE_OAUTH_OWNER_TOKEN" > "$TOKEN_FILE"
  fi
  export DEVSPACE_OAUTH_OWNER_TOKEN
fi

if [[ -z "${DEVSPACE_PUBLIC_BASE_URL:-}" ]]; then
  DEVSPACE_PUBLIC_BASE_URL="http://127.0.0.1:$PORT"
  export DEVSPACE_PUBLIC_BASE_URL
  echo "DEVSPACE_PUBLIC_BASE_URL is not set; starting local-only MCP endpoint."
  echo "ChatGPT needs a public HTTPS origin, for example https://devspace.example.com"
fi

export HOST="$HOST"
export PORT="$PORT"
export DEVSPACE_ALLOWED_ROOTS="$ALLOWED_ROOTS"
export DEVSPACE_CONFIG_DIR="$CONFIG_DIR"
export DEVSPACE_STATE_DIR="$STATE_DIR"
export DEVSPACE_WORKTREE_ROOT="$WORKTREE_ROOT"
export DEVSPACE_TOOL_MODE="${DEVSPACE_TOOL_MODE:-minimal}"
export DEVSPACE_TOOL_NAMING="${DEVSPACE_TOOL_NAMING:-short}"
export DEVSPACE_WIDGETS="${DEVSPACE_WIDGETS:-full}"
export DEVSPACE_SKILLS="${DEVSPACE_SKILLS:-1}"
export DEVSPACE_AGENT_DIR="${DEVSPACE_AGENT_DIR:-$HOME/.codex}"
export DEVSPACE_LOG_FORMAT="${DEVSPACE_LOG_FORMAT:-pretty}"
DEVSPACE_BIN="${DEVSPACE_BIN:-$(command -v devspace || true)}"
if [[ -z "$DEVSPACE_BIN" ]]; then
  DEVSPACE_BIN="npx @waishnav/devspace"
fi

echo "DevSpace local URL: http://$HOST:$PORT/mcp"
echo "DevSpace public MCP URL: ${DEVSPACE_PUBLIC_BASE_URL%/}/mcp"
echo "Allowed roots: $DEVSPACE_ALLOWED_ROOTS"
echo "Owner token file: $TOKEN_FILE"
if [[ "${DEVSPACE_SHOW_OWNER_TOKEN:-0}" == "1" ]]; then
  echo
  echo "Owner password:"
  cat "$TOKEN_FILE"
  echo
else
  echo "Owner password is hidden. Read it locally with: cat \"$TOKEN_FILE\""
fi

exec $DEVSPACE_BIN serve
