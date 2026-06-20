# 遠端 MinerU 算力節點部署與批次回傳流程

本文記錄如何用另一台 MacBook 作為 MinerU 算力節點。目標是讓主控端負責任務切分、去重與入庫前索引；遠端算力機只負責接收任務批次、使用自己本機已同步好的官方 PDF、執行 MinerU、回傳輸出結果。

目前已驗證可同步的 MinerU 環境版本、freeze 清單與安裝順序，另見：

- [docs/mineru-environment-sync.md](/Users/tim/tw-national-exam-catalog/docs/mineru-environment-sync.md)
- [requirements/mineru-3.3.1-py314-freeze.txt](/Users/tim/tw-national-exam-catalog/requirements/mineru-3.3.1-py314-freeze.txt)

## 角色與網路位置

| 角色 | Tailscale IP | 職責 |
|---|---:|---|
| 主控端 | `100.96.146.93` | 保存完整 repo、官方 PDF、paired index、MinerU 全域狀態、去重與合併 |
| 遠端算力機 | `100.96.207.80` | 接收批次 PDF、在本機 SSD 跑 MinerU、回傳 output 與 result CSV |

原則：遠端算力機不要直接寫入主控端的 MinerU output 目錄。MinerU 會產生大量中間檔，跨網路直接寫入較慢，也比較容易留下半成品。正確流程是遠端本機跑完，再用 `rsync` 整批回傳。

此版本工作流預設是「只同步任務，不同步 PDF」。遠端算力機應事先自行準備完整官方 PDF 樹，主控端只負責分配哪些 PDF 要跑。

## 遠端算力機目錄規劃

遠端算力機統一使用：

```text
/Users/tim/AI_workspace
```

建議建立以下結構：

```text
/Users/tim/AI_workspace/
  OCR_model/
    MinerU/
      venv_mineru/
        bin/
          mineru
  national_exam_mineru_worker/
    incoming_batches/
    running_batches/
    finished_batches/
    logs/
```

其中：

- `OCR_model/MinerU`：MinerU 安裝位置。
- `incoming_batches`：主控端傳來、尚未開始跑的批次。
- `running_batches`：正在執行的批次。
- `finished_batches`：已完成、等待或已經回傳的批次。
- `logs`：遠端執行紀錄。

## 遠端算力機基本部署

遠端算力機可以直接從 GitHub 抓這個專案，並建立標準工作目錄：

```bash
bash scripts/setup_remote_mineru_worker.sh
```

這個腳本會：

- 建立 `/Users/tim/AI_workspace/national_exam_mineru_worker`。
- 建立 `incoming_batches`、`running_batches`、`finished_batches`、`logs`。
- 從 GitHub clone 或 pull `tw-national-exam-catalog`。
- 檢查 MinerU binary 是否存在。

若尚未 clone repo，也可以先手動建立資料夾：

```bash
mkdir -p /Users/tim/AI_workspace/OCR_model
mkdir -p /Users/tim/AI_workspace/national_exam_mineru_worker/incoming_batches
mkdir -p /Users/tim/AI_workspace/national_exam_mineru_worker/running_batches
mkdir -p /Users/tim/AI_workspace/national_exam_mineru_worker/finished_batches
mkdir -p /Users/tim/AI_workspace/national_exam_mineru_worker/logs
```

MinerU 建議部署到：

```text
/Users/tim/AI_workspace/OCR_model/MinerU/venv_mineru/bin/mineru
```

部署完成後確認：

```bash
/Users/tim/AI_workspace/OCR_model/MinerU/venv_mineru/bin/mineru --version
```

主控端目前的預設 MinerU 路徑是 `~/AI workspace/OCR_model/MinerU/...`，遠端算力機則使用 `~/AI_workspace/OCR_model/MinerU/...`。因此遠端執行時一律明確指定：

```bash
--mineru-bin /Users/tim/AI_workspace/OCR_model/MinerU/venv_mineru/bin/mineru
```

## 最新注意參數

這一版已確認過的實際運行參數如下：

- 本機主跑建議維持 `WORKERS=2`，上限 `3`，不要再往上加。
- MinerU 建議固定用 `MINERU_METHOD=ocr`。
- VLM 推論後端固定用 `MINERU_BACKEND=vlm-engine`。
- 圖像描述固定用 `MINERU_IMAGE_ANALYSIS=false`。
- 本機 queue 預設用 `PART_SORT_ORDER=asc`，讓 `part001` 往上跑。
- `scripts/run_mineru_pdf_batch.py` 現在會優先使用 batch 自己帶進來的 `--pdf-index`，不會回頭吃全域索引。
- 遠端機壓力高時，先不要再開新的 worker。
- 遠端若要接續跑，只讀那邊權威 batch 來源，不要在本機重新切同一份 part。
- 遠端權威 batch 來源目前以 `/Users/tim/tw-national-exam-catalog/國考題資料夾/Registry/mineru_remote_batches` 為準。

補充說明：

- MinerU `3.3.1` 雖然我們仍固定用 `vlm-engine`，但 CLI 預設值比舊版更重，特別是 `image-analysis=True` 時，單一 worker 可能把記憶體推到約 `12 GB`。
- 這次觀察到的高記憶體佔用，不是因為 backend 自動切成 `hybrid-engine`，而是新版預設功能變多。
- 因此目前主流程明確鎖定 `ocr + vlm-engine + image-analysis=false`，先把批次穩定度放在第一位；若之後要補做圖像描述，再另外開專用流程。

若之後要手動重啟本機 queue，建議使用：

```bash
PART_SORT_ORDER=asc WORKERS=2 MINERU_METHOD=ocr MINERU_BACKEND=vlm-engine \
MINERU_IMAGE_ANALYSIS=false \
  python3 scripts/start_local_split_batch_queue.py
```

## 批次資料夾格式

每一批由主控端產生，格式如下：

```text
mineru_remote_batch_YYYYMMDD-HHMMSS_partNNN/
  batch_manifest.csv
  pdf_asset_index_batch.csv
  question_answer_pairs_batch.csv      # 若此批來自 paired-primary，可包含
  國考題資料夾/
  output/
  logs/
```

必要欄位：

- `batch_manifest.csv`：批次任務清單，至少包含 `pdf_path`、`pdf_relative`、`sha256`、`document_role`、`group_name`。
- `pdf_asset_index_batch.csv`：只含本批 PDF 的 asset index；保留 `relative_asset_path`，遠端 runner 會在啟動前把它改寫成遠端本機的實際 PDF 絕對路徑。
- `question_answer_pairs_batch.csv`：若需要保持題目答案 pairing，保留本批涉及的 paired rows；其中 PDF path 欄位同樣要能在遠端批次目錄中解析。

主控端產生批次時必須先排除已完成項目；遠端只處理批次內 PDF，不負責判斷全域是否重複。

## 主控端批次狀態流

主控端會用下列資料夾表示批次狀態：

```text
國考題資料夾/Registry/mineru_remote_batches/
  outgoing/                 # 尚未送出
  assigned/<worker>/        # 已送到指定遠端 worker
  returned/<worker>/        # 已由遠端回傳，等待主控端合併
  merged/<worker>/          # 已合併回本機 output 與 results
```

這樣做的好處是很單純：

- `create_mineru_remote_batch.py` 會排除已完成 PDF。
- 它也會排除任何已經出現在 `outgoing/`、`assigned/`、`returned/`、`merged/` 的 `batch_manifest.csv` 裡的 PDF。
- 因此同一份 `paired-primary` PDF 不會被重複切成第二個 batch。

## 主控端拆解待做清單

主控端用 `scripts/create_mineru_remote_batch.py` 產生遠端批次。第一輪建議先 dry-run：

```bash
python3 scripts/create_mineru_remote_batch.py \
  --scope paired-primary \
  --batch-size 50 \
  --batch-count 1 \
  --dry-run
```

確認清單合理後，正式建立批次：

```bash
python3 scripts/create_mineru_remote_batch.py \
  --scope paired-primary \
  --batch-size 50 \
  --batch-count 1
```

這個命令預設就是 `manifest-only`，不會把 PDF 複製進 batch。

若真的需要把 PDF 一起塞進 batch，再顯式指定：

```bash
python3 scripts/create_mineru_remote_batch.py \
  --scope paired-primary \
  --batch-size 50 \
  --batch-count 1 \
  --batch-mode copy-pdfs
```

預設排序是 `--order reverse`，也就是從待做清單尾端開始切。這是為了降低跟主控端目前背景 MinerU 任務撞到同一批 PDF 的機率。若要按照正向順序切，可加：

```bash
--order forward
```

產生位置：

```text
國考題資料夾/Registry/mineru_remote_batches/outgoing/
```

若要一次切成多批、每批 50 份：

```bash
python3 scripts/create_mineru_remote_batch.py \
  --scope paired-primary \
  --batch-size 50 \
  --batch-count 10
```

這會建立 `10` 個 batch，每個 batch `50` 份 PDF；若剩餘不足 50，最後一批只會放剩下的數量。

## 主控端傳送批次到遠端

若要手動傳送，假設批次目錄位於：

```text
/Users/tim/tw-national-exam-catalog/國考題資料夾/Registry/mineru_remote_batches/outgoing/mineru_remote_batch_001
```

傳送到遠端：

```bash
rsync -avh --progress \
  /Users/tim/tw-national-exam-catalog/國考題資料夾/Registry/mineru_remote_batches/outgoing/mineru_remote_batch_001/ \
  tim@100.96.207.80:/Users/tim/AI_workspace/national_exam_mineru_worker/incoming_batches/mineru_remote_batch_001/
```

建議先測試 SSH / rsync：

```bash
ssh tim@100.96.207.80 'hostname && pwd'
rsync -avh --dry-run \
  /Users/tim/tw-national-exam-catalog/README.md \
  tim@100.96.207.80:/Users/tim/AI_workspace/national_exam_mineru_worker/
```

較建議直接用主控端同步腳本：

```bash
bash scripts/push_remote_mineru_batches.sh 100.96.207.80
```

這個腳本會：

- 從 `outgoing/` 讀取 batch。
- 用 `rsync` 傳到遠端 `incoming_batches/`。
- 傳送的主要是 manifest、batch index 與 runner 腳本，而不是官方 PDF 本體。
- 傳送成功後，把本機 batch 移到 `assigned/100-96-207-80/`。

若只想先送 2 批：

```bash
BATCH_LIMIT=2 bash scripts/push_remote_mineru_batches.sh 100.96.207.80
```

## 遠端算力機執行批次

遠端算力機若已經從 GitHub clone/pull 這個 repo，可以直接用標準 runner：

```bash
bash /Users/tim/AI_workspace/national_exam_mineru_worker/repo/scripts/run_remote_mineru_batch.sh \
  mineru_remote_batch_001
```

預設使用 `workers=2`。若遠端機器壓力較大，可以改成：

```bash
WORKERS=1 bash /Users/tim/AI_workspace/national_exam_mineru_worker/repo/scripts/run_remote_mineru_batch.sh \
  mineru_remote_batch_001
```

runner 會自動：

1. 從 `incoming_batches` 移到 `running_batches`。
2. 進入批次工作目錄。
3. 把 `pdf_asset_index_batch.csv` 轉成 `pdf_asset_index_runtime.csv`，其中 `asset_path` 會指向遠端本機自己的 PDF 目錄。
4. 使用批次內的 `scripts/run_mineru_pdf_batch.py` 執行 MinerU。
5. 完成後移到 `finished_batches`。
6. 將 log 寫入 `/Users/tim/AI_workspace/national_exam_mineru_worker/logs`。

遠端 runner 預設使用的官方 PDF 根目錄是：

```text
/Users/tim/AI_workspace/national_exam_mineru_worker/repo/國考題資料夾
```

若遠端把官方 PDF 放在別處，可在執行前覆寫：

```bash
REMOTE_ASSET_ROOT=/path/to/國考題資料夾 \
bash /Users/tim/AI_workspace/national_exam_mineru_worker/repo/scripts/run_remote_mineru_batch.sh \
  mineru_remote_batch_001
```

若需要手動排查，也可以進入批次工作目錄後執行：

```bash
REMOTE_ASSET_ROOT=/Users/tim/AI_workspace/national_exam_mineru_worker/repo/國考題資料夾 \
bash /Users/tim/AI_workspace/national_exam_mineru_worker/repo/scripts/run_remote_mineru_batch.sh \
  mineru_remote_batch_001
```

## 遠端回傳結果到主控端

主控端建議使用 Homebrew 版 `rsync 3.4.4`。腳本會優先使用 `/opt/homebrew/bin/rsync`，若需要指定其他路徑，可用 `RSYNC_BIN=/path/to/rsync` 覆蓋。

若要手動從遠端推回，遠端算力機執行：

```bash
rsync -avh --progress \
  /Users/tim/AI_workspace/national_exam_mineru_worker/finished_batches/mineru_remote_batch_001/ \
  tim@100.96.146.93:/Users/tim/tw-national-exam-catalog/國考題資料夾/Registry/mineru_remote_batches/returned/mineru_remote_batch_001/
```

也可以由主控端拉回：

```bash
rsync -avh --progress \
  tim@100.96.207.80:/Users/tim/AI_workspace/national_exam_mineru_worker/finished_batches/mineru_remote_batch_001/ \
  /Users/tim/tw-national-exam-catalog/國考題資料夾/Registry/mineru_remote_batches/returned/mineru_remote_batch_001/
```

較建議由主控端主動拉回：

```bash
bash scripts/pull_remote_mineru_batches.sh 100.96.207.80
```

這個腳本會：

- 掃描遠端 `finished_batches/`。
- 把完成批次同步到本機 `returned/100-96-207-80/`。
- 預設略過本機已經在 `merged/100-96-207-80/` 的批次，避免遠端保留舊資料時重複拉回。
- 保留遠端檔案，方便再次檢查。

正式拉回前可以先 dry-run：

```bash
DRY_RUN=1 bash scripts/pull_remote_mineru_batches.sh 100.96.207.80
```

若要先小批量確認：

```bash
BATCH_LIMIT=3 bash scripts/pull_remote_mineru_batches.sh 100.96.207.80
```

若要拉回後立刻合併進主控端 MinerU output：

```bash
MERGE_AFTER_PULL=1 bash scripts/pull_remote_mineru_batches.sh 100.96.207.80
```

若遠端批次內容需要嚴格鏡像到本機 `returned/`，可以加上 `RSYNC_DELETE=1`；一般不建議預設使用，避免排查期間誤刪本機保留檔。

## 主控端合併與不重複機制

主控端是唯一負責全域去重的地方。合併遠端結果時依序檢查：

1. `sha256` 是否已存在於主控端完成結果。
2. `pdf_path` 或 `pdf_relative` 是否已存在於主控端完成結果。
3. `expected_md` 對應的 markdown 是否已存在。
4. 若同一 PDF 同時有多份結果，保留先完成且 `status=ok` 的結果，其他標記為 duplicate provenance，不覆寫原始結果。

主控端目前的本機批次已支援「看到 expected markdown 已存在就跳過」，因此遠端回傳後再啟動本機補跑，不應重跑已合併的 PDF。

合併動作用：

```bash
python3 scripts/merge_remote_mineru_batches.py
```

這個腳本會：

- 掃描 `returned/<worker>/mineru_remote_batch_*`。
- 將遠端 `國考題資料夾/20_mineru_output/by_official_catalog/` 內容複製回本機對應位置。
- 產生一份已改寫成「本機絕對路徑」的 `mineru_results__remote-merge__*.csv`，放到：

```text
國考題資料夾/Registry/mineru_runs/remote_imports/
```

- 合併成功後，將 batch 從 `returned/` 移到 `merged/`。

若只想先演練不真的複製：

```bash
python3 scripts/merge_remote_mineru_batches.py --dry-run
```

答案邏輯仍以 paired index 為準：

- 若有 `_MOD`，`answer_pdf_primary` 使用 `_MOD`。
- 若沒有 `_MOD`，才使用 `_ANS`。
- 遠端 MinerU 只產生解析結果，不決定答案優先權。

## 建議批次大小

第一輪遠端測試建議：

```text
20-50 份 PDF
```

確認 MinerU、rsync、回傳合併都正常後，再增加到：

```text
100-200 份 PDF / batch
```

若遠端 MacBook 記憶體與 GPU 壓力較高，維持：

```text
workers=1
```

若狀況穩定，再提升到：

```text
workers=2
```

不建議遠端預設使用 3 workers；3 workers 只作為短時間備用策略。

## 失敗與續跑

若遠端中斷：

1. 不刪除 batch。
2. 重新執行相同命令。
3. 不加 `--force`。
4. 已經產生 expected markdown 的 PDF 會被跳過。

若回傳中斷：

```bash
rsync -avh --progress --partial
```

可保留部分傳輸成果並續傳。

## 安全邊界

- 遠端算力機不需要 PostgreSQL。
- 遠端算力機不需要 GitHub 權限。
- 遠端算力機不寫入主控端 repo。
- 遠端算力機只保存暫存 PDF 與 MinerU output。
- 公開 repo 不應 commit 批次 PDF、MinerU output、圖片或資料庫 dump。

## 後續自動化方向

目前建議先用批次資料夾與 `rsync`，原因是穩定、易除錯、遇到斷線可恢復。等遠端工作流確認穩定後，再加入：

- `scripts/create_mineru_remote_batch.py`：主控端切出未完成 PDF 批次。
- `scripts/run_remote_mineru_batch.sh`：遠端標準啟動器。
- `scripts/merge_remote_mineru_results.py`：主控端合併遠端 result CSV 並去重。
- PostgreSQL job queue：多台 worker 自動 claim job。
