# Stage 1 pilot - measured results

Same input, both sides. Inputs are the copies under `data/raw` (digest-pinned in
`data/sample_manifest.jsonl`); the legacy side is the existing MinerU markdown for the
identical PDFs. No number in this file is estimated: everything below was produced by
`scripts/run_pilot.py` over the files named.

## 1. Sample

| metric | value |
|---|---|
| papers processed | 30 |
| question papers | 10 |
| answer papers | 10 |
| corrected-answer papers | 10 |
| categories | 3 |
| years | [100, 104, 106, 110, 111] |
| with legacy MinerU markdown | 24 |

## 2. Stage C triage: how much OCR budget is avoidable

| triage class | papers |
|---|---|
| NATIVE_TEXT_LAYOUT_RISK | 20 |
| LEGACY_ENCODING | 7 |
| NATIVE_TEXT_GOOD | 3 |

- no-text-layer cases needing real OCR: **0 / 30**
- usable text layer present (OCR avoidable for the text task): **30 / 30**

## 3. Stage D dual-parser agreement (independent engines)

| classification | papers |
|---|---|
| STRUCTURAL_DISAGREEMENT | 18 |
| TEXT_DISAGREEMENT | 12 |

Observed distributions (used to set the thresholds, not guessed):

| signal | n | min | p05 | p50 | p90 | p95 | max | mean |
|---|---|---|---|---|---|---|---|---|
| content_similarity | 30 | 0.9922 | 0.9953 | 1.0 | 1.0 | 1.0 | 1.0 | 0.9988 |
| structure_similarity | 30 | 0.7028 | 0.7434 | 0.9291 | 0.9892 | 0.9896 | 0.9909 | 0.8939 |

## 4. Glyph integrity of the native text layer (the 簡體 complaint)

| source | papers with hits | total hits | max/paper | mean/paper |
|---|---|---|---|---|
| native PDF text layer | 1 | 1 | 1 | 0.033 |
| legacy MinerU markdown | 12 | 64 | 12 | 2.667 |

- papers whose native layer contains U+FFFD/PUA/control characters: 7 (total 1336 characters)

## 5. Legacy vs new, same document (§15.3 taxonomy)

| verdict | papers |
|---|---|
| EXTRA_TEXT | 13 |
| NO_LEGACY_OUTPUT | 6 |
| UNICODE_SYMBOL_ERROR\|EXTRA_TEXT | 6 |
| UNICODE_SYMBOL_ERROR | 4 |
| UNICODE_SYMBOL_ERROR\|EXTRA_TEXT\|QUESTION_SPLIT_ERROR | 1 |

## 5b. Level-0 deterministic repair impact (same inputs)

| classification | before repair | after level-0 repair |
|---|---|---|
| STRUCTURAL_DISAGREEMENT | 18 | 5 |
| TEXT_DISAGREEMENT | 12 | 25 |


- papers that moved STRUCTURAL → TEXT under the repair: **13**. This is the expected 
  effect of masking chrome: once the layout noise is out of the way, the two engines are
  left disagreeing about *characters*, which is the kind of dispute that has to be read,
  not averaged. A fall in the STRUCTURAL column is therefore not a fall in agreement.

- apparent content disputes cleared by declared rules only: **0**
- still requiring escalation (quarantine or targeted crop work): **25**

Question structure recovered by the deterministic segmenter (engine A view):

| signal | n | min | p05 | p50 | p90 | p95 | max | mean |
|---|---|---|---|---|---|---|---|---|
| questions_a | 30 | 0 | 0.0 | 43.0 | 79.1 | 80.0 | 80 | 41.8333 |
| questions_b | 30 | 0 | 0.0 | 53.5 | 80.0 | 80.0 | 94 | 45.6667 |
| residual_a | 30 | 5 | 5.9 | 13.5 | 142.0 | 142.0 | 144 | 54.0333 |
| missing_options_a | 30 | 0 | 0.0 | 2.0 | 75.0 | 75.55 | 77 | 23.5667 |
| option_collisions_a | 30 | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 | 0.0 |
| question_number_gaps_a | 30 | 0 | 0.0 | 1.0 | 1.0 | 1.0 | 1 | 0.6667 |
| anchors | 30 | 0 | 0.0 | 0.0 | 9.8 | 80.0 | 80 | 8.1 |

## 5c. Segmentation gate: read the paper before you read the questions

| gate | papers |
|---|---|
| accepted | 10 |
| refused | 0 |

Item types read out of the declared parts (甲、申論 / 乙、測驗 in one file are two papers):

| paper item type | papers |
|---|---|
| mixed | 6 |
| single-part | 4 |


| quantity | legacy MinerU (own registry) | new deterministic path (measured here) |
|---|---|---|
| registry rows | 15712 | 30 |
| status mix | {"ok": 4030, "skipped_existing": 2890, "error": 8788, "timeout": 4} | - |
| wall seconds per paper (mean) | 121.883 | 0.047 |
| wall seconds per paper (p50) | 34.541 | 0.026 |
| wall seconds per paper (p95) | 247.3438 | 0.141 |
| total wall hours spent by legacy ok-runs | 136.3 | - |
| GPU/VLM calls per paper | 1 full-document VLM pass (not instrumented in the registry) | 0 |
| peak RSS (MB) of this process | - | 84.7 |

## 7. Issues raised (issues.jsonl)

| kind/severity | count |
|---|---|
| simplified_contamination/medium | 12 |
| parser_disagreement/high | 12 |
| unmapped_glyph/high | 7 |
| native_simplified_contamination/high | 1 |

## 8. What these measurements say about the thresholds (and what they may not say)

- content agreement, observed p05: `0.9953`. **An observation, not a gate.** The gate is a
  declared value that comes out of the gold set (`PROPOSED_WORKFLOW.md` §6 and §7.1);
  setting it where the sample happens to pass is the silent error the protocol forbids.
- structure agreement, observed p50: `0.9291`; 15 of 30 papers sit below it and are the
  deterministic-repair candidates. Same caveat: it locates the work, it does not excuse a
  threshold.
- simplified-glyph alarm: any hit in the *native* layer is investigated (it means a broken ToUnicode CMap, not a wording issue); hits in the *legacy* side are treated as extraction artefacts and are the primary argument for retiring the character maps

