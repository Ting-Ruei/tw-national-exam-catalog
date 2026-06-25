#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

SESSION_NAME="${DEVSPACE_SCREEN_SESSION:-tw-national-exam-devspace}"
LOG_PATH="${DEVSPACE_LOG_PATH:-tmp/devspace/devspace.log}"

mkdir -p "$(dirname "$LOG_PATH")"

SCREEN_LIST="$(screen -ls 2>/dev/null || true)"

if grep -Fq ".$SESSION_NAME" <<<"$SCREEN_LIST"; then
  echo "DevSpace screen session already running: $SESSION_NAME"
  grep -F ".$SESSION_NAME" <<<"$SCREEN_LIST" || true
  exit 0
fi

: > "$LOG_PATH"
screen -dmS "$SESSION_NAME" bash -lc "cd '$PROJECT_ROOT' && bash scripts/start_devspace_chatgpt_mcp.sh >> '$LOG_PATH' 2>&1"

sleep 3
SCREEN_LIST="$(screen -ls 2>/dev/null || true)"
grep -F ".$SESSION_NAME" <<<"$SCREEN_LIST" || {
  echo "DevSpace screen session did not stay alive. Recent log:"
  tail -n 80 "$LOG_PATH" || true
  exit 1
}

echo "DevSpace screen session started: $SESSION_NAME"
echo "Log: $PROJECT_ROOT/$LOG_PATH"
tail -n 20 "$LOG_PATH" || true
