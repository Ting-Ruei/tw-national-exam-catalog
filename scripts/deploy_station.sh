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
#     scripts/deploy_station.sh --force             # 明知有迴圈在跑還是要部署（那一輪會少做事）
#
# 它**不帶**語料（常駐機已有，且只留 by_official_catalog），也**不帶** `qbr/data/`（那是筆電的
# 產物，不是部署內容）。審核紀錄永遠不在 rsync 範圍內——它是常駐機的家，方向是反過來的
# （`scripts/pull_station_reviews.sh`）。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CATALOG="$(cd "${HERE}/.." && pwd)"
# The station is named by its **Tailscale** name, not its LAN address: the same machine answers on
# `192.168.10.70` only when the caller is on that LAN, and this repo is worked on from more than one
# host. Override with `QBR_STATION=192.168.10.70` to pin the LAN address.
STATION="${QBR_STATION:-timmac-studio}"
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
  # 錯題討論區的兩條人工／代理流。基本原則是**提示詞的約束**，代理的反問記的是模型卡在哪；
  # 兩者都只存在於使用者敲下的那一次，無法從題庫重建。名字與 `serve_question_review_ui.py`
  # 的 `PRINCIPLES_STREAM`／`REPAIR_QUESTIONS_STREAM` 一致。
  question_review_principles.jsonl
  question_repair_questions.jsonl
)
# 還有人類的偏好設定與任何非事件檔的人工產物。
PROTECTED_EXTRA=(review_ui_preferences.json)

# 站上獨有的**目錄**，其中一個是容器的掛載點。
#
# `manual-assets/` 是補圖的家（人工為題目補的圖）。它只存在於常駐機：容器把它單獨掛成
# `rw`（見 compose.yaml 的第五條掛載），所以在站上被建立，而筆電的工作樹裡沒有它。
# 於是帶 `--delete` 的 rsync 會把它當成「目標多出來的目錄」而嘗試刪除——**失敗**，因為
# 容器正把它掛著（`rsync ... unlinkat: Permission denied`，2026-09-24 實測）。
# 那次 rsync 回 rc=23，而 `deploy_station.sh` 在它之後繼續跑，所以整個部署看起來像成功。
#
# 保護它的理由與保護人類紀錄相同：它不是從題庫重建得出來的資料，靜默刪掉就沒有第二次。
# 差別只在刪不掉的時候它會吵，而吵的方式是一行 rsync 錯誤——很容易被當成雜訊。
PROTECTED_DIRS=(manual-assets)
# 站上獨有的設定檔。**這曾經被這支腳本的 `--delete` 刪掉過。** 筆電的 `.gitignore:31`
# 忽略了 `deploy/qbr-review/.env`，所以筆電根本沒有它，因此 rsync 把站上的那一份當成
# 「目標多出來的檔」刪除——而那是**唯一**記載站上埠號（8765 而非 8774）與絕對路徑的地方。
# 它一不見，watchdog 下次重建容器就會掉回 compose.yaml 的預設值，服務從 8765 換到 8774，
# 而使用者的書籤與已安裝的 PWA 就斷了。已於 2026-09-21 重建。
PROTECTED_CONFIG=(.env)

DO_QUEUE=0
DO_RESTART=0
DO_FORCE=0
for arg in "$@"; do
  case "${arg}" in
    --queue) DO_QUEUE=1 ;;
    --restart) DO_RESTART=1 ;;
    --force) DO_FORCE=1 ;;
    -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "未知參數：${arg}" >&2; exit 2 ;;
  esac
done

# 0a. **部署前應驗腳本版本**（量到的 2026-09-29：保護寫在分支、部署跑在 main——
# crops 保護 commit `670e205` 已推上分支，而部署用的是 main 的版本，`--delete` 照刪）。
# 這支腳本自己帶著的保護要能證明它帶著，否則不部署：
# (1) 保護寫了但沒提交 ⇒ 你以為在跑的版本不是正在跑的版本，拒絕；
# (2) 兩個量到的保護（crops 只增不刪、candidates 備份排除）不在腳本裡，拒絕——
#     一份舊的腳本副本會安靜地少保護，這個檢查讓它自己先吵出來。
# 真的要硬跑（明知無保護時）用 --force，但那是「我看過了」的決定，不是預設。
if [[ "${DO_FORCE}" != 1 ]]; then
  if [[ -n "$(git status --porcelain -- "${BASH_SOURCE[0]}")" ]]; then
    echo "拒絕部署：deploy_station.sh 本身有未提交的修改。" >&2
    echo "  這支腳本的保護就是部署的安全底線；沒提交的保護不算保護（2026-09-29 正是這樣刪掉的）。" >&2
    echo "  先提交（或 git checkout -- scripts/deploy_station.sh 還原），再用 --force 明講要照跑。" >&2
    exit 2
  fi
  if ! grep -q -- "exclude=crops/" "${BASH_SOURCE[0]}" || \
     ! grep -q -- "exclude=candidates.jsonl\.\*" "${BASH_SOURCE[0]}"; then
    echo "拒絕部署：這份 deploy_station.sh 缺少量到的保護（crops 只增不刪／candidates 備份排除）。" >&2
    echo "  跑的是保護之前的舊版本——2026-09-29 的兩次資料損失都以這種方式發生。" >&2
    echo "  更新到含保護的版本再部署；明知要無保護部署才用 --force。" >&2
    exit 2
  fi
fi

# 0. **迴圈在跑就不准部署。**
#
# 實測 2026-09-24 18:06：迴圈走到 ② 與 ③ 之間時，一次 `--restart` 部署把站上的
# `repair_daemon.sh` 換掉，bash 讀到新檔就從那裡繼續，那一輪的 ③④⑤ 被安靜跳過——
# 日誌在 ② 之後就寫「第 1 輪結束」，看起來像正常收工。被換掉腳本的那一輪不是壞掉，
# 是**少做事**，而少做事沒有任何錯誤訊息。
#
# 所以檢查點在**任何 rsync 之前**：站上有 `repair_daemon` 或它跑的那幾支程式就算在跑。
# 真的要蓋過去（例如迴圈卡死）用 `--force`，但那是「我知道那一輪會少做事」的決定。
if [[ "${DO_FORCE}" != 1 ]]; then
  # The bracketed first character prevents pgrep from matching its own command line.
  RUNNING="$(ssh -n -o BatchMode=yes "${STATION}" \
    'pgrep -fl "[r]epair_daemon|[c]onfirm_dispute|[a]pply_dispute_repairs|[a]pply_text_corrections|[s]can_category_principles|[a]sk_about_blocks" || true')"
  if [[ -n "${RUNNING}" ]]; then
    {
      echo "常駐機上有迴圈在跑，這次不部署（換掉腳本會讓那一輪安靜地少做事）："
      echo "${RUNNING}" | sed 's/^/    /'
      echo "  等它結束；明知要覆蓋就加 --force。查進度："
      echo "    ssh ${STATION} 'ls -t ${REMOTE_HOME}/code/qbr/runs/repair_daemon-*.log | head -1'"
    } >&2
    exit 3
  fi
fi

cd "${CATALOG}"

SRC_HEAD="$(git rev-parse HEAD)"
SRC_DIRTY="$(git status --porcelain | wc -l | tr -d ' ')"
echo "來源 ${CATALOG}"
echo "  HEAD  ${SRC_HEAD:0:12}$( [[ "${SRC_DIRTY}" -gt 0 ]] && echo "  +${SRC_DIRTY} 個未提交檔（工作樹直送，站上也許沒有等價 commit）" )"

# **部署前應驗腳本版本**（量到的 2026-09-29：保護寫在分支、部署跑在 main——
# crops 保護 commit `670e205` 已推上分支，而部署用的是 main 的版本，`--delete` 照刪）。
# 這支腳本自己帶著的保護要能證明它帶著，否則不部署：
# (1) 保護寫了但沒提交 ⇒ 你以為在跑的版本不是正在跑的版本，拒絕；
# (2) 兩個量到的保護（crops 只增不刪、candidates 備份排除）不在腳本裡，拒絕——
#     一份舊的腳本副本會安靜地少保護，這個檢查讓它自己先吵出來。
# 真的要硬跑（明知無保護時）用 --force，但那是「我看過了」的決定，不是預設。
if [[ "${DO_FORCE}" != 1 ]]; then
  if [[ -n "$(git -C "${CATALOG}" status --porcelain -- "${BASH_SOURCE[0]}")" ]]; then
    echo "拒絕部署：deploy_station.sh 本身有未提交的修改。" >&2
    echo "  這支腳本的保護就是部署的安全底線；沒提交的保護不算保護（2026-09-29 正是這樣刪掉的）。" >&2
    echo "  先提交（或 git checkout -- scripts/deploy_station.sh 還原），再用 --force 明講要照跑。" >&2
    exit 2
  fi
  if ! grep -q -- "exclude=crops/" "${BASH_SOURCE[0]}" || \
     ! grep -q -- "exclude=candidates.jsonl\.\*" "${BASH_SOURCE[0]}"; then
    echo "拒絕部署：這份 deploy_station.sh 缺少量到的保護（crops 只增不刪／candidates 備份排除）。" >&2
    echo "  跑的是保護之前的舊版本——2026-09-29 的兩次資料損失都以這種方式發生。" >&2
    echo "  更新到含保護的版本再部署；明知要無保護部署才用 --force。" >&2
    exit 2
  fi
fi

# 1. 程式碼。排除語料／產物／版本控制／虛擬環境——那些都不是「部署內容」。
#
# `qbr/runs/` 是**常駐機自己的**迴圈日誌，理由與下面的 `deploy/qbr-review/.env` 完全相同，
# 而且同樣已經發生過：它沒有被排除，所以筆電上不同的檔名讓 rsync 把站上那一份當成
# 「目標多出來的檔」刪掉。實測 2026-09-24——站上 `repair-daemon.out.log` 說它這一輪寫到
# `runs/repair_daemon-20260924-113324.log`，而那個檔案在站上**不存在**；留在 `runs/` 裡的
# 反而是**筆電的**日誌（檔頭寫著筆電的佇列路徑 `/Users/tim/AI workspace/...`）。
# 後果不只是少一份日誌：在站上讀 `runs/` 會讀到另一台機器的紀錄，而它看起來一模一樣。
rsync -a --delete \
  --exclude='國考題資料夾*' --exclude='tmp/' --exclude='qbr/data/' \
  --exclude='qbr/runs/' \
  --exclude='.git/' --exclude='.venv/' --exclude='__pycache__/' \
  --exclude='.DS_Store' --exclude='*.pyc' \
  --exclude='deploy/qbr-review/.env' \
  ./ "${STATION}:qbr-review/code/"
echo "  程式碼已同步（站上獨有的 deploy/qbr-review/.env 與它自己的 qbr/runs/ 已排除在刪除範圍外）"

# 2. 掛載點。compose 把語料掛在唯讀的 /workspace 裡面，Docker 建不出那個目錄，
#    所以來源樹裡必須先有它（否則容器以 read-only file system 失敗）。
ssh -n -o BatchMode=yes "${STATION}" "mkdir -p ${REMOTE_HOME}/code/國考題資料夾 ${REMOTE_HOME}/logs"

# 2b. 看得見的紙本。
#
# 官方 PDF 把整頁掃描存成 JPEG 2000，而 Chrome 的 PDF 檢視器解不開（實測 2026-09-23：
# 畫面全白、停在「正在擷取 PDF 檔的文字…」、畫布數 0——審題者讀成「沒顯示」而且「很慢」）。
# Adobe／Preview 開得起來，所以下載時看不出問題。修正是在建置階段把那些影像流換成 JPEG，
# 產物放在 `10_official_pdf_browser_safe/`，伺服器偏好那一份（`review_ui/paths.py`）。
#
# **語料本身不在 rsync 範圍**（上面 `--exclude='國考題資料夾*'`，它由 assets 掛載提供），
# 所以推語料不是這支腳本的事；但產物樹只跟著語料走，就沒人會記得更新它。
# 這裡把它一起帶過去：它只增不刪，而且來源缺席時整段跳過——一隻新筆電不該因為
# 還沒建過產物就讓部署失敗。
DERIVED_PAPERS="國考題資料夾/10_official_pdf_browser_safe"
if [[ -d "${DERIVED_PAPERS}" ]]; then
  echo "  同步看得見的紙本產物…"
  rsync -a "${DERIVED_PAPERS}" "${STATION}:qbr-review/assets/國考題資料夾/"
  DERIVED_COUNT="$(find "${DERIVED_PAPERS}" -name '*.pdf' | wc -l | tr -d ' ')"
  echo "  ${DERIVED_COUNT} 份瀏覽器可繪製的紙本已同步"
else
  echo "  （沒有 ${DERIVED_PAPERS}；先跑 qbr/scripts/build_browser_safe_papers.py --apply）"
fi

# 3. 佇列（選擇性）。crops 隨佇列走，**但從 `--delete` 排除、分兩步同步**。
#
# 量到的（2026-09-29 佇列重建）：筆電重建後 crops 5,047 張，常駐機 21,543 張。差距是**裁切
# 證據圖**（`*-dispute.png` 等）：它們由 `crop_run_figures --queue` 在常駐機上裁出，引用它們的
# `question_ai_findings.jsonl` 以常駐機為家——筆電重建不重裁這些圖（build 的
# 「finding crops: … missing」說的就是它們）。帶 `--delete` 的 rsync 會把這 16.5k 張「筆電沒有
# 的」當成多餘檔刪掉，等於銷毀已承接 AI findings 的證據。所以主同步排除 `crops/`，再用一條
# **不帶 `--delete`** 的 rsync 把筆電的 crop 只增不刪地補上。
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
  # 目錄要排除**它自己與底下的內容**。`--exclude=manual-assets` 不夠：rsync 仍會嘗試清掉
  # 該目錄（而它掛在容器上，刪不掉），`--exclude=manual-assets/` 才會連同內容一起放過。
  for name in "${PROTECTED_DIRS[@]}"; do
    PROTECT_ARGS+=("--exclude=${name}/")
  done
  # crops 從 --delete 排除（理由見上面第 3 點的註），改走下面那條只增不刪的同步。
  PROTECT_ARGS+=("--exclude=crops/")

  # **candidates.jsonl 的備份變體也從 --delete 排除。**
  #
  # 量到的（2026-09-29 21:58 佇列部署）：常駐機上唯一的重灌前完整備份
  # `candidates.jsonl.before-italic-restore-20260926T115250` 是「筆電沒有的檔」，帶 --delete
  # 的 rsync 安靜地把它刪了——之後 79,090 列的 refs 全空，而 09-24 以前的備份都是小語料版，
  # 無法還原。備份檔不隨重建重生（它們是「重建前」那一刻的證據），刪掉就沒有第二次。
  # `candidates.jsonl` 本身不受此樣式影響（沒有 `.` 字尾不會被比對到），照常同步。
  PROTECT_ARGS+=("--exclude=candidates.jsonl.*")

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
            question_ai_findings.jsonl question_review_principles.jsonl \
            question_repair_questions.jsonl \
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

  # crops 只增不刪：筆電的新 crop 補上；常駐機上筆電沒有的裁切證據圖（AI findings 引用的）
  # 保留。`--delete` 在這條上會刪證據，見上面第 3 點的註。
  rsync -a "${CATALOG}/qbr/data/review-queues/live/review-ui/crops/" \
    "${STATION}:qbr-review/queue/review-ui/crops/"

  # 掃描的紀錄。**它在佇列根目錄，不在 `review-ui/` 底下**，所以上面的 rsync 不會帶它——
  # 而它正是討論區「排隊中 N 題」的來源。沒有它，那一塊永遠顯示「還沒跑過掃描」，
  # 即使掃描真的在跑（實測 2026-09-24：站上 `~/qbr-review/queue/scan_state.json` 不存在）。
  #
  # 它**不是人類產物**（掃描可以重跑），所以同步它是安全的。但兩個數字的意義要分清：
  # `pending` 才是討論區顯示的排隊數；其餘的鍵是「已看過的指紋」。
  if [[ -f "${CATALOG}/qbr/data/review-queues/live/scan_state.json" ]]; then
    rsync -a "${CATALOG}/qbr/data/review-queues/live/scan_state.json" \
      "${STATION}:qbr-review/queue/scan_state.json"
    # 用 `scan_state` 自己的讀取器，不要在這裡手寫第二份 schema 解讀。
    # 這個檔的形狀是 `{candidate_key: 指紋, ..., "pending": [...]}`，第一版用 `d.get("done")`
    # 去數，永遠印 0——一個看起來很合理的錯數字。
    COUNTS="$(cd "${CATALOG}/qbr" && .venv/bin/python -c '
import sys
sys.path.insert(0, "src")
from qbr import scan_state

root = sys.argv[1]
state = scan_state.load_state(root)
print(len(scan_state.pending_keys(root)), len(state) - (1 if "pending" in state else 0))
' "${CATALOG}/qbr/data/review-queues/live" 2>/dev/null || echo '? ?')"
    echo "  掃描紀錄已同步（排隊中 ${COUNTS%% *} 題、已看過 ${COUNTS##* } 題）"
  else
    echo "  （沒有 scan_state.json；討論區的排隊數會顯示「還沒跑過掃描」）"
  fi

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

  # 5b. **驗證跑著的容器真的用上了新的掛載。**
  #
  # 這一步是補一個實測過的靜默缺口（2026-09-24）：`deploy/qbr-review/compose.yaml` 早就改成
  # 「`review-ui/` 整包 rw」（見該檔 2026-09-24 註解），但站上的容器還是舊的
  # 「只留 `question_review_events.jsonl` 一條 rw」。檔案同步了、compose 同步了，而
  # `docker inspect` 仍然顯示舊的四條掛載——因為**容器沒被重建**。症狀是「偏好存不住」
  # 「原則不見了」「補圖失敗」，全都長得像程式缺陷，而根因是掛載。
  #
  # 所以部署的完成條件不是「指令跑完」，是「容器內的實際掛載與 compose 一致」。驗的是
  # 三件事：`/queue/review-ui` 是 rw、五條 stream 真的寫得進去、測試不留痕跡。
  #
  # 驗證程式放在 `deploy/qbr-review/verify-mounts.sh`（已經被 rsync 送到站上），不是這裡的
  # heredoc：`ssh -n` 配 heredoc 會把 stdin 關掉，而 heredoc 本身就是 stdin——兩個 heredoc
  # 疊在一起時內層收不到任何東西，安靜地什麼都不驗（第一次寫就是這樣，輸出是空的）。
  echo "  驗證容器掛載與實際可寫性…"
  ssh -n -o BatchMode=yes "${STATION}" "bash ${REMOTE_HOME}/code/deploy/qbr-review/verify-mounts.sh" || {
    RC=$?
    echo "  部署未通過驗證（rc=${RC}）——服務在跑，但寫入路徑沒有生效。" >&2
    exit "${RC}"
  }
fi
