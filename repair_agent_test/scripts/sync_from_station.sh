#!/usr/bin/env bash
# 把常駐機（Mac Studio）的**最新狀態**撈回筆電 —— 每個 session 開工前先跑這一支。
#
# 為什麼要有這一支：筆電的 `qbr/data/review-queues/live/` 是一份**會過期的快照**，
# 常駐機才是家。2026-09-27 實際發生：筆電的 `candidates.jsonl` 停在 2026-09-24（修復前），
# 站上已是 2026-09-26（修復後）——**跨頁截圖 699 張頁頂 vs 2 張**。在筆電上量，
# 量到的是兩天前的世界，差一點把一個**已經修好的 bug** 當成還開著報出去（qa-log Q13）。
#
# 方向刻意是**單向**（站上 → 筆電）：筆電永遠不當 review event 的 writer。
# 人工決定（`question_review_events.jsonl`）由 `scripts/pull_station_reviews.sh` 負責，
# 那支有備份與驗筆數；本支負責**其餘的 live 狀態**：題目佇列、AI 讀法、圖歸屬、crops 清單。
#
#     repair_agent_test/scripts/sync_from_station.sh            # 撈回（預設）
#     repair_agent_test/scripts/sync_from_station.sh --dry-run  # 只看會發生什麼
#     repair_agent_test/scripts/sync_from_station.sh --status    # 只比對時間與筆數
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SANDBOX="$(cd "${HERE}/.." && pwd)"
CATALOG="$(cd "${SANDBOX}/.." && pwd)"
# Tailscale name by default; `QBR_STATION=192.168.10.70` pins the LAN address.
STATION="${QBR_STATION:-timmac-studio}"
SSH_KEY="${QBR_SSH_KEY:-$HOME/.ssh/ai_learning_platform_deploy}"
REMOTE="/Users/tim/qbr-review/queue/review-ui"
LOCAL="${CATALOG}/qbr/data/review-queues/live/review-ui"

# 本支負責的檔案。人工事件流**不在這裡**（由 pull_station_reviews.sh 負責，它有備份與驗筆數）。
STREAMS=(
  candidates.jsonl
  figure_ownership.json
  question_ai_findings.jsonl
  question_repair_questions.jsonl
)

MODE="pull"
for arg in "$@"; do
  case "${arg}" in
    --dry-run) MODE="dry" ;;
    --status)  MODE="status" ;;
    *) echo "unknown argument: ${arg}" >&2; exit 2 ;;
  esac
done

remote_stat() {
  ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=no -o BatchMode=yes "tim@${STATION}" \
    "for f in ${STREAMS[*]}; do \
       if [ -f ${REMOTE}/\$f ]; then \
         printf '%s\t%s\t%s\n' \"\$f\" \"\$(stat -f %Sm -t '%Y-%m-%d %H:%M' ${REMOTE}/\$f)\" \"\$(wc -l < ${REMOTE}/\$f | tr -d ' ')\"; \
       else printf '%s\tMISSING\t0\n' \"\$f\"; fi; \
     done" 2>/dev/null
}

local_stat() {
  for f in "${STREAMS[@]}"; do
    if [ -f "${LOCAL}/${f}" ]; then
      printf '%s\t%s\t%s\n' "${f}" "$(stat -f "%Sm" -t "%Y-%m-%d %H:%M" "${LOCAL}/${f}")" "$(wc -l < "${LOCAL}/${f}" | tr -d ' ')"
    else
      printf '%s\tMISSING\t0\n' "${f}"
    fi
  done
}

echo "=== 站上（${STATION}）==="
remote_stat
echo "=== 筆電 ==="
local_stat

if [ "${MODE}" = "status" ]; then
  exit 0
fi

if [ "${MODE}" = "dry" ]; then
  echo "(dry-run：不搬檔)"
  exit 0
fi

echo
echo "=== 撈回 ==="
for f in "${STREAMS[@]}"; do
  if ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=no -o BatchMode=yes "tim@${STATION}" \
       "test -f ${REMOTE}/${f}" 2>/dev/null; then
    scp -q -i "${SSH_KEY}" -o StrictHostKeyChecking=no \
      "tim@${STATION}:${REMOTE}/${f}" "${LOCAL}/${f}"
    echo "  撈回 ${f}"
  else
    echo "  跳過 ${f}（站上沒有）"
  fi
done

echo
echo "=== 人工決定流（separate script, has backup + count check）==="
bash "${CATALOG}/scripts/pull_station_reviews.sh" 2>&1 | tail -4

echo
echo "=== 撈回後 ==="
local_stat
