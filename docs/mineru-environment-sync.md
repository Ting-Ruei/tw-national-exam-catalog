# MinerU 環境同步紀錄

本文記錄目前主控端已驗證可用的 MinerU 執行環境，供另一台 MacBook 直接同步。

## 目前已驗證版本

- Python: `3.14.5`
- MinerU CLI: `3.3.1`
- venv 路徑:
  - 主控端: `/Users/tim/AI workspace/OCR_model/MinerU/venv_mineru`
  - 遠端算力機建議: `/Users/tim/AI_workspace/OCR_model/MinerU/venv_mineru`

## 目前主流程設定

本 repo 目前大規模批次主流程固定使用：

- method: `ocr`
- backend: `vlm-engine`

原因：

- `3.3.x` 預設容易走到 `hybrid-engine`，輸出目錄會變成 `hybrid_auto/`
- 我們目前既有批次、索引、paired 與後續入庫流程都以 `vlm/` 為主
- 因此主流程先鎖定 `vlm-engine`，維持輸出結構穩定

相關腳本：

- [scripts/run_mineru_pdf_batch.py](/Users/tim/tw-national-exam-catalog/scripts/run_mineru_pdf_batch.py)
- [scripts/benchmark_mineru_workers.py](/Users/tim/tw-national-exam-catalog/scripts/benchmark_mineru_workers.py)

## 另一台機器的建議安裝位置

```text
/Users/tim/AI_workspace/
  OCR_model/
    MinerU/
      venv_mineru/
```

## 建議同步流程

### 1. 建立 venv

```bash
mkdir -p /Users/tim/AI_workspace/OCR_model/MinerU
cd /Users/tim/AI_workspace/OCR_model/MinerU
python3.14 -m venv venv_mineru
```

若該機器的 `python3.14` 尚未準備好，先安裝可用的 Python `3.14.x`。

### 2. 安裝 MinerU 主套件

```bash
/Users/tim/.local/bin/uv pip install \
  -p /Users/tim/AI_workspace/OCR_model/MinerU/venv_mineru/bin/python \
  -U "mineru[all]==3.3.1"
```

### 3. 以 freeze 檔對齊套件

本次已凍結的套件版本清單在：

- [requirements/mineru-3.3.1-py314-freeze.txt](/Users/tim/tw-national-exam-catalog/requirements/mineru-3.3.1-py314-freeze.txt)

安裝方式：

```bash
/Users/tim/.local/bin/uv pip install \
  -p /Users/tim/AI_workspace/OCR_model/MinerU/venv_mineru/bin/python \
  -r /path/to/tw-national-exam-catalog/requirements/mineru-3.3.1-py314-freeze.txt
```

建議順序是：

1. 先裝 `mineru[all]==3.3.1`
2. 再用 freeze 檔補齊精確版本

這樣比較不容易因為單一 wheel 差異而卡住。

### 4. 驗證

```bash
/Users/tim/AI_workspace/OCR_model/MinerU/venv_mineru/bin/python --version
/Users/tim/AI_workspace/OCR_model/MinerU/venv_mineru/bin/mineru --version
```

預期：

- Python `3.14.5`
- `mineru, version 3.3.1`

## 主控端目前 freeze 基準

以下為這次同步時主控端的關鍵套件版本：

- `mineru==3.3.1`
- `mineru_vl_utils==1.0.5`
- `mlx==0.31.1`
- `mlx-lm==0.29.1`
- `mlx-vlm==0.3.9`
- `torch==2.12.0`
- `torchvision==0.27.0`
- `onnxruntime==1.27.0`
- `transformers==4.57.6`
- `openai==2.42.0`
- `pandas==3.0.3`

## 注意事項

- `pip freeze` 內含大量相依套件，另一台機器若是同架構的 Apple Silicon Mac，通常可直接對齊。
- 若另一台機器是不同 Python patch version 或不同系統環境，少數 wheel 可能需要重解依賴。
- 目前本 repo 的大規模批次已驗證 `3.3.1 + vlm-engine` 可運作。
- 後續若要加入 `hybrid_auto` 作為 fallback，再另外補文件，不要直接改動主流程。
