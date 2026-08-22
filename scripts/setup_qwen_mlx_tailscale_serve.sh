#!/usr/bin/env bash
set -euo pipefail

# Prepare or apply a tailnet-only Tailscale Serve mapping for the Mac-hosted
# Ollama endpoint. This script never calls `tailscale funnel`.

MODE="plan"
LOCAL_OLLAMA_URL="${QWEN_MLX_OLLAMA_URL:-http://127.0.0.1:11434}"
TAILSCALE_TARGET="${QWEN_MLX_TAILSCALE_TARGET:-http://127.0.0.1:11434}"
TAILSCALE_HTTPS_PORT="${QWEN_MLX_TAILSCALE_HTTPS_PORT:-443}"
MODEL="${QWEN_MLX_MODEL:-qwen3.8:27b-mlx}"

usage() {
  printf '%s\n' \
    'Usage: setup_qwen_mlx_tailscale_serve.sh [--plan|--apply|--status|--disable]' \
    '' \
    '  --plan     Print checks and the exact Serve command; default.' \
    '  --apply    Verify local Ollama/model, then apply tailnet-only Serve.' \
    '  --status   Read the current Serve status only.' \
    '  --disable  Explicitly reset Serve; no other service is changed.'
}

while (($# > 0)); do
  case "$1" in
    --plan) MODE="plan" ;;
    --apply) MODE="apply" ;;
    --status) MODE="status" ;;
    --disable) MODE="disable" ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

case "$LOCAL_OLLAMA_URL" in
  http://127.0.0.1:*|http://localhost:*) ;;
  *)
    printf 'QWEN_MLX_OLLAMA_URL must point to local Ollama (127.0.0.1 or localhost), got %s\n' "$LOCAL_OLLAMA_URL" >&2
    exit 2
    ;;
esac

if [[ "$TAILSCALE_TARGET" != "http://127.0.0.1:"* && "$TAILSCALE_TARGET" != "http://localhost:"* ]]; then
  printf 'QWEN_MLX_TAILSCALE_TARGET must proxy local Ollama, got %s\n' "$TAILSCALE_TARGET" >&2
  exit 2
fi

print_plan() {
  printf '%s\n' \
    'Qwen MLX Tailscale Serve plan' \
    "  local Ollama: ${LOCAL_OLLAMA_URL}" \
    "  model tag: ${MODEL}" \
    "  tailnet HTTPS port: ${TAILSCALE_HTTPS_PORT}" \
    "  target: ${TAILSCALE_TARGET}" \
    '' \
    'Read-only checks:' \
    "  curl --fail --max-time 5 ${LOCAL_OLLAMA_URL}/api/tags" \
    '  ollama list' \
    '  tailscale serve status' \
    '' \
    'Owner-approved apply command:' \
    "  tailscale serve --bg --https=${TAILSCALE_HTTPS_PORT} ${TAILSCALE_TARGET}" \
    '' \
    'This plan intentionally contains no public Funnel operation.'
}

if [[ "$MODE" == "plan" ]]; then
  print_plan
  exit 0
fi

if ! command -v tailscale >/dev/null 2>&1; then
  printf 'tailscale CLI is not installed or is not on PATH.\n' >&2
  exit 2
fi

if [[ "$MODE" == "status" ]]; then
  exec tailscale serve status
fi

if [[ "$MODE" == "disable" ]]; then
  printf 'Resetting the current Tailscale Serve configuration on this host.\n'
  exec tailscale serve reset
fi

if ! command -v ollama >/dev/null 2>&1; then
  printf 'ollama CLI is not installed or is not on PATH.\n' >&2
  exit 2
fi

if ! curl --fail --silent --show-error --max-time 5 "${LOCAL_OLLAMA_URL}/api/tags" >/dev/null; then
  printf 'Local Ollama health check failed at %s/api/tags\n' "$LOCAL_OLLAMA_URL" >&2
  exit 2
fi

if ! ollama list | awk 'NR > 1 {print $1}' | grep -F -x -- "$MODEL" >/dev/null; then
  printf 'Model tag %s was not found in ollama list. Set QWEN_MLX_MODEL to the exact installed tag.\n' "$MODEL" >&2
  exit 2
fi

printf 'Applying tailnet-only Serve mapping to local Ollama.\n'
tailscale serve --bg --https="${TAILSCALE_HTTPS_PORT}" "${TAILSCALE_TARGET}"
tailscale serve status
