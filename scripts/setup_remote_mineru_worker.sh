#!/bin/bash
# Prepare a remote MacBook as a MinerU worker for this project.
#
# This script is intended to run on the remote worker machine.

set -euo pipefail

WORKSPACE_ROOT="${AI_WORKSPACE_ROOT:-/Users/tim/AI_workspace}"
WORKER_ROOT="${WORKER_ROOT:-$WORKSPACE_ROOT/national_exam_mineru_worker}"
REPO_URL="${REPO_URL:-https://github.com/Ting-Ruei/tw-national-exam-catalog.git}"
REPO_DIR="${REPO_DIR:-$WORKER_ROOT/repo}"

mkdir -p "$WORKSPACE_ROOT/OCR_model"
mkdir -p "$WORKER_ROOT/incoming_batches"
mkdir -p "$WORKER_ROOT/running_batches"
mkdir -p "$WORKER_ROOT/finished_batches"
mkdir -p "$WORKER_ROOT/logs"

if [[ -d "$REPO_DIR/.git" ]]; then
  git -C "$REPO_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$REPO_DIR"
fi

MINERU_BIN="${MINERU_BIN:-$WORKSPACE_ROOT/OCR_model/MinerU/venv_mineru/bin/mineru}"

echo "Remote MinerU worker prepared"
echo "  workspace:  $WORKSPACE_ROOT"
echo "  worker:     $WORKER_ROOT"
echo "  repo:       $REPO_DIR"
echo "  mineru_bin: $MINERU_BIN"

if [[ -x "$MINERU_BIN" ]]; then
  "$MINERU_BIN" --version || true
else
  echo "MinerU executable not found yet: $MINERU_BIN"
  echo "Install MinerU there, or pass MINERU_BIN=/path/to/mineru when running batches."
fi
