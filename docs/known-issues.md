# MOEX Official Subject Catalog Issues (ROC 100-115)

Source: 考選部考畢試題查詢平臺 `https://wwwq.moex.gov.tw/exam/wFrmExamQandASearch.aspx`.

## Summary

- `record_count`: 70555
- `exam_code_count`: 336
- `category_count`: 11907
- `error_count`: 0
- issue row CSV: `moex_subject_catalog__y100-115_issue_rows.csv`

## Quality Checks

- 115020 benchmark: 42 subject rows / 11 categories.
- Missing question links: 0.
- Missing answer links: 36520. User decision: this primarily means fully handwritten/essay exams, or subjects whose essay portion has no standard answer link; it is not treated as a download failure.
- Correction links present: 7220.
- Duplicate official subject keys: 0.
- Long polluted exam labels (>500 chars): 0.

## Issue Counts

- `note:answer_link_missing`: 36520
- `note:category_label_missing`: 7
- `exam_label_year_mismatch`: 4

## Decisions / Rules

1. `answer_link_missing` is not a download failure by default. It usually means the exam is fully handwritten/essay, or that only the multiple-choice portion has an answer key while the handwritten portion has no standard answer PDF.
2. `category_label_missing` rows still need manual review because the official checkbox code exists but its visible category label was not paired by the parser.
3. `exam_label_year_mismatch` should be treated as a special exam/re-exam case, not automatically corrected. Current observed case is listed below.
4. Official names are preserved raw. Full-width/half-width parentheses, compatibility characters such as `年`, and old abbreviated titles are intentionally not normalized in this catalog. Add normalized columns later, never overwrite official raw values.

## Year Mismatch Cases

- catalog year `109`, exam_code `109200`, label `107年消防警察特考重新考試`

## Category Label Missing Rows

- `102` `102170` `603` subject `1201` `海運學`
- `102` `102170` `603` subject `1914` `電子計算機概論`
- `102` `102170` `604` subject `1201` `海運學`
- `102` `102170` `604` subject `1914` `電子計算機概論`
- `102` `102170` `614` subject `1202` `海運學概要`
- `102` `102170` `712` subject `1910` `電子資料處理概要`
- `102` `102170` `713` subject `1910` `電子資料處理概要`

Follow-up check on 2026-06-17:

- The source HTML contains subject-level checkboxes and PDF links for these rows.
- The source HTML does not contain corresponding category-level labels such as `ctl00_holderContent_chk_102170_603`.
- Therefore this is an official page special structure / orphan subject-row case, not simply a failed text-normalization issue.
- Some PDFs are shared across multiple category codes:
  - `603/604/601/602/605/606/607/608/609/610/611 + subject 1201` all return `102170_60140.pdf`.
  - `603/604/602/605/606/607/609/610/611 + subject 1914` all return `102170_60230.pdf`.
  - `712/714/715 + subject 1910` all return `102170_71230.pdf`.
- These rows need an explicit manual override table or a hierarchy-aware parser rule before being treated as fully classified.

## Missing Answer Links By Year

- ROC 115: 262
- ROC 114: 2194
- ROC 113: 1813
- ROC 112: 2476
- ROC 111: 2074
- ROC 110: 2401
- ROC 109: 2100
- ROC 108: 2786
- ROC 107: 2172
- ROC 106: 2673
- ROC 105: 2265
- ROC 104: 2841
- ROC 103: 2353
- ROC 102: 2804
- ROC 101: 2371
- ROC 100: 2935

## Top Exams With Missing Answer Links

- ROC 104 `104080`: 743 rows - 104年公務人員高等考試三級考試暨普通考試
- ROC 108 `108090`: 735 rows - 108年公務人員高等考試三級考試暨普通考試
- ROC 103 `103080`: 734 rows - 103年公務人員高等考試三級考試暨普通考試
- ROC 105 `105080`: 716 rows - 105年公務人員高等考試三級考試暨普通考試
- ROC 107 `107090`: 711 rows - 107年公務人員高等考試三級考試暨普通考試
- ROC 109 `109090`: 695 rows - 109年公務人員高等考試三級考試暨普通考試
- ROC 106 `106090`: 658 rows - 106年公務人員高等考試三級考試暨普通考試
- ROC 110 `110090`: 655 rows - 110年公務人員高等考試三級考試暨普通考試
- ROC 101 `101090`: 653 rows - 101年公務人員高等考試三級考試暨普通考試
- ROC 112 `112090`: 652 rows - 112年公務人員高等考試三級考試暨普通考試
- ROC 102 `102090`: 631 rows - 102年公務人員高等考試三級考試暨普通考試
- ROC 106 `106170`: 630 rows - 106年公務人員升官等考試、106年關務人員升官等考試、106年交通事業鐵路人員、公路人員、港務人員升資考試
- ROC 111 `111090`: 615 rows - 111年公務人員高等考試三級考試暨普通考試
- ROC 100 `100120`: 613 rows - 100年公務人員高等考試三級考試暨普通考試
- ROC 108 `108170`: 602 rows - 108年公務人員升官等考試、關務人員升官等考試、交通事業郵政人員升資考試、交通事業公路人員升資考試、交通事業港務人員升資考試
- ROC 104 `104160`: 579 rows - 104年公務人員及關務人員升官等考試、104年交通事業公路人員及港務人員升資考試
- ROC 102 `102170`: 573 rows - 102年公務及關務升官等考試、102年交通事業郵政、港務及公路人員升資考試
- ROC 114 `114080`: 492 rows - 114年公務人員高等考試三級考試暨普通考試
- ROC 113 `113080`: 489 rows - 113年公務人員高等考試三級考試暨普通考試
- ROC 102 `102190`: 443 rows - 102年特種考試地方政府公務人員考試
- ROC 103 `103180`: 439 rows - 103年特種考試地方政府公務人員考試
- ROC 100 `100210`: 438 rows - 100年公務人員升官等、關務人員升官等考試
- ROC 101 `101190`: 437 rows - 101年特種考試地方政府公務人員考試
- ROC 108 `108190`: 425 rows - 108年特種考試地方政府公務人員考試
- ROC 104 `104180`: 425 rows - 104年特種考試地方政府公務人員考試
- ROC 100 `100240`: 420 rows - 100年特種考試地方政府公務人員考試
- ROC 105 `105180`: 409 rows - 105年特種考試地方政府公務人員考試
- ROC 112 `112200`: 403 rows - 112年特種考試地方政府公務人員考試
- ROC 111 `111190`: 401 rows - 111年特種考試地方政府公務人員考試
- ROC 110 `110170`: 392 rows - 110年公務人員升官等考試、110年關務人員升官等考試、110年交通事業公路人員升資考試、110年交通事業港務人員升資考試

## Extraction Assumptions

- `category_code` and `subject_code` come from official checkbox IDs, not from guessed text.
- PDF availability is detected from official `wHandExamQandA_File.ashx` links: `Q` question, `S` answer, `M` correction.
- The registry key is `moex:{exam_code}:{category_code}:{subject_code}:{question_set}`. Document role is added later when downloading PDF assets.
- This catalog records subject-level identity and link availability only. It does not download PDFs and does not parse question contents.

## Locked 27 Category Name Follow-up

- Generated stability report: `locked_27_category_name_stability__y100-115.md`.
- Generated occurrence CSV: `locked_27_category_occurrences__y100-115.csv`.
- Generated canonical name table: `locked_27_canonical_category_names.csv`.
- Matching rule was corrected to exact normalized category names only. Substring matching is unsafe because `醫師(一)` also appears inside `中醫師(一)` and `牙醫師(一)`.
- Canonical platform names use half-width parentheses, for example `牙醫師(一)` and `藥師(二)`.
- Official raw names and labels remain preserved in catalog metadata.
- Observed full-width / half-width parentheses mixing only for `牙醫師(一)`, `牙醫師(二)`, `藥師(一)`, `藥師(二)`.
- `公職...` variants are explicitly excluded from the current ingestion scope because professional/technical licensing exams are the current target. They must not be silently merged into professional exam-event identity.
