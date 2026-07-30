# AI Audit Subject Workflow

This workflow separates task export, model review, and result import. It is designed to prevent local heuristic fallback or accidental OpenAI API calls from being mistaken for Codex/ChatGPT model review.

## Current Model Decision

- `gpt-5.6-luna` through Codex subagents is the current high-quality review path.
- Fork subagents from the current Codex task so they inherit the parent model and repository context. Do not route LUNA work through ask-bridge or the ChatGPT website.
- Local `heuristic` labels remain useful as a cheap smoke test, but they are not model review.
- Current correction-first prompt version: `codex_gpt56_luna_question_audit_v3`.

## Export By Subject

Generate subject-separated tasks:

```bash
python3 scripts/export_subject_codex_audit_batches.py \
  --chunk-size 500 \
  --model-target gpt-5.6-luna \
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

## Run One Subject With A Codex Subagent

Pick one row from `subject_audit_manifest__<timestamp>.csv`, then ask Codex/ChatGPT to read that subject folder's `CODEX_SUBJECT_AUDIT_PROMPT.md`.

The subagent should:

- read only the task JSONL in that subject's `chunks/`;
- output one JSON object per input candidate;
- write results to the expected `codex_question_audit_results__...jsonl` paths;
- follow `docs/skills/national-exam-ai-audit/SKILL.md`;
- never call local heuristic scripts;
- never call OpenAI API fallback;
- never edit human review events.
- provide a complete, UI-safe correction whenever a local text/notation replacement is safe;
- include every original option in order whenever `suggested_correction.options` is present.

## Import Results

Import one subject:

```bash
python3 scripts/import_codex_audit_results.py \
  "國考題資料夾/30_normalized_items/question_candidates/subject_codex_audit_tasks/<timestamp>/<subject_dir>" \
  --model gpt-5.6-luna \
  --reviewer codex-gpt56-luna-subject-audit \
  --notes "Codex GPT-5.6 LUNA subject audit v3; advisory only."
```

Import a whole run after multiple subjects are finished:

```bash
python3 scripts/import_codex_audit_results.py \
  "國考題資料夾/30_normalized_items/question_candidates/subject_codex_audit_tasks/<timestamp>" \
  --model gpt-5.6-luna \
  --reviewer codex-gpt56-luna-subject-audit \
  --notes "Codex GPT-5.6 LUNA subject audit v3; advisory only."
```

The import script appends to `question_ai_review_events.jsonl` only. It does not change human review state.

## What Not To Use For Model Quality Tests

Avoid this command for 5.4 / 5.4-mini quality testing:

```bash
python3 scripts/run_question_ai_audit_batch.py
```

That script is useful for the Review UI button and local/API smoke tests, but it falls back to local heuristic when `OPENAI_API_KEY` is absent. It can blur the difference between actual model review and rule-based checks.

## Suggested Validation Loop

1. Run one representative subject pilot with `gpt-5.6-luna`.
2. Import results with reviewer `codex-gpt56-luna-subject-audit`.
3. In Review UI, filter by the model/reviewer and inspect `AI 有疑點` plus `AI 有建議校正`.
4. Verify every option correction preserves the complete option set and order.
5. Compare false positives, missed OCR issues, table/image handling, and one-click correction coverage before scaling the run.
