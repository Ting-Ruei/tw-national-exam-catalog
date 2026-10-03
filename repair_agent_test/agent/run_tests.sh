#!/bin/sh
# Run the agent's contracts, with the sandbox store redirected and **without leaking it**.
#
# Why this wrapper exists, measured 2026-09-28: `export REPAIR_AGENT_STORE=/tmp/x` followed by
# starting the UI server in the same shell hands the *server* a temp directory that is deleted a
# moment later. The server then resolved the store to that dead path and reported
# `crop_png: null` for a question whose crop was on disk the whole time — which reads exactly like a
# crop bug and is actually a stale environment variable. `REPAIR_AGENT_STORE` is scoped to the test
# process here, so nothing else can inherit it.
#
#   ./run_tests.sh
set -e
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
STORE=$(mktemp -d)
trap 'rm -rf "$STORE"' EXIT
REPAIR_AGENT_STORE="$STORE" node --test "$DIR/test_agent.mjs" "$DIR/test_consumer.mjs" "$DIR/test_workorder.mjs" "$DIR/test_landing.mjs" "$DIR/test_dispatch.mjs"
