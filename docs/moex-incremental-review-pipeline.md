# 考選部增量掃描到審核平台工作流

更新時間：2026-07-29（Asia/Taipei）

## 架構決策

本流程以 `scripts/run_moex_incremental_review_pipeline.py` 作為唯一的 deterministic worker。現在可以在本機手動執行；未來移到 AI MAX 395 後，讓 n8n 只負責排程、手動按鈕、失敗通知與重試，不把下載、MinerU 或 SQL 規則重寫在 n8n 節點裡。

Dify 不適合負責檔案下載、checkpoint、MinerU process 與 PostgreSQL migration；它可以在候選題已進審核層後，負責 AI advisory、RAG 或詳解草稿。AI 結果仍不得自動通過或阻擋題目。

目前人工審核權威是 AI395 的 `http://192.168.10.90:8765/` 與其 loopback-only PostgreSQL；本機與舊 Mac Studio PostgreSQL 只可視為驗證／回退資料，不得把各自事件反向覆蓋 production。每次新增題目應先同步 PDF 與 MinerU 產物，再用 `merge` 匯入 AI395，最後直接從 AI395 UI 驗證題目與 PDF。遠端 DB 維護必須經 SSH tunnel，不得把 54329 發布到 LAN。

```text
考選部當年度 catalog
        ↓ URL/registry-key 增量比對
本次 selected changes + checkpoint
        ↓ locked-27 精確類科路由
官方 Q / ANS / MOD PDF + 完整類科 manifest
        ↓ 只對本次 pair 執行
MinerU OCR（同類科／同考次目錄批次，只載入一次模型；舊 20,000 份歷史佇列保持停止）
        ↓
candidate JSONL + parse issue CSV
        ↓ 題號完整性 + 題組線索完整性 gate
已審 candidate 逐題欄位 diff
        ↓ 無既有人工審核失效風險才可 merge-only upsert
PostgreSQL review staging
        ↓
Review UI 人工題目／答案審核
```

## 本機執行

只掃描並產生差異報告，不下載、不改 checkpoint：

```bash
python3 scripts/run_moex_incremental_review_pipeline.py --scan-only
```

完整處理所有新出現且屬 locked-27 的類科：

```bash
python3 scripts/run_moex_incremental_review_pipeline.py --workers 2
```

限定某一考試代號（例如 115 年第二次專技高考）：

```bash
python3 scripts/run_moex_incremental_review_pipeline.py \
  --exam-code 115090 \
  --no-start-review-ui \
  --review-ui-url "$REVIEW_PRIMARY_UI_URL" \
  --workers 2
```

醫學增量流程的 grouped MinerU 預設不設執行時限，會等每個類科完整跑完才進到 candidate 與 PostgreSQL 階段。已完成的 Markdown 會在重跑時自動跳過，因此程序中止或失敗後可直接用同一指令安全續跑。

流程只有在 PDF、MinerU、candidate 與 PostgreSQL 匯入都成功後，才更新 `catalogs/moex_subject_catalog__y100-115.csv` checkpoint。任何階段失敗都可安全重跑同一指令：已下載 PDF 會以 checksum/manifest 辨識，已有 MinerU Markdown 會跳過，candidate 使用 `merge` upsert，不會刪除同類科歷史 staging 題目，也不會改寫 append-only 人工審核事件。

Candidate 進 PostgreSQL 前有三個 deterministic gate：

1. 題號結構不得有 `no_questions_parsed`、重複題號、題號缺口或固定題數缺漏／越界。這些情況代表題目本身可能不存在於 Review UI，不能交給人工審核補救。
2. 明示題組線索必須綁定完整範圍；`承上題` 必須找得到前題。題組數可以是 0，但不能有「看得到線索、候選卻遺失」的情況。
3. `ingest_question_candidates_to_postgres.py` 會逐題比較新舊可審內容。若題幹、選項、答案、題組、圖片、品質旗標等欄位改變，且相應的題目／答案／題組已有封閉人工審核，會先輸出 `reviewed_candidate_changes.json` 並中止，不能直接覆蓋。後續只能對報告列出的實際受影響題目追加保留舊註記的 `reset_review`／`reset_group_review`，再重試。

Parser 版本升級本身不會讓所有題目退回；gate 比對的是實際可審欄位，並排除單純 `parser_version` 與 raw block 的機械差異。

每次執行的狀態、差異 CSV、選定 pair 與各階段 log 位於：

```text
國考題資料夾/Registry/incremental_pipeline/<run-id>/
```

大型 PDF、MinerU output、candidate 與 checkpoint 都在 `.gitignore` 涵蓋的本機資料根，不應 commit。

## n8n 介面

n8n 工作流分成四個階段：

1. `Schedule Trigger`（例如每天 20:30，`Asia/Taipei`）與 `Manual Trigger` 匯入同一路徑。
2. `Execute Command` 執行 deterministic worker，不啟動本機 Review UI：

   ```bash
   cd "$TW_EXAM_REPO" && python3 scripts/run_moex_incremental_review_pipeline.py --workers 2 --no-start-review-ui --review-ui-url "$REVIEW_PRIMARY_UI_URL"
   ```

3. 以 `scripts/prepare_review_host_sync_bundle.py` 依本次 `run_state.json` 建立精準 bundle；先用 `artifacts.files-from.txt` rsync 到審核主機並核對 `artifact_manifest.csv`，再執行 `ingest_indexes_to_postgres.py` 與 `ingest_question_candidates_to_postgres.py --sync-mode merge`，兩者指定 `--postgres-host "$REVIEW_PRIMARY_DB_HOST" --postgres-port "$REVIEW_PRIMARY_DB_PORT"`。
4. 直接查詢遠端 UI 與 SQL 數量後寫入 `Registry/remote_review_sync/<sync-id>/sync_state.json`；依 exit code 分流成功／失敗通知。失敗重試必須從既有 bundle 接續，不依賴 catalog 再次偵測同一批題目。

若第 3 步因 reviewed-candidate diff gate 中止，n8n 必須把報告列為需人工處理，不得用無條件重試或跳過 gate 的方式繼續。

建議 n8n 設定：

- 同一時間只允許一個 production execution，避免兩次掃描同時重建 manifest/index。
- worker 也會取得 `Registry/incremental_pipeline/production.lock`；即使排程與手動按鈕同時觸發，後來的執行會明確失敗而不會碰資料。
- 關閉 n8n execution timeout（或設成不會中止長時間工作）；MinerU worker 的 `--mineru-timeout 0` 表示無時限。不要由 n8n 對單一 PDF 各自開 worker。
- `TW_EXAM_REPO`、`ASSET_ROOT`、`MINERU_BIN` 用環境變數，不在 workflow JSON 寫死機器路徑。
- `REVIEW_PRIMARY_UI_URL`、`REVIEW_PRIMARY_DB_HOST`、`REVIEW_PRIMARY_DB_PORT`、`REVIEW_PRIMARY_PROJECT_ROOT` 與 SSH key 路徑由 n8n credential／environment 提供；資料庫密碼不得寫入 workflow JSON 或 Git。
- 失敗重試直接重跑 worker，不清除 `.part` 以外的正式資產，不啟動移機交接文件所列的歷史 split-batch queue。

## 目前遠端審核主庫狀態

2026-07-20 已將考試代號 `115090` 同步到 `192.168.10.70`：114 份官方文件、57 組題目／答案、114 筆成功 MinerU provenance、4,456 題及 80 筆 parser issue。同步前已建立完整 custom-format `pg_dump`；1,483 個檔案逐一通過 SHA-256。匯入前後題目、答案及 AI 審核事件的 count 與 max ID 完全相同，因此既有審核紀錄未被改寫。

2026-07-29 重新稽核同一批 115-2 原始 Markdown，確認完整應為 4,480 題；舊 parser 將 `8.76 歲…`、`21.3 週齡…` 等「題號＋年齡」誤當小數，共漏 24 題。修正後 57 份試卷的題數分布為 2 份×100、4 份×50、51 份×80，無題號結構 blocker。另找到 27 個待人工題組／61 題，其中 7 組有明示範圍。這是 parser 預檢結果，不代表 4,480 題已覆蓋遠端主庫；套用前仍須通過逐題 diff 與 reset 流程。

稽核紀錄位於：

```text
國考題資料夾/Registry/remote_review_sync/20260720-134018/sync_state.json
```

手動「只看有沒有新題」可另建一條 n8n branch，執行 `--scan-only` 並讀取 `all_document_changes.csv`。因 scan-only 不提交 checkpoint，之後完整 branch 仍能看到並處理同一批更新。

## AI MAX 395 搬移

搬移時保持 repo 內 CLI 介面不變，只調整：

```bash
export TW_EXAM_REPO=/srv/tw-national-exam-catalog
export ASSET_ROOT=/data/tw-national-exam-catalog/國考題資料夾
export MINERU_BIN=/opt/mineru/venv/bin/mineru
```

接著驗證 `mineru --version`、Docker Compose PostgreSQL、Review UI、資料根掛載與一筆 `--scan-only`。確認完成前不要恢復 `MIGRATION_HANDOFF.md` 中暫停的 20,000 份歷史佇列。

## 資料安全邊界

- 官方 PDF 與 MinerU raw 永不被 parser 覆寫。
- 新 candidate 以 merge-only 方式進 SQL；全量 parser refresh 才可使用原本的 category replace 行為。
- 若未來偵測到既有題目的 Q/ANS/MOD URL 或內容被官方替換，應先產生待人工確認的 source-refresh 事件；不要沿用舊的答案通過狀態自動正式入庫。
- AI/Dify 輸出只進 advisory table，不寫人工 review event。
