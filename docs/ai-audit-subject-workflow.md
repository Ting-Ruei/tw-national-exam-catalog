# AI Audit Subject Workflow

This workflow separates task export, model review, and result import. It is designed to prevent local heuristic fallback or accidental OpenAI API calls from being mistaken for Codex/ChatGPT model review.

## Current Model Decision

- `5.4` is the current balanced choice for quality and cost.
- `5.4-mini` still needs validation. Do not treat `gpt-5.4-mini` pilot labels as reliable until a clean task/result loop is proven.
- Local `heuristic` labels are useful as a cheap smoke test, but they are not model review.

## Export By Subject

Generate subject-separated tasks:

```bash
python3 scripts/export_subject_codex_audit_batches.py \
  --chunk-size 500 \
  --model-target 5.4 \
  --ai-policy pending-or-unreliable
```

The exporter only writes task files. It does not call a model and does not write `question_ai_review_events.jsonl`.

Output structure:

```text
國考題資料夾/30_normalized_items/question_candidates/subject_codex_audit_tasks/<timestamp>/
  subject_audit_manifest__<timestamp>.csv
  subject_audit_summary__<timestamp>.json
  001__<考別>__<科目>/
    CODEX_SUBJECT_AUDIT_PROMPT.md
    subject_manifest.json
    chunks/
      codex_question_audit_tasks__...__part0001.jsonl
      codex_question_audit_results__...__part0001.jsonl  # expected output path
```

`--ai-policy pending-or-unreliable` exports candidates that have no reliable AI audit or only unreliable pilot/local labels. Current unreliable labels include:

- `heuristic`
- `gpt-5.4-mini`
- `codex-gpt5-pilot`
- reviewer `batch-ai-audit`
- reviewer `codex-5.4mini-pilot`
- reviewer `codex-pilot-5parts`

## Run One Subject With Codex

Pick one row from `subject_audit_manifest__<timestamp>.csv`, then ask Codex/ChatGPT to read that subject folder's `CODEX_SUBJECT_AUDIT_PROMPT.md`.

The model should:

- read only the task JSONL in that subject's `chunks/`;
- output one JSON object per input candidate;
- write results to the expected `codex_question_audit_results__...jsonl` paths;
- follow `docs/skills/national-exam-ai-audit/SKILL.md`;
- never call local heuristic scripts;
- never call OpenAI API fallback;
- never edit human review events.

## Import Results

Import one subject:

```bash
python3 scripts/import_codex_audit_results.py \
  "國考題資料夾/30_normalized_items/question_candidates/subject_codex_audit_tasks/<timestamp>/<subject_dir>" \
  --model 5.4 \
  --reviewer codex-5.4-subject-audit \
  --notes "Codex 5.4 subject audit; advisory only."
```

Import a whole run after multiple subjects are finished:

```bash
python3 scripts/import_codex_audit_results.py \
  "國考題資料夾/30_normalized_items/question_candidates/subject_codex_audit_tasks/<timestamp>" \
  --model 5.4 \
  --reviewer codex-5.4-subject-audit \
  --notes "Codex 5.4 subject audit; advisory only."
```

The import script appends to `question_ai_review_events.jsonl` only. It does not change human review state.

## What Not To Use For Model Quality Tests

Avoid this command for 5.4 / 5.4-mini quality testing:

```bash
python3 scripts/run_question_ai_audit_batch.py
```

That script is useful for the Review UI button and local/API smoke tests, but it falls back to local heuristic when `OPENAI_API_KEY` is absent. It can blur the difference between actual model review and rule-based checks.

## Suggested Validation Loop

1. Run one subject with `5.4`.
2. Import results with reviewer `codex-5.4-subject-audit`.
3. In Review UI, filter by the model/reviewer and inspect `AI 有疑點` plus `AI 有建議校正`.
4. Run the same subject, or a matched chunk, with `5.4-mini`.
5. Compare false positives, missed OCR issues, table/image handling, and suggested corrections before scaling `5.4-mini`.
