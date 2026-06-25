# External Data Location

Updated: 2026-06-24 09:30 Asia/Taipei

The local data root `國考題資料夾_其他類型/` has completed its current PDF download and MinerU processing stage, then was moved off the local disk to avoid occupying internal storage.

External location:

```text
/Volumes/2T外接硬碟/tw-national-exam-catalog/國考題資料夾_其他類型
```

Current status at move time:

- PDF files: 7,335
- MinerU markdown files: 7,335
- MinerU `content_list.json` files: 7,335
- MinerU `middle.json` files: 7,335
- MinerU batch state: 294 done, 0 partial, 0 failed, 0 pending
- MinerU output size: about 5.5G
- Total data root size: about 6.8G
- Reconciliation status: 58 strict-path errors reconciled; 0 unresolved; no rerun needed

This dataset is not needed for the current local workflow. If it is needed again later, copy or move the folder back into the repository root before running scripts that expect `ASSET_ROOT=國考題資料夾_其他類型`.

