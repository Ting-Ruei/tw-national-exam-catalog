#!/usr/bin/env bash
# 常駐修復代理：**掃描**、**修復**、**回報**是三件不同的事，這裡把它們分開。
#
#     qbr/scripts/repair_daemon.sh scan      # 對 queue 做一次廉價掃描，只更新 scan_state.json
#     qbr/scripts/repair_daemon.sh repair    # 人明確啟動一次 pending-only 本機紙本判讀
#     qbr/scripts/repair_daemon.sh report    # 只讀進度摘要
#     qbr/scripts/repair_daemon.sh loop      # launchd 用：掃描、回報，不呼叫模型
#     qbr/scripts/repair_daemon.sh           # = loop
# ── 為什麼要拆（2026-09-24，owner 的決定）
#
# 舊版是一個 30 分鐘的迴圈，每一輪都做同一件事。三個具體的問題：
#
# 1. **選題空轉。** `repair_daemon.sh` 自己記著：原策略是「找沒有偵測器能解釋的 block」，
#    但三條偵測規則上線後 304 題全部都有偵測器 → 每輪掃零題。條件式清單會縮到零。
#
#    2026-09-24 再加一條 owner 的規則：「我才說後面的agent循環是僅針對block去看」。閘門是**人的
#    standing block**，不是偵測器種類。實測本機鏡射：813 題有爭議種類，其中只有 105 題是人 block
#    的；反過來 279 題被 block 的裡面有 **174 題一個種類都沒有**——舊閘門替沒人看過的題排隊，同時
#    對人親手標記的題視而不見。現在掃描記的是**指紋**（讀法＋人的決定＋註解，種類有就一起記），
#    只有變了才重排；而 `pending` 裡只留人 block 的題（不是人 block 的會在同一輪被清掉——它們的
#    指紋已經存過，所以不會自己回來；但人之後標了 block 會改變指紋，那就真的回來了）。
#
# 2. **掃到不等於做完。** owner 說「我認為掃描到跟做完了是兩回事」。一輪找到 100 題、模型只做完
#    5 題，剩下 95 題不能被當成已完成。現在 `pending` 是獨立的狀態，只有修復回報成功才移出。
#
# 3. **反覆吃算力。** owner 說「掃描如果依照規則觸發，可能會反覆吃算力，導致能力低下」。
#    現在掃描便宜（純比對指紋，不呼叫模型），修復貴（呼叫模型）而且有配額，兩者頻率可以不同。
#
# ── 治理界線
#
# 排程只跑 G2 證據路徑：掃描寫入本機 scan_state.json，回報只讀。它不呼叫模型、不修改
# candidates、image_refs、review events 或原則流；失敗以非零結束碼交給 launchd 記錄。
#
# `repair` 是人明確啟動的單次動作，只讀 pending ∩ standing block，呼叫本機 endpoint，寫 advisory
# findings。它不執行 apply、落地、裁圖、經驗套用或原則提案。B 表示該修復不正確：下一輪應把
# 這筆拒絕作為新上下文重新判讀；達到明確重試上限才交給人，而不是把 B 當成「已看過」。
#
# 任何會改候選內容、image_refs 或 review event 的 stage 都不由本 daemon 排程。這些仍是獨立的
# G3/G4 路徑，必須依當次 owner approval 與 catalog 治理程序操作。
#
# 不會自動 accept/block，也不會把模型輸出寫成 reviewer identity。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QBR="$(cd "${HERE}/.." && pwd)"
# **The interpreter is a parameter, because the machine that runs the loop is not always the machine
# that built it.** The station runs the loop from its deployed code tree and has no `qbr/.venv` —
# its system Python 3.9 already carries PyMuPDF and Pillow, which is everything the loop imports
# (verified 2026-09-24: `ai_findings`, `discuss`, `extract`, `paths`, `reread`, `ask_about_blocks`,
# `repair_loop` all import there). Hardcoding the venv made the daemon un-runnable on the host that
# is supposed to keep it alive, and a resident loop that only works on a laptop that gets closed is
# the gap this whole deployment exists to close.
PY="${QBR_PYTHON:-${QBR}/.venv/bin/python}"
if [[ ! -x "${PY}" ]]; then
  echo "[daemon] 找不到 Python：${PY}" >&2
  echo "[daemon] 用 QBR_PYTHON=/path/to/python 指定，或先在 ${QBR} 建 .venv" >&2
  exit 3
fi

QUEUE="${QUEUE:-${QBR}/data/review-queues/live}"
# `QUEUE` is accepted relative **to the checkout**, not to `qbr/`. The daemon `cd`s into `qbr/` to
# run the scripts, so a relative default would resolve as `qbr/qbr/...` — which is exactly what the
# first launchd run hit: it reported "找不到 .../qbr/qbr/data/.../candidates.jsonl" and skipped the
# round. Made absolute here, once, so no later line has to remember which directory it is in.
case "${QUEUE}" in
  /*) : ;;
  *) QUEUE="$(cd "${QBR}/.." && pwd)/${QUEUE}" ;;
esac
WINDOW="${WINDOW:-1}"                   # 人工 repair 模式一輪至多送一題
LANE="${LANE:-mtplx-35b}"              # 本機端點；外部模型不得作為 fallback
case "${LANE}" in
  mtplx-35b|splash) ;;
  *) echo "[daemon] 拒絕非核准的本機 lane: ${LANE}" >&2; exit 2 ;;
esac
INTERVAL="${INTERVAL:-1800}"           # 間隔由 launchd 的 StartInterval 決定
ONCE="${ONCE:-0}"                      # 1 = 跑一輪就結束（給 launchd 用）

MODE="${1:-loop}"
case "${MODE}" in
  loop|scan|repair|report) ;;
  *) echo "用法: $0 [loop|scan|repair|report]" >&2; exit 2 ;;
esac

RUN_DIR="${RUN_DIR:-${QBR}/runs}"
mkdir -p "${RUN_DIR}"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="${RUN_DIR}/repair_daemon-${STAMP}.log"


# An explicit repair uses one shared argument list. It is never called by `run_once`; the user invokes
# `repair` after inspecting the pending scan.
run_repair() {
  set -- --queue "${QUEUE}" --model "${LANE}" --limit "${WINDOW}" --pending-only --skip-confirmed
  ( cd "${QBR}" && "${PY}" scripts/confirm_dispute.py "$@" )
}

run_once() {
  echo "[daemon] G2 scan 開始 $(date -u +%FT%TZ)" | tee -a "${LOG}"
  if ( cd "${QBR}" && "${PY}" scripts/scan_for_repairs.py --queue "${QUEUE}" ) \
      >>"${LOG}" 2>&1; then
    echo "[daemon] scan 完成" | tee -a "${LOG}"
  else
    local rc=$?
    echo "[daemon] scan 失敗（rc=${rc}）" | tee -a "${LOG}"
    return "${rc}"
  fi

  if ( cd "${QBR}" && "${PY}" scripts/report_repair_progress.py --queue "${QUEUE}" ) \
      >>"${LOG}" 2>&1; then
    echo "[daemon] report 完成" | tee -a "${LOG}"
  else
    local rc=$?
    echo "[daemon] report 失敗（rc=${rc}）" | tee -a "${LOG}"
    return "${rc}"
  fi
  return 0
}

if [[ "${MODE}" == "scan" || "${MODE}" == "repair" || "${MODE}" == "report" ]]; then
  case "${MODE}" in
    scan)   ( cd "${QBR}" && "${PY}" scripts/scan_for_repairs.py --queue "${QUEUE}" ) ;;
    repair) run_repair ;;
    report) ( cd "${QBR}" && "${PY}" scripts/report_repair_progress.py --queue "${QUEUE}" ) ;;
  esac
  exit $?
fi

echo "[daemon] 起動 $(date -u +%FT%TZ) queue=${QUEUE} interval=${INTERVAL}s"
echo "[daemon] log=${LOG}"

# `ONCE=1` 是給 launchd 用的形狀：**排程器負責「每 30 分鐘」，這支負責「做一輪」。**
# 一個自己睡 30 分鐘的常駐，在 launchd 底下會變成「開機時起一個、之後永遠同一支在睡」——
# 重開機或崩潰後的行為要靠 launchd 的 KeepAlive 去猜，而 `StartInterval` 本來就是為這件事存在的。
# 舊版是前景無限迴圈；現在兩種都支援，launchd 走 ONCE=1。
turn=0
while true; do
  turn=$((turn + 1))
  echo "[daemon] 第 ${turn} 輪 $(date -u +%FT%TZ)" | tee -a "${LOG}"
  turn_rc=0
  run_once || turn_rc=$?
  echo "[daemon] 第 ${turn} 輪結束 $(date -u +%FT%TZ) rc=${turn_rc}" | tee -a "${LOG}"
  if [[ "${ONCE}" == "1" ]]; then
    echo "[daemon] ONCE=1，結束" | tee -a "${LOG}"
    exit "${turn_rc}"
  fi
  sleep "${INTERVAL}"
done
