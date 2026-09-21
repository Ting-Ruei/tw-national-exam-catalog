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
LOCAL_DIR="${CATALOG}/qbr/data/review-queues/live/review-ui"

# 要推回常駐機的事件流。兩個都是**無法從語料重建**的資料，所以走同一套規則、同一套檢查。
#
#   question_review_events.jsonl  人的決定（最貴的東西）
#   question_ai_findings.jsonl    模型對「人已 block」的題目寫的筆記（哪裡錯、怎麼修）
#
# 第二個名字與 `qbr/src/qbr/ai_findings.py` 的 `STREAM` 同一個。它不是人的決定，但同樣
# 記的是「模型看過某一次特定讀法之後說了什麼」，而那些讀法正是沒人能一眼分類的個案——
# 一次同步或重建把它靜默刪掉，就沒有第二次。這支腳本原本只推第一個，於是我在筆電跑出來的
# 71 筆 finding **只存在筆電上**：一個沒有人搬的 store 會消失。
STREAMS=(
  question_review_events.jsonl
  question_ai_findings.jsonl
)

DRY_RUN=0
for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,26p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "未知參數：${arg}" >&2; exit 2 ;;
  esac
done

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

# 把一個事件流推回常駐機。回傳 0 = 成功，1 = 拒絕（不確定的時候不要寫）。
push_stream() {
  local name="$1"
  local remote_log="${REMOTE_DIR}/${name}"
  local local_log="${LOCAL_DIR}/${name}"

  echo ""
  echo "── ${name}"

  if [[ ! -f "${local_log}" ]]; then
    echo "   筆電上沒有這個檔——沒有東西可以推（先跑 scripts/pull_station_reviews.sh）"
    return 0
  fi

  # 1. 常駐機活著嗎，以及它現在幾行。
  #
  # 回傳格式是 `exists:count`，因為「**檔案不存在**（所以 0 行）」與「**檔案存在但只有 0 行**」
  # 是不同的狀態。原本只看行數，所以第一次推一個全新的流就被自己的守衛擋下來：站上 0 行、筆電 71 行，
  # 守衛說「站上是家，不該比副本少」——對人的決定日誌來說那個守衛是對的，但對一個還沒上站過的
  # 新流來說，站上 0 行正是正確的起始狀態。分不清楚就會把「第一次」永久擋住。
  # 注意 `\$(wc …)` 要跳脫：這整串是 ssh 的引數，不跳脫的話 `$()` 會在**本機**先展開，
  # 於是本機去找常駐機的路徑（實測：`No such file or directory`），而 `if` 判斷還照樣回 ok:0。
  local remote_state remote_exists remote_count
  remote_state="$(ssh -n -o BatchMode=yes -o ConnectTimeout=8 "${STATION}" \
    "if [ -f ${remote_log} ]; then echo yes:\$(wc -l < ${remote_log}); else echo no:0; fi" 2>/dev/null \
    | tr -d ' ' || true)"
  if [[ -z "${remote_state}" ]]; then
    echo "   連不上常駐機 ${STATION}，或讀不到 ${remote_log}。" >&2
    echo "   （什麼都不做——不確定的時候不要寫無法重建的紀錄）" >&2
    return 1
  fi
  remote_exists="${remote_state%%:*}"
  remote_count="${remote_state##*:}"
  local_count="$(wc -l < "${local_log}" | tr -d ' ')"

  if [[ "${remote_exists}" == "no" ]]; then
    echo "   常駐機 ${STATION}：還沒有這個流（第一次推）"
  else
    echo "   常駐機 ${STATION}：${remote_count} 行"
  fi
  echo "   筆電            ：${local_count} 行"

  # 2. 家裡的東西變少了嗎。只在**檔案已經存在**且比副本少時才會發生，而那正是要停下來看的事。
  #    「檔案不存在」不是變少，是還沒有。（這兩個案例都真實發生過：人類日誌 347→346 是真的變少；
  #    模型筆記 0 行是還沒上站。同一個守衛要能分出來。）
  if [[ "${remote_exists}" == "yes" ]] && [[ "${remote_count}" -lt "${local_count}" ]] && [[ "${DRY_RUN}" == 0 ]]; then
    echo "   拒絕推送：常駐機（${remote_count}）比筆電（${local_count}）還少。" >&2
    echo "     站上是家，它不該比副本少。先查清楚少了什麼再繼續。" >&2
    return 1
  fi

  # 3. 算出「站上還沒有的**事件**」，寫到暫存檔。
  #
  # **不能用整行比對**——我第一版就是那樣寫，而它錯了：合併佇列時管線會在事件上補
  # `_carried_from` 欄位，所以同一個事件在兩邊是**不同的字串**。實測：站上 347 行、筆電 346 行，
  # 而整行比對會說「要附加 151 行」——把 151 個已經有人做的決定再寫一次。附加重複事件不是無害的：
  # 它會讓「誰審了什麼」的帳多算一次，而審核紀錄的帳就是它的全部意義。
  #
  # **第二個錯：不能用 `$(...)` 接結果。** 命令替換會吃掉結尾的換行，所以附加的最後一行會沒有換行。
  # 寫到暫存檔就沒有這個問題。
  local tmp added_file added_count
  tmp="$(mktemp -t qbr-push.XXXXXX)"
  added_file="$(mktemp -t qbr-added.XXXXXX)"
  ssh -n -o BatchMode=yes "${STATION}" \
    "if [ -f ${remote_log} ]; then cat ${remote_log}; fi" > "${tmp}"
  "${PYTHON}" "${MERGE_PY}" "${tmp}" "${local_log}" "${added_file}"
  added_count="$(wc -l < "${added_file}" | tr -d ' ')"
  echo "   要附加的 ：${added_count} 行（以事件身分比對，已排除 _carried_from）"

  if [[ "${added_count}" == "0" ]]; then
    echo "   站上已經有筆電的每一個事件——沒有東西要推。"
    rm -f "${tmp}" "${added_file}"
    return 0
  fi

  if [[ "${DRY_RUN}" == 1 ]]; then
    echo "   （dry-run）會把這 ${added_count} 行附加到 ${remote_log}"
    head -5 "${added_file}"
    [[ "${added_count}" -gt 5 ]] && echo "     …（其餘 $((added_count - 5)) 行）"
    rm -f "${tmp}" "${added_file}"
    return 0
  fi

  # 4. 附加前先留備份，備份在**常駐機上**做——那才是紀錄的家，而且萬一這支腳本後面失敗，
  #    備份仍然在。順序是刻意的：先備份，後寫入。
  local stamp
  stamp="$(date +%Y%m%d-%H%M%S)"
  ssh -o BatchMode=yes "${STATION}" "bash -s" <<REMOTE_BACKUP
set -e
mkdir -p "\${HOME}/qbr-review/backups"
if [ -f "${remote_log}" ]; then
  cp "${remote_log}" "\${HOME}/qbr-review/backups/${name}.pre-push-${stamp}.jsonl"
  echo "      已備份 → ~/qbr-review/backups/${name}.pre-push-${stamp}.jsonl"
else
  echo "      （站上還沒有這個檔，沒有東西要備份）"
fi
REMOTE_BACKUP

  # 5. 附加。用 `>>` 而不是 scp：scp 會截斷，而截斷就是刪掉無法重建的紀錄。
  ssh -o BatchMode=yes "${STATION}" "cat >> ${remote_log}" < "${added_file}"
  rm -f "${tmp}" "${added_file}"

  # 6. 驗行數，不是驗指令成功。`ssh` 回 0 只證明連線沒斷。
  local after expected
  after="$(ssh -n -o BatchMode=yes "${STATION}" "wc -l < ${remote_log}" | tr -d ' ')"
  expected="$((remote_count + added_count))"
  if [[ "${after}" != "${expected}" ]]; then
    echo "   推送後行數 ${after} ≠ 預期 ${expected}——請看備份檔。" >&2
    return 1
  fi
  echo "   已推回：${remote_count} → ${after} 行（附加 ${added_count}）"
  return 0
}

FAILED=0
for stream in "${STREAMS[@]}"; do
  push_stream "${stream}" || FAILED=1
done

echo ""
if [[ "${FAILED}" != 0 ]]; then
  echo "有事件流沒有推成功——看上面的訊息。" >&2
  exit 1
fi
echo "全部事件流都已處理。伺服器會在偵測到檔案變動時自行重載（serve_question_review_ui.py:2560），不必重啟。"
