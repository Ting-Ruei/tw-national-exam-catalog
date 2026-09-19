# 全國專技術題题库重建 — 建議工作流程（measured proposal, v0.1）

Location: `pi_test/question_bank_rebuild/`. Everything below was produced on this MacBook
against copies of the official assets; `AI Max 395` / AI395 and its services (Review UI,
PostgreSQL, the 395 OCR fleet) were treated as absent, as instructed, and no reachable
service was configured or written to.

Safety of this run:

| action | status |
|---|---|
| writes into `tw-national-exam-catalog/國考題資料夾/**` | **none** (read-only) |
| writes into `platform-app/**` | **none** |
| writes into `pi_test/question_bank_rebuild/**` | yes, this sandbox only |
| input assets used | 30 official PDFs copied to `data/raw/`, digest-pinned in `data/sample_manifest.jsonl` |
| legacy corpus used | read-only comparison target (`20_mineru_output`, `Registry/mineru_runs`) |
| model inference used | **zero** calls. Deliberately: the point of the measurement was to find out how much of the pipeline does not need any |

---

## 1. Why a new workflow at all — the numbers from the legacy pipeline

From the legacy pipeline's own run registry (`Registry/mineru_runs/*/mineru_results__*.csv`,
15,712 rows, read-only):

| quantity | legacy MinerU-centric path |
|---|---|
| registry rows | 15,712 (7,032 distinct PDFs attempted) |
| `ok` | 4,030 |
| `error` | 8,788 — of which **8,739 are one operational artefact**: `PermissionError: [Errno 1] Operation not permitted` |
| `timeout` | 4 |
| wall time of the `ok` runs alone | **136.3 h** |
| seconds/paper `ok`: mean / p50 / p90 / p99 / max | 121.9 / 34.5 / 232.3 / 1196.2 / 1379.5 |
| images emitted per `ok` paper (mean) | 3.3 |
| PDFs that never reached `ok` | 3,015 (43% of attempted) |

Reading: the 56% failure rate is mostly *plumbing* (worker file-permission environment), not
recognition. But the cost side is real — 136 h of wall time for 4,030 papers, one full-document
VLM pass each, and still no character-level guarantee, because the pipeline's own
`quality_metrics` are derived **from the MinerU output itself** (self-referential), exactly what
MOEX protocol §16 forbids.

## 2. What the same 30 official PDFs do under the deterministic path

Measured here (`reports/stage_c_d_results.jsonl`, `reports/pilot_summary.md`):

| quantity | measured value |
|---|---|
| papers with a usable native text layer (no OCR needed for text) | **30 / 30** |
| papers with no text layer at all (real OCR needed) | **0 / 30** |
| triage classes seen | `NATIVE_TEXT_LAYOUT_RISK` 20, `LEGACY_ENCODING` 7, `NATIVE_TEXT_GOOD` 3 |
| wall seconds/paper (triage + dual extraction): mean / p50 / p95 | **0.045 / 0.024 / 0.139** |
| peak RSS of the whole worker process | 84 MB |
| VLM/OCR calls per paper | **0** |
| dual-engine character agreement (raw): p05 / median / mean | 0.9953 / **1.0000** / 0.9988 |
| dual-engine structural (reading-order) agreement: min / p05 / median | 0.7028 / 0.7434 / 0.9291 |

Throughput consequence: **~2,700× faster per paper than the legacy `ok` mean**, on CPU, with no
GPU reservation, no queue, no timeout class to handle. At that price the corpus can be
re-extracted *from the official PDFs* on every rule change instead of patched forward with
character maps.

## 3. The two reported defects — root cause, not symptom

### 3.1 「簡体字出现在 OCR 输出中」

| source | papers with simplified-only codepoints | total hits | max/paper |
|---|---|---|---|
| legacy MinerU markdown (`20_mineru_output`) | **12 / 24** | **64** | 12 |
| official PDF native text layer | 1 / 30 | 1 | 1 |

Representative lines from the legacy side (`1041_物理治療師_物理治療學概論…`):
`C. 日内瓦宣言`, `A. 敏兹柏格（Mint…`, `D. …仰卧起坐`, `C（胸部）3D（横膈膜）`, `C. 同侧偏盲`.
The official PDF of the same paper contains **none** of those codepoints.

> Conclusion: the simplified characters are **manufactured by the VLM OCR step**, they are not
> in the source. Therefore `scripts/ocr_normalization_util.py` (`OCR_CHAR_MAP`,
> `OCR_CONTEXT_MAP`, `OCR_SYMBOL_MAP`, 493 lines of `OCR_K` rules) is a repair shop for a
> self-inflicted wound. Its own documented risk (§「注意事项」) — 「不能把简单的把它当作…乱」,
> i.e. blind whole-document 簡→繁 conversion breaking 专有名词 — is not a hypothetical: it is
> the same class of defect it is meant to cure. Under the deterministic path it retires.
> The single native-side hit (`1111_營養師_食品衛生與安全`: `苯并芘（benzo…`) is a *different*
> fault: it must be checked against the rendered page (official wording vs broken ToUnicode
> CMap) and never auto-corrected (protocol §11.3).

### 3.2 「题目图片的裁切位置不正确」

Three causes, all visible in the geometry:

1. **Chrome duplication.** These PDFs are print forms distilled by an HTML print-form pipeline
   (`pdfinfo`: `Creator: PDFCreator Version 1.2.3`, `Producer: GPL Ghostscript 9.04`, `Title`
   = a `localhost:8080/GT/PrintForm/…` URL). The title block (`代號：10350`, `頁次：4－1`,
   `類  科：`, `科  目：`, `座號：`, `考試時間：`, `※注意：…`) is drawn repeatedly per page,
   and the two engines interleave those draws differently — measured effect: **12/30 papers
   classified as a content disagreement when it was only a layout disagreement**.
2. **Line granularity differs per engine** (measured on one 10-page paper: PyMuPDF 316 rows vs
   poppler 125 rows for the same page). Any index-based "drop the first N lines" rule is
   therefore engine-dependent and manufactures fake disputes → chrome must be masked by
   *geometry* (same text at same ordinate on many pages), which both engines observe
   independently.
3. **The option letters are not text.** In the 選擇題 sections the A/B/C/D markers are drawn as
   **End-User-Defined Characters**, font `EUDC`, private-use codepoints (`U+E129`, `U+E18C`…),
   e.g. `…（acute pancreatitis）。` lines carry no `A.`/`B.` at all in the text layer.
   This is the true root cause of the whole `選項合併 C.D` / `题干断裂` defect family
   (49/232 of a recent paper): a text-only parser cannot recover the labels, so the legacy
   pipeline leaned on the VLM's guessed boxes and the crops came out wrong.

Additional hazard found while measuring (not in the protocol's error list): **CJK Compatibility
Ideographs** (`U+F967 '不'` observed) — visually identical to the ordinary ideograph, different
codepoint. Silent, and exactly what corrupts a 题号-keyed database. Must be an alarm class, not a
normalization rule.

### 3.3 A third defect the legacy schema never modelled

Item type is not uniform, and the legacy normalised record hardcodes
`"question_type": "single_choice"`. Measured census over the 10 question papers:

| anchor style detected (per paper, validated by number continuity) | papers | consequence |
|---|---|---|
| `number_dot` (`1.` …), 4 options recovered | 3 → 80/80 items with ≥2 options | true MCT |
| `bare_number_line` (number alone on its own line), 0 options recovered | 7 | 申論/混合卷 papers: 「甲、申論題部分：（50 分）」+ 一、二、三 CJK-numbered items, **and** a 40-item MCT section whose option bullets are EUDC glyphs |
| `cjk_number_line` (一、二、…) | detected via `patterns/cjk_numerals.json` | needed for 申論 sections |

So one PDF is often a **mixed paper** (申論 part + 選擇 part), and answer/corrected-answer
sheets use yet another numbering (5/10 with `bare_number_line`, 5/10 with no detectable
numbering at all). A single global rule cannot parse all — protocol §9 is right, and the
implementation must *detect the style per paper and validate it by continuity*, which is what
`repair.detect_anchor_style()` does (coverage ≥ 0.90 and 0 gaps achieved on 10/10 question
papers; `number_of_questions` recovered: 40–80 per paper).

## 4. Objective comparison of the three candidate workflows

| | A. legacy (in use) | B. 先前改進方案 (`docs/sustainable-question-bank-workflow-todo.md`, `parser-rule-inventory.md`, `text-normalization-rules.md`) | C. MOEX protocol v1 (design) | D. **proposed here (measured)** |
|---|---|---|---|---|
| centre of the pipeline | MinerU + full-doc VLM | MinerU + VLM, with more rules and more review | deterministic extraction; OCR only on escalation | same, plus **geometry-derived** gates |
| OCR calls / paper | 1 full-document VLM pass | 1 | 0 for `NATIVE_TEXT_*`, escalation for the rest | 0 for 30/30 in sample; **region-level** (bullet strips, PUA glyphs) instead of page-level |
| measured cost / paper | 121.9 s mean, 136.3 h total | same centre, so same cost (rules aim to reduce it, budget not measured) | not measured | **0.045 s mean, 84 MB RSS, CPU** |
| reproducibility | medium-low: env-dependent, `PermissionError` mass failure, no per-run seed/manifest | same | high: manifest + checksums | high + **dual-engine** evidence, digest-pinned inputs |
| independent verification | self-referential `quality_metrics` | + AI advisory (same provider, so same blind spot) | ✓ two channels required | ✓ two *independent engines* (MuPDF, poppler) + gold-set calibration |
| 簡體字 handling | OCR then repair via `OCR_CHAR_MAP`/`OCR_CONTEXT_MAP` (493 lines) | keep + extend the maps | 只修字符 for punctuation/units; **no 漢字符 conversion** | **remove the cause**: no VLM on text-bearing layers; PUA/compatibility ideographs are alarm classes, never auto-mapped |
| 裁切 (crops) | VLM bboxes, page-level | page-level, reviewed | region-level on escalation | **geometry-derived**: crop = embedded image bbox ∪ text-block bbox ∪ union-find of overlapping blocks; label sequence from ordered EUDC bullets + adjacent strip OCR of the marker only |
| item types | single_choice hardcoded | + manual field | + manual field | **detected & validated** (MCT / 申論 / mixed), style detected per paper |
| failure mode that matters | silent wrong answer published | same, plus rule-explosion (3,143-line parser) | gate too strict ⇒ 100% quarantine | see §6: gate must be defined on a *declared canonical form* |
| provenance | partial | better | full | full + engine-attributed line records (which engine saw what) |

## 5. Proposed workflow (stages, gates, budgets)

```
A intake      official PDF + official metadata; three PDFs per paper (題目/答案/更正答案)
              sha256 pinned, immutable; manifest row per file (§17.1 style)
B registry    catalog index: category/year/session/role + 座號-style keys; no text work
C triage      cheap PyMuPDF scan only: pages, words, images, fonts, CJK census,
              damaged-code census (U+FFFD, PUA, halfwidth/fullwidth, compatibility ideographs)
              -> class: NATIVE_TEXT_GOOD | LAYOUT_RISK | LEGACY_ENCODING | SCANNED_IMAGE | MIXED
D dual parse  two independent engines, position-addressed rows (page,x0,y0,x1,y1,text,font,size)
              engine α = PyMuPDF (blocks, spans, fonts, embedded images)
              engine β = poppler pdftotext -bbox (word-level boxes, -raw order)
              both kept; both compared; neither trusted alone
E chrome mask geometry-based header/footer removal (same text, same ordinate, many pages)
              + declared rules for 代號/頁次/類科/科目/座號/考試時間/※注意/甲申論 section headers
              + PUA bullet tokenisation: {BULLET} is structure, never a character
F segment     detect_anchor_style() per paper, validated by number_of_questions continuity
              item type: mct | constructed_response | mixed (per section)
              options from ordered bullets + geometry; option_count gate per item type
G assets      crop = union(image bbox, covering text-block bbox, adjacent bullet strip)
              render at 300 dpi, keep original bytes + crop record in manifest
              strip-OCR of the bullet marker only when the label is ambiguous (Level-1, tiny)
H verify      independent: re-render the region, compare against the two engines' rows;
              answer-key merge across 題目/答案/更正答案 with conflict log
I gate        publish | quarantine | escalate (see §6); no silent normalisation of 漢字符
J publish     versioned package + manifest + provenance; rollback = republish previous
```

Escalation ladder with **hard budgets** (protocol §13, made enforceable):

| level | means | budget per paper | when |
|---|---|---|---|
| 0 | declared rules, geometry, dual-engine consensus | free, ~0.05 s | always |
| 1 | region render + strip OCR of one glyph/line (small text, local) | ≤ 12 crops, ≤ 6 s | ambiguous bullet label, PUA run |
| 2 | VLM on **one question region** only | ≤ 4 regions, ≤ 40 s | no text layer for that region |
| 3 | VLM on whole page | ≤ 1 page per paper, needs a reason recorded | `SCANNED_IMAGE` papers only |
| 4 | human review | queue | anything the gates reject |

Any paper exceeding level 3 twice, or whose gate verdict is REJECT, goes to `quarantine/` with
its evidence bundle — it is **not** published, and it is **not** silently repaired (§16.3).

## 6. Gate thresholds — calibrated vs still to be calibrated

Measured, and what I refuse to invent:

| gate | proposed | status |
|---|---|---|
| `NATIVE_TEXT_USABLE` | chars/page ≥ 200 ∧ CJK ≥ 60% ∧ no PUA flood | provisional, matches 30/30 |
| content agreement | ≥ 0.999 **on the declared canonical form** | **not yet valid** — see below |
| structural agreement | ≥ 0.90 for `NATIVE_TEXT_GOOD`, ≥ 0.98 for no-review | observed median 0.9291 → most papers need the chrome mask first |
| `question_count` | == official `number_of_questions` (or declared default if absent: 40/80 by category) | achievable: 10/10 papers, 0 gaps |
| option completeness | MCT: 4 options; 申論: no options required | measured: 80/80 in the 3 MCT papers |
| simplified ratio (native side) | > 0 → alarm, investigate; **never auto-fix** | 1/30 |
| compatibility ideographs / PUA / halfwidth forms | count == 0 after tokenisation | 7/30 papers carry PUA runs |
| **segmentation continuity** | the paper must claim a run reaching `expected`, with no more holes
  than `RESIDUAL_LINE_BUDGET` of the lines it leaves unassigned | declared 0.35; measured 10/10
  question papers of the pilot pass, 6 of them only as *mixed* papers read part by part |
| **alignment floor** | a reading is judged only when the question it names reads ≥ `ALIGN_MIN`
  alike to the record, on the better of the two witnesses (the reviewer's stem, the machine's);
  declared 0.90 | measured, of 7,169: **6,555 judged, 303 quarantined, 311 not to be
  found** (the figures of the first sitting, 2,160 / 454 / 4,555, were a resolution
  defect; they are retracted in §七 of `reports/DEFECTS-AND-FIXES.md`) |
| **the paper itself** | the paper a key belongs to is taken from the manifests the corpus ships with (`Registry/asset_manifests`), which name, for the key, the year, the ordinal of the examination, the subject, the role of the sheet, the path and the digest. Only where no row answers is a name guessed at, and then the record says `guessed:` | measured: 7,169 of 7,169 resolved by authority and 0 by the guess; the manifests answer for 100% of the bank's 2,341 keys, 120 digests checked and all agreeing |
| **a witness must speak** | a field nothing was written into is an absence, not a dissent: it is `not-reviewed`, never a drift and never a correction | measured: 1,074 of the 1,075 "drifts" of the stem were of this kind |
| **the marks are boundaries** | an item is divided at its private-use option marks when, and only when, the runs correspond to the number of options the paper declares through its own readable items; where they do not, the two fields are refused (`incomparable-unsplit-options`), the ground being written on the record | measured: 6,524 of 7,169 carry four options, 24 refused; `missing-labels` from 5,505 down to 0 |
| **answer authority** | the P leg's answer comes only from the official sheets, the 更正答案
  prevailing over the 答案 wherever it speaks; never inferred out of the question | measured:
  3,479 of 7,169 records rest on a correction sheet (3,341 with it, 138 of it alone); 4 have
  no authority at all, and are reported as having none |

**Honest limitation of the first gate design.** Taken raw, the dual-engine content gate would
quarantine 8/10 question papers (median 0.9977 < 0.999). Decomposing the dispute mass (body
region, chrome-masked) shows it is not content at all:

| class of difference | share of dispute mass |
|---|---|
| whitespace / line-joining (`\n`, spaces) | 1,862 (~79%) |
| CJK ideographs (genuine, must be adjudicated) | **213 (~9%)** |
| PUA/EUDC bullet glyphs (structure, not characters) | 186 (~8%) |
| ASCII/Latin punctuation & spacing | 76 (~3%) |
| fullwidth/CJK punctuation | 9 (<1%) |
| CJK compatibility ideograph `U+F967` | 1 (hazard class) |

⇒ The gate is only meaningful on a **declared canonical form** whose equivalence classes are
enumerated in writing (whitespace within a line; PUA bullets as structural tokens; width
variants *flagged, never mapped*; compatibility ideographs *flagged*). After that contract, the
residual is 213 ideograph-units over 10 papers — a reviewable backlog, not a flood. Setting
0.9953 because that is where the sample happens to pass would be exactly the «silent error» the
protocol prohibits; the number must come from a gold set (§7), not from the data it is supposed
to police.

**Two clauses of the canonical form were learned the hard way, and are declared here so that
they are not learned twice** (`src/qbr/canon.py`, `reports/DEFECTS-AND-FIXES.md`):

* *Presentation markup is not the text.* A reviewer who writes `GABA<sub>A</sub> 受體` to keep
  the meaning of a subscript has not changed the question. `canon.strip_markup()` removes a
  tag on the declared whitelist when the field pairs it (`<sub>x</sub>`), when the same name
  opens twice unclosed (`H<sub>2<sub>O`), or when it is void (`<br>`); an isolated angle
  bracket — `A<B 且 C>D`, common in a chemistry option — is left exactly as it stands. The
  fold is for comparison only; the record keeps its markup, and only the canonical image of it
  is written.
* *A comparison needs two witnesses, and a floor.* The machine's reading of a stem is the text
  as the legacy pipeline damaged it; the reviewer's is as a person repaired it. The pick is
  made on the better of the two, and where neither reaches the floor the record is
  quarantined, not judged. Before this clause was in force, 51.4% of the sampled records were
  being compared against a question other than the one they named, and the disagreement rates
  of the report were a measurement of the reading.

## 7. Next milestones (in order, with the reason)

1. **Gold set, 200 questions, human-adjudicated** (stratified: 8 categories × 5 eras × item
   types × the 3 legacy error classes). Needed to calibrate every threshold above (§16.2).
   Cost estimate: reading 200 items ≈ 2 min each ⇒ ~7 h of expert time, one-off.
2. **Declared canonical-form contract** — **done**, in `src/qbr/canon.py` (`fold`, `strip_markup`, `norm`, `serialise`) and enforced by the tests: width classes folded on the match side only, PUA/EUDC runs carried as structure, presentation markup removed under a declared whitelist, compatibility ideographs folded for comparison and never rewritten. Re-running `scripts/run_pilot.py` gives the valid content gate.
   2b. **Numbering styles of the print form** — the `bare_number_space` shape (`20 下列…`) was
   missing from the declared templates, and detection was being scored on the raw rows while
   segmentation consumed the merged ones; both are repaired, and `segment_best()` now tries the
   ranked candidates and keeps the one that actually yields the run the paper claims. One paper
   that yielded a single record out of eighty now yields eighty, with zero gaps.
3. **PUA bullet handling**: tokenise `EUDC` runs as `{BULLET}`; recover the label sequence from
   ordered bullets + geometry; strip-OCR the marker region only when the sequence is ambiguous.
   This is the fix for the 選項 family, replacing "the VLM will know it".
4. **Answer/corrected-answer merge** — **done** (`canon.parse_answer_table`,
   `canon.merge_answer_tables`, `compare_three.answer_authority_for`). Two things the earlier
   note missed: a 更正答案 sheet is a *re-published* table, complete in itself, so it prevails
   wherever it agrees or disagrees, item by item; and it heads its column of numbers with
   **題序** where an 答案 sheet writes **題號** — both words must be declared, in both scripts,
   or the correction parses to nothing (measured: 7 of 12 sampled correction sheets did).
5. **Crops**: implement `crop = image_bbox ∪ text_block_bbox ∪ bullet_strip`, 300 dpi, manifest
   rows; verify visually against the rendered page (the two defect families both live here).
6. **Quarantine + review UI**: the sandbox writes `quarantine/` bundles with evidence; the
   existing `review_ui/` may consume them later (not required for the pilot, and not run
   against AI395/PostgreSQL).
7. **Scale-up**: 1,000 papers ≈ 45 s CPU + 0 GPU. If (and only if) the strip-OCR of bullet
   strips needs a model, that — and only that — may go to the DGX endpoint
   `http://192.168.10.90:8888`; per-paper budget must be recorded in the manifest.

## 8. What I need from you (decisions, not approvals of production changes)

1. **Retirement of `OCR_CHAR_MAP` / `OCR_CONTEXT_MAP` / `OCR_SYMBOL_MAP`** for the 101+ track —
   my measurement says they repair damage the pipeline itself creates. Confirm, or ask for a
   side-by-side on 200 items first.
2. **The 51 legacy errors to avoid** list (§3 of `text-normalization-rules.md`) — keep as
   `100及以前` legacy track only, or migrate?
3. **Gold-set owner**: who adjudicates the 200 questions (me, with you reviewing the rendered
   pages, is fine for the pilot).
4. Whether **item-type detection** (MCT/申論/mixed) may replace the hardcoded
   `question_type: single_choice` in the normalised-record schema.

---

### Files in this sandbox

| path | content |
|---|---|
| `data/raw/**` | 30 official PDFs, copies (`data/sample_manifest.jsonl` = digests + provenance) |
| `src/qbr/triage.py` | Stage C (`triage_pdf`) |
| `src/qbr/extract.py` | Stage D dual engines + comparison (`extract_pair`, `compare`) |
| `src/qbr/cjk.py` | codepoint census (`audit_text`), simplified-only detector (2,325 chars) |
| `src/qbr/repair.py` | Level-0 rules, chrome mask, anchor-style detection, segmentation |
| `src/qbr/patterns/cjk_numerals.json` | declared CJK numeral table (validated by continuity) |
| `scripts/build_sample_set.py` | stratified sample builder |
| `scripts/run_pilot.py` | the comparison run; writes `reports/` |
| `scripts/analyze_pilot.py` | writes `reports/pilot_summary.md` (all numbers in §2, §3, §6) |
| `reports/stage_c_d_results.{jsonl,csv}` | per-paper measurement rows |
| `reports/issues.jsonl` | per-issue records with severity |
| `reports/BLOCKERS.md` | blockers (§21) — currently none hard; §6 lists the honest limitations |
