#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
runtime_python="${LOCAL_REVIEW_PYTHON:-$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3}"
if [[ ! -x "$runtime_python" ]]; then runtime_python=python3; fi
exec "$runtime_python" scripts/manage_local_review.py "$@"
