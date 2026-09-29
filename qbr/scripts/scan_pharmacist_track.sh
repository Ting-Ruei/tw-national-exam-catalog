#!/usr/bin/env bash
# Scoped local category reread; requires an explicitly selected queue.
#
# The previous launcher sourced a remote LiteLLM secret file, called a retired DGX lane, ran three
# unbounded jobs concurrently, and swallowed child failures. Keep this manual utility local and
# sequential. A queue and a task-approved scope/budget must be supplied by the operator.
set -euo pipefail

QBR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${QBR_PYTHON:-${QBR}/.venv/bin/python}"
if [[ ! -x "${PY}" ]]; then
  echo "[scan] 找不到 Python：${PY}" >&2
  exit 3
fi
QUEUE="${QUEUE:?請設定當次明確核准的 QUEUE}"
WINDOW="${WINDOW:-1}"
if ! [[ "${WINDOW}" =~ ^[1-9][0-9]*$ ]]; then
  echo "[scan] WINDOW 必須是正整數" >&2
  exit 2
fi
RUNS="${RUNS:-${QBR}/runs}"
mkdir -p "${RUNS}"
STAMP="$(date +%Y%m%d-%H%M%S)"

lane() {
  local tag="$1"; shift
  local log="${RUNS}/scan-pharmacist-${tag}-${STAMP}.log"
  local rc=0
  if (cd "${QBR}" && "${PY}" scripts/scan_category_principles.py \
      --queue "${QUEUE}" --limit "${WINDOW}" --skip-confirmed --apply \
      --model mtplx-35b --category "$@") >>"${log}" 2>&1; then
    rc=0
  else
    rc=$?
  fi
  echo "[scan] ${tag} 結束 rc=${rc} $(date -u +%FT%TZ)" >>"${log}"
  return "${rc}"
}

echo "[scan] 本機順序處理，queue=${QUEUE} limit/category=${WINDOW}"
for lane_args in "yishi1|藥師(一)" "yishi2|藥師(二)" "yishi0|藥師"; do
  tag="${lane_args%%|*}"
  category="${lane_args#*|}"
  if lane "${tag}" "${category}"; then
    :
  else
    rc=$?
    echo "[scan] ${tag} 失敗（rc=${rc}）；停止後續 lane" >&2
    exit "${rc}"
  fi
done

(cd "${QBR}" && "${PY}" scripts/scan_category_principles.py \
  --queue "${QUEUE}" --category __pharmacist_track__ --report)
