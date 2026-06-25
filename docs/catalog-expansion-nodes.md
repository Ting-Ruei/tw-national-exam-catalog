# Catalog Expansion Nodes

本文記錄民國 100-115 年考選部 catalog 的拓展節點。這份紀錄用來避免把「目前正在跑的資料夾」誤認為完整國考全集，也提供後續下載、MinerU 拆解與題庫 parser 排程依據。

## 範圍基準

完整 catalog：

- catalog 科目列：70,555
- distinct exam codes：336
- exam code + category 組合：11,907
- PDF URL 文件數：111,810
- 類科名稱：1,114

目前主線：

- `國考題資料夾/`：醫事 locked27 主線與既有 Review UI / parser 工作。
- `國考題資料夾_其他類型/`：已啟動的 97 個「專技高普考其他類科種子集」。

重要差異：

- `catalogs/other_professional_high_categories_excluding_locked27__y100-115.csv` 不是完整非醫學全集。
- 這份 seed 清單只有 97 類、5,392 catalog rows、7,335 PDF URL 文件。
- 真正排除 locked27 後的剩餘全集有 1,087 類、67,358 catalog rows、104,257 PDF URL 文件。

## 已建立的拓展節點

分類索引由下列腳本產生：

```bash
python3 scripts/build_moex_expansion_nodes.py
```

輸出檔：

- `catalogs/moex_expansion_node_summary__y100-115.csv`
- `catalogs/moex_expansion_category_summary__y100-115.csv`
- `catalogs/moex_expansion_subject_summary__y100-115.csv`
- `catalogs/moex_official_category_track_summary__y100-115.csv`
- `catalogs/moex_official_category_subjects__y100-115.csv`

目前節點：

| node_id | catalog rows | PDF URL 文件 | 類科 | 科目名 | 用途 |
|---|---:|---:|---:|---:|---|
| `locked27_medical_current` | 3,197 | 7,553 | 27 | 159 | 醫事 locked27 現行主線。 |
| `professional_high_other_seed` | 5,392 | 7,335 | 97 | 386 | 已另開資料夾與背景 MinerU queue 的 97 類專技種子集。 |
| `professional_technical_remaining` | 846 | 1,656 | 20 | 70 | 其餘專門職業及技術人員考試。 |
| `civil_service_core` | 19,499 | 28,603 | 212 | 1,278 | 公務人員高普初等與一般行政類。 |
| `civil_service_special` | 32,980 | 52,993 | 545 | 1,322 | 公務人員特種考試，例如警察、司法、關務、外交、原民、身障、地方等。 |
| `promotion_rank_exam` | 7,647 | 11,249 | 346 | 546 | 升官等與升資考試。 |
| `language_tourism` | 990 | 2,413 | 40 | 28 | 導遊、領隊與外語類。 |
| `other_unclassified` | 4 | 8 | 1 | 4 | 尚待人工檢查或新增規則者。 |

以上是第一版機械分類。後續如果要處理歷史名稱變體或跨制度類科，應優先調整 `scripts/build_moex_expansion_nodes.py`，再重新產生三份 CSV。

## 官方考科類別拆解

後續拓展以考選部官方考科類別為主軸，不使用自由領域分類作為主分類。官方類別索引由下列腳本產生：

```bash
python3 scripts/build_moex_official_category_tracks.py
```

主分類鍵：

- `exam_level`
- `category_label`
- `category_code`
- `category_name`
- `subject_code`
- `subject_name`

同一個 `category_name` 若出現在不同 `exam_level` 或 `category_label`，必須視為不同官方軌道，不可直接混併。後續可新增輔助標籤協助 parser 風險判斷，但不得覆蓋官方分類。

輸出：

- `moex_official_category_track_summary__y100-115.csv`：官方考科類別軌摘要。
- `moex_official_category_subjects__y100-115.csv`：官方考科類別軌下的科目清單。

## 後續拆解原則

1. 新節點使用獨立 asset root，例如 `國考題資料夾_非醫學全集/` 或更細的領域資料夾。
2. 不把完整非 locked27 全集直接混入 `國考題資料夾_其他類型/`，避免 97 類 seed 集的進度與統計被稀釋。
3. 每個節點先產生 PDF asset index，再決定是否建立 paired index。公職與申論題答案型態和醫事選擇題不同，parser 不應假設全部都有 A-D 選項。
4. MinerU 可先用 `all-official` 全文件拆解；題目結構化 parser 需依節點另行設計。
5. 科目分類先保留官方科目名稱，後續才新增 canonical subject mapping，不覆寫官方 raw name。
