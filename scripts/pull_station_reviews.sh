#!/usr/bin/env bash
# 把常駐機（TimsMac.lan:8765）上的人類審核決定拉回筆電。
#
# 為什麼需要這支腳本：審題介面的**家已經搬到常駐機**（`192.168.10.70:8765`），
# 因為筆電可以關掉帶走，而審核要能隨時進行。但人類決定是唯一不可重建的東西
# （`qbr/reports/two_review_stores.md` 就是在講這件事：兩個審核儲存就是兩個可以
# 不一致的地方）。所以「家」只能有一個，而筆電的角色是**消費者**：它把決定拉回來，
# 拿去做套件、報告與備份，**不自己寫審核紀錄**。
#
# 這支腳本是單向的：站上 → 筆電。反向（筆電 → 站上）刻意不做，因為那會讓筆電
# 變回第二個 writer——正是要避免的事。
#
#     scripts/pull_station_reviews.sh              # 拉回並驗證
#     scripts/pull_station_reviews.sh --dry-run    # 只看會發生什麼
#
# 兩個設計點：
#
# 1. **覆蓋前先留備份。** 筆電上的紀錄是上一次拉的結果，不是歷史；但「拉回」仍會
#    蓋掉它，而蓋掉人類決定是最不該出錯的一步。所以先複製一份帶時間戳的。
# 2. **拉回後驗筆數，不是驗指令成功。** scp 成功只證明檔案搬了，不證明內容對。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CATALOG="$(cd "${HERE}/.." && pwd)"
STATION="${QBR_STATION:-192.168.10.70}"
REMOTE_LOG="/Users/tim/qbr-review/queue/review-ui/question_review_events.jsonl"
LOCAL_LOG="${CATALOG}/qbr/data/review-queues/live/review-ui/question_review_events.jsonl"

DRY_RUN=0
for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "未知參數：${arg}" >&2; exit 2 ;;
  esac
done

# 站上幾筆（也順便確認站是活的——不可達時不要靜靜地什麼都不做）
REMOTE_COUNT="$(ssh -n -o BatchMode=yes -o ConnectTimeout=8 "${STATION}" \
  "wc -l < ${REMOTE_LOG}" 2>/dev/null | tr -d ' ' || true)"
if [[ -z "${REMOTE_COUNT}" ]]; then
  echo "連不上常駐機 ${STATION}，或讀不到 ${REMOTE_LOG}" >&2
  echo "（筆電上的紀錄保持不動——寧可不拉，也不要拿一個不知道是什麼的檔案蓋掉它）" >&2
  exit 1
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
echo "常駐機 ${STATION} 目前 ${REMOTE_COUNT} 筆"

if [[ -f "${LOCAL_LOG}" ]]; then
  LOCAL_COUNT="$(wc -l < "${LOCAL_LOG}" | tr -d ' ')"
  echo "筆電目前       ${LOCAL_COUNT} 筆"
else
  LOCAL_COUNT=0
  echo "筆電目前       沒有紀錄檔"
fi

if [[ "${REMOTE_COUNT}" == "${LOCAL_COUNT}" ]]; then
  echo "筆數相同——沒有新決定，什麼都不做。"
  exit 0
fi

if [[ "${DRY_RUN}" == 1 ]]; then
  echo "（dry-run）會拉回 ${REMOTE_COUNT} 筆並覆蓋筆電的 ${LOCAL_COUNT} 筆"
  exit 0
fi

# 覆蓋前先備份：這是人類決定，不是產物。
if [[ -f "${LOCAL_LOG}" ]]; then
  BACKUP="${LOCAL_LOG%.jsonl}.pre-pull-${STAMP}.jsonl"
  cp "${LOCAL_LOG}" "${BACKUP}"
  echo "已備份筆電舊紀錄 → ${BACKUP}"
fi

scp -q "${STATION}:${REMOTE_LOG}" "${LOCAL_LOG}"
AFTER="$(wc -l < "${LOCAL_LOG}" | tr -d ' ')"
if [[ "${AFTER}" != "${REMOTE_COUNT}" ]]; then
  echo "拉回後筆數 ${AFTER} ≠ 站上 ${REMOTE_COUNT}——拉回不完整。" >&2
  exit 1
fi
echo "已拉回 ${AFTER} 筆 → ${LOCAL_LOG}"
