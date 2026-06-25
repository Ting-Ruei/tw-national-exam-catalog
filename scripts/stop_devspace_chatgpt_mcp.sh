#!/usr/bin/env bash
set -euo pipefail

SESSION_NAME="${DEVSPACE_SCREEN_SESSION:-tw-national-exam-devspace}"
SCREEN_LIST="$(screen -ls 2>/dev/null || true)"

if grep -Fq ".$SESSION_NAME" <<<"$SCREEN_LIST"; then
  screen -S "$SESSION_NAME" -X quit
  echo "Stopped DevSpace screen session: $SESSION_NAME"
else
  echo "No DevSpace screen session found: $SESSION_NAME"
fi
