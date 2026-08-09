# AI395 runtime 維護與本機退場計畫

更新：2026-08-09（Asia/Taipei）

## 現行角色

- AI395 是預設部署、驗證與除錯目標；SSH alias 為 `ai395`，Tailscale IP 為
  `100.65.112.73`，operator checkout 為 `/home/tim/src/tw-national-exam-catalog`。
- AI395 production stack 是唯一 writer：desktop `192.168.10.90:8765`、mobile
  `192.168.10.90:8766/mobile/`、PostgreSQL `127.0.0.1:54329`。UI 需要 Basic Auth，DB 不發布到 LAN。
- 隔離 restore drill 的容器已停止、volume 仍保留；若另行啟動，仍只可使用
  `127.0.0.1:8875/8876/54330` 並不得接收 production 審核事件。
- Mac Studio Review UI 已停止；其 PostgreSQL、資產、Compose volume 與 freeze snapshot
  保留至至少 2026-09-08，僅供回退，不得與 AI395 同時啟用 writer。
- MacBook local Compose 是 legacy fallback；不再作預設維護目標，也不啟動新的長時間
  MinerU、AI audit、queue 或正式審核工作。

截至 2026-08-09 14:47 +08:00 的 live evidence：production release
`e89c60fd7502a0fce1c47c7b9577a211888504a9`；canonical regeneration queue 為 0；final
manifest SHA-256 為 `e261084b60fc94f7672fa85552ee397dacd9bf5bc9b28c8004a24326f1fb0e57`；
11 個 DB counts/max-id 與 freeze snapshot 完全一致。完整證據見
`docs/ai395-production-cutover-2026-08-09.md`。

## 每次維護的預設流程

```bash
bash scripts/ai395_catalog_runtime.sh checkout
bash scripts/ai395_catalog_runtime.sh status
bash scripts/ai395_catalog_runtime.sh verify
```

需要瀏覽 UI 時另開一個 terminal：

```bash
bash scripts/ai395_catalog_runtime.sh tunnel
```

直接使用 LAN production UI（需登入）：

```text
http://192.168.10.90:8765/
http://192.168.10.90:8766/mobile/
```

`tunnel` 仍可用於 DB 維護；本機 `8875/8876` 會轉送 production UI，DB `54329` 轉送
AI395 loopback。runtime helper 刻意不暴露 restore 或 enable-writes 動作。

程式修改仍在 MacBook Git worktree 完成並測試；形成 reviewed commit 後，才讓 AI395
operator checkout 前進到 exact SHA。不得直接修改 immutable release，也不得留下 server-only patch。

## 一個月並行退場

| 日期 | 本機角色 | 要求 |
|---|---|---|
| 2026-08-09～08-15 | warm fallback | 本機 stack 可運行，但只綁 loopback、取消自動重啟；所有日常驗證優先使用 AI395。 |
| 2026-08-16～08-29 | stopped fallback | 平時執行 `docker compose stop`；只有記錄原因的 fallback test 才暫時啟動，完成即停止。 |
| 2026-08-30～09-08 | cold standby | 本機容器保持停止；保留 PostgreSQL volume、資產與最後驗證紀錄，不執行維護工作。 |
| 2026-09-09 起 | retirement review | 確認 AI395 availability、備份與回退證據後，另行核准是否移除本機 container/image。不得自動執行 `down -v` 或刪除資料。 |

本機 fallback 啟動前必須先確認 `.env` 仍含：

```text
CATALOG_RESTART_POLICY=no
POSTGRES_BIND=127.0.0.1
REVIEW_UI_BIND=127.0.0.1
```

狀態與停止命令：

```bash
docker compose ps
docker compose stop
```

停止只停容器並保留 volume。任何 volume、資產、dump 或 migration staging 的刪除都需要
獨立核准。

## Production 與回退邊界

AI395 writer cutover 已完成；一個月退場期的目的改為保留可驗證回退能力。平時不得啟動
Mac Studio 或 MacBook Review UI。若 AI395 尚未接受任何新寫入，可先停止 AI395 UI，再驗證並
恢復舊 Mac writer；若 AI395 已有新寫入，必須先凍結 AI395、建立 fresh dump，並在隔離 Mac
volume 還原與核對，不能直接開啟兩個 writer。

審核 UI 的資訊架構與「題目目前在哪個階段」模型尚未在本次搬遷中重構；該議題列為 cutover
後的下一個設計工作，不應用臨時欄位或批次 reset 代替正式階段模型。

## 交接來源

本文件整理自 AI395 架構 repository 於 2026-08-09 驗證的：

- `docs/MIGRATED_SERVICES_SESSION_HANDOFF.md`
- `docs/handoffs/TW_NATIONAL_EXAM_CATALOG_SESSION_TRANSFER_2026-08-09.md`
- `docs/services/TW_NATIONAL_EXAM_CATALOG_AI395_RUNTIME.md`
- `docs/services/TW_NATIONAL_EXAM_CATALOG_ASSET_CONFLICT_RESOLUTION.md`

若外部架構文件與本文件的 live evidence 不一致，先停止變更並重新做只讀 audit；不得以
mtime、舊 PID 或單一來源覆蓋 checksum-backed conflict resolution。
