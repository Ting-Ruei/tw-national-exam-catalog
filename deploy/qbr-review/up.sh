#!/usr/bin/env bash
# 啟動／更新新版題庫審題介面（Docker，開機自動起來）。
#
# 這是**唯一**需要記得的指令：
#
#     deploy/qbr-review/up.sh                 # 建立 image、準備佇列、起服務、自己驗一次
#     deploy/qbr-review/up.sh --rebuild       # 先從套件重跑佇列，再起（管線程式改過時用這個）
#
# 它做三件事，而且每一件都先檢查再動作：
#
#   1. **確認要服務的佇列真的存在且非空。** 一個 0 題的佇列會開出一個看起來壞掉的介面，
#      而「介面壞掉」與「佇列沒建好」是兩件事——這裡先把它們分開，並在標題就講清楚。
#   2. **把審核紀錄檔準備好。** 它是唯一可寫的東西，而 Docker 的 bind mount 若指向不存在的
#      路徑會建立一個**目錄**而不是檔案，之後 server 開檔就會失敗。所以先 touch 出來。
#   3. **起服務並自己量一次。** 「啟動成功」不是「畫得出來」，所以這支腳本最後會真的打
#      API 並看題數，數字不對就以非零結束。
#
# 所有變數展開都加大括號：全形字元緊接在變數名後面時，某些 locale 會把它吃進變數名，
# 這支腳本第一版就因此死在 `LOG…: unbound variable`（`scripts/review_run.sh` 也記過同一件事）。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CATALOG="$(cd "${HERE}/../.." && pwd)"
cd "${CATALOG}"

REBUILD=0
for arg in "$@"; do
  case "${arg}" in
    --rebuild) REBUILD=1 ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "未知參數：${arg}" >&2; exit 2 ;;
  esac
done

# `.env` 若存在，它的值優先於 compose.yaml 的預設；沒有的話用預設。
if [[ -f "${HERE}/.env" ]]; then
  set -a; . "${HERE}/.env"; set +a
fi

QUEUE_DIR="${QBR_QUEUE_DIR:-${CATALOG}/qbr/data/review-queues/live}"
PORT="${REVIEW_UI_PORT:-8774}"
BIND="${REVIEW_UI_BIND:-0.0.0.0}"

# 相對路徑一律解成絕對路徑，因為後面要用它做檔案檢查。基準是 **compose 檔所在目錄**，
# 不是 catalog 根目錄：docker compose 自己的 bind mount 就是這樣解析相對路徑的，
# 兩邊不一致就會出現「腳本檢查 A、服務真的掛 B」的假通過。
abs() { case "$1" in /*) printf '%s' "$1" ;; *) printf '%s' "${HERE}/${1#./}" ;; esac; }
QUEUE="$(abs "${QUEUE_DIR}")"
LOG="$(abs "${QBR_REVIEW_LOG:-${QUEUE}/review-ui/question_review_events.jsonl}")"

# ── 0. 找到 docker ─────────────────────────────────────────────────────
# **非互動式 ssh 沒有 /usr/local/bin 在 PATH 裡。** 實測：`ssh host 'bash up.sh'` 得到的
# PATH 只有 `/usr/bin:/bin:/usr/sbin:/sbin`，於是 `docker: command not found`——而這正是
# `scripts/deploy_station.sh --restart` 走的路，所以部署會回報成功但服務沒有重啟。
# `ensure-up.sh` 早就用這個寫法解決同一件事；這裡跟它一致（一條規則，一個地方）。
DOCKER="$(command -v docker || echo /usr/local/bin/docker)"

# ── 1. 佇列 ────────────────────────────────────────────────────────────────
CANDIDATES="${QUEUE}/review-ui/candidates.jsonl"
if [[ ! -f "${CANDIDATES}" ]]; then
  echo "佇列不存在：${CANDIDATES}" >&2
  echo "先用 --rebuild，或先建佇列：" >&2
  echo "  cd qbr && .venv/bin/python scripts/build_review_queue.py --work data/runs/<...> --out ${QUEUE}" >&2
  exit 1
fi

if [[ "${REBUILD}" == 1 ]]; then
  echo "== 重跑佇列（用目前的管線程式碼）=="
  RUNS=( "${CATALOG}"/qbr/data/runs/20260921-expansion/* )
  if [[ ! -d "${RUNS[0]}" ]]; then
    echo "找不到套件來源 ${CATALOG}/qbr/data/runs/20260921-expansion/*" >&2
    exit 1
  fi
  # `--carry-from` 指向**佇列根目錄**（不是它的 review-ui/），否則審核紀錄不會被帶過來。
  ( cd "${CATALOG}/qbr" && .venv/bin/python scripts/build_review_queue.py \
      --work "${RUNS[@]}" --out "${QUEUE}" --carry-from "${QUEUE}" )
fi

COUNT="$(wc -l < "${CANDIDATES}" | tr -d ' ')"
if [[ "${COUNT}" -eq 0 ]]; then
  echo "${CANDIDATES} 有 0 題——這個佇列沒建好，介面會開出空的。" >&2
  exit 1
fi

# ── 2. 審核紀錄 ────────────────────────────────────────────────────────────
mkdir -p "$(dirname "${LOG}")"
touch "${LOG}"
RECORDS="$(wc -l < "${LOG}" | tr -d ' ')"

# ── 3. 起服務 ──────────────────────────────────────────────────────────────
# 匯出**絕對**路徑，否則 `.env` 裡的相對路徑會被 compose 以自己的目錄為基準再解一次，
# 而這支腳本剛剛才用同一個基準檢查過它——兩者必須是同一個字串才算真的檢查過。
export QBR_QUEUE_DIR="${QUEUE}" QBR_REVIEW_LOG="${LOG}"

echo "== 建立 image =="
"${DOCKER}" compose -f "${HERE}/compose.yaml" build

echo "== 起服務 =="
# `up -d` 對一個已經在跑的容器**什麼都不做**（它說 `Container qbr-review-ui Running`），
# 而這個容器跑的是 Python——**程式碼是 bind mount 進去的，但已經載入的模組不會自己重讀**。
# 實測：改了 `serve_question_review_ui.py`、跑了 `deploy_station.sh --restart`、`up.sh` 也
# 回報成功，但容器的 `StartedAt` 還是 11:20（一小時前），gzip 與 HTTP/1.1 完全沒生效——
# 部署「成功」而服務仍是舊行為，是這類部署最會騙人的失敗。
# 所以要真的重建容器。`--force-recreate` 只重建容器，不動 image 快取。
"${DOCKER}" compose -f "${HERE}/compose.yaml" up -d --remove-orphans --force-recreate

echo "== 確認真的換了程序 =="
# 「重建成功」不是「換了程序」：量容器的 StartedAt，不是看指令的退出碼。
STARTED="$("${DOCKER}" inspect qbr-review-ui --format '{{.State.StartedAt}}' 2>/dev/null || echo '')"
echo "  容器啟動於 ${STARTED}"

echo "== 等待就緒 =="
for _ in $(seq 1 60); do
  if curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/api/queue_index" >/dev/null 2>&1; then break; fi
  sleep 2
done

# 「啟動成功」不是「畫得出來」，所以這裡量的是 API 回的數字，不是容器的狀態。
INDEX="$(curl -fsS --max-time 10 "http://127.0.0.1:${PORT}/api/queue_index" || true)"
if [[ -z "${INDEX}" ]]; then
  echo "服務起來了但 API 沒有回應——看 log：" >&2
  echo "  ${DOCKER} compose -f ${HERE}/compose.yaml logs --tail 40" >&2
  exit 1
fi

SERVED="$(printf '%s' "${INDEX}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("questions") or 0)')"
if [[ "${SERVED}" != "${COUNT}" ]]; then
  echo "服務中的題數 ${SERVED} 不等於佇列的 ${COUNT}——服務的不是這份佇列。" >&2
  exit 1
fi

echo
echo "審題介面   http://${BIND}:${PORT}/v2"
echo "（其他裝置用 http://<這台的 LAN IP>:${PORT}/v2）"
echo "佇列       ${CANDIDATES}"
echo "題數       ${COUNT}"
echo "審核紀錄   ${LOG}（目前 ${RECORDS} 筆）"
echo "停止       ${DOCKER} compose -f ${HERE}/compose.yaml down"
echo "看 log     ${DOCKER} compose -f ${HERE}/compose.yaml logs -f"
