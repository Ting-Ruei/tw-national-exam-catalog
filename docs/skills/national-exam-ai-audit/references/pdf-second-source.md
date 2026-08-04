# 官方 PDF 第二參考源

`MinerU` 產生的 candidate 是第一來源；官方 PDF 的 Python/多引擎抽取是第二來源，兩者不互相覆寫。
執行：

```bash
python3 scripts/build_pdf_reference_source.py \
  --pdf <official-question.pdf> \
  --output-dir tmp/pdf-reference/<sha256-prefix> \
  --render-dir tmp/pdf-reference/<sha256-prefix>/pages
```

工具會保存 PDF SHA-256、`pdfinfo`、頁碼、PDF 物件層摘要，以及每頁的
`pdftotext -layout`、`pdftotext -raw`、`pypdf`、`pdfplumber` 原文與比較正規化文字。
輸出 `pages.jsonl` 的 `consensus` 只有在至少兩個獨立 extractor family（Poppler、pypdf、pdfplumber）
相互一致時才標成 `usable_consensus`；下列情況一律標 `needs_manual_source_review`：

- extractor 互相不一致、只有單一 family 有文字，或文字層是空的；
- `�`/控制字元、重複行、PDF `/Contents` 多串流、疑似重疊文字層；
- 需要用頁面 PNG 觀察版面。PNG 只是 visual fallback，不得再送一輪 OCR 來覆蓋 MinerU。

## 題目比對規則

1. 先以題號、考別、頁碼與候選 `source_fingerprint` 定位頁面。
2. 在 `pages.jsonl` 的各 engine 原文中搜尋候選的局部 `before` 與候選建議的 `after`，保存頁碼、engine、normalized span 與座標（若日後加上 word-level extractor）。
3. 只有「候選字串存在且官方 PDF 明確是另一個局部字串」才可建立 `official_pdf_source_mismatch`；只看到學名不熟悉、縮寫或單一 Python 引擎差異，仍是 `human_pdf`。
4. PDF 第二來源結果應作為 sparse finding 的 `source_class`/`evidence`，不直接寫入題目、答案或 review event。人工作成正式決策後，才另行建立 active exact rule。

這樣可以處理考選部 PDF 的多層內容：不強迫 Python 選一個看似正確的文字層，而是留下可重現的引擎差異，將真正不確定的頁面送到 PDF/圖片人工核對。
