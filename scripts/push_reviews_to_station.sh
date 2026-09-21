#!/usr/bin/env bash
# 把筆電上修過的人類審核決定推回常駐機（TimsMac.lan:8765）。
#
# 這是 `scripts/pull_station_reviews.sh` 的另一半，合起來是使用者要的那個工作迴圈：
#
#     scripts/pull_station_reviews.sh       # 從常駐機拉一份下來看
#     （在筆電上檢視／修正）
#     scripts/push_reviews_to_station.sh    # 把新的決定推回去
#
# ## 為什麼是「附加合併」而不是「覆蓋」
#
# 常駐機是審核紀錄的**家**；筆電是消費者。中間可能有人在站上繼續審（實驗室那台電腦就是），
# 所以直接 scp 覆蓋會把那期間的決定整批刪掉——而且是靜默刪掉。
#
# 這支腳本因此只做一件事：讀兩邊的行，把**站上還沒有的**那幾行附加到站上的檔案尾端。
# 站上已有的行一個位元組都不動，站上多出來的行也一個都不刪。
#
# 伺服器端的寫入是 `open("a")`（`serve_question_review_ui.py:3993`），而且它在檔案簽章
# 變動時會重新載入（同檔 `:2560`），所以「在服務跑著的時候附加」是它本來就支援的行為，
# 不需要停機。
#
# ## 它會拒絕的情況
#
#   * 連不上常駐機（不確定的事不要做）
#   * 站上的紀錄比上次拉回時**還少**（家裡的東西變少了，那不是我可以繼續寫的狀態）
#   * 站上的最後幾行不在筆電檔案裡，而且筆電檔案不是站上檔案的 superset
#
#     scripts/push_reviews_to_station.sh              # 推回（附加）
#     scripts/push_reviews_to_station.sh --dry-run    # 只看會附加幾行
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CATALOG="$(cd "${HERE}/.." && pwd)"
STATION="${QBR_STATION:-192.168.10.70}"
REMOTE_DIR="/Users/tim/qbr-review/queue/review-ui"
REMOTE_LOG="${REMOTE_DIR}/question_review_events.jsonl"
LOCAL_LOG="${CATALOG}/qbr/data/review-queues/live/review-ui/question_review_events.jsonl"

DRY_RUN=0
for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,26p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "未知參數：${arg}" >&2; exit 2 ;;
  esac
done

if [[ ! -f "${LOCAL_LOG}" ]]; then
  echo "筆電上沒有 ${LOCAL_LOG}——沒有東西可以推。" >&2
  echo "（先跑 scripts/pull_station_reviews.sh）" >&2
  exit 1
fi

# 1. 常駐機活著嗎，以及它現在幾行。
REMOTE_COUNT="$(ssh -n -o BatchMode=yes -o ConnectTimeout=8 "${STATION}" \
  "wc -l < ${REMOTE_LOG}" 2>/dev/null | tr -d ' ' || true)"
if [[ -z "${REMOTE_COUNT}" ]]; then
  echo "連不上常駐機 ${STATION}，或讀不到 ${REMOTE_LOG}。" >&2
  echo "（什麼都不做——不確定的時候不要寫人類的紀錄）" >&2
  exit 1
fi
LOCAL_COUNT="$(wc -l < "${LOCAL_LOG}" | tr -d ' ')"

echo "常駐機 ${STATION}：${REMOTE_COUNT} 行"
echo "筆電              ：${LOCAL_COUNT} 行"

# 2. 家裡的東西變少了嗎。這只有在有人從站上刪東西時才會發生，而那正是要停下來看的事。
if [[ "${REMOTE_COUNT}" -lt "${LOCAL_COUNT}" ]] && [[ "${DRY_RUN}" == 0 ]]; then
  echo "拒絕推送：常駐機的紀錄（${REMOTE_COUNT}）比筆電的（${LOCAL_COUNT}）還少。" >&2
  echo "  站上是家，它不該比副本少。先查清楚少了什麼再繼續。" >&2
  exit 1
fi

# 3. 算出「站上還沒有的**事件**」，寫到暫存檔。
#
# **不能用整行比對**——我第一版就是那樣寫，而它錯了：合併佇列時管線會在事件上補
# `_carried_from` 欄位（`"_carried_from": "qbr/data/review-queues/live"`），所以同一個
# 事件在兩邊是**不同的字串**。實測：站上 347 行、筆電 346 行，而整行比對會說「要附加 151 行」
# ——把 151 個已經有人做的決定再寫一次。附加重複事件不是無害的：它會讓「誰審了什麼」的帳
# 多算一次，而審核紀錄的帳就是它的全部意義。
#
# 身分要建在事件自己的欄位上，而且要**排除 `_carried_from`**：那是運輸資訊（這個事件
# 從哪個佇列被帶過來的），不是決定本身。兩邊對同一個決定有不同的運輸資訊是正常的。
#
# **第二個錯：不能用 `$(...)` 接結果。** 命令替換會吃掉結尾的換行，所以附加的最後一行
# 會沒有換行——事件內容其實對了，但 `wc -l` 少算一行，於是驗收會失敗（實測：真的失敗了），
# 而若沒驗收就會留下一個「下一行跟它黏在一起」的檔案。寫到暫存檔就沒有這個問題。
TMP="$(mktemp -t qbr-push.XXXXXX)"
ADDED_FILE="$(mktemp -t qbr-added.XXXXXX)"
trap 'rm -f "${TMP}" "${ADDED_FILE}"' EXIT
ssh -n -o BatchMode=yes "${STATION}" "cat ${REMOTE_LOG}" > "${TMP}"

# 事件身分的規則在 `qbr/scripts/merge_events.py`，它用的是管線自己的
# `review_queue.record_identity`——不是這裡再寫一份。
#
# 用 qbr 的 venv；它不在時退回系統 python3，因為這支腳本只讀 json，不需要 qbr 的任何依賴。
# 硬寫死直譯器會讓一台沒建 venv 的機器完全推不了紀錄，而那是最不該因為環境就失敗的一步。
MERGE_PY="${CATALOG}/qbr/scripts/merge_events.py"
if [[ -x "${CATALOG}/qbr/.venv/bin/python" ]]; then
  PYTHON="${CATALOG}/qbr/.venv/bin/python"
else
  PYTHON="$(command -v python3 || echo /usr/bin/python3)"
fi
"${PYTHON}" "${MERGE_PY}" "${TMP}" "${LOCAL_LOG}" "${ADDED_FILE}"
ADDED_COUNT="$(wc -l < "${ADDED_FILE}" | tr -d ' ')"
echo "要附加的      ：${ADDED_COUNT} 行（以事件身分比對，已排除 _carried_from）"

if [[ "${ADDED_COUNT}" == "0" ]]; then
  echo "站上已經有筆電的每一個事件——沒有東西要推。"
  exit 0
fi

if [[ "${DRY_RUN}" == 1 ]]; then
  echo "（dry-run）會把這 ${ADDED_COUNT} 行附加到 ${REMOTE_LOG}"
  head -5 "${ADDED_FILE}"
  [[ "${ADDED_COUNT}" -gt 5 ]] && echo "  …（其餘 $((ADDED_COUNT - 5)) 行）"
  exit 0
fi

# 4. 附加前先留備份，備份在**常駐機上**做——那才是紀錄的家，而且萬一這支腳本後面失敗，
#    備份仍然在。順序是刻意的：先備份，後寫入。
STAMP="$(date +%Y%m%d-%H%M%S)"
ssh -o BatchMode=yes "${STATION}" "bash -s" <<REMOTE_BACKUP
set -e
mkdir -p "\${HOME}/qbr-review/backups"
cp "${REMOTE_LOG}" "\${HOME}/qbr-review/backups/question_review_events.pre-push-${STAMP}.jsonl"
echo "    已備份 → ~/qbr-review/backups/question_review_events.pre-push-${STAMP}.jsonl"
REMOTE_BACKUP

# 5. 附加。用 `>>` 而不是 scp：scp 會截斷，而截斷就是刪掉人類的決定。
ssh -o BatchMode=yes "${STATION}" "cat >> ${REMOTE_LOG}" < "${ADDED_FILE}"

# 6. 驗行數，不是驗指令成功。`ssh` 回 0 只證明連線沒斷。
AFTER="$(ssh -n -o BatchMode=yes "${STATION}" "wc -l < ${REMOTE_LOG}" | tr -d ' ')"
EXPECTED="$((REMOTE_COUNT + ADDED_COUNT))"
if [[ "${AFTER}" != "${EXPECTED}" ]]; then
  echo "推送後行數 ${AFTER} ≠ 預期 ${EXPECTED}——請看備份檔。" >&2
  exit 1
fi
echo "已推回：${REMOTE_COUNT} → ${AFTER} 行（附加 ${ADDED_COUNT}）"
echo "伺服器會在偵測到檔案變動時自行重載（serve_question_review_ui.py:2560），不必重啟。"
