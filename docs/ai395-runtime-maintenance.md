# AI395 runtime 維護與本機退場計畫

更新：2026-08-09（Asia/Taipei）

## 現行角色

- AI395 是預設部署、驗證與除錯目標；SSH alias 為 `ai395`，Tailscale IP 為
  `100.65.112.73`，operator checkout 為 `/home/tim/src/tw-national-exam-catalog`。
- AI395 現行 Catalog stack 是隔離 restore drill：desktop `127.0.0.1:8875`、mobile
  `127.0.0.1:8876`、PostgreSQL `127.0.0.1:54330`。它不是 production writer。
- Mac Studio `http://192.168.10.70:8765/` 與其 PostgreSQL 仍是唯一 production
  review authority。`REVIEW_PRIMARY_*` 在正式 single-writer cutover 前不得改指 AI395。
- MacBook local Compose 是 legacy fallback；不再作預設維護目標，也不啟動新的長時間
  MinerU、AI audit、queue 或正式審核工作。

截至 2026-08-09 的 live evidence：AI395 checkout `618543b0335a5f5b36e1b18e872fa25c7db40e3a`
為 clean；restore drill 兩個容器已連續運行 13 小時、restart 0；DB expected/actual counts
完全一致。Versioned canonical candidate 已通過 physical acceptance，但仍有 15 個
regeneration items，`/srv/ai395/data/tw-national-exam-catalog/main` 尚未建立。

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

然後只作 read-only inspection：

```text
http://127.0.0.1:8875/
http://127.0.0.1:8876/mobile/
```

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

## 與 production cutover 的分離

一個月本機退場不等於 production 搬家完成。AI395 writer cutover 仍至少需要：15 個
regeneration items 歸零、canonical `main` promotion、Mac Studio maintenance freeze、fresh
DB dump 與 asset delta、第二位置備份、rollback 演練、UI/security acceptance，以及使用者
對 single-writer 切換的明確核准。在此之前，AI395 restore drill 不得接收正式審題寫入。

## 交接來源

本文件整理自 AI395 架構 repository 於 2026-08-09 驗證的：

- `docs/MIGRATED_SERVICES_SESSION_HANDOFF.md`
- `docs/handoffs/TW_NATIONAL_EXAM_CATALOG_SESSION_TRANSFER_2026-08-09.md`
- `docs/services/TW_NATIONAL_EXAM_CATALOG_AI395_RUNTIME.md`
- `docs/services/TW_NATIONAL_EXAM_CATALOG_ASSET_CONFLICT_RESOLUTION.md`

若外部架構文件與本文件的 live evidence 不一致，先停止變更並重新做只讀 audit；不得以
mtime、舊 PID 或單一來源覆蓋 checksum-backed conflict resolution。
