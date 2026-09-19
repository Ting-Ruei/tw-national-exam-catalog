# question_bank_rebuild — sandbox for the 全國exam question-bank rebuild

Sandbox for `tw-national-exam-catalog` work under `pi_test/`, per the instruction that all tests
live here and needed resources are **copied** in rather than referenced for writing.

Read `PROPOSED_WORKFLOW.md` first: it is the deliverable (three-way comparison of the legacy
MinerU-centric path, the 先前改進方案 in `docs/`, the MOEX protocol v1, and the measured proposal),
with the numbers behind each claim.

## Layout

```
data/raw/                 copies of the official PDFs (read-only originals stay untouched)
data/sample_manifest.jsonl  digest-pinned inventory of those copies + legacy comparison targets
src/qbr/                  deterministic core (no model calls)
  triage.py                 cheap classification of a PDF
  extract.py                two independent extraction engines + comparison
  cjk.py                    codepoint census, simplified-only detector (never a converter)
  repair.py                 Level-0 rules, geometry chrome mask, anchor-style detection
  canon.py                  the declared canonical form: fold, strip_markup, norm, serialise,
                            the answer table and its merge, the comparison and its verdicts
  repair.detect_anchor_styles / segment_best / segment_mixed
                            the paper is read part by part (甲、申論 + 乙、測驗), each part on
                            the style that actually yields the run the paper claims
  patterns/cjk_numerals.json  declared numeral table, validated by continuity
scripts/                  build_sample_set.py | run_pilot.py | analyze_pilot.py
                            read_the_registry.py  the manifests the corpus ships with, which are
                                               the authority for which paper a registry key was
                                               harvested from (100% of the bank's keys answered,
                                               digests verified); `three_way.resolve_pdf` asks it
                                               first and only then guesses, and says which it did
                            where_is_the_text.py  over the whole corpus, where the text of a
                                               record that could not be alignment really is
                            three_way.py       --mode queue | gold | blind   the review queue,
                                               the adjudication sheet, the unseen AI leg
                            compare_three.py                                 the whole corpus,
                                               two witnesses to a record and a floor under the
                                               alignment: below it, nothing is judged
                            qdb.py            read-only against a disposable copy of the bank
data/gold/                compare_records.jsonl (the measured sample) | three_way.jsonl (the sheet)
data/db_snapshot/         hr_candidates.csv + provenance.json, the export the queue is read from
reports/                  pilot_summary.md, stage_c_d_results.{jsonl,csv}, issues.jsonl,
                          review_queue.{csv,json}, three_way.md, compare_three.md,
                          DEFECTS-AND-FIXES.md (what was wrong here, and the numbers of the fix)
tests/                    pytest suite asserting the measured invariants
```

## Reproduce

```sh
./.venv/bin/pip install pymupdf pdfminer-six pypdf pillow pytest hanzidentifier   # opencc-python did not build here
brew install poppler                                     # engine β: pdftotext/pdfinfo
./.venv/bin/python scripts/build_sample_set.py --count 30 --per-bucket 2
./.venv/bin/python scripts/run_pilot.py
./.venv/bin/python scripts/analyze_pilot.py
./.venv/bin/python -m pytest tests -q                    # 27 passed, ~33 s

# the three-way review. Two draws, and the difference matters:
#   * no --ids-from  → a fresh *stratified* draw (`stratified()`, per quality_status bucket),
#                      which is the right thing for an opportun check of the pipeline;
#   * --ids-from FILE → the *same* candidates as an earlier run, which is the only thing a
#                      before/after comparison of two versions of the code may be made on.
# The shipped `data/gold/compare_records.jsonl` is the 7,169-candidate pinned sample below.
./.venv/bin/docker run -d --rm --name exam_copy_probe -e POSTGRES_DB=qbank_copy \
    -e POSTGRES_USER=probe -e POSTGRES_PASSWORD=probe postgres:16-alpine   # see qdb.py's header
./.venv/bin/python scripts/compare_three.py                                  # a fresh stratified draw
./.venv/bin/python scripts/compare_three.py --universe 0 --per-bucket 6 \
    --ids-from _backup_prefix_20260913/gold/compare_records.jsonl            # the 7,169, as shipped
./.venv/bin/python scripts/three_way.py --mode queue --show 12             # the review queue
./.venv/bin/python scripts/three_way.py --mode gold                        # the adjudication sheet
./.venv/bin/python scripts/three_way.py --mode blind                      # needs QBR_AUDIT_* below

export QBR_AUDIT_ENDPOINT=http://192.168.10.90:8888/v1/chat/completions
export QBR_AUDIT_MODEL=<a model name>           # the blind leg has never been run: no endpoint set
```

`--ids-from` is what makes the two columns of §三 of `reports/DEFECTS-AND-FIXES.md` comparable:
the sample is pinned by id, so a re-read measures the same questions, and the reading — not the
corpus — is what changes.

## Two archives, kept on purpose

* `_backup_prefix_20260913/` — this sandbox exactly as it stood before the repairs of
  `reports/DEFECTS-AND-FIXES.md` were made: the code, the reports, and the sample of 7,169
  candidates the before/after columns are measured over.
* `_verify_20260913/rerun_with_current_code/` — the outputs of the re-runs made with that
  pre-fix code (hashes and byte counts included), which is how the queue was shown to be
  reproducible byte for byte before anything in it was changed.

## Rules of this sandbox

1. **Read-only upstream.** Nothing outside this directory is modified; the corpus under
   `tw-national-exam-catalog/國考題資料夾/` is only read as a comparison target.
2. **Deterministic first, model last.** The pilot makes zero model calls. Where a model is
   unavoidable it is a *region* (a bullet strip, a figure), never a whole document, and it must
   record its own cost in the manifest.
3. **No silent normalisation of 漢字符.** Detection only; correction is a reviewed action.
   Character maps that would rewrite CJK codepoints are retired from this design (see §3.1 of
   `PROPOSED_WORKFLOW.md`).
4. **Two engines, both compared.** A paper is not publishable unless the independent engines
   agree on the declared canonical form; disagreement is quarantined, not averaged.
5. **Thresholds come from a gold set**, never from the data they are meant to police.
6. `Ryzen AI MAX 395+` / AI395 services (Review UI, PostgreSQL, the 395 OCR fleet) are treated
   as absent. The only reachable inference endpoint for later, *region-level* use is
   `http://192.168.10.90:8888` (DGX Spark), and it is not used by this pilot.

## Known state at the end of this session

* Stage C + D + Level-0 rules + segmentation: working, measured, tested.
* The declared canonical form, the answer-table merge (with 更正答案 prevailing), the
  segmentation continuity gate and the alignment floor are all in force; the seven defects that
  made the earlier report untrustworthy are set out in `reports/DEFECTS-AND-FIXES.md`, with the
  before/after figures over the same 7,169 candidates.
* What the reading now says, over the pinned sample of 7,169 human-reviewed candidates:
  6,555 (91.4%) are judged, 303 are quarantined with the ground of it on the record, 311 are
  not to be found; among the judged, the human record agrees with the official text on 59.0%
  of stems, and the recorded answer agrees with the official key — the 更正答案 prevailing —
  on 7,100 of 7,165. The earlier figure of this file ("two-thirds cannot be found, and that is
  a finding about the corpus") was a measurement of a resolver that guessed at the paper; it
  is retracted, with the rest of it, in §七 of `reports/DEFECTS-AND-FIXES.md`.
* The paper is no longer guessed at from the shape of its name. `scripts/read_the_registry.py`
  reads the manifests the corpus ships with, which name for every one of the bank's 2,341
  distinct registry keys the file that was downloaded under it; they are asked first, the
  digests are verified, and a resolution that had to be guessed at is labelled `guessed:` on
  the face of the record (`卷之所在：` in `reports/compare_three.md`).
* Open: the 200-question gold set (the source of every threshold here, all of which are
  currently *declared*, none *fitted*); bullet-strip label recovery for the 76.8% of judged
  records still reported `missing-labels:A,B,C,D`; and the blind AI leg, never yet run.
* `reports/BLOCKERS.md`: no hard blockers.
