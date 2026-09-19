# 三路对账 (three-way comparison: machine / human / official PDF)

Authority on disagreement: the official PDF text layer (`pdf`); for an answer, the official answer sheet, the 更正答案 prevailing where it speaks.
Sampled 75 of 35496 human-reviewed candidates; 0 could not be traced to a PDF.

## 读前必定（alignment: a reading is only judged when it reads the same question）

| 分级 | 题数 |
|---|---|
| judged | 60 |
| quarantined | 6 |
| not-found | 9 |

配准度（match ratio）: min 0.000, p50 1.000, max 1.000

## 判定（verdicts）

Only the rows of the `judged` grade above are a statement about the corpus; the rest is a statement about the reading of it.

| verdict | count |
|---|---|
| stem:three-ways-differ | 22 |
| all-agree | 18 |
| three-ways-differ | 16 |
| stem:human-introduced-change | 15 |
| no-item | 15 |
| stem:no-item | 15 |
| human-introduced-change | 14 |
| stem:all-agree | 14 |
| machine-wrong-human-fixed-it | 9 |
| stem:both-sides-wrong-vs-pdf | 5 |
| stem:machine-wrong-human-fixed-it | 4 |
| both-sides-wrong-vs-pdf | 3 |

## How to read it

- `machine-wrong-human-fixed-it` - the legacy pipeline was wrong and the human corrected it (expected, healthy).
- `human-introduced-change` - machine equalled the PDF, the human moved away from it: a possible hidden error in the gold set.
- `both-sides-wrong-vs-pdf` - both differ from the official text: the defect is still live in the database.
- `three-ways-differ` - needs a ruling; send to the review queue.

