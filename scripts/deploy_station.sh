#!/usr/bin/env bash
# 把筆電的工作樹部署到常駐機，並記下「送出去的是哪一份」。
#
# 這支腳本是**唯一**該用來更新常駐機的東西。手打 rsync 會漏掉三件事而沒人發現：
# `record-deploy.sh` 需要的來源 revision、`code/國考題資料夾` 那個掛載點，
# 以及——最要緊的——**審核紀錄要從 `--delete` 的範圍裡排掉**。
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

# 常駐機上的人工紀錄。**這些永遠不可以被 rsync 刪掉或蓋掉**，所以它們同時是：
#   (1) rsync 的 --exclude 清單， (2) 部署前備份的清單。
#
# 為什麼要有這份清單而不是只寫 `--exclude='question_review_events.jsonl'`：
# 管線認得 **六個** 事件流（見 `build_review_queue.py::review_events_to_carry`），
# 而第一版只排除了其中一個。其餘五個（答案審核、AI 審核／回饋／學習、修正回饋）
# 只要在常駐機上產生過，重建後帶 `--delete` 的 rsync 就會把它們刪掉——而且是靜默刪掉，
# 因為 rsync 刪一個「筆電沒有」的檔案不會有任何輸出。
EVENT_STREAMS=(
  question_review_events.jsonl
  answer_review_events.jsonl
  question_ai_review_events.jsonl
  question_ai_feedback_events.jsonl
  question_ai_learning_events.jsonl
  question_correction_feedback_events.jsonl
  # AI 對「人已標 block」的題目所寫的筆記（哪裡錯、怎麼修）。它不是人類決定，但同樣是
  # 無法從語料重建的資料——它記的是模型看過某一次特定讀法之後說了什麼，而那些讀法正是
  # 沒人能一眼分類的個案。保護它的理由與保護人類紀錄完全相同：一次同步或一次重建
  # 把它靜默刪掉，就沒有第二次。這個名字與 `qbr/src/qbr/ai_findings.py` 的 `STREAM` 同一個。
  question_ai_findings.jsonl
)
# 還有人類的偏好設定與任何非事件檔的人工產物。
PROTECTED_EXTRA=(review_ui_preferences.json)
# 站上獨有的設定檔。**這曾經被這支腳本的 `--delete` 刪掉過。** 筆電的 `.gitignore:31`
# 忽略了 `deploy/qbr-review/.env`，所以筆電根本沒有它，因此 rsync 把站上的那一份當成
# 「目標多出來的檔」刪除——而那是**唯一**記載站上埠號（8765 而非 8774）與絕對路徑的地方。
# 它一不見，watchdog 下次重建容器就會掉回 compose.yaml 的預設值，服務從 8765 換到 8774，
# 而使用者的書籤與已安裝的 PWA 就斷了。已於 2026-09-21 重建。
PROTECTED_CONFIG=(.env)

DO_QUEUE=0
DO_RESTART=0
for arg in "$@"; do
  case "${arg}" in
    --queue) DO_QUEUE=1 ;;
    --restart) DO_RESTART=1 ;;
    -h|--help) sed -n '2,16p' "${BASH_SOURCE[0]}"; exit 0 ;;
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
  --exclude='deploy/qbr-review/.env' \
  ./ "${STATION}:qbr-review/code/"
echo "  程式碼已同步（站上獨有的 deploy/qbr-review/.env 已排除在刪除範圍外）"

# 2. 掛載點。compose 把語料掛在唯讀的 /workspace 裡面，Docker 建不出那個目錄，
#    所以來源樹裡必須先有它（否則容器以 read-only file system 失敗）。
ssh -n -o BatchMode=yes "${STATION}" "mkdir -p ${REMOTE_HOME}/code/國考題資料夾 ${REMOTE_HOME}/logs"

# 3. 佇列（選擇性）。crops 是佇列自帶的，所以要整棵一起來。
#
# **人工紀錄先備份，再同步，而且從 --delete 的範圍排除。** 順序是刻意的：
# 先留一份帶時間戳的離線副本，接著 rsync 才動到那個目錄。
# 備份在常駐機上做（`~/qbr-review/backups/`），因為那是紀錄的家；
# 就算這支腳本後面失敗，備份仍然在。
if [[ "${DO_QUEUE}" == 1 ]]; then
  PROTECT_ARGS=()
  for name in "${EVENT_STREAMS[@]}" "${PROTECTED_EXTRA[@]}" "${PROTECTED_CONFIG[@]}"; do
    PROTECT_ARGS+=(--exclude="${name}")
  done

  echo "  先備份常駐機上的人工紀錄…"
  # 注意這裡**沒有 `-n`**。`ssh -n` 把 stdin 接到 `/dev/null`，於是下面的 heredoc 根本沒送到常駐機，
  # 備份指令不會執行、也不會報錯——一個靜默失敗的備份。上一版就是這樣：它印了「先備份」四個字，
  # 實際什麼都沒做。`-n` 在其他地方是要的（避免 ssh 把迴圈的 stdin 吃走），這裡它正好相反。
  ssh -o BatchMode=yes "${STATION}" "bash -s" <<'REMOTE_BACKUP'
set -e
Q="${HOME}/qbr-review/queue/review-ui"
D="${HOME}/qbr-review/backups"
mkdir -p "${D}"
T="$(date +%Y%m%d-%H%M%S)"
found=0
for name in question_review_events.jsonl answer_review_events.jsonl \
            question_ai_review_events.jsonl question_ai_feedback_events.jsonl \
            question_ai_learning_events.jsonl question_correction_feedback_events.jsonl \
            question_ai_findings.jsonl \
            review_ui_preferences.json; do
  [ -f "${Q}/${name}" ] || continue
  cp "${Q}/${name}" "${D}/${name}.${T}.bak"
  found=$((found + 1))
done
if [ "${found}" -gt 0 ]; then
  cp "${Q}/question_review_events.jsonl" "${D}/question_review_events.latest.jsonl" 2>/dev/null || true
  echo "    已備份 ${found} 個人工作業檔 → ${D}/*.${T}.bak"
else
  echo "    （常駐機上還沒有人工作業檔）"
fi
REMOTE_BACKUP

  rsync -a --delete "${PROTECT_ARGS[@]}" \
    "${CATALOG}/qbr/data/review-queues/live/review-ui/" \
    "${STATION}:qbr-review/queue/review-ui/"

  # 同步後立刻驗：紀錄還在不在、行數有沒有變少。
  # 「rsync 成功」只證明指令跑完，不證明人類決定還在——所以量的是行數。
  AFTER="$(ssh -n -o BatchMode=yes "${STATION}" "wc -l < ${REMOTE_HOME}/queue/review-ui/question_review_events.jsonl 2>/dev/null || echo 0" | tr -d ' ')"
  echo "  佇列已同步；常駐機審核紀錄 ${AFTER} 筆（${#EVENT_STREAMS[@]} 個事件流＋偏好設定都已排除在刪除範圍外）"
fi

# 4. 記下 provenance。用遠端環境變數把來源帶過去，讓 record-deploy.sh 不必猜。
ssh -n -o BatchMode=yes "${STATION}" \
  "cd ${REMOTE_HOME}/code && QBR_SRC_REPO='${CATALOG}' QBR_SRC_HEAD='${SRC_HEAD}' QBR_SRC_DIRTY='${SRC_DIRTY}' bash deploy/qbr-review/record-deploy.sh >/dev/null && echo '  已記錄 DEPLOYED.json'"

# 5. 重啟（選擇性）。不重啟就不會生效——鏡射檔案不會讓跑著的容器換程式。
if [[ "${DO_RESTART}" == 1 ]]; then
  ssh -n -o BatchMode=yes "${STATION}" "cd ${REMOTE_HOME}/code && bash deploy/qbr-review/up.sh 2>&1 | tail -8"
fi
