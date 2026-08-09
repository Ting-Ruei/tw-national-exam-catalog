# AI395 Catalog production cutover evidence

完成時間：2026-08-09 14:47（Asia/Taipei）

## 結論

AI395 `192.168.10.90` 已成為 `tw-national-exam-catalog` 唯一 production Review UI 與
PostgreSQL writer。桌面入口是 `http://192.168.10.90:8765/`，手機入口是
`http://192.168.10.90:8766/mobile/`；兩者共用同一個 process、ReviewState、PostgreSQL 與
formal-sync worker，並要求 Basic Auth。

舊 Mac Studio `192.168.10.70` 的 Review UI 已停止，8765 已確認未監聽；PostgreSQL 仍健康，
資產、Compose volume 與 fresh snapshot 均保留。至少在 2026-09-08 前不得刪除或讓它與
AI395 同時接受寫入。

## 不可變版本與資產

- Git/release SHA：`e89c60fd7502a0fce1c47c7b9577a211888504a9`
- release：`/srv/ai395/releases/tw-national-exam-catalog/e89c60fd7502a0fce1c47c7b9577a211888504a9`
- Review UI image ID：`sha256:6e51bbbf36d6532e85c5f974c8ce2fe5130c64147deec22408dd9c7994f918fb`
- canonical main：`/srv/ai395/data/tw-national-exam-catalog/main`
- versioned candidate：`/srv/ai395/data/tw-national-exam-catalog/main-candidates/main-20260809-652b0e0e-bf42c2c8`
- final entries：109,929（109,802 files、127 symlinks）
- regeneration queue：15 → 0
- final physical manifest SHA-256：`e261084b60fc94f7672fa85552ee397dacd9bf5bc9b28c8004a24326f1fb0e57`
- finalization evidence：`/srv/ai395/migration-inbox/tw-national-exam-catalog/migration-evidence/canonical-builds/main-20260809-652b0e0e-bf42c2c8-finalize-e89c60f-v1`
- 人工補圖 writable layer：393 files、33 MiB；freeze 後 checksum delta 為 0。

最後一次 Mac Studio asset pull 未使用 `--delete`，Linux filename-object map 6/6 通過。

## Fresh database 與雙主機備份

freeze snapshot：`postgres-git-20260809-144116`

- Mac Studio：`/Users/tim/ai395-migration-backups/tw-national-exam-catalog/cutover-20260809/postgres-git-20260809-144116`
- AI395：`/srv/ai395/migration-inbox/tw-national-exam-catalog/macstudio-snapshot/postgres-git-20260809-144116`

兩邊 `SHA256SUMS` 全部通過。snapshot 包含 PostgreSQL custom dump、schema、無密碼 globals、
restore list、Git bundle、tracked binary patch、untracked list/archive 與 worktree 狀態。

freeze 與 restore 後的 11 個基線完全相同：

```text
8295|191839|46336|20658|543944|15440|15429|9973|92095|41890|688253
```

欄位依序為 official documents、candidates、question review events、answer review events、AI review
events、formal questions、formal answers、assets，以及三種 event max id。

PostgreSQL 為 18.4，pgvector 為 0.8.2。Review UI 使用獨立 `tw_exam_review_ui` login role；驗收時
`rolsuper=false`、`rolcreatedb=false`、`rolcreaterole=false`。owner/admin 密碼與 UI 密碼只存在
AI395 mode-600 host env，不進 Git 或此文件。

## Security 與 smoke 結果

- PostgreSQL 只發布於 `127.0.0.1:54329`。
- Review UI 只綁固定 LAN `192.168.10.90:8765/8766`，未登入回應 401。
- 程式與 canonical main 以 read-only mount 提供；`40_manual_assets` 使用獨立 writable mount。
- `/file` 的正式 asset request 為 200；嘗試讀 `/workspace/requirements/review-ui.txt` 為 404。
- desktop、mobile、candidate API、pipeline API 均為 200。
- cutover 唯讀階段 POST 為 503；啟用 writer 後非破壞性 invalid POST 為 400。
- app-role 寫入測試在交易內成功後 rollback；probe row 不存在。
- manual-asset probe 只建立暫存檔並自動刪除。
- formal sync pending 為 0；production 容器 healthy。
- request body 上限 16 MiB、request timeout 60 秒、單 IP POST 上限 120/min。
- 隔離 restore-drill 容器已停止但 volume 保留；AI395 上只有 production Review UI process 在運行。
- AI395 ROCm 可辨識 Ryzen AI MAX+ 395，MinerU runtime 為 3.4.4；未發現 Catalog user/system timer、cron 或額外 pipeline worker。OCR production schedule 需另做 3.4.4 golden regression 後才可啟用。

正式容器啟動不執行 pip install；requirements 已建入 immutable image。

## 操作

一般狀態與驗證：

```bash
bash scripts/ai395_catalog_runtime.sh status
bash scripts/ai395_catalog_runtime.sh verify
```

AI395 host-side production control：

```text
/srv/ai395/stacks/tw-national-exam-catalog/production/ai395_catalog_production.sh
```

取得 UI username/password 時，由 operator 在自己的 terminal 讀 mode-600 env；不要貼進 issue、
commit、聊天或瀏覽器 URL：

```bash
ssh ai395 'grep -E "^REVIEW_UI_BASIC_AUTH_(USERNAME|PASSWORD)=" /etc/ai395/tw-national-exam-catalog/production.env'
```

## 回退

若 AI395 尚無 freeze 後新寫入：先停止 AI395 Review UI，確認 8765 不再接受請求，再驗證舊 Mac
counts 後恢復舊 Mac 唯一 writer。

若 AI395 已接受新寫入：先凍結 AI395，建立 fresh AI395 dump 並保存兩份；只可還原到隔離的
Mac volume，核對 counts/max ids/append-only events 後才切 authority。不得直接打開舊 Mac UI，
也不得讓兩個 writer 並行。

## 後續工作（本次刻意未重構）

下一階段重新檢討反覆修訂的 review workflow，為每題建立明確、可查詢且可稽核的階段模型，
再依該模型修理 UI。這次 cutover 只加入必要的 auth、readonly、file boundary、request limits 與
immutable deployment，沒有重新定義題目審核語意，也沒有批次改寫 append-only review events。
