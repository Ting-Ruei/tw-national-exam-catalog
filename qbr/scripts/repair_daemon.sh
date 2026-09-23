#!/usr/bin/env bash
# 常駐修理代理：每 INTERVAL 秒對「人已 block 且有可看紙本的爭議」的那批題目，
# 截圖問地端模型，寫下機械差異（advisory finding）。
#
#     qbr/scripts/repair_daemon.sh                 # 前景（給 launchd / systemd 用）
#     nohup qbr/scripts/repair_daemon.sh >/tmp/repair_daemon.log 2>&1 &   # 手動背景
#
# 為什麼不是 `repair_agent.py --every N`：那支是「跑 N 輪就結束」，適合有人在看的一次工作。
# 這支是「沒有結束條件」的常駐，因為使用者要的是「30 分鐘自動掃描」——
# 一個會自己停掉的迴圈不是常駐，只是比較慢的一次執行。
#
# ## 它做什麼，以及為什麼換成 confirm_dispute
#
# 第一版接 `repair_agent.py`，而它的工作清單是「沒有任何偵測器解釋的 block」。
# 三條新爭議規則上線後，304 題**全部**都有偵測器了，於是每一輪都選不到題：
# 實測每輪都印「本批沒有待問的 candidate_key / 累計 0 題」。一個 30 分鐘迴圈
# 一直跑卻什麼都不做，比沒有迴圈更糟——它看起來像在做事。
#
# 誠實的工作清單是「人已 block、且帶著一個可以看紙本確認的爭議」。那不是
# 「沒人能分類」的殘餘，而是一個**不會縮到零**的集合，而且答案是一個減法
# （紙本轉錄 − 抽取文字），不是模型的意見（`confirm_dispute.py` 的設計）。
# `--skip-confirmed` 以 `reading_sha256` 判斷「這一段讀法已經問過紙本了」，
# 所以同一題不會每輪重拍重問；文字真的被修好而改變時，它會自動重新排進來。
#
# 治理界線（AGENTS.md）：這支只寫 advisory finding，**不寫任何 review event、不改任何題目文字**，
# 所以停在 G2。它不會自動 accept/block，也不會替人做決定。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QBR="$(cd "${HERE}/.." && pwd)"
ROOT="$(cd "${QBR}/.." && pwd)"
PY="${QBR}/.venv/bin/python"

QUEUE="${QUEUE:-${QBR}/data/review-queues/live}"
WINDOW="${WINDOW:-5}"
LANE="${LANE:-splash}"
INTERVAL="${INTERVAL:-1800}"      # 30 分鐘
ONCE="${ONCE:-0}"                 # 1 = 只跑一輪就結束（驗證用）

mkdir -p "${QBR}/runs"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="${QBR}/runs/repair_daemon-${STAMP}.log"
echo "[daemon] 起動 $(date -u +%FT%TZ) queue=${QUEUE} lane=${LANE} window=${WINDOW} interval=${INTERVAL}s"
echo "[daemon] log=${LOG}"

turn=0
while true; do
  turn=$((turn + 1))
  echo "[daemon] 第 ${turn} 輪 $(date -u +%FT%TZ)" | tee -a "${LOG}"
  # 一輪失敗不該讓常駐死掉：印出來、下一輪再試。
  # `--model` 就是 lane：`LANE=splash` 走截圖轉錄那條線，不改的話這裡的變數等於裝飾。
  # 截圖預設就存進 queue 自己的 `review-ui/crops/`，會被同一個路由服務，
  # 所以「模型看過的圖，人也看得到」，不必另外指定。
  #
  # `--principles` 讀審題者在錯題討論區寫下的基本原則（預設就是 queue 自己那一份），
  # 每一條都會原封不動加進轉錄提示詞——人在介面上寫一句，下一輪就照著讀，不必重建。
  # `--escalate` 是「這一輪解決不了」的出口：紙本讀不到、或紙本與抽取一致但人仍然阻擋時，
  # 它寫一個 `ask` 到 `question_repair_questions.jsonl`，下一輪的提示詞讀得到，
  # 人也會在討論區看到它。**不是**再問一次同一個模型，也不是替人做決定。
  if ( cd "${QBR}" && "${PY}" scripts/confirm_dispute.py \
        --queue "${QUEUE}" --model "${LANE}" \
        --blocked-only --skip-confirmed --limit "${WINDOW}" --escalate ) >>"${LOG}" 2>&1; then
    echo "[daemon] 第 ${turn} 輪完成 $(date -u +%FT%TZ)" | tee -a "${LOG}"
  else
    echo "[daemon] 第 ${turn} 輪失敗（rc=$?），${INTERVAL}s 後重試" | tee -a "${LOG}"
  fi
  if [[ "${ONCE}" == "1" ]]; then
    echo "[daemon] ONCE=1，結束" | tee -a "${LOG}"
    break
  fi
  sleep "${INTERVAL}"
done
