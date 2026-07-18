# 專案移機交接狀態

更新時間：2026-07-18 21:07:18（Asia/Taipei）

## 目前角色

- 本機的 MinerU 背景佇列已暫停，不會自動恢復。
- 本機後續定位為考題伺服器與相關服務部署主機。
- 大批量 PDF/MinerU 處理預計整批移植到另一台電腦後再繼續。
- 暫停時未刪除、移動或清理任何正式 PDF 與既有 MinerU 輸出。

## 暫停點

- 任務：`20260717-20000-verified`，共 20,000 份、800 批，每批 25 份。
- 已完成批次：116。
- 部分完成批次：10。
- 待處理批次：674。
- 下一個續跑批次：`part674`；停機前該批次已完成 3 份，重啟時會依實際 Markdown 跳過。
- 正式任務實際完成：3,109 份。
- 正式任務尚未完成：16,891 份，其中 16,847 份仍在佇列、44 份位於 partial 批次。

## 全量清冊

- 官方 PDF：96,922 份，缺失 0 份。
- MinerU 完成：37,438 份。
- 目前佇列中且未完成：16,847 份。
- 未排入目前佇列：42,637 份。
- 資料根目錄：約 38 GB；PDF 約 13 GB；MinerU 輸出約 24 GB。

權威交接報告：

`國考題資料夾_非醫學剩餘全集/Registry/reports/migration_handoff__20260718-210718/README.md`

機器可讀 checkpoint：

`國考題資料夾_非醫學剩餘全集/Registry/reports/migration_handoff__20260718-210718/checkpoint.json`

完成狀態逐檔清冊：

`國考題資料夾_非醫學剩餘全集/Registry/reports/mineru_completion_inventory__20260718-210718/mineru_completion_inventory.csv`

## 重要限制

舊日誌和部分 manifest 仍包含 `/Users/tim/tw-national-exam-catalog` 絕對路徑。新電腦若不使用相同路徑，必須設定新的 `ASSET_ROOT` 與 `MINERU_BIN`。佇列會從 `relative_asset_path` 重建 runtime PDF 路徑，不應直接沿用舊的 `asset_path`。

在新電腦完成搬移與數量驗證前，不要在本機重新啟動 `start_local_split_batch_queue.py`。
