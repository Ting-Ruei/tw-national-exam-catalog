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
# Tailscale name by default; `QBR_STATION=192.168.10.70` pins the LAN address.
STATION="${QBR_STATION:-timmac-studio}"
REMOTE_DIR="/Users/tim/qbr-review/queue/review-ui"
LOCAL_DIR="${CATALOG}/qbr/data/review-queues/live/review-ui"

# 兩條流，理由不同但方向相同：
#
# * `question_review_events.jsonl` 是人類決定本體——唯一不可重建的東西。
# * `question_review_principles.jsonl` 是人在討論區寫下的**基本原則**，也就是提示詞的約束。
#   它拉回來的理由與決定相同：筆電上的副本會**少報**。實測 2026-09-24——第一輪的策展是在
#   筆電上跑的，那條原則寫進了筆電的流，而站上（真正跑迴圈、真正讀進提示詞的那台）
#   一個字都沒有；`deploy_station.sh` 因為把它列進 `EVENT_STREAMS`，只保護不搬運。
#   不拉回來，筆電就會一直拿著一份比站上舊的清單，而看起來像「這就是全部的原則」。
STREAMS=(
  "question_review_events.jsonl"
  "question_review_principles.jsonl"
)

DRY_RUN=0
for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "未知參數：${arg}" >&2; exit 2 ;;
  esac
done

# 站上幾筆（也順便確認站是活的——不可達時不要靜靜地什麼都不做）。
# 兩條流一起數：任一條讀不到就是連不上或不完整，寧可不拉。
REMOTE_LINES="$(ssh -n -o BatchMode=yes -o ConnectTimeout=8 "${STATION}" \
  "cd ${REMOTE_DIR} && wc -l ${STREAMS[*]}" 2>/dev/null || true)"
if [[ -z "${REMOTE_LINES}" ]]; then
  echo "連不上常駐機 ${STATION}，或讀不到 ${REMOTE_DIR} 下的流" >&2
  echo "（筆電上的紀錄保持不動——寧可不拉，也不要拿一個不知道是什麼的檔案蓋掉它）" >&2
  exit 1
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
STALE=()
for name in "${STREAMS[@]}"; do
  remote_count="$(awk -v n="${name}" '$2 == n {print $1}' <<<"${REMOTE_LINES}")"
  if [[ -z "${remote_count}" ]]; then
    echo "站上沒有 ${name}——不拉（空檔案會把筆電的蓋掉）。" >&2
    exit 1
  fi
  local_path="${LOCAL_DIR}/${name}"
  local_count=0
  [[ -f "${local_path}" ]] && local_count="$(wc -l < "${local_path}" | tr -d ' ')"
  printf '%-42s 站上 %6s 筆 · 筆電 %6s 筆\n' "${name}" "${remote_count}" "${local_count}"
  [[ "${remote_count}" == "${local_count}" ]] || STALE+=("${name}:${remote_count}:${local_count}")
done

if [[ "${#STALE[@]}" == 0 ]]; then
  echo "兩條流的筆數都相同——沒有新資料，什麼都不做。"
  exit 0
fi

if [[ "${DRY_RUN}" == 1 ]]; then
  echo "（dry-run）會覆蓋：${STALE[*]}"
  exit 0
fi

for entry in "${STALE[@]}"; do
  IFS=: read -r name remote_count local_count <<<"${entry}"
  local_path="${LOCAL_DIR}/${name}"
  # 覆蓋前先備份：這是人寫的東西，不是產物。
  if [[ -f "${local_path}" ]]; then
    cp "${local_path}" "${local_path%.jsonl}.pre-pull-${STAMP}.jsonl"
    echo "已備份筆電舊紀錄 → ${local_path%.jsonl}.pre-pull-${STAMP}.jsonl"
  fi
  scp -q "${STATION}:${REMOTE_DIR}/${name}" "${local_path}"
  after="$(wc -l < "${local_path}" | tr -d ' ')"
  if [[ "${after}" != "${remote_count}" ]]; then
    echo "拉回後 ${name} 筆數 ${after} ≠ 站上 ${remote_count}——拉回不完整。" >&2
    exit 1
  fi
  echo "已拉回 ${name}：${local_count} → ${after} 筆"
done
