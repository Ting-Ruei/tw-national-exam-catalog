# 專案移機交接狀態

> 現行狀態（2026-08-09）：AI395 已成為預設部署、驗證與除錯目標；操作入口與
> 一個月本機退場計畫見 `docs/ai395-runtime-maintenance.md`。AI395 現在仍是隔離
> restore drill，Mac Studio `192.168.10.70:8765` 仍是唯一 production writer。
>
> 歷史狀態警告（2026-08-07）：本文件保存 2026-07-20 的非醫學 MinerU queue
> checkpoint，不是 Ryzen AI Max 395 的現行搬遷手冊。PID、批次數與「目前正在執行」
> 等敘述都必須在切換前重新產生，不能直接採信。完整主機、Git、PostgreSQL、資產、
> 驗收與回退流程請以 `docs/ryzen-ai-max-395-migration-runbook.md` 為準。

更新時間：2026-07-20 11:30:46（Asia/Taipei）

## 目前角色

- 20,000 份非醫學 MinerU 歷史佇列已於 2026-07-20 17:24:29 從 `part515` 恢復執行。
- 目前固定 `WORKERS=1`、`TIMEOUT_SECONDS=0`，讓工作一路跑到 queue 清空；不得另開第二個 worker／queue。
- 本機目前執行 MinerU queue 與醫學增量 worker；人工審核權威為 `http://192.168.10.70:8765/`。
- Active queue PID/PGID 為 `31901`，PID 檔為 `Registry/mineru_remote_batches/local_queue__active.pid`。
- 執行 log：`Registry/mineru_remote_batches/local_queue__20260720-172429.log`。
- 暫停時未刪除、移動或清理任何正式 PDF、batch directory 或既有 MinerU 輸出。

最新機器可讀 running checkpoint：

`國考題資料夾_非醫學剩餘全集/Registry/reports/mineru_queue_resume__20260720-172429/status.json`

恢復驗證：首批正確選到 `part515`，既有 13 份 Markdown 全部回報 `skipped_existing`；其後前 2 份新工作已成功完成，queue 正持續往下處理。

## 恢復起點（取代 2026-07-18 checkpoint）

- 任務：`20260717-20000-verified`，共 20,000 份、800 批，每批 25 份。
- 實際完成：6,831 份。
- 尚未完成：13,169 份。
- 批次狀態：`local_done=209`、`local_partial=76`、`local_running=1`、`outgoing=514`。
- 下一個續跑批次：`part515`，保留在 `local_running`。
- `part515` 已完成 13 份、尚未完成 12 份；續跑時會依實際 Markdown 自動跳過已完成檔案。
- 已停止 process group `40821`，並驗證 queue PID 不再執行。
- stale PID 已封存為 `Registry/mineru_remote_batches/local_queue__paused__20260720-113041.pid`。

最新機器可讀 checkpoint：

`國考題資料夾_非醫學剩餘全集/Registry/reports/mineru_queue_pause__20260720-113041/checkpoint.json`

批次摘要與 `part515` 逐檔完成／未完成清單位於同一報告目錄。

## 全量清冊

最新實際 filesystem inventory：

- 官方 PDF：96,922 份。
- MinerU 完成：41,158 份。
- 正式任務 `outgoing` 佇列：12,850 份。
- 暫停中的 `part515` 未完成：12 份（inventory 因 batch directory 保留在 `local_running`，顯示為 `running`，但目前沒有執行程序）。
- 未排入目前正式任務：42,902 份。
- PDF 缺失：0 份。

逐檔清冊：

`國考題資料夾_非醫學剩餘全集/Registry/reports/mineru_completion_inventory__20260720-113041/mineru_completion_inventory.csv`

## 中斷後續跑方式

目前 queue 正在執行，不要重複下列命令。只有在 PID `31901` 已確認不存在且需要中斷復原時，才使用目的地主機的路徑重新啟動；`run_local_split_batch_queue.sh` 會優先選擇 `local_running`：

```bash
ASSET_ROOT='/new/path/國考題資料夾_非醫學剩餘全集' \
MINERU_BIN='/new/path/to/mineru' \
WORKERS=1 \
TIMEOUT_SECONDS=0 \
python3 scripts/start_local_split_batch_queue.py
```

`TIMEOUT_SECONDS=0` 表示不對單份 MinerU 工作設定時限；啟動後讓該批一路完成，再進行後續工作。

啟動後第一批必須是 `mineru_remote_batch_20260717-20000-verified_part515`，且應先跳過已有 Markdown 的 13 份，再處理其餘 12 份。

## 重要限制

舊日誌和部分 manifest 仍包含舊機器絕對路徑。AI MAX 395 必須設定新的 `ASSET_ROOT` 與 `MINERU_BIN`；queue 會從 `relative_asset_path` 重建 runtime PDF 路徑，不應直接沿用舊的 `asset_path`。

2026-07-18 的 `migration_handoff__20260718-210718` 僅保留為歷史紀錄；其 `part674` 暫停點已過期，不能再作為續跑依據。
