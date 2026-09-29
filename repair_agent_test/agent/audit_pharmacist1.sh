#!/bin/bash
# Audit the 99 disputed 藥師(一) questions that carry the designer's own notes.
#
# Designer's ruling 2026-09-29 #4: 先審藥師(一)（334 題中 99 題有 notes），再藥師(二).
# This is a **read-and-judge** pass, not a re-extraction: the agent reads the paper, compares it
# with the stored text, and writes its own judgement to `store/agent_feedback.jsonl`.
#
# Governance: G2. The agent writes only its own stream; `question_review_events.jsonl`
# (human, append-only) is never touched. Findings are advisory.
#
# Usage: audit_pharmacist1.sh <keys-file> <out-dir>
set -uo pipefail

# The loop normally runs detached (`nohup ... &`), where the login shell's PATH does not exist.
# Without a PATH, every question returns rc=127 in 0s and the summary reads like "the model refused
# 98 questions" (measured 2026-09-29). NVM's directory is added only if `node` is not already
# reachable, so the script carries no user-specific path of its own.
NODE="$(command -v node || true)"
if [ -z "$NODE" ]; then
  for candidate in "$HOME"/.nvm/versions/node/*/bin/node /opt/homebrew/bin/node /usr/local/bin/node; do
    [ -x "$candidate" ] && NODE="$candidate" && break
  done
fi
[ -x "${NODE:-}" ] || { echo "找不到 node；請把 node 放進 PATH 再跑" >&2; exit 1; }
export PATH="$(dirname "$NODE"):/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

KEYS="$1"
OUT="$2"
# This script lives in the agent directory, so it works from wherever it is started.
AGENT="$(cd "$(dirname "$0")" && pwd)"
STORE="$AGENT/store/agent_feedback.jsonl"

mkdir -p "$OUT"
SUMMARY="$OUT/summary.tsv"
[ -f "$SUMMARY" ] || printf 'key\trc\tseconds\tfeedback_lines\n' > "$SUMMARY"

cd "$AGENT" || exit 1
total=$(grep -c . "$KEYS")
i=0
while read -r key; do
  [ -n "$key" ] || continue
  i=$((i + 1))
  # **Resume is "run the same list again".** The summary file is the ledger: a key that is already
  # in it is skipped, so a loop that was killed (the harness reaps a shell's children when the call
  # ends — measured 2026-09-29, twice) does not judge the same question twice when it restarts.
  if cut -f1 "$SUMMARY" | grep -qxF "$key"; then
    echo "[$i/$total] $key 已完成，跳過"
    continue
  fi
  before=$(wc -l < "$STORE" 2>/dev/null || echo 0)
  started=$(date +%s)
  # `timeout` is not a binary on macOS (it is a shell builtin in the interactive shell only), so the
  # guard is perl's alarm+exec. Measured 2026-09-29, twice: calling `timeout` here made every
  # question return rc=127 in 0s ("the model refused 98 questions"), and a watchdog subshell left a
  # `sleep 900` behind and blocked the loop for 15 minutes after the first question finished.
  perl -e 'alarm shift; exec @ARGV' 900 "$NODE" agent.mjs "看這一題並判讀：$key" > "$OUT/$key.out" 2>&1
  rc=$?
  seconds=$(( $(date +%s) - started ))
  after=$(wc -l < "$STORE" 2>/dev/null || echo 0)
  printf '%s\t%s\t%s\t%s\n' "$key" "$rc" "$seconds" "$((after - before))" >> "$SUMMARY"
  echo "[$i/$total] $key rc=$rc ${seconds}s judgement+$((after - before))"
done < "$KEYS"
echo "audit finished: $i questions"
