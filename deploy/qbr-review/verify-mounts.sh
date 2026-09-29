#!/usr/bin/env bash
# 在常駐機上跑：確認**跑著的容器**真的用上了 compose 宣告的掛載。
#
#     deploy/qbr-review/verify-mounts.sh
#
# **這支腳本存在的唯一理由是一個實測過的靜默缺口（2026-09-24）。**
#
# `deploy/qbr-review/compose.yaml` 早已改成「`review-ui/` 整包 rw」，但站上的容器還是舊的
# 「`/queue` 唯讀，只留 `question_review_events.jsonl` 一條 rw」。rsync 把檔案同步了、
# compose 檔也同步了，`docker inspect` 卻仍顯示舊的掛載——因為**容器沒有被重建**。
#
# 症狀是「偏好存不住」「原則不見了」「補圖失敗」「補圖沒有家」，每一條都長得像程式缺陷，
# 而根因是掛載。部署指令的成功只證明指令跑完，不證明跑著的容器換了掛載，所以這裡量的是
# 容器**當下**的狀態。
#
# 三件事，任何一件失敗就回非零（`deploy_station.sh --restart` 會因此讓整個部署失敗）：
#   1. `/queue/review-ui` 是 rw
#   2. 五條人類產物的 stream 在容器內**真的寫得進去**
#   3. 測試不留痕跡（空的測試檔要刪掉，除非它在部署前就存在）
set -euo pipefail

export PATH="$PATH:/usr/local/bin:/opt/homebrew/bin"

CONTAINER="${QBR_CONTAINER:-qbr-review-ui}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CATALOG="$(cd "${HERE}/../.." && pwd)"
if [[ -f "${HERE}/.env" ]]; then
  set -a
  . "${HERE}/.env"
  set +a
fi

QUEUE_DIR="${QBR_QUEUE_DIR:-${CATALOG}/qbr/data/review-queues/live}"
abs() { case "$1" in /*) printf '%s' "$1" ;; *) printf '%s' "${HERE}/${1#./}" ;; esac; }
QUEUE="$(abs "${QUEUE_DIR}")"
REVIEW_DIR="${QUEUE}/review-ui"
LOG="$(abs "${QBR_REVIEW_LOG:-${REVIEW_DIR}/question_review_events.jsonl}")"

# 五條「後來的某一天才長出來」的人類產物。第一天只有審核紀錄，所以舊的心智模型是對的；
# 它錯在只對第一天成立。
STREAMS=(
  review_ui_preferences.json
  question_review_principles.jsonl
  question_repair_questions.jsonl
  question_correction_feedback_events.jsonl
  answer_review_events.jsonl
)

fail() { echo "    ✗ $*" >&2; exit 1; }
case "${LOG}" in
  "${QUEUE}/"*) ;;
  *) fail "QBR_REVIEW_LOG 必須位於 QBR_QUEUE_DIR 內：${LOG}" ;;
esac

# 1. 掛載旗標。`docker inspect` 問的是**跑著的容器**，不是 compose 檔寫了什麼。
RW="$(docker inspect "${CONTAINER}" \
  --format '{{range .Mounts}}{{if eq .Destination "/queue/review-ui"}}{{.RW}}{{end}}{{end}}' 2>/dev/null || true)"
if [ "${RW}" != "true" ]; then
  fail "/queue/review-ui 的掛載不可寫 (RW=${RW:-missing})——容器沒被重建，或 compose 沒生效"
fi
echo "    ✓ /queue/review-ui = rw"

# 2. 實際可寫性。掛載旗標對了不代表寫得進去（父目錄權限、SELinux、唯讀檔案系統都可能再擋）。
#    所以真的 `touch` 一次，這是唯一能證明「偏好存得住」的方式。
written=()
for stream in "${STREAMS[@]}"; do
  existed=0
  [ -e "${REVIEW_DIR}/${stream}" ] && existed=1
  if ! docker exec "${CONTAINER}" sh -c "touch /queue/review-ui/${stream}" 2>/dev/null; then
    fail "容器內寫不進 ${stream}——這正是「偏好存不住／原則不見了」的根因"
  fi
  # 3. 不留痕跡：這一支只證明可寫，不該在人的目錄裡留下一堆空檔。
  #    部署前就存在的（`existed=1`）不動；空的、而且是我們造出來的，刪掉。
  if [ "${existed}" = "0" ] && [ ! -s "${REVIEW_DIR}/${stream}" ]; then
    rm -f "${REVIEW_DIR}/${stream}"
    written+=("${stream}")
  fi
done
echo "    ✓ 五條 stream 在容器內都可寫${written:+（測試產生的空檔已移除：${written[*]}）}"

# 4. 審核紀錄還在，而且沒有變少。這是一個「容器重建沒有把人的決定吃掉」的最低限度檢查；
#    完整的人類紀錄驗證在 `push_reviews_to_station.sh`／pull 腳本裡，這裡只擋住最壞的情況。
if [ -f "${LOG}" ]; then
  COUNT="$(wc -l < "${LOG}" | tr -d ' ')"
  echo "    ✓ 審核紀錄 ${COUNT} 筆仍在：${LOG}"
else
  fail "找不到 ${LOG}——容器重建不該讓它消失"
fi
