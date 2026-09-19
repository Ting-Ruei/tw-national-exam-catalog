# 五者之缺陷，名物之修復 — defects found, and the fixes applied

Date: 2026-09-13 (Asia/Taipei). Scope: the three-way review workflow of this sandbox
(`scripts/three_way.py`, `scripts/compare_three.py`, `src/qbr/canon.py`, `src/qbr/repair.py`).

The figures quoted in `reports/compare_three.md` before this revision were produced over the
same 7,169 candidates as after it, so the two columns below are comparable question for
question (`.venv/bin/python scripts/compare_three.py --universe 0 --per-bucket 6 --ids-from
_backup_prefix_20260913/gold/compare_records.jsonl` reproduces the earlier draw, and `--ids-from`
keeps the sample identical).

---

## 一、五者之缺陷 (the defects, and what they cost)

| # | defect | where | what it did to the figures |
|---|---|---|---|
| 1 | **Undeclared numbering style.** The print form also numbers its questions `20 下列…` (number, space, text). No template declared it, so such a paper matched no style at all. | `repair._ANCHOR_TEMPLATES` | the segmenter raised 1 record out of 80 (`1021_醫事檢驗師_生物化學與臨床生化學.pdf`), 75 lines left unassigned |
| 2 | **Detection and segmentation were fed two different views.** The style was scored on the raw extraction rows, then applied to the reading-order merged lines. | `three_way._parse_items` | a *complete* paper read as a one-question paper, while the continuity check still reported `coverage 1.0, gaps 0` — the gate passed, because a run of one has no holes |
| 3 | **One style per paper, for a paper that is two.** Official papers staple an essay part (`甲、申論題部分`, numbered 一、二、…) to a test part (`乙、測驗題部分`, numbered 1…40). | `repair.segment_questions` | only one half of a mixed paper was ever read; `question_type` of the whole file followed the half that happened to score better |
| 3b | **A compatibility ideograph blinded the section detector.** `甲、申論題部分` carries 論 as U+F941 in one paper and U+8AD6 in another — visually identical, code-point different, and the literal keyword comparison missed the whole part. | `repair.is_section_head` | the essay part was folded into the cover; 4 items lost per paper |
| 4 | **No floor under the alignment.** The content search adopted the best of all candidates however unlike the reference, and a number miss carried no weight: matches at ratio 0.029 were accepted and then *judged*. | `three_way._pick_target` | **51.4%** of the sample (3,682 of 7,115 with both numbers) was compared against a question other than the one it named; 71.6% of readings are now known to have been unsound |
| 5 | **The answer was read out of the question.** The P leg inferred its answer with `extract_answer(stem)` — the witness cross-examined on his own deposition — and the authority (the answer sheet) was kept in a separate, unquoted column. Where no 更正答案 was opened, the superseded key went unchallenged: `_ANS` was tested before `_MOD` and the loop stopped at the first sheet. Worse, a 更正答案 sheet prints **題序** over its column of numbers where 答案 prints **題號**, and only the second word was declared — so **7 of 12 sampled correction sheets parsed to an empty table**. | `canon.record`, `canon._verdict`, `compare_three.load_pdfs_sources`, `canon.parse_answer_table` | the `answer` column of the report read "4,966 legacy errors corrected by hand" and "0.9% agree", which measured nothing but how often the human record was empty |
| 6 | **Presentation markup counted as a change of the text.** Reviewers who wrote `GABA<sub>A</sub> 受體` to keep the meaning of the subscript were recorded as having drifted from the official text. | `canon.fold` | `human-drift-from-pdf`, the most alarming line in the old report, was mostly this |
| 7 | **Report cosmetics.** A verdict that contains a vertical bar (`UNICODE_SYMBOL_ERROR\|EXTRA_TEXT`) broke the table it stood in; a row wider than its header shifted the columns of §5b. | `analyze_pilot._table` | `reports/pilot_summary.md` §5/§5b rendered as ragged, unreadable tables |

## 二、名物之修復 (the fixes)

| # | fix | declared, not assumed |
|---|---|---|
| 1 | new template `bare_number_space` in `_ANCHOR_TEMPLATES` | guarded by the continuity gate below, so a stray `85 mmHg` cannot pass for a question |
| 2 | `analyse_items()` reads the paper once; both the merged text *and* the raw rows are offered to `detect_anchor_styles()`, and every candidate style is **tried** by segmenting with it (`segment_best`). Chosen by: fewest holes in the claimed run → longest claimed run → most questions → fewest residual lines. | `diagnostics["tried"]` records each candidate considered, with its result |
| 3 | `segment_mixed()` splits the paper at the declared part headings and reads each part on its own style; every record carries `section` and `item_type`, the paper a `paper_item_type` (`mct` / `constructed_response` / `mixed`). | `_SECTION_WORDS`, `_SECTION_LETTERS`, `_TABLE_HEAD_WORDS` are declared code points, not literals |
| 3b | matching is done on a compatibility-folded, space-free view (`_fold_probe`, NFKC); **stored text is never rewritten** — the rule of this sandbox (detection only) holds | a cover (prelude) that carries no questions is not judged as a failed part |
| 4 | `canon.ALIGN_MIN = 0.90` is the declared floor. Below it there is no match: the item is not found, `alignment` is reported, and `canon.compare(..., aligned=False)` refuses to judge any field (`unaligned-quarantine`). Two witnesses are consulted — the reviewer's stem and the machine's — and the best of them decides. | quarantine, never guess (protocol §16.3) |
| 5 | the P leg's answer comes **only** from the official sheets: `canon.record("P", item, answer_authority=…)`; `sheet_files_for()` returns both sheets; `answer_authority_for()` merges them with the 更正答案 prevailing and reports which sheet spoke (`answer-authority-source`). `parse_answer_table` accepts 題號 **and** 題序, in both scripts. | absence is reported as absence: `no-P` / `no-authority`, never as agreement |
| 6 | `canon.strip_markup()` removes a tag when the field pairs it (`<sub>x</sub>`), when the same name opens twice unclosed (`H<sub>2<sub>O`), or when it is void (`<br>`); an isolated angle bracket — `A<B 且 C>D` in a chemistry option — is left exactly as it is. | the fold is for comparison only; the stored record keeps its markup |
| 7 | `_table()` escapes the bar and pads/truncates a row to its header | a surplus value is announced in the last cell, not silently re-aligned |

## 三、比對 (the same 7,169 candidates, before → after)

| measure | before | after |
|---|---|---|
| questions recovered over the 30-paper pilot (mean/paper, engine A) | 41.2 (median 40) | 41.8 (median 43); engine B median 22 → 53.5 |
| pilot papers passing the continuity gate | *not measured* (the gate passed trivially) | 10 of 10 question papers; 6 read as `mixed`, 4 as `single-part` |
| readings judged (alignment established) | 7,169 (all, judged) | **2,160 (30.1%)**; 454 quarantined (gate refused), 4,555 not found |
| `stem: agree` | 15.0% of all | **62.6%** of the judged (1,352) |
| `stem: divergent` | 34.7% | 0.1% of the judged (3) |
| `stem: error-in-both-vs-pdf` (the defect still live in the database) | 24.1% | 0.5% of the judged (11) |
| `options: agree` | 8.3% | 23.5% of the judged (508) |
| answers confirmed by the official sheet (`answer-agrees-authority: yes`) | 4,473 | **4,528** |
| answers differing from it | 2,622 | 2,636 |
| answers with no authority at all | (not distinguished) | 5 |
| 更正答案 read into the comparison | never | **2,704** records (2,411 `answer+corrected`, 293 `corrected`) |
| correction sheets that parsed to nothing (sampled 12) | 7 | **0** |

Two readings of one candidate, then, and what they mean:

* the old report's 42% of diverging stems and 24% of "error in both" were **the reading**, not
  the corpus; where the reading is established, the agreement rate is above 60%;
* what *is* a finding about the corpus: **63.7% of the sampled candidates cannot be found in the
  official paper their own registry key names**. A control over 14 of them searched every PDF of
  the whole session folder (not only the resolved one) and none reached 0.90 — so this is not a
  resolution error nor a numbering error. These are candidates that were never harvested from
  these papers, or whose text was so damaged that no reading of them is possible.

## 四、Still open (not fixed here, on purpose)

* the option labels drawn as End-User-Defined glyphs (PUA `EUDC` bullets) are still structure
  without characters: `missing-labels:A,B,C,D` stands in 76.8% of the judged records; the fix is
  region-level strip OCR of the bullet only (escalation ladder, rung 1), not a guess;
* `--mode blind` (the AI answering the paper unseen) still has not been run — no endpoint is
  configured; it is the only leg that can say whether an answer is *right*, as opposed to
  *recorded as the official one*;
* the 200-question gold set to calibrate `ALIGN_MIN`, `AGREE` and the segmentation gate against
  is still the owner's to adjudicate; every threshold quoted above is a declared value, chosen
  from the protocol, and none of them was fitted to the data it polices.

---

## 附：what is kept where

* `_backup_prefix_20260913/` — the whole sandbox as it stood **before** these repairs
  (`src/`, `scripts/`, `reports/`, `data/gold/`, and the three planning documents), so that the
  figures above can be re-derived and the old behaviour re-exhibited;
* `_verify_20260913/rerun_with_current_code/` — the outputs of the verification re-runs made
  with that pre-fix code, with their sha256 and byte counts, which is how the queue was shown
  to reproduce byte for byte (2.0 s) before anything in it was altered.

---

# 第二通（the second sitting）: defects 9 to 13, and one retraction

The first part of this file reported a pipeline that judged only what it could align, and a
corpus of which two thirds of the sampled records were said not to be in the papers they were
named by. **The latter was an error of the instrument, and it is retracted below.** Five more
defects were found, all of them in the measuring, and all of them fixed; the figures of the last
table of §三 above are superseded by those of §六 below, over the same pinned 7,169 candidates.

## 五、五者之新的缺陷 (what was wrong, and what it cost)

| # | defect | where | what it did |
|---|---|---|---|
| 9 | **The containment measure was built on the wrong elements of a matching block.** A block is the triple *(start in the one, start in the other, length)*; the measure summed the difference of the two starts, which are positions in two different sequences and mean nothing apart. | `canon.containment` | the "fraction" could exceed one — observed 1.22, 1.79, 19.84 — so the floor of 0.90 let through every reading, and 6,751 of 7,169 records were "judged" against questions they were not. It also cancelled, on its own, the quarantine the alignment gate had just been built to impose |
| 10 | **The printer's marks that bound the options were never divided at.** Where the embedded ToUnicode map sends a bullet to nothing, the whole of an item — question and options — arrives as one merged line, and the segmenter, which knows an option by a line beginning with its label, finds no option at all. | `repair` (the reading of an item) | 5,505 records (76.8% of the judged) reported `missing-labels:A,B,C,D`; and the P leg's stem carried the options inside it, so that a comparison of it with the question alone measured the appendage and was called a change of the text |
| 11 | **An item swallowed its neighbour.** The reading-order merge pulls a number that was a cell of its own onto the line before it, so that question 58 held question 59 whole inside it. | the merged view `analyse_items` consumes | 123 of 2,664 items (4.6%), in 21 of 45 papers; the swallowed question was not to be had at its own number, and the dividing of the options inside the holder failed besides |
| 12 | **An absent witness was read as a dissenting one.** `serialise` returned `""` for a field nothing was written into, the truth table took the empty string for a testimony, and `not _similar(h, p)` was satisfied. | `canon.compare` | 1,074 of 1,075 `human-drift-from-pdf` verdicts of the stem, and 848 of 854 of the options, were the reviewers having written **nothing at all** being recorded as having changed the official text |
| 13 | **The paper was guessed at, not asked for.** The corpus ships 120 manifests (`Registry/asset_manifests/*.csv`) carrying, per asset, `year, exam_ordinal, exam_code, category, subject_code, subject, document_role, destination, sha256, registry_key`; the resolver inferred all of that out of the shape of a file name — a three-digit year, a session digit read out of a *paper code*, a role by which of the suffixes the name happened to carry. | `three_way.resolve_pdf` | **the whole of the unalignable population.** Of the 3,401 readings the alignment could not establish, the text of 2,492 was standing in another paper of the same year and another 次, which the manifest had named for that very key in a column of its own. 100% of the bank's 2,341 keys are answered for by the manifests, and their digests verified (120 checked, 120 matched). The recorded `destination` of 2,325 of them needed rebasing: the checkout has moved, and the manifests kept the old home |

## 六、比對之二 (the same 7,169 candidates, once more)

| measure | 第二通之前 | 之後 |
|---|---|---|
| readings judged / quarantined / not to be found | 2,160 / 454 / 4,555 | **6,555 (91.4%) / 303 / 311** |
| the ground of the resolution | heuristic (guessed) | **manifests: 7,024 `registry+rebased` + 145 `registry`; guessed: 0** |
| `stem: agree` (of the judged) | 62.6% | **59.0%** (3,863) |
| `stem: human-drift-from-pdf` | 757 (mostly defect 12) | **24** — the drift that remains is drift |
| `stem: not reviewed at all` | (reported as drift) | 2,036 |
| `stem: error in both` (the defect live in the bank) | 11 | 208 (3.2%) |
| `options: agree` | 508 | 1,511 |
| `options: missing-labels:A,B,C,D` | 5,505 (76.8%) | **0** |
| items whose options were divided at the marks | — | 6,524 of 7,169 carry four options; 24 could not be divided and are refused (§10) |
| `answer` agreed with the official key | 4,528 yes / 2,636 no | **7,100 yes / 65 no / 4 without any key** |
| the 更正答案 read into the comparison | 2,704 | **3,479** (3,341 with the sheet, 138 of it alone) |
| where the 3,401 unalignable readings were | *asserted absent from the papers* | **in another paper 2,492 · same paper, another number 141 · no witness to search for 765 · nowhere in the corpus 3** |

## 七、撤 (the retraction, in terms)

`reports/BLOCKERS.md` §1 of the first sitting asserted: *"the text of those candidates is not in
the official papers of that session… it is not a resolution error, not a numbering error."* That
was measured with defect 9 in place (a measure that could return more than one, and did, so that
the floor said nothing), and its control was a search of the session folder only, over 14
records, on a pipeline that still guessed at the paper. Repeated over the whole of the unalignable
population (3,401 records), against the whole of the corpus (8,295 PDFs, folded once and cached),
by exact windows of both witnesses, it says the contrary: seven in ten of them were in a paper
after all, and the fault was in the resolving of the key, not in the bank. What survives as a
finding about the corpus is small, and is stated in `reports/BLOCKERS.md` §1 as restated.

**Of the four rules of comparison, and of the classes of things measured** — since four of the
five defects above were defects of the *measure*, not of the *matter*, and all five were found by
reading the instrument against what the instrument had said:

1. a measure must be bounded, and its bounds must be asserted in a test (defect 9: no test
   asserted that a fraction is not greater than one);
2. an absence must be reported as an absence, and never as a presence of the opposite (defect 12);
3. what can be asked of an authority must not be guessed at from a shape (defect 13: the manifest
   was there the whole time, in the same tree);
4. a division admitted must be declared with the evidence for it, and refused when the counts do
   not correspond (defects 10 and 11: `support: "paper"` or `"corpus"`, and `None` otherwise).
