# Ryzen AI Max 395 專案搬遷手冊

更新：2026-08-07（Asia/Taipei）

## 目的與現行邊界

本手冊準備將 `tw-national-exam-catalog` 的程式、PostgreSQL、Review UI、官方 PDF、
MinerU 產物、人工補圖、queue/checkpoint 與本地 AI worker 搬到 Ryzen AI Max 395。

這次要求是「轉移前準備」，不是正式切換通知。因此在使用者明確宣布 cutover 完成以前：

- 人工審核與 production PostgreSQL 的唯一權威仍是 Mac Studio
  `http://192.168.10.70:8765/`。
- 不得讓新主機和 Mac Studio 同時接受人工審核寫入。
- 不得刪除 Mac Studio 的 PostgreSQL volume、資產或舊機回退能力。
- `MIGRATION_HANDOFF.md` 只保存 2026-07-20 的歷史 MinerU queue checkpoint，不能當成
  新主機的即時續跑點。

## 2026-08-07 實測基線

以下是盤點當下的快照，不是永遠不變的真值；freeze/cutover 當天必須重新產生。

### Mac Studio（現行權威端）

| 項目 | 實測值 |
| --- | --- |
| 主機 | `TimsMac.lan`, macOS 26.5.2, arm64 |
| 專案路徑 | `/Users/tim/tw-national-exam-catalog` |
| Git HEAD | `3925d978a07e0bed6c0b6c3e622cc385c124051b`（2026-07-18） |
| Git 漂移 | 34 個 tracked 修改、149 個 untracked 檔；尚未可直接搬遷 |
| PostgreSQL | 18.4，容器內 arm64，`pgvector/pgvector:0.8.2-pg18` |
| Docker | 29.5.3；PostgreSQL healthy，Review UI ports 8765/8766 |
| UI 回應 | 2026-08-07 盤點時桌面 8765、手機 8766 均為 HTTP 200 |
| PostgreSQL volume | `tw-national-exam-catalog_postgres_data` |
| DB 大小 | 2,390,668,991 bytes（約 2.28 GiB） |
| 主資料根 | 約 18 GiB |
| 主機磁碟 | 460 GiB，已用 337 GiB，剩餘約 100 GiB |

資料庫基線：

| 表／指標 | 數量 |
| --- | ---: |
| `exam.official_documents` | 8,295 |
| `exam.question_candidates` | 191,839 |
| `exam.question_review_events` | 46,336；max id 92,095 |
| `exam.answer_review_events` | 20,658；max id 41,890 |
| `exam.question_ai_review_events` | 543,944；max id 688,253 |
| `exam.questions` | 15,440 |
| `exam.answers` | 15,429 |
| `exam.assets` | 9,973 |

`exam.assets` 的 9,973 筆資料全部有 `relative_asset_path`。其中 9,280 筆 `asset_path`
仍是 `/Users/tim/...`，但可用 relative path 重綁到 Linux 資料根。Review UI 已改為優先把任何
含 `國考題資料夾` 的舊絕對路徑重綁到 `ASSET_ROOT`，不要求在 Ryzen 上偽造 Mac 路徑。

現有 custom-format dump 只有「維修前快照」，不是定期災備：

- 2026-08-04：119 MiB。
- 2026-07-29：93 MiB、94 MiB。
- 2026-07-20：91 MiB。

切換必須另做新的完整 logical dump；舊 dump 只保留歷史回退證據。

### MacBook／目前工作區（程式與非 production 資產來源）

| 項目 | 實測值 |
| --- | ---: |
| Git HEAD | `d53a5d327c39b7aed546b96adab137fee23cabb5`（2026-08-04） |
| `國考題資料夾/` | 約 20 GiB；官方 PDF 8,295；總檔案 107,191 |
| `國考題資料夾_其他類型/` | 約 7.2 GiB；官方 PDF 14,670；總檔案 186,914 |
| `國考題資料夾_非醫學剩餘全集/` | 約 80 GiB；官方 PDF 96,922；總檔案 930,992 |
| 非醫學細分 | official PDF 約 13 GiB、MinerU output 約 65 GiB、Registry 約 2.6 GiB |

三個本機資料根合計約 107 GiB、1,225,097 個檔案，全部被 `.gitignore` 排除。只做
`git clone` 會遺失它們；大量小檔也代表 target filesystem inode、rsync enumeration 與 checksum
時間都要納入維護窗口。
MacBook 與 Mac Studio 的主資料根可能有同一路徑但內容版本不同，必須分開傳到 staging、比較
SHA-256，不能用單次 rsync 互相覆蓋。

## 權威資料矩陣

| 資料 | 搬遷來源 | 規則 |
| --- | --- | --- |
| Production PostgreSQL | Mac Studio | 以 freeze 後的新 `pg_dump -Fc` 為唯一來源 |
| 人工 review/answer events | Mac Studio PostgreSQL | append-only；不得用 MacBook JSONL 覆蓋 |
| Production 程式碼 | Git 歷史 + Mac Studio worktree + MacBook worktree | 先對齊成乾淨、可測試、可標記的單一 commit |
| 主資料根的 PDF/MinerU/manual assets | Mac Studio 與 MacBook | 各自 manifest；同路徑異 hash 一律列 conflict |
| 其他類型資料根 | MacBook／外接儲存來源 | SHA-256 驗證後搬入獨立目錄 |
| 非醫學全集與歷史 queue | MacBook | 保留 Registry、batch state、log 與 checkpoint；不直接相信舊 PID |
| `.env`、API key、DB password、SSH key | 重新建立 | 不進 Git、不進 asset manifest、不複製到模型 prompt |
| Ollama model blobs／MinerU cache | 可重下載或另做 cache 備份 | 不可取代 model tag、digest、版本與 golden test 紀錄 |

## 目標主機基線

建議以原生 Ubuntu Linux 為 production host，不以 Windows/WSL 作第一版正式主機。AMD 在
2026-08-07 的正式 Ryzen Linux matrix 將 Ryzen AI Max+ 395（gfx1151）列在 ROCm 7.2.1／
Ubuntu 24.04.4 的 production support，PyTorch 基線為 2.9.1、Python 3.12、官方驗證 FP16。
安裝時仍須重查 AMD 當日 matrix，不能把本文件當成永遠固定的 driver lock：

- AMD Linux support matrix：<https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityryz/native_linux/native_linux_compatibility.html>
- AMD Ryzen Linux install：<https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/installryz/native_linux/install-ryzen.html>

建議目錄：

```text
/srv/tw-national-exam-catalog/                    # pinned Git worktree
/data/tw-national-exam-catalog/國考題資料夾/
/data/tw-national-exam-catalog/國考題資料夾_其他類型/
/data/tw-national-exam-catalog/國考題資料夾_非醫學剩餘全集/
/data/tw-national-exam-catalog/migration-inbox/   # 兩台來源分開 staging
/opt/tw-national-exam/mineru/                     # Linux/ROCm 專用 venv
/srv/national-exam-audit/                         # Ollama shadow runs
/backup/tw-national-exam-catalog/                 # 不與 /data 同一個唯一故障點
```

容量下限：

- 目前已知 Git 外資料約 107 GiB，Mac Studio 另有約 18 GiB（重疊量尚未證明）。
- Target preflight 以 250 GiB free 作硬下限，只夠 staging、DB、驗證與有限快照。
- 若同機保存多個 Ollama 模型、MinerU cache、雙份 migration staging 與本地備份，實務上建議
  至少 1 TiB 可用空間；希望保留長期成長與兩份本地快照則以 2 TiB 級為宜。
- `/backup` 最好是另一顆磁碟或另一台主機；同一 NVMe 上的第二個資料夾不算災備。
- 初期 MinerU 與 Ollama concurrency 都設 1；量測 RAM／GPU shared memory／溫度／IO 後再調高。

## 已完成的可搬遷化調整

- `compose.yaml` 支援 `POSTGRES_BIND`、host `ASSET_ROOT` 與 container
  `ASSET_CONTAINER_ROOT`，資產不必放在 Git worktree 裡。
- PostgreSQL 與 Review UI 的 published ports 預設只綁 localhost；目標機必須把
  `REVIEW_UI_BIND` 明確設為核准的固定 LAN／VPN IP，不使用 `0.0.0.0`。
- Review UI 使用 `ASSET_ROOT`，並能重綁舊 `/Users/tim/.../國考題資料夾/...` 路徑。
- MinerU batch runner 的預設 executable 可由 `MINERU_BIN` 設定。
- remote MinerU worker 不再寫死 `/Users/tim`，預設改用 `$HOME/AI_workspace`。
- PostgreSQL image pull 暫存路徑改用 `${TMPDIR:-/tmp}`，可在 Linux 使用。
- `.env.ryzen.example` 提供不含真實 secret 的目標設定骨架。
- `migration_preflight.py`、`build_migration_asset_manifest.py` 與
  `postgres_migration_backup.sh` 提供可重跑證據。

## P0 阻擋項目

未完成下列項目以前不得切換：

1. Mac Studio worktree 的 34 個 tracked 修改與 149 個 untracked 檔尚未整理；production
   code 不能只以舊 HEAD 或 MacBook HEAD 猜測。
2. MacBook worktree 也有進行中的修改；兩端需先做 diff、測試、commit/tag 或不可變 bundle。
3. 尚未在 Ryzen 實機驗證 MinerU 3.3.1 的 ROCm backend、輸出目錄與 parser 回歸。
4. 尚未產生兩台來源的全量 SHA-256 asset manifest 與同路徑 conflict report。
5. 尚未在 Ryzen 的隔離 volume 做 PostgreSQL restore drill。
6. 尚未決定 Ryzen 的固定 LAN IP／hostname、TLS/VPN、防火牆、實際 RAM 與磁碟配置。
7. 尚未建立切換當日的新 production dump 與第二位置備份。

## 第一階段：程式碼對齊

先在兩台來源各產生只讀 preflight；失敗是預期的，報告會列出未整理項：

```bash
python3 scripts/migration_preflight.py \
  --mode source \
  --deep \
  --json-output /safe/backup/source-preflight.json
```

程式碼對齊規則：

1. 保存 Mac Studio 的 `git status`、`git diff --binary HEAD`、untracked file list 與 repository
   bundle；不可先 `reset` 或清理。
2. 比對 Mac Studio worktree 與 MacBook 現行 HEAD。MacBook 比 Mac Studio HEAD 多出的 commit 不代表
   自動涵蓋所有 production 未提交修改。
3. 將確定需要的 production 修改帶回一個整合 branch，執行完整測試。
4. 產生乾淨 commit，記錄 commit SHA，建立 migration tag。新主機只部署此 SHA。
5. 任何包含 `.env`、key、DB dump、官方大型資產的檔案不得 commit。

`postgres_migration_backup.sh` 同時保存 repository bundle、tracked binary patch、untracked 檔案清單
與 untracked archive；snapshot 權限預設為 owner-only。它不是 code review 的替代品。

## 第二階段：來源資產清冊與初次同步

### 產生 manifest

MacBook 範例：

```bash
python3 scripts/build_migration_asset_manifest.py build \
  --root macbook-main='/path/to/國考題資料夾' \
  --root macbook-other='/path/to/國考題資料夾_其他類型' \
  --root macbook-nonmedical='/path/to/國考題資料夾_非醫學剩餘全集' \
  --output /safe/backup/macbook-assets.csv \
  --sha256
```

Mac Studio 範例：

```bash
python3 scripts/build_migration_asset_manifest.py build \
  --root macstudio-main='/Users/tim/tw-national-exam-catalog/國考題資料夾' \
  --output /safe/backup/macstudio-assets.csv \
  --sha256
```

最終驗收必須使用 `--sha256`。metadata-only 只能估算，不能證明內容相同。Manifest 本身也有
`.summary.json` 與 SHA-256，兩者一起保存。

### 分流 rsync

兩個來源先進不同 staging，不使用 `--delete`：

```bash
rsync -a --partial --info=progress2 \
  '/source/國考題資料夾/' \
  'target:/data/tw-national-exam-catalog/migration-inbox/macbook-main/'
```

```bash
rsync -a --partial --info=progress2 \
  '/Users/tim/tw-national-exam-catalog/國考題資料夾/' \
  'target:/data/tw-national-exam-catalog/migration-inbox/macstudio-main/'
```

在 target 驗證每一個來源 staging：

```bash
python3 scripts/build_migration_asset_manifest.py verify \
  --manifest /safe/backup/macstudio-assets.csv \
  --root macstudio-main=/data/tw-national-exam-catalog/migration-inbox/macstudio-main \
  --require-sha256 \
  --check-extra
```

比較兩份主資料根：

```bash
python3 scripts/build_migration_asset_manifest.py compare \
  --left-manifest /safe/backup/macbook-assets.csv \
  --left-label macbook-main \
  --right-manifest /safe/backup/macstudio-assets.csv \
  --right-label macstudio-main \
  --output /safe/backup/main-root-comparison.csv
```

合併規則：

- `same_sha256`：內容相同，可只保留 final root 一份。
- `left_only`／`right_only`：保留並記錄來源。
- `content_conflict`：停止自動合併。人工事件、manual assets、Review UI preference 等 production
  內容優先調查 Mac Studio；官方 PDF/MinerU 則依 registry key、source URL、hash 與時間判定。
- `same_metadata`：沒有 hash，只能視為未證明相同；final cutover 不接受。
- Final root 完成後再建一份新的 SHA-256 manifest，不能只保留兩份來源 manifest。

### 建立 versioned canonical candidate

`scripts/build_canonical_asset_root.py` 是 fail-closed builder。預設只產生 plan；必須明確加
`--apply` 才會建立全新的 candidate，而且 output root 已存在時一律拒絕。它不會修改兩個
source-separated staging roots，也不會更新 `main` symlink、Review UI mount 或 production authority。

執行時必須綁定 source manifests、comparison、approved resolution、Linux filename maps 與 safe
symlink plan 的 SHA-256。先用同一組參數執行 `--verify-source-files` dry-run，確認 report 為
`dry_run_complete`，才對另一個全新 report/candidate 目錄加 `--apply`：

```bash
python3 scripts/build_canonical_asset_root.py \
  --left-manifest /migration-evidence/macbook-manifest/macbook-assets.csv \
  --left-label macbook-main \
  --left-root /migration-inbox/macbook-assets/macbook-main \
  --expect-left-manifest-sha256 LEFT_MANIFEST_SHA256 \
  --right-manifest /migration-evidence/macstudio-manifests/macstudio-main.csv \
  --right-label macstudio-main \
  --right-root /migration-inbox/macstudio-main \
  --expect-right-manifest-sha256 RIGHT_MANIFEST_SHA256 \
  --comparison /migration-evidence/comparison/main-root-comparison.csv \
  --expect-comparison-sha256 COMPARISON_SHA256 \
  --resolution /migration-evidence/conflict-resolution/main-root-conflict-resolution-v1.csv \
  --expect-resolution-sha256 RESOLUTION_SHA256 \
  --name-map macbook-main=/migration-evidence/macbook-linux-filenames/linux-filename-map.tsv \
  --expect-name-map-sha256 macbook-main=MACBOOK_NAME_MAP_SHA256 \
  --name-map macstudio-main=/migration-evidence/macstudio-linux-filenames/linux-filename-map.tsv \
  --expect-name-map-sha256 macstudio-main=MACSTUDIO_NAME_MAP_SHA256 \
  --safe-symlink-plan macbook-main=/migration-evidence/macbook-symlinks/safe-symlink-plan.tsv \
  --expect-safe-symlink-plan-sha256 macbook-main=SAFE_SYMLINK_PLAN_SHA256 \
  --output-root /srv/ai395/data/tw-national-exam-catalog/main-candidates/BUILD_ID \
  --report-dir /migration-evidence/canonical-builds/BUILD_ID-dry-run \
  --verify-source-files
```

`canonical-build-report.json`、`canonical-build-plan.csv`、`regeneration-queue.csv` 與 apply 後的
`canonical-physical-manifest.csv` 是一組 evidence。任何 evidence hash、manifest row、conflict
resolution、overlong filename mapping、source bytes/hash 或 symlink target 不符，exit code 都是 2。
Apply 中途失敗會保留 `.canonical-build-incomplete.json`，不得把該目錄提升為 current。

目前核准的 conflict policy 會先省略 27 個衝突項目，其中 15 個進 regeneration queue。因此
`candidate_complete` 仍不等於 `ready_for_promotion`；必須完成衍生報告/runtime pointer 重建、fresh
writer-freeze delta、UI/DB 驗收與單一 writer 批准，才可切換 authority。

## 第三階段：PostgreSQL logical backup 與 restore drill

arm64 Mac Studio 與 amd64 Ryzen 之間不得複製 `/var/lib/postgresql` 或 Docker named volume。
使用 PostgreSQL 18 `pg_dump -Fc`／`pg_restore`。

在已對齊程式碼且有安全備份磁碟的 Mac Studio 執行：

```bash
bash scripts/postgres_migration_backup.sh /safe/backup/tw-national-exam-catalog
```

輸出包含：

- custom-format full dump。
- schema-only SQL。
- 不含 role password hash 的 globals SQL。
- `pg_restore --list` 驗證結果。
- 11 個關鍵 count/max-id 基線。
- Git bundle、tracked patch、untracked list/archive。
- `SHA256SUMS`。

把 snapshot 複製到至少兩個位置並驗證 checksum。不要只留在 Mac Studio 內建磁碟。

### 隔離 restore drill

使用不同 Compose project name、不同 host ports 與全新 volume；不要在來源端執行
`docker compose down -v`：

```bash
docker compose \
  -p tw-exam-restore-drill \
  --env-file .env.restore-drill \
  up -d postgres
```

```bash
docker compose \
  -p tw-exam-restore-drill \
  --env-file .env.restore-drill \
  exec -T postgres \
  pg_restore -U national_exam -d tw_national_exam_dev \
  --clean --if-exists --no-owner --no-acl \
  < /safe/backup/tw_national_exam_dev.dump
```

還原後先只讀比對 table count、event max id、DB size、schema、extension，再啟動隔離 Review UI。
Restore drill 端口建議 8875/8876/54330，避免誤認 production。

## 第四階段：Ryzen 軟體與 MinerU 驗證

現有 `requirements/mineru-3.3.1-py314-freeze.txt` 是 Apple Silicon／Python 3.14 環境快照，內含
`mlx`、`mlx-lm`、`mlx-metal`、`mlx-vlm`，不能直接套到 Linux/AMD。正確做法：

1. 依 AMD 當日 production matrix 安裝 Ubuntu／ROCm，先通過 `rocminfo` 與官方 PyTorch smoke test。
2. 另建 Python 3.12 ROCm venv；不要污染系統 Python 或 Review UI container。
3. 先鎖 `mineru==3.3.1`、method `ocr`、backend `vlm-engine`、
   `MINERU_IMAGE_ANALYSIS=false`，但允許 Linux/ROCm 重新解相依。
4. 記錄完整 `pip freeze`、ROCm、kernel、model revision、command、host 與 output hash。
5. 選至少五類 golden PDFs：一般題、早期題號、題組、圖表、MOD／更正答案。
6. 比對必要 Markdown、layout/content list、題數、選項、題組、圖片與 parser regression；不是只看 CLI
   exit code。
7. ROCm 能支援 Ryzen GPU，不代表 MinerU 3.3.1 的特定 `vlm-engine` 已被本專案驗證。若 GPU lane
   未通過，先保留 Mac/CPU worker，不能在 cutover 當天臨時改 backend 或升 MinerU。

Ollama 仍依 `docs/skills/national-exam-ai-audit/runtimes/ai-max-395.yaml` 從 concurrency 1、
shadow-only 開始。模型必須記錄實際 tag/digest；gold certification 前不得自動寫人工事件。

## 第五階段：目標設定與 preflight

```bash
cp .env.ryzen.example .env
```

替換每個 `CHANGE_ME`，尤其是 DB password、URL-encoded `DATABASE_URL`／
`REVIEW_UI_DATABASE_URL`、新 LAN IP、SSH user/key。強密碼若含 `@`、`:`、`/` 等字元，兩個 URL
必須 percent-encode；Compose 內的 Review UI 連線使用 `REVIEW_UI_DATABASE_URL`。
`.env` 權限設為 owner-only，且不進 Git。

```bash
python3 scripts/migration_preflight.py \
  --mode target \
  --env-file .env \
  --deep \
  --json-output /safe/backup/target-preflight.json
```

Target 必須沒有 fail：

- Linux/x86_64、Ubuntu 24.04 baseline。
- git、rsync、Docker Compose、rocminfo、ollama 可用。
- Git worktree 乾淨且 HEAD 等於 migration SHA。
- `ASSET_ROOT`、`MINERU_BIN` 存在。
- DB password 不是 example/default。
- free space 達門檻。
- 所有 final asset manifest 0 mismatch。

網路安全：

- PostgreSQL 預設在 Ryzen template 綁 `127.0.0.1`；若需要遠端維護，優先 SSH tunnel/VPN。
- Review UI 8765/8766 只允許信任的 LAN/VPN，不直接公開到 Internet。
- Ollama 11434 不對外暴露。
- `OPENAI_API_KEY` 可留空；AI advisory 與 production 人工審核權限分離。

## Cutover 當日順序

1. 宣布 review freeze，確認無人在 Review UI 操作。
2. 停止 Mac Studio 的 Review UI writer；PostgreSQL 暫時保留供 final dump。
3. 重新記錄 Git SHA/status、DB counts/max ids、queue/process、資產 manifest。
4. 執行新的 `postgres_migration_backup.sh`，複製到 target 與第二備份位置並驗 hash。
5. 執行 final rsync（仍不使用 `--delete`），驗證 final SHA-256 manifest。
6. 在 Ryzen 全新 production volume 還原 DB；比對 count/max id 與 dump checksum。
7. 啟動 Ryzen PostgreSQL、Review UI；先 read-only 驗證桌面／手機 UI、PDF、MinerU layout、題圖、
   manual assets、filter preference 與正式表概覽。
8. 執行 `--scan-only`；不得在 cutover 驗證中直接下載或 ingest 新資料。
9. 跑一個隔離歷史樣本的 MinerU→parser→staging golden flow 與 Ollama shadow eval。
10. 完成驗收後才把 `REVIEW_PRIMARY_*`、LAN bookmark、n8n/排程與 `AGENTS.md` 權威主機改到 Ryzen。
11. 只啟用 Ryzen writer；Mac Studio Review UI 保持停止但資料完整。
12. 觀察至少一個明確回退窗口後，另行決定 Mac Studio 退役；本流程不自動刪除舊資料。

## 驗收清單

### Code/config

- [ ] Target HEAD 等於 migration SHA，worktree clean。
- [ ] `docker compose config` 成功，image digest 已記錄。
- [ ] `.env`、SSH key、API key 未進 Git/snapshot manifest。
- [ ] AGENTS/README/automation 的 primary host 只在正式切換後更新。

### PostgreSQL

- [ ] PostgreSQL major 18、pgvector extension、schema 全部存在。
- [ ] 8 個核心 table count 與 3 個 event max id 等於 freeze snapshot。
- [ ] `pg_restore` 無未處理錯誤；DB size 差異有合理解釋。
- [ ] append-only events 未被 rewrite；正式 questions/answers 數量相符。
- [ ] 9,973 筆 asset 均有 relative path；Mac 絕對路徑不影響開檔。

### Assets/UI

- [ ] 三個 final data roots 的 SHA-256 verify 為 0 mismatch。
- [ ] 同路徑 conflict 全部有人為處置紀錄。
- [ ] 桌面 8765、手機 8766、PDF、題圖、manual assets 可讀。
- [ ] Review UI 只有一個 production writer。

### Pipelines/AI

- [ ] `--scan-only` 成功且不改 checkpoint。
- [ ] MinerU golden corpus 通過結構與 parser regression。
- [ ] 舊 queue 依最新 filesystem state 重建 checkpoint，不依 2026-07-20 PID。
- [ ] Ollama tag/digest、prompt/profile、token journal 已記錄，仍 advisory-only。
- [ ] 發布只做 dry-run；搬機驗收不順便推 production package。

### Backup/rollback

- [ ] Final DB dump 與 asset manifests 至少兩個獨立位置，checksum 已驗。
- [ ] Mac Studio writer 已停但 DB/資產/compose 均未刪除。
- [ ] 已定義回退窗口、負責人與「新主機已有寫入」時的資料回流方案。

## 回退

若 Ryzen 尚未接受任何人工寫入：

1. 先停止 Ryzen Review UI，確保只有零個 writer。
2. 以 freeze 前狀態重新啟動 Mac Studio Review UI。
3. 驗證 Mac Studio DB counts/max ids 與 UI，再恢復唯一 writer。

若 Ryzen 已接受人工寫入：

1. 先 freeze Ryzen writer並做新的 target logical dump／count snapshot。
2. 不可直接啟動舊 Mac DB，否則會遺失 Ryzen 期間的新 append-only events。
3. 優先把最新 Ryzen full dump 還原到隔離 Mac volume並驗證；若要事件級 merge，必須另做
   candidate key、event id、schema version 與 conflict audit。
4. 完成驗證後才重新指定 Mac Studio 為唯一 writer。

任何回退都不得用「兩邊先開著再看」的方式處理。

## 尚需使用者在新機到貨後確認

- 實際作業系統是否採 Ubuntu 24.04 native。
- RAM 容量、內建 NVMe 容量、第二備份位置。
- 固定 LAN IP／hostname／VPN/TLS 方案。
- PostgreSQL 是否只供本機容器使用，或保留受控遠端 ingest。
- Ryzen 是一次接管全部 production，或先只當 inference/MinerU worker 觀察一段時間。

推薦順序是先作 worker shadow、restore drill、完整驗收，再進行 production authority cutover。
