---
name: build-exam-question-bank
description: Build a Taiwan national exam question bank from official PDFs — run the qbr pipeline end to end (triage, dual-engine extraction, answer-sheet merge, figure crops, disputes, package, review queue), know the stage order and why it is that order, and add a rule without breaking a paper that already works. Use when reading a new exam paper or subject, when a paper fails to parse or an answer looks wrong, when adding or fixing an extraction rule, when rebuilding a review queue, or when a question's figures or options look wrong in the Review UI.
---

# Build Exam Question Bank (`qbr`)

The pipeline in `tw-national-exam-catalog/qbr/`. Read `qbr/AGENTS.md` for the rules;
this is the working procedure.

**The one thing to get right:** the acceptance standard is **two engines agreeing on the paper**,
not a green test suite. A green test can prove a wrong rule.

## Stage order (it is a specification, not a suggestion)

```
S0_intake   freeze Q / ANS / MOD sheets by digest
S1_triage   cheap classification of the paper
S2_dual     two independent extraction engines, compared item by item
S3_gate     does the paper publish? (blocking classes listed in the report)
S4_records  one record per question, answer merged
S5_package  immutable package + lineage
S6_verify   re-read the package against the paper
```

```sh
cd tw-national-exam-catalog/qbr

# One paper, all seven stages. This is the acceptance path.
.venv/bin/python scripts/golden_path.py run \
    --registry-key moex:115090:308:0504:1 \
    --year 115 --ordinal 2 --category 醫事檢驗師 --subject 生物化學與臨床生化學 \
    --asset-root "../國考題資料夾" --out /tmp/run1 \
    --package-version tw-national-exam-medtech-v0.0.1

# A whole category
.venv/bin/python scripts/batch_run.py     --category 醫事檢驗師 --work /tmp/work
.venv/bin/python scripts/batch_package.py --category 醫事檢驗師 --work /tmp/work
.venv/bin/python scripts/crop_run_figures.py --work /tmp/work          # AFTER packaging
.venv/bin/python scripts/build_review_queue.py --work /tmp/work --out /tmp/live

.venv/bin/python -m pytest tests/ -q
```

### `--registry-key` may or may not carry a role

The catalog spells the paper `moex:115090:308:0504:1` and the sheets `...:question` /
`...:answer`. **Both are accepted**; `package.paper_key()` strips the role. But `--year`,
`--ordinal`, `--category`, `--subject` are **not derivable** from the key in general (the key's
fields are `paper:category_code:subject_code:session`, not year/ordinal), so pass them.
A key passed without them produced `resolution: registry-manifest` with no correction sheet and
two 送分 answers silently published as single letters.

## Three order traps (all were hit)

1. **Crops come after packaging.** `disputes_for_paper()` needs option pictures **bound**
   (`image_refs[].asset_role == 'option-image'`) to tell a picture-option question from a broken
   one. 274 questions have empty option text; **251 are correct** (the option *is* the picture),
   **23 are real defects**. Disputes are therefore computed twice — at packaging (so an uncropped
   queue still carries them) and again by `crop_run_figures.py` (authoritative).
2. **Rebuilding into the served directory leaves the list and the candidate file briefly
   inconsistent.** Pause the service during a rebuild. Known-unsolved.
3. **A rebuild must not lose review records.** Carrying is automatic; see `qbr/AGENTS.md`.

## Adding or fixing a rule

Before the change:

1. **Measure what the other rule would lose.** No negative control, no measurement.
   `scripts/test_arbitration.py` and `scripts/verify_crops.py` are the models to copy.
2. **Ask whether it is a rule at all.** If it is "read what this text means", it belongs in a
   **prompt** (`prompts/`), not in a script. Scripts keep the **properties of the paper**
   (pages, character counts, font families, ink, block geometry, columns) — things measuring the
   same page twice gives the same answer for.
3. **State it in terms of the paper, never in terms of the parse.** A rule whose input is the
   parser's output makes the parser and the check confirm each other. That is a circle.
4. **Check it holds on both engines.**

After the change:

5. **Re-run the golden, not just your test.** `tests/test_merge_golden.py` compares a whole paper
   field for field.

## Diagnostics

```sh
# Where is the text of a record that would not align?
.venv/bin/python scripts/where_is_the_text.py
# Skeleton generality across the corpus (both engines)
.venv/bin/python scripts/measure_skeleton.py
.venv/bin/python scripts/compare_skeleton.py
# Crop completeness — ink *outside* the frame is not evidence; this measures the crop
.venv/bin/python scripts/verify_crops.py
# Ask a local model about a question or a crop
.venv/bin/python scripts/consult_local_model.py --help
```

Every script takes `--help`. Ports are parameters — read `QBR_MODEL_BASE_URL` /
`QBR_MODEL_NAME` / `QBR_MODEL_API_KEY`, never a hard-coded address.

## When an answer looks wrong

Check in this order, cheapest first:

1. **Is there a correction sheet?** `_MOD.pdf`. **A corrections sheet is a re-issued table, not
   only a note**: it reprints every answer, marks changed cells `＃`, and states the meaning in a
   note at the foot (`備註：第10題一律給分`). `corrections.py` merges it as a table **and** reads
   it as corrections, in that order.
2. **Did the sheet get found?** `run_manifest.json` → `S0_intake.detail.resolution` and
   `corrected_present`. `registry-manifest` alone means the directory was never consulted, which is
   a defect if a sheet is missing — the registry and the directory are **complementary**, not
   alternatives.
3. **Is the official answer `送分` or `A或D`?** Then it is not a wrong answer;
   `answer_payload.accepted_values` carries all accepted letters.
4. **Is the question picture-optioned?** Empty `options` + picture objects inside the question's
   own band = correct, not broken.

## Figures

- **Geometry and ink decide *where* a figure is. A local model decides *what* it is.**
- **Do not do located-screenshot** for image questions — that is what the model is for.
- **The crop frame is a property of the paper, not of the parse.**
- **Ink outside the crop frame cannot be used to judge completeness.**
- The crop standard must be **consistent** and must **include the question number**.
- Models in use: `ornith-1.5-mtplx-35b` (127.0.0.1:18120, fastest), `Qwen3.8-27B`
  (127.0.0.1:8082), `qwen3.8-flash-next` (DGX). **MTPLX builds, not Ollama; not `medgemma`.**
  All three saturate the arbitration test (111/111) — pick by latency.

## Report honestly

`reports/` is evidence, not specification. Every report states **what it does not prove**.
Two habits that produced most of the value here:

- **Separate measurement from interpretation.** Numbers first, meaning second.
- **When the user says "the picture is not visible", first check whether the picture was scanned
  at all.** The first three explanations were all about rendering; the cause was that the scan
  never saw it.
