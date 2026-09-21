#!/usr/bin/env bash
# 記下「常駐機上跑的到底是哪一份程式」。
#
# 為什麼需要它：部署是把**工作樹**鏡射過去，而工作樹不一定等於任何一個 commit
# （實測：站上跑的 `scripts/serve_question_review_ui.py` 含一筆未提交的 `content_type_of` 修正，
# 那是另一個工作流的成果，而它讓圖片能正確顯示）。所以「站上跑的是什麼」不能只靠 git 回答。
#
#     deploy/qbr-review/record-deploy.sh            # 在常駐機上跑，寫出 DEPLOYED.json
#
# 它記三件事：
#   1. 部署時間、來源機器、來源 repo 的 HEAD 與 dirty 狀態；
#   2. 服務本身（server + v2.html + queue）的 sha256——這樣「介面變了」可以歸因；
#   3. 佇列與審核紀錄的筆數——「畫得出來」要用數字講，不是用感覺。
set -euo pipefail

HOME_DIR="${QBR_HOME:-/Users/tim/qbr-review}"
PORT="${REVIEW_UI_PORT:-8765}"
OUT="${HOME_DIR}/DEPLOYED.json"

sha() { [[ -f "$1" ]] && shasum -a 256 "$1" | awk '{print $1}' || echo null; }

QUEUE="${HOME_DIR}/queue/review-ui"
SERVER="${HOME_DIR}/code/scripts/serve_question_review_ui.py"
V2="${HOME_DIR}/code/review_ui/v2.html"

# 來源 repo 的狀態。
#
# **重要：來源是「送出部署的那台機器」，不是這台。** 這台上確實有一份舊的
# `~/tw-national-exam-catalog`（HEAD 3925d978，116 個未提交檔），但它**跟本部署無關**——
# 那是舊的 checkout。第一版就是讀到它，把來源記成錯的 commit，所以改成由送部署的那台
# 把 revision 帶進來（見 scripts/deploy_station.sh，它會匯出 QBR_SRC_HEAD / QBR_SRC_REPO）。
SRC_REPO="${QBR_SRC_REPO:-unknown}"
SRC_HEAD="${QBR_SRC_HEAD:-unknown}"
SRC_DIRTY="${QBR_SRC_DIRTY:-unknown}"

# 服務當下的數字（真的打 API，不是讀檔猜）。
SERVED="$(curl -fsS --max-time 8 "http://127.0.0.1:${PORT}/api/queue_index" 2>/dev/null \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("questions") or 0)' 2>/dev/null \
  || echo 0)"

cat > "${OUT}" <<JSON
{
  "recorded_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "host": "$(hostname)",
  "port": ${PORT},
  "source_repo": "${SRC_REPO}",
  "source_head": "${SRC_HEAD}",
  "source_dirty_files": "${SRC_DIRTY}",
  "note": "站上跑的是工作樹鏡射。source_head 由送部署的那台提供；source_dirty_files > 0 代表它含未提交的改動（實測：一筆 content_type_of 修正，讓圖能顯示）。這台上的 ~/tw-national-exam-catalog 是舊 checkout，不是本部署的來源。",
  "server_sha256": "$(sha "${SERVER}")",
  "v2_sha256": "$(sha "${V2}")",
  "candidates_sha256": "$(sha "${QUEUE}/candidates.jsonl")",
  "review_log_sha256": "$(sha "${QUEUE}/question_review_events.jsonl")",
  "review_events": $(wc -l < "${QUEUE}/question_review_events.jsonl" | tr -d ' '),
  "served_questions": ${SERVED}
}
JSON

echo "已寫出 ${OUT}"
python3 -m json.tool "${OUT}"
