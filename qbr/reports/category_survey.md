# 分類統計 (category survey) — measured

Read-only survey of the official PDFs; copies live in `data/survey/`, digest-pinned in
`data/survey_manifest.jsonl`. Times are wall seconds for the full local pipeline
(triage + dual parse + chrome mask + segmentation), this machine, no model calls.

## 1. Coverage

| category | papers | subjects | years | deep year | pages | questions | options ok | seconds |
|---|---|---|---|---|---|---|---|---|
| 藥師 | 12 | 3 | 101–107 | 106 | 86 | 676 | 670 | 1.53 |
| 醫事放射師 | 20 | 3 | 101–115 | 115 | 244 | 1601 | 1600 | 3.64 |
| 醫事檢驗師 | 20 | 4 | 101–115 | 115 | 236 | 1445 | 1440 | 3.52 |

## 2. Universal rules vs category-specific rules

| dimension | dominant value | share | same everywhere | classification |
|---|---|---|---|---|
| anchor_style | number_dot | 0.94 | yes | **universal** |
| chrome_heavy | no | 1.00 | yes | **universal** |
| eudc_bullets | no | 0.94 | yes | **universal** |
| image_density | none | 0.39 | no | **per-category** |
| option_markers_present | yes | 0.94 | yes | **universal** |
| options_per_question_mode | 4 | 0.94 | yes | **universal** |
| question_count | 80 | 0.81 | no | **per-category** |

Read `classification = universal` as: one shared rule can serve all categories.
`per-category` means the rule must be keyed by category (a 個科系 rule table),
otherwise a single global rule would mis-segment (and then silently produce) wrong items.

### Value distribution per category

- **anchor_style** (universal)
    - 藥師: number_dot×11, bare_number_line×1
    - 醫事放射師: number_dot×20
    - 醫事檢驗師: number_dot×18, bare_number_line×2
- **chrome_heavy** (universal)
    - 藥師: no×12
    - 醫事放射師: no×20
    - 醫事檢驗師: no×20
- **eudc_bullets** (universal)
    - 藥師: no×11, yes×1
    - 醫事放射師: no×20
    - 醫事檢驗師: no×18, yes×2
- **image_density** (per-category)
    - 藥師: none×8, many×3, few×1
    - 醫事放射師: few×10, none×5, many×5
    - 醫事檢驗師: none×7, few×7, many×6
- **option_markers_present** (universal)
    - 藥師: yes×11, no×1
    - 醫事放射師: yes×20
    - 醫事檢驗師: yes×18, no×2
- **options_per_question_mode** (universal)
    - 藥師: 4×11, 0×1
    - 醫事放射師: 4×20
    - 醫事檢驗師: 4×18, 0×2
- **question_count** (per-category)
    - 藥師: 50×7, 80×4, 44×1
    - 醫事放射師: 80×20
    - 醫事檢驗師: 80×18, 62×1, None×1

## 3. Data-quality signals (per paper, only the interesting ones)

| category | year | subject | questions | opt/q | agree(masked) | content | anchors cov | gaps | simplified | PUA | images |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 醫事檢驗師 | 115 | 微生物學與臨床微生物學(包括細菌與黴 | 80 | 4 | TEXT_DISAGREEMENT | 0.99895 | 1.0 | 0 | 0 | 0 | 10 |
| 醫事檢驗師 | 115 | 生物化學與臨床生化學 | 80 | 4 | STRUCTURAL_DISAGREEMENT | 0.99943 | 1.0 | 0 | 1 | 0 | 9 |
| 醫事檢驗師 | 115 | 生物化學與臨床生化學 | 80 | 4 | TEXT_DISAGREEMENT | 0.99853 | 1.0 | 0 | 2 | 0 | 0 |
| 醫事檢驗師 | 115 | 臨床生理學與病理學 | 80 | 4 | STRUCTURAL_DISAGREEMENT | 0.99928 | 1.0 | 0 | 1 | 0 | 13 |
| 醫事檢驗師 | 101 | 微生物學及臨床微生物學(包括細菌與黴 | 5 | 0 | TEXT_DISAGREEMENT | 0.99017 | 1.0 | 0 | 0 | 325 | 0 |
| 醫事檢驗師 | 102 | 微生物學及臨床微生物學(包括細菌與黴 | 0 | 0 | TEXT_DISAGREEMENT | 0.99747 | 1.0 | 0 | 0 | 322 | 0 |
| 醫事檢驗師 | 103 | 微生物學與臨床微生物學(包括細菌與黴 | 80 | 4 | TEXT_DISAGREEMENT | 0.99811 | 1.0 | 0 | 1 | 0 | 2 |
| 醫事檢驗師 | 104 | 微生物學與臨床微生物學(包括細菌與黴 | 80 | 4 | TEXT_DISAGREEMENT | 0.98989 | 1.0 | 0 | 5 | 0 | 0 |
| 醫事檢驗師 | 105 | 微生物學與臨床微生物學(包括細菌與黴 | 80 | 4 | TEXT_DISAGREEMENT | 0.99011 | 1.0 | 0 | 2 | 0 | 3 |
| 醫事檢驗師 | 106 | 微生物學與臨床微生物學(包括細菌與黴 | 80 | 4 | TEXT_DISAGREEMENT | 0.98964 | 1.0 | 0 | 0 | 0 | 2 |
| 醫事檢驗師 | 107 | 微生物學與臨床微生物學(包括細菌與黴 | 80 | 4 | STRUCTURAL_DISAGREEMENT | 0.99918 | 1.0 | 0 | 1 | 0 | 0 |
| 醫事檢驗師 | 110 | 微生物學與臨床微生物學(包括細菌與黴 | 80 | 4 | TEXT_DISAGREEMENT | 0.99879 | 1.0 | 0 | 0 | 0 | 0 |
| 醫事檢驗師 | 114 | 微生物學與臨床微生物學(包括細菌與黴 | 80 | 4 | TEXT_DISAGREEMENT | 0.99829 | 1.0 | 0 | 1 | 0 | 1 |
| 藥師 | 106 | 藥事行政與法規 | 50 | 4 | TEXT_DISAGREEMENT | 0.98011 | 1.0 | 0 | 0 | 0 | 0 |
| 藥師 | 106 | 藥劑學(包括生物藥劑學) | 81 | 4 | TEXT_DISAGREEMENT | 0.98868 | 1.0 | 0 | 0 | 0 | 4 |
| 藥師 | 106 | 藥劑學(包括生物藥劑學) | 80 | 4 | TEXT_DISAGREEMENT | 0.997 | 1.0 | 0 | 0 | 0 | 11 |
| 藥師 | 106 | 藥物分析與生藥學(包括中藥學) | 80 | 4 | TEXT_DISAGREEMENT | 0.98748 | 1.0 | 0 | 0 | 0 | 6 |
| 藥師 | 101 | 藥事行政與法規 | 5 | 0 | TEXT_DISAGREEMENT | 0.99344 | 1.0 | 0 | 0 | 202 | 0 |
| 藥師 | 104 | 藥事行政與法規 | 50 | 4 | TEXT_DISAGREEMENT | 0.98282 | 1.0 | 0 | 0 | 0 | 0 |
| 藥師 | 105 | 藥事行政與法規 | 50 | 4 | TEXT_DISAGREEMENT | 0.9819 | 1.0 | 0 | 0 | 0 | 0 |
| 醫事放射師 | 115 | 基礎醫學(包括解剖學、生理學與病理學 | 80 | 4 | STRUCTURAL_DISAGREEMENT | 0.99928 | 1.0 | 0 | 2 | 0 | 0 |
| 醫事放射師 | 115 | 基礎醫學(包括解剖學、生理學與病理學 | 80 | 4 | STRUCTURAL_DISAGREEMENT | 0.99965 | 1.0 | 0 | 3 | 0 | 0 |
| 醫事放射師 | 102 | 基礎醫學(包括解剖學、生理學與病理學 | 80 | 4 | STRUCTURAL_DISAGREEMENT | 1.0 | 1.0 | 0 | 1 | 0 | 2 |
| 醫事放射師 | 103 | 基礎醫學(包括解剖學、生理學與病理學 | 80 | 4 | TEXT_DISAGREEMENT | 0.99758 | 1.0 | 0 | 1 | 0 | 1 |
| 醫事放射師 | 104 | 基礎醫學(包括解剖學、生理學與病理學 | 80 | 4 | TEXT_DISAGREEMENT | 0.9863 | 1.0 | 0 | 2 | 0 | 0 |
| 醫事放射師 | 105 | 基礎醫學(包括解剖學、生理學與病理學 | 80 | 4 | TEXT_DISAGREEMENT | 0.98808 | 1.0 | 0 | 4 | 0 | 3 |
| 醫事放射師 | 106 | 基礎醫學(包括解剖學、生理學與病理學 | 80 | 4 | TEXT_DISAGREEMENT | 0.98727 | 1.0 | 0 | 1 | 0 | 2 |
| 醫事放射師 | 107 | 基礎醫學(包括解剖學、生理學與病理學 | 80 | 4 | STRUCTURAL_DISAGREEMENT | 1.0 | 1.0 | 0 | 1 | 0 | 2 |
| 醫事放射師 | 108 | 基礎醫學(包括解剖學、生理學與病理學 | 80 | 4 | TEXT_DISAGREEMENT | 0.99854 | 1.0 | 0 | 1 | 0 | 5 |
| 醫事放射師 | 109 | 基礎醫學(包括解剖學、生理學與病理學 | 80 | 4 | STRUCTURAL_DISAGREEMENT | 1.0 | 1.0 | 0 | 1 | 0 | 4 |
| 醫事放射師 | 111 | 基礎醫學(包括解剖學、生理學與病理學 | 80 | 4 | STRUCTURAL_DISAGREEMENT | 0.99926 | 1.0 | 0 | 1 | 0 | 2 |
| 醫事放射師 | 113 | 基礎醫學(包括解剖學、生理學與病理學 | 80 | 4 | TEXT_DISAGREEMENT | 0.99898 | 1.0 | 0 | 2 | 0 | 0 |
| 醫事放射師 | 114 | 基礎醫學(包括解剖學、生理學與病理學 | 80 | 4 | TEXT_DISAGREEMENT | 0.99898 | 1.0 | 0 | 3 | 0 | 1 |

## 4. Crop / geometry test (imaging category)

| category | year | file | page | bbox | blank | kind | rows inside | chars inside | aspect (object/render) | suspect |
|---|---|---|---|---|---|---|---|---|---|---|
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 2 | 50.4,613.5,141.8,678.8 | False | figure_only | 0 | 0 | None/1.4 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 2 | 50.4,685.4,178.3,750.2 | False | figure_only | 0 | 0 | None/1.972 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 3 | 50.4,34.6,158.5,95.7 | False | figure_only | 0 | 0 | None/1.767 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 3 | 50.4,102.3,195.5,167.2 | False | figure_only | 0 | 0 | None/2.229 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 3 | 45.4,189.4,115.3,220.2 | False | figure_only | 0 | 0 | None/2.226 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 6 | 45.4,28.8,295.5,88.6 | False | figure_only | 0 | 0 | None/4.18 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 6 | 45.4,88.6,295.5,148.3 | False | figure_only | 0 | 0 | None/4.139 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 6 | 45.4,148.3,295.5,207.7 | False | figure_only | 0 | 0 | 4.193/4.18 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 6 | 45.4,337.7,251.0,408.2 | False | figure_only | 0 | 0 | 4.193/2.891 | True |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 6 | 45.4,408.3,251.0,478.8 | False | figure_only | 0 | 0 | 4.193/2.891 | True |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 6 | 45.4,478.8,251.0,549.4 | False | figure_only | 0 | 0 | 2.918/2.915 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 6 | 45.4,549.4,251.0,619.9 | False | figure_only | 0 | 0 | 2.918/2.891 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 6 | 45.4,619.9,251.0,687.7 | False | figure_only | 0 | 0 | 2.918/3.018 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 9 | 45.6,436.8,494.1,512.4 | False | figure_only | 0 | 0 | None/5.937 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 9 | 45.6,512.4,494.1,586.6 | False | figure_only | 0 | 0 | None/6.032 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 10 | 45.4,334.2,280.9,393.1 | False | figure_only | 0 | 0 | None/3.98 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 10 | 45.4,393.2,280.9,452.2 | False | figure_only | 0 | 0 | None/3.98 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 10 | 45.4,452.2,280.9,511.2 | False | figure_only | 0 | 0 | 4.0/3.94 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 10 | 45.4,511.2,280.9,569.3 | False | figure_only | 0 | 0 | 4.0/4.062 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 15 | 45.4,157.7,475.0,193.0 | False | figure_only | 0 | 0 | None/11.95 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 15 | 45.4,193.0,475.0,228.2 | False | figure_only | 0 | 0 | None/11.95 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 15 | 45.4,228.3,475.0,263.5 | False | figure_only | 0 | 0 | None/11.95 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 15 | 45.4,263.5,475.0,298.8 | False | figure_only | 0 | 0 | None/11.95 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 15 | 45.4,298.8,475.0,334.1 | False | figure_only | 0 | 0 | None/12.153 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 15 | 45.4,334.1,475.0,369.4 | False | figure_only | 0 | 0 | None/11.95 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 15 | 45.4,369.4,475.0,404.6 | False | figure_only | 0 | 0 | None/11.95 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 15 | 45.4,404.7,475.0,439.9 | False | figure_only | 0 | 0 | None/11.95 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線器材學(包括磁振學與超音 | 15 | 45.4,440.0,475.0,469.0 | False | figure_only | 0 | 0 | None/14.633 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 4 | 45.4,178.6,247.2,250.6 | False | figure_only | 0 | 0 | None/2.785 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 4 | 45.4,250.6,247.2,322.6 | False | figure_only | 0 | 0 | None/2.785 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 4 | 45.4,322.6,247.2,394.6 | False | figure_only | 0 | 0 | None/2.785 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 4 | 45.4,394.6,247.2,466.6 | False | figure_only | 0 | 0 | None/2.785 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 4 | 45.4,466.6,247.2,538.0 | False | figure_only | 0 | 0 | None/2.808 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 7 | 45.4,328.3,494.4,363.6 | False | figure_only | 0 | 0 | 12.735/12.483 | True |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 7 | 45.4,363.6,494.4,398.9 | False | figure_only | 0 | 0 | 12.735/12.695 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 7 | 45.4,398.9,494.4,434.2 | False | figure_only | 0 | 0 | 12.735/12.483 | True |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 7 | 45.4,434.2,494.4,469.4 | False | figure_only | 0 | 0 | 12.735/12.483 | True |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 7 | 45.4,469.5,494.4,504.7 | False | figure_only | 0 | 0 | 12.735/12.483 | True |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 7 | 45.4,504.7,494.4,540.0 | False | figure_only | 0 | 0 | 12.735/12.695 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 7 | 45.4,540.0,494.4,575.3 | False | figure_only | 0 | 0 | 12.735/12.695 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 7 | 45.4,575.3,494.4,610.6 | False | figure_only | 0 | 0 | 12.735/12.483 | True |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 7 | 45.4,610.6,494.4,645.8 | False | figure_only | 0 | 0 | 12.735/12.483 | True |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 7 | 45.4,645.9,494.4,681.1 | False | figure_only | 0 | 0 | 12.735/12.483 | True |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 7 | 45.4,681.1,494.4,716.4 | False | figure_only | 0 | 0 | 12.735/12.695 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 7 | 45.4,716.4,494.4,751.7 | False | figure_only | 0 | 0 | 12.735/12.695 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 7 | 45.4,751.7,494.4,777.8 | False | figure_only | 0 | 0 | 12.735/16.644 | True |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 8 | 45.4,259.2,343.1,306.0 | False | figure_only | 0 | 0 | None/6.372 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 8 | 45.4,306.0,343.1,352.8 | False | figure_only | 0 | 0 | None/6.291 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 8 | 45.4,352.8,343.1,399.6 | False | figure_only | 0 | 0 | None/6.291 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 8 | 45.4,399.6,343.1,446.4 | False | figure_only | 0 | 0 | None/6.372 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 8 | 45.4,446.4,343.1,490.1 | False | figure_only | 0 | 0 | None/6.808 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 9 | 50.4,130.3,143.7,197.0 | False | figure_only | 0 | 0 | None/1.402 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 9 | 50.4,203.8,173.7,265.9 | False | figure_only | 0 | 0 | None/1.971 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 9 | 50.4,272.9,158.0,338.6 | False | figure_only | 0 | 0 | None/1.631 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 9 | 50.4,344.9,199.2,413.6 | False | figure_only | 0 | 0 | None/2.147 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 13 | 45.4,157.7,269.6,227.5 | False | figure_only | 0 | 0 | None/3.178 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 13 | 45.4,227.5,269.6,297.4 | False | figure_only | 0 | 0 | None/3.205 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 13 | 45.4,297.4,269.6,367.2 | False | figure_only | 0 | 0 | None/3.178 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 13 | 45.4,367.2,269.6,436.1 | False | figure_only | 0 | 0 | None/3.261 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 15 | 56.2,280.8,217.0,357.8 | False | figure_only | 0 | 0 | None/2.085 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 15 | 56.2,357.9,217.0,433.8 | False | figure_only | 0 | 0 | None/2.102 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 16 | 45.4,274.3,422.6,316.8 | False | figure_only | 0 | 0 | None/8.75 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 16 | 45.4,316.8,422.6,359.3 | False | figure_only | 0 | 0 | None/8.873 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 16 | 45.4,359.3,422.6,401.8 | False | figure_only | 0 | 0 | None/8.75 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 16 | 45.4,401.8,422.6,444.2 | False | figure_only | 0 | 0 | None/8.75 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 16 | 45.4,444.2,422.6,486.7 | False | figure_only | 0 | 0 | None/8.75 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 16 | 45.4,486.7,422.6,529.2 | False | figure_only | 0 | 0 | None/8.75 | False |
| 醫事放射師 | 115 | 1152_醫事放射師_放射線器材學(包括磁振學與超音 | 16 | 45.4,529.2,422.6,571.7 | False | figure_only | 0 | 0 | None/8.873 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線治療原理與技術學.pdf | 2 | 237.6,82.8,486.4,118.3 | False | figure_only | 0 | 0 | None/6.917 | False |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線治療原理與技術學.pdf | 12 | 45.4,232.6,524.5,267.1 | False | figure_only | 0 | 0 | 13.875/13.559 | True |
| 醫事放射師 | 115 | 1151_醫事放射師_放射線治療原理與技術學.pdf | 12 | 45.4,267.1,524.5,301.7 | False | figure_only | 0 | 0 | 13.875/13.793 | False |

Totals: 111 images inspected, 1 blank, 9 with text inside, 14 aspect mismatch, 16 suspect.

