#!/usr/bin/env bash
# Serve a golden-path run for review, on this machine only.
#
# The run directory is the input and the review log lives inside it, so a review session is
# self-contained: copy the run somewhere, review it there, keep or discard the whole thing.
# Nothing here writes to a database, a network host, or the authoritative catalog.
#
#   scripts/review_run.sh /tmp/qbr-golden-001
#   scripts/review_run.sh /tmp/qbr-golden-001 8770
set -euo pipefail

RUN="${1:-/tmp/qbr-golden-001}"
PORT="${2:-${REVIEW_UI_PORT:-8774}}"
# Binding to 127.0.0.1 means the workstation that started it is the only machine that can open it,
# which is correct for a staged review but is also why a phone on the same network gets nothing.
# Set REVIEW_UI_HOST=0.0.0.0 deliberately when a second screen is wanted.
HOST="${REVIEW_UI_HOST:-127.0.0.1}"

CATALOG="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

CANDIDATES="$RUN/review-ui/candidates.jsonl"
ISSUES="$RUN/review-ui/issues.csv"
LOG="$RUN/review-ui/question_review_events.jsonl"

for required in "$CANDIDATES" "$ISSUES"; do
  if [[ ! -f "$required" ]]; then
    echo "missing $required" >&2
    echo "build the run first:" >&2
    echo "  cd tw-national-exam-catalog/qbr && .venv/bin/python scripts/golden_path.py run --registry-key ..." >&2
    exit 1
  fi
done

count=$(wc -l < "$CANDIDATES" | tr -d ' ')
# A queue with no questions serves a console that looks broken and reports nothing, which is how a
# stale or half-built run gets mistaken for a UI defect. Refuse it here, with the reason.
if [[ "$count" -eq 0 ]]; then
  echo "$RUN holds 0 questions - this run was never built, or was built empty." >&2
  echo "the UI would open with nothing to show." >&2
  exit 1
fi

# The port is an input, not a constant to remember. If it is taken, say so and stop rather than
# fail on bind after claiming to be serving.
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  holder=$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | awk 'NR==2{print $1" (pid "$2")"}')
  echo "port $PORT is already in use by $holder." >&2
  echo "another review server is probably already running; open that one, or pick a free port:" >&2
  echo "  $0 $RUN 8775" >&2
  exit 1
fi

# Counts are read from the candidate file itself, and every count is braced: a full-width
# character immediately after a variable name is absorbed into the name by some locales, which
# made this script die with `withfig（: unbound variable` and serve nothing.
figures=$(grep -c '"option_key"' "$CANDIDATES" 2>/dev/null || true)
withfig=$(grep -c '"image_refs": \[{' "$CANDIDATES" 2>/dev/null || true)
: "${figures:=0}"
: "${withfig:=0}"
echo "run      $RUN"
echo "題數     $count"
echo "有圖題   ~${withfig}（選項圖 ~${figures}）"
echo "審題介面 http://$HOST:$PORT/v2"
echo "決策紀錄 $LOG"
echo "停止     Ctrl-C"
echo

# The question crops live under the run directory and are served through `/file`. The
# Review UI only serves files under an allowed root, so the run is registered as one.
export REVIEW_UI_ADDITIONAL_ASSET_ROOTS="$RUN${REVIEW_UI_ADDITIONAL_ASSET_ROOTS:+:$REVIEW_UI_ADDITIONAL_ASSET_ROOTS}"

exec python3 "$CATALOG/scripts/serve_question_review_ui.py" \
  --candidate-jsonl "$CANDIDATES" \
  --issue-csv "$ISSUES" \
  --review-log "$LOG" \
  --review-backend jsonl \
  --host "$HOST" --port "$PORT"
