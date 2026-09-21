#!/usr/bin/env bash
# 把筆電的工作樹部署到常駐機，並記下「送出去的是哪一份」。
#
# 這支腳本是**唯一**該用來更新常駐機的東西。手打 rsync 會漏掉兩件事而沒人發現：
# `record-deploy.sh` 需要的來源 revision，以及 `code/國考題資料夾` 那個掛載點。
#
#     scripts/deploy_station.sh                     # 部署目前的程式碼（不含佇列與語料）
#     scripts/deploy_station.sh --queue             # 連佇列一起（重建佇列後用）
#     scripts/deploy_station.sh --restart           # 部署後重啟服務
#
# 它**不帶**語料（常駐機已有，且只留 by_official_catalog），也**不帶** `qbr/data/`（那是筆電的
# 產物，不是部署內容）。審核紀錄永遠不在 rsync 範圍內——它是常駐機的家，方向是反過來的
# （`scripts/pull_station_reviews.sh`）。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CATALOG="$(cd "${HERE}/.." && pwd)"
STATION="${QBR_STATION:-192.168.10.70}"
REMOTE_HOME="/Users/tim/qbr-review"

DO_QUEUE=0
DO_RESTART=0
for arg in "$@"; do
  case "${arg}" in
    --queue) DO_QUEUE=1 ;;
    --restart) DO_RESTART=1 ;;
    -h|--help) sed -n '2,14p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "未知參數：${arg}" >&2; exit 2 ;;
  esac
done

cd "${CATALOG}"

SRC_HEAD="$(git rev-parse HEAD)"
SRC_DIRTY="$(git status --porcelain | wc -l | tr -d ' ')"
echo "來源 ${CATALOG}"
echo "  HEAD  ${SRC_HEAD:0:12}$( [[ "${SRC_DIRTY}" -gt 0 ]] && echo "  +${SRC_DIRTY} 個未提交檔（工作樹直送，站上也許沒有等價 commit）" )"

# 1. 程式碼。排除語料／產物／版本控制／虛擬環境——那些都不是「部署內容」。
rsync -a --delete \
  --exclude='國考題資料夾*' --exclude='tmp/' --exclude='qbr/data/' \
  --exclude='.git/' --exclude='.venv/' --exclude='__pycache__/' \
  --exclude='.DS_Store' --exclude='*.pyc' \
  ./ "${STATION}:qbr-review/code/"
echo "  程式碼已同步"

# 2. 掛載點。compose 把語料掛在唯讀的 /workspace 裡面，Docker 建不出那個目錄，
#    所以來源樹裡必須先有它（否則容器以 read-only file system 失敗）。
ssh -n -o BatchMode=yes "${STATION}" "mkdir -p ${REMOTE_HOME}/code/國考題資料夾 ${REMOTE_HOME}/logs"

# 3. 佇列（選擇性）。crops 是佇列自帶的，所以要整棵一起來。
if [[ "${DO_QUEUE}" == 1 ]]; then
  rsync -a --delete --exclude='question_review_events.jsonl' \
    "${CATALOG}/qbr/data/review-queues/live/review-ui/" \
    "${STATION}:qbr-review/queue/review-ui/"
  echo "  佇列已同步（審核紀錄刻意排除——它是常駐機的家）"
fi

# 4. 記下 provenance。用遠端環境變數把來源帶過去，讓 record-deploy.sh 不必猜。
ssh -n -o BatchMode=yes "${STATION}" \
  "cd ${REMOTE_HOME}/code && QBR_SRC_REPO='${CATALOG}' QBR_SRC_HEAD='${SRC_HEAD}' QBR_SRC_DIRTY='${SRC_DIRTY}' bash deploy/qbr-review/record-deploy.sh >/dev/null && echo '  已記錄 DEPLOYED.json'"

# 5. 重啟（選擇性）。不重啟就不會生效——鏡射檔案不會讓跑著的容器換程式。
if [[ "${DO_RESTART}" == 1 ]]; then
  ssh -n -o BatchMode=yes "${STATION}" "cd ${REMOTE_HOME}/code && bash deploy/qbr-review/up.sh 2>&1 | tail -8"
fi
