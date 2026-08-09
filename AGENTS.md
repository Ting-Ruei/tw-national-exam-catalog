# tw-national-exam-catalog Agent Instructions

This repository catalogs Taiwan national exam PDFs, MinerU outputs, parsed question candidates, review events, and PostgreSQL schema drafts.

## Ground Rules

- Do not commit secrets, API keys, owner tokens, database passwords, or copyrighted textbook content.
- Keep official exam PDFs, MinerU outputs, candidate JSONL, review JSONL, manual assets, and other large derived artifacts out of Git unless the user explicitly asks for a small sample.
- Treat `國考題資料夾/` and `國考題資料夾_其他類型/` as local data roots; inspect carefully before writing.
- Human review events are append-only. Do not rewrite existing `question_review_events.jsonl`, `answer_review_events.jsonl`, or future AI review logs unless the user explicitly asks for a repair script.
- If parser changes alter already-reviewed candidate content, append a per-question `reset_review` event and preserve previous notes.
- Do not auto-accept or auto-block questions from AI output alone. AI review is advisory.

## Useful Commands

```bash
python3 -m py_compile scripts/serve_question_review_ui.py scripts/build_question_candidates_from_mineru.py
bash scripts/ai395_catalog_runtime.sh checkout
bash scripts/ai395_catalog_runtime.sh verify
bash scripts/ai395_catalog_runtime.sh tunnel
```

## Review UI

- Since 2026-08-09, AI395 (`ssh ai395`, Tailscale `100.65.112.73`) is the default deployment, verification, and debugging target. Its mutable checkout is `/home/tim/src/tw-national-exam-catalog`.
- The current AI395 Catalog service is the isolated restore drill at loopback `8875/8876` with PostgreSQL on `54330`. Reach it through `scripts/ai395_catalog_runtime.sh tunnel`; do not treat it as a production writer.
- Production authority has not moved yet: the Mac Studio at `http://192.168.10.70:8765/` remains the only human-review writer and its PostgreSQL remains the production review truth until a separately approved single-writer cutover.
- The MacBook Compose stack is a legacy fallback during the retirement window ending no earlier than 2026-09-08. Keep it loopback-only, do not start new long-running maintenance jobs there, and never treat its events as production.
- Move code through reviewed Git commits. Keep the AI395 operator checkout clean, build immutable releases from exact SHAs, and never patch an immutable release or leave a server-only change.
- Do not create or repoint `/srv/ai395/data/tw-national-exam-catalog/main`, mount the versioned candidate into production, stop the Mac Studio writer, or use `rsync --delete` without explicit cutover approval.
- Question review writes to `question_review_events.jsonl`.
- Answer review writes to `answer_review_events.jsonl`.
- AI format audit writes to `question_ai_review_events.jsonl` and must remain advisory until a human accepts the question.

## DevSpace / ChatGPT MCP

When editing on the MacBook, use this repository as the workspace root:

```text
/Users/tim/AI workspace/ai_learning_platform/tw-national-exam-catalog
```

The corresponding AI395 operator checkout is `/home/tim/src/tw-national-exam-catalog`; it is a runtime/deployment target, not a second source of uncommitted code.

Prefer small, inspectable edits and summarize verification commands. If a task touches database schema, review UI behavior, parser rules, or large data workflows, explain the data impact before changing files.
