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
docker compose up -d postgres review-ui
docker compose logs -f review-ui
bash scripts/postgres_apply_schema.sh
```

## Review UI

- The current authoritative maintenance and human-review server is the Mac Studio at `http://192.168.10.70:8765/`; its repository root is `/Users/tim/tw-national-exam-catalog`.
- Other computers, including Tim's MacBook Pro, are remote-maintenance or local validation clients only. Their `http://127.0.0.1:8765/` containers are not production.
- For deployment, restart, production database, or live UI work, first verify the target is the Mac Studio (`192.168.10.70`) and validate the result against that URL. Do not treat a localhost success as production success.
- Keep using the Mac Studio as the authoritative server until the user explicitly announces a server migration.
- Question review writes to `question_review_events.jsonl`.
- Answer review writes to `answer_review_events.jsonl`.
- AI format audit writes to `question_ai_review_events.jsonl` and must remain advisory until a human accepts the question.

## DevSpace / ChatGPT MCP

When ChatGPT connects through DevSpace, open this repository as the workspace root:

```text
/Users/tim/tw-national-exam-catalog
```

Prefer small, inspectable edits and summarize verification commands. If a task touches database schema, review UI behavior, parser rules, or large data workflows, explain the data impact before changing files.
