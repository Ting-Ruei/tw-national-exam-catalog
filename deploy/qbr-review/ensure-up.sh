#!/usr/bin/env bash
# 讓審題介面「自己回來」。
#
# Docker Desktop 的 AutoStart 與 compose 的 restart=unless-stopped 覆蓋了「重開機後自己回來」，
# 但覆蓋不到兩件事：容器被手動停掉、Docker 起來時容器還沒被建立。這支腳本補這兩個缺口，
# 而且是**幂等**的——服務正常時它什麼都不做、不寫任何 log。
#
# 它量的是 **API 回的題數**，不是容器的狀態：「啟動成功」不是「畫得出來」。
#
# ── 這支腳本第一版錯在哪（2026-09-21，實測踩到）────────────────────────────
#
# 1. **啟動 Docker Desktop 的路徑寫成 `open -ga Docker`——不會動。**
#    這台的 Docker 是**巢狀**的：`/Applications/Docker.app/Contents/MacOS/Docker Desktop.app`。
#    `open -ga Docker` 回傳 0、但什麼都沒發生，所以 watchdog 每 5 分鐘「嘗試啟動」一次，
#    每次都靜默失敗。改用明確的巢狀 bundle 路徑（下面 DOCKER_APP），不依賴 LaunchServices
#    的名字解析。
# 2. **不能用 process 名字當健康判準。** 主 process 叫 `Docker Desktop`，不是 `Docker`；
#    `pgrep -x Docker` 在 Docker 正常執行時也會說「不在」。唯一可信的判準是 `docker info`。
#
# ⚠️ 還有一件比程式錯誤更重要的事，寫在這裡給下一個人看：
#    **不要為了「模擬重開機」去 `osascript quit` Docker Desktop。**
#    這台同時是考題平台的常駐機（exam_edge / exam_db / ai_learning_platform-* 都在上面）。
#    我為了驗證這支 watchdog，quit 了 Docker Desktop，結果它的 VM 卡在
#    `no route to host`，整個平台下線約 30 分鐘才救回來。要驗證容器修復，就**只停容器**
#    （`docker stop qbr-review-ui`）——那才是這支腳本真正負責的缺口。
set -uo pipefail

# 機器相關的路徑集中在這裡，換一台部署時只改這幾行。
HOME_DIR="${QBR_HOME:-/Users/tim/qbr-review}"
COMPOSE_DIR="${QBR_HOME:-/Users/tim/qbr-review}/code/deploy/qbr-review"
LOG="${HOME_DIR}/logs/ensure-up.log"
PORT="${REVIEW_UI_PORT:-8765}"
DOCKER="$(command -v docker || echo /usr/local/bin/docker)"

# Docker Desktop 的**巢狀** bundle。用絕對路徑，不用名字（見上面第 1 個修正）。
DOCKER_APP="${DOCKER_APP:-/Applications/Docker.app/Contents/MacOS/Docker Desktop.app}"

log() { printf '%s  %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "${LOG}"; }

# 1. Docker 本身在不在。不在就把它叫起來，這一輪不做事（下一輪再驗，因為開機要幾分鐘）。
if ! "${DOCKER}" info >/dev/null 2>&1; then
  log "docker 不在執行中，嘗試啟動 Docker Desktop（${DOCKER_APP}）"
  if [[ -d "${DOCKER_APP}" ]]; then
    open -a "${DOCKER_APP}" 2>>"${LOG}" || log "open 回傳非零"
  else
    open -ga Docker 2>>"${LOG}" || log "找不到巢狀 bundle，退回 open -a Docker"
  fi
  exit 0
fi

# 2. API 真的打一次。通 → 什麼都不做就結束（幂等）。
if curl -fsS --max-time 8 "http://127.0.0.1:${PORT}/api/queue_index" >/dev/null 2>&1; then
  exit 0
fi

# 3. Docker 在、但 API 不通 → 重新拉起。up.sh 自己會驗題數並在數字不符時非零結束。
log "Docker 在跑但 API 不通，重新拉起服務"
if bash "${COMPOSE_DIR}/up.sh" >> "${LOG}" 2>&1; then
  log "已重新拉起"
else
  log "重新拉起失敗，見上方輸出"
fi
