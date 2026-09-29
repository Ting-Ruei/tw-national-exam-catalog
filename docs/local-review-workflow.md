# 本機 Local Review Workflow

Status: current local-only workflow.

本流程在 repository 內建立一次不可覆寫的 deterministic review run，讀取當次任務指定的
source/candidate artifacts，產出可追溯的 review bundle。它不連線外部模型、不寫外部資料庫、
不啟動服務，也不自動 accept、block 或 publish。

## 安全邊界

- 每個 run 使用新的 `tmp/local-review/<run-id>/` 目錄；不可覆寫既有 run。
- source、candidate、analysis、summary 與 review events 必須同屬一個 run-id 並保存 hashes。
- 人工 review events append-only；模型輸出若經當次任務核准，只能作 advisory evidence。
- `production_write_count` 必須為 `0`；目前沒有 production writer 或 reconciliation connector。
- 未經 owner-approved contract，不得把 local JSONL、SQLite 或 bundle 匯入任何外部資料庫。

## 建議流程

1. 先讀 `configs/local_review.json` 與當次 task contract。
2. 凍結 source/candidate manifest，記錄每個輸入的 hash。
3. 執行 deterministic evidence collection 與 gate。
4. 在 local v2 review UI 檢查 findings；必要時由人明確選擇 review decision。
5. 追加 review event，不修改既有 event。
6. 由 reviewer 檢查 summary、hash、unresolved disagreement 與 write count。
7. 需要新輸出時建立新 run，不重用舊 run directory。

## 目前不提供

- external model provider、remote worker、remote UI、remote database 或 scheduler。
- automatic model calls、automatic materialization、production publish 或 rollback command。
- 任何以歷史 host、provider、profile 或 deployment 文件作為 fallback 的行為。

未來若需要 advisory model，必須另建 owner-approved contract，明確宣告 local endpoint、資料
權限、context/budget、輸出 schema、human gate 與 rollback；缺少任一項就停在 evidence boundary。
