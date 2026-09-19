#!/usr/bin/env bash
set -euo pipefail

# Prepare or apply a tailnet-only Tailscale Serve mapping for the Mac-hosted
# Ollama endpoint. The endpoint proxies all Ollama models; QWEN_MLX_MODEL is
# only a preflight check for the Qwen test model. This script never calls
# `tailscale funnel`.

MODE="plan"
LOCAL_OLLAMA_URL="${QWEN_MLX_OLLAMA_URL:-http://127.0.0.1:11434}"
TAILSCALE_TARGET="${QWEN_MLX_TAILSCALE_TARGET:-http://127.0.0.1:11434}"
TAILSCALE_HTTPS_PORT="${QWEN_MLX_TAILSCALE_HTTPS_PORT:-443}"
MODEL="${QWEN_MLX_MODEL:-qwen3.8:27b-mlx}"
TAILSCALE_BIN="${TAILSCALE_BIN:-}"

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
    'Ollama Tailscale Serve plan (Qwen MLX preflight)' \
    "  local Ollama: ${LOCAL_OLLAMA_URL}" \
    "  model tag: ${MODEL}" \
    "  tailnet HTTPS port: ${TAILSCALE_HTTPS_PORT}" \
    "  target: ${TAILSCALE_TARGET}" \
    "  API scope: this tailnet, subject to Tailscale ACLs" \
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

resolve_tailscale_bin() {
  if [[ -n "$TAILSCALE_BIN" && -x "$TAILSCALE_BIN" ]]; then
    return 0
  fi
  if command -v tailscale >/dev/null 2>&1; then
    TAILSCALE_BIN="$(command -v tailscale)"
    return 0
  fi
  # App Store and standalone macOS clients bundle the CLI in the app.
  for candidate in \
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale" \
    "/Applications/Tailscale.app/Contents/Macos/tailscale"; do
    if [[ -x "$candidate" ]]; then
      TAILSCALE_BIN="$candidate"
      return 0
    fi
  done
  return 1
}

tailscale_cli() {
  TAILSCALE_BE_CLI=1 "$TAILSCALE_BIN" "$@"
}

if [[ "$MODE" == "plan" ]]; then
  print_plan
  exit 0
fi

if ! resolve_tailscale_bin; then
  printf 'Tailscale CLI was not found. Install CLI integration or set TAILSCALE_BIN to the macOS app binary.\n' >&2
  exit 2
fi

if [[ "$MODE" == "status" ]]; then
  tailscale_cli serve status
  exit $?
fi

if [[ "$MODE" == "disable" ]]; then
  printf 'Resetting the current Tailscale Serve configuration on this host.\n'
  tailscale_cli serve reset
  exit $?
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
tailscale_cli serve --bg --https="${TAILSCALE_HTTPS_PORT}" "${TAILSCALE_TARGET}"
tailscale_cli serve status
