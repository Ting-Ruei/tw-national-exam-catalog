# 三路对比报告（正规化之后）

依据：官方 PDF 文字层（P）为准则；L = 旧方法（MinerU），H = 人工审核记录。
答案之准则：官方答案卷（_ANS），有更正答案者（_MOD）优先。
样本 7169 题；数字均为「与 P 的偏差」计数，非合格率判定。

## 对齐（alignment：先定，后判）

| 读数分级 | 题数的 | 占样本 |
|---|---|---|
| judged | 6555 | 91.4% |
| quarantined | 303 | 4.2% |
| not-found | 311 | 4.3% |

判读方式：number=6718、none=331、content=120

卷之所在：registry+rebased=7024、registry=145

## 总计（按字段，全部样本）

**字段：stem**

| 判定 | 计数 | 佔該字段 |
|---|---|---|
| agree | 3863 | 53.9% |
| not-reviewed | 2036 | 28.4% |
| unaligned-quarantine | 614 | 8.6% |
| legacy-only-differs | 243 | 3.4% |
| error-in-both-vs-pdf | 208 | 2.9% |
| legacy-error-fixed-by-human | 132 | 1.8% |
| divergent | 25 | 0.3% |
| incomparable-unsplit-options | 24 | 0.3% |
| human-drift-from-pdf | 24 | 0.3% |

**字段：options**

| 判定 | 计数 | 佔該字段 |
|---|---|---|
| not-reviewed | 1577 | 22.0% |
| agree | 1511 | 21.1% |
| divergent | 1435 | 20.0% |
| legacy-error-fixed-by-human | 990 | 13.8% |
| error-in-both-vs-pdf | 652 | 9.1% |
| unaligned-quarantine | 614 | 8.6% |
| legacy-only-differs | 276 | 3.8% |
| human-drift-from-pdf | 85 | 1.2% |
| incomparable-unsplit-options | 24 | 0.3% |
| no-P | 5 | 0.1% |

**字段：answer**

| 判定 | 计数 | 佔該字段 |
|---|---|---|
| not-reviewed | 4539 | 63.3% |
| agree | 1952 | 27.2% |
| unaligned-quarantine | 614 | 8.6% |
| legacy-only-differs | 58 | 0.8% |
| human-drift-from-pdf | 4 | 0.1% |
| no-P | 2 | 0.0% |

## 可信子集合（仅对齐合格者：judged）

**字段：stem**

| 判定 | 计数 | 佔可信子集合 |
|---|---|---|
| agree | 3863 | 58.9% |
| not-reviewed | 2036 | 31.1% |
| legacy-only-differs | 243 | 3.7% |
| error-in-both-vs-pdf | 208 | 3.2% |
| legacy-error-fixed-by-human | 132 | 2.0% |
| divergent | 25 | 0.4% |
| incomparable-unsplit-options | 24 | 0.4% |
| human-drift-from-pdf | 24 | 0.4% |

**字段：options**

| 判定 | 计数 | 佔可信子集合 |
|---|---|---|
| not-reviewed | 1577 | 24.1% |
| agree | 1511 | 23.1% |
| divergent | 1435 | 21.9% |
| legacy-error-fixed-by-human | 990 | 15.1% |
| error-in-both-vs-pdf | 652 | 9.9% |
| legacy-only-differs | 276 | 4.2% |
| human-drift-from-pdf | 85 | 1.3% |
| incomparable-unsplit-options | 24 | 0.4% |
| no-P | 5 | 0.1% |

**字段：answer**

| 判定 | 计数 | 佔可信子集合 |
|---|---|---|
| not-reviewed | 4539 | 69.2% |
| agree | 1952 | 29.8% |
| legacy-only-differs | 58 | 0.9% |
| human-drift-from-pdf | 4 | 0.1% |
| no-P | 2 | 0.0% |

## normalisation 期间发现的标记

- rejected:read: 208
- rejected:duplicate: 96
- stem-carries-unsplit-bullets: 24
- rejected:gaps: 7
- options-merged-into-stem: 7

## 答案与官方答案卷的对照

- yes: 7100
- no: 65
- no-authority: 4

准则来源（答案取自哪一卷）：
- answer: 3686
- answer+corrected: 3341
- corrected: 138
- none: 4

## 分类（按考）

### 藥師

| 字段 | 判定 | 计数 |
|---|---|---|
| stem | agree | 566 |
| stem | not-reviewed | 259 |
| stem | unaligned-quarantine | 175 |
| stem | legacy-error-fixed-by-human | 16 |
| stem | incomparable-unsplit-options | 15 |
| stem | legacy-only-differs | 14 |
| stem | human-drift-from-pdf | 11 |
| stem | error-in-both-vs-pdf | 9 |
| stem | divergent | 8 |
| options | legacy-error-fixed-by-human | 229 |
| options | agree | 225 |
| options | not-reviewed | 177 |
| options | unaligned-quarantine | 175 |
| options | divergent | 153 |
| options | error-in-both-vs-pdf | 56 |
| options | human-drift-from-pdf | 26 |
| options | legacy-only-differs | 17 |
| options | incomparable-unsplit-options | 15 |
| answer | not-reviewed | 487 |
| answer | agree | 393 |
| answer | unaligned-quarantine | 175 |
| answer | legacy-only-differs | 18 |

### 藥師(一)

| 字段 | 判定 | 计数 |
|---|---|---|
| stem | agree | 580 |
| stem | not-reviewed | 133 |
| stem | unaligned-quarantine | 41 |
| stem | legacy-error-fixed-by-human | 35 |
| stem | error-in-both-vs-pdf | 24 |
| stem | legacy-only-differs | 8 |
| stem | human-drift-from-pdf | 6 |
| stem | divergent | 4 |
| options | agree | 215 |
| options | legacy-error-fixed-by-human | 171 |
| options | divergent | 161 |
| options | not-reviewed | 139 |
| options | error-in-both-vs-pdf | 60 |
| options | unaligned-quarantine | 41 |
| options | human-drift-from-pdf | 23 |
| options | legacy-only-differs | 20 |
| options | no-P | 1 |
| answer | agree | 483 |
| answer | not-reviewed | 294 |
| answer | unaligned-quarantine | 41 |
| answer | legacy-only-differs | 11 |
| answer | human-drift-from-pdf | 2 |

### 藥師(二)

| 字段 | 判定 | 计数 |
|---|---|---|
| stem | agree | 227 |
| stem | not-reviewed | 138 |
| stem | unaligned-quarantine | 29 |
| stem | legacy-error-fixed-by-human | 13 |
| stem | legacy-only-differs | 10 |
| stem | error-in-both-vs-pdf | 7 |
| stem | human-drift-from-pdf | 3 |
| stem | divergent | 2 |
| options | not-reviewed | 102 |
| options | agree | 94 |
| options | legacy-error-fixed-by-human | 67 |
| options | divergent | 66 |
| options | error-in-both-vs-pdf | 41 |
| options | unaligned-quarantine | 29 |
| options | legacy-only-differs | 23 |
| options | human-drift-from-pdf | 7 |
| answer | not-reviewed | 223 |
| answer | agree | 171 |
| answer | unaligned-quarantine | 29 |
| answer | legacy-only-differs | 4 |
| answer | human-drift-from-pdf | 2 |

### 醫事放射師

| 字段 | 判定 | 计数 |
|---|---|---|
| stem | agree | 1523 |
| stem | not-reviewed | 1269 |
| stem | unaligned-quarantine | 230 |
| stem | legacy-only-differs | 205 |
| stem | error-in-both-vs-pdf | 146 |
| stem | legacy-error-fixed-by-human | 34 |
| stem | divergent | 11 |
| stem | human-drift-from-pdf | 1 |
| options | not-reviewed | 1072 |
| options | divergent | 882 |
| options | legacy-error-fixed-by-human | 403 |
| options | error-in-both-vs-pdf | 312 |
| options | agree | 296 |
| options | unaligned-quarantine | 230 |
| options | legacy-only-differs | 198 |
| options | human-drift-from-pdf | 23 |
| options | no-P | 3 |
| answer | not-reviewed | 3160 |
| answer | unaligned-quarantine | 230 |
| answer | legacy-only-differs | 18 |
| answer | agree | 9 |
| answer | no-P | 2 |

### 醫事檢驗師

| 字段 | 判定 | 计数 |
|---|---|---|
| stem | agree | 967 |
| stem | not-reviewed | 237 |
| stem | unaligned-quarantine | 139 |
| stem | legacy-error-fixed-by-human | 34 |
| stem | error-in-both-vs-pdf | 22 |
| stem | incomparable-unsplit-options | 9 |
| stem | legacy-only-differs | 6 |
| stem | human-drift-from-pdf | 3 |
| options | agree | 681 |
| options | error-in-both-vs-pdf | 183 |
| options | divergent | 173 |
| options | unaligned-quarantine | 139 |
| options | legacy-error-fixed-by-human | 120 |
| options | not-reviewed | 87 |
| options | legacy-only-differs | 18 |
| options | incomparable-unsplit-options | 9 |
| options | human-drift-from-pdf | 6 |
| options | no-P | 1 |
| answer | agree | 896 |
| answer | not-reviewed | 375 |
| answer | unaligned-quarantine | 139 |
| answer | legacy-only-differs | 7 |

