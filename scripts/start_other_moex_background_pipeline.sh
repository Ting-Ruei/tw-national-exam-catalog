#!/bin/bash
# Backward-compatible wrapper for the Python detached launcher.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"
python3 scripts/start_other_moex_background_pipeline.py
