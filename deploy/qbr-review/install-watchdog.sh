#!/usr/bin/env bash
# 在常駐機上安裝「自己回來」的 watchdog。
#
# 為什麼要有一支安裝腳本：watchdog 只有兩件事要做對——把腳本放對位置、把 launchd job 註冊起來，
# 而這兩件事**手做過一次之後就沒有人記得細節**。repo 裡有 plist 卻沒有安裝步驟，等於沒有。
#
#     deploy/qbr-review/install-watchdog.sh            # 安裝／更新
#     deploy/qbr-review/install-watchdog.sh --uninstall
#
# 它是幂等的：重跑會 bootout 舊的再 bootstrap 新的，不會累積重複的 job。
#
# **這支腳本在哪裡跑？** 在**常駐機**上（`ssh 192.168.10.70`）。若路徑不是預設的
# `/Users/tim/qbr-review`，先匯出 `QBR_HOME=/your/path`，它會同步改 plist 與腳本裡的路徑。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.timsvms.qbr-review-ensure"
HOME_DIR="${QBR_HOME:-/Users/tim/qbr-review}"
AGENT_DIR="${HOME}/Library/LaunchAgents"
BIN="${HOME_DIR}/bin/ensure-up.sh"
PLIST_SRC="${HERE}/${LABEL}.plist"
PLIST_DST="${AGENT_DIR}/${LABEL}.plist"

UNINSTALL=0
for arg in "$@"; do
  case "${arg}" in
    --uninstall) UNINSTALL=1 ;;
    -h|--help) sed -n '2,14p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "未知參數：${arg}" >&2; exit 2 ;;
  esac
done

UID_NUM="$(id -u)"

if [[ "${UNINSTALL}" == 1 ]]; then
  launchctl bootout "gui/${UID_NUM}/${LABEL}" 2>/dev/null || true
  rm -f "${PLIST_DST}"
  echo "已移除 ${LABEL}（腳本 ${BIN} 保留）"
  exit 0
fi

# 1. 腳本就位。若 QBR_HOME 不是預設值，就地把路徑改掉——plist 和腳本的預設值都要一致。
mkdir -p "$(dirname "${BIN}")" "${AGENT_DIR}" "${HOME_DIR}/logs"
sed "s#/Users/tim/qbr-review#${HOME_DIR}#g" "${HERE}/ensure-up.sh" > "${BIN}"
chmod +x "${BIN}"
if ! bash -n "${BIN}"; then
  echo "ensure-up.sh 語法錯誤，中止" >&2
  exit 1
fi

# 2. plist 就位（同樣改路徑）。
sed "s#/Users/tim/qbr-review#${HOME_DIR}#g" "${PLIST_SRC}" > "${PLIST_DST}"
plutil -lint "${PLIST_DST}" >/dev/null

# 3. 註冊（先 bootout，讓重跑是幂等的，不是疊加）。
launchctl bootout "gui/${UID_NUM}/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/${UID_NUM}" "${PLIST_DST}"

# 4. 驗它真的註冊了——「bootstrap 沒有錯誤」不是「job 存在」。
sleep 2
if launchctl list | grep -q "${LABEL}"; then
  echo "已安裝並註冊：${LABEL}"
else
  echo "bootstrap 沒有報錯，但 ${LABEL} 不在 launchctl list 裡——沒有真的註冊。" >&2
  exit 1
fi

# 5. 立刻跑一次（RunAtLoad 也會，但這裡明確驗一次；服務正常時它不寫 log）。
bash "${BIN}"
echo "已就緒。log：${HOME_DIR}/logs/ensure-up.log（服務正常時應為空）"
