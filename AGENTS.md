# tw-national-exam-catalog Agent Instructions

This repository catalogs Taiwan national exam PDFs, MinerU outputs, parsed question candidates, review events, and PostgreSQL schema drafts.

## Ground Rules

- Do not commit secrets, API keys, owner tokens, database passwords, or copyrighted textbook content.
- Keep official exam PDFs, MinerU outputs, candidate JSONL, review JSONL, manual assets, and other large derived artifacts out of Git unless the user explicitly asks for a small sample.
- Treat `國考題資料夾/` and `國考題資料夾_其他類型/` as local data roots; inspect carefully before writing.
- Human review events are append-only. Do not rewrite existing `question_review_events.jsonl`, `answer_review_events.jsonl`, or future AI review logs unless the user explicitly asks for a repair script.
- If parser changes alter already-reviewed candidate content, append a per-question `reset_review` event and preserve previous notes.
- Do not auto-accept or auto-block questions from AI output alone. AI review is advisory.

## Governance and GitHub Change Control

- The human-readable governance authority is `docs/governance/README.md`; the machine-readable companion is `governance/policy.json`.
- New work uses a `codex/*` or `agent/*` branch and a pull request. Agents must not push new work directly to `main`, self-approve a PR, force-push `main`, or treat a merged PR as production approval.
- Agents may act autonomously through G2 only: G0 read-only inspection, G1 branch/PR work, and G2 advisory or isolated staging work. Every G3 production mutation needs exact per-run human approval. G4 human review decisions, restore, writer changes, append-only repair, and material deletion remain owner-only.
- Unattended OpenClaw cron or hook runs cannot obtain approval mid-run. They must stop after producing evidence, a report, an issue, a package, or a PR; they may not continue into G3/G4 work.
- Changes to governance, GitHub workflows, database schema, production deployment, or AI395 control paths require owner review through CODEOWNERS.
- Keep agent operational evidence separate from human question-review events. An agent must never impersonate a human reviewer.

## Useful Commands

```bash
python3 scripts/validate_agent_governance.py
python3 -m unittest discover -s tests
python3 -m py_compile scripts/serve_question_review_ui.py scripts/build_question_candidates_from_mineru.py
bash scripts/ai395_catalog_runtime.sh checkout
bash scripts/ai395_catalog_runtime.sh verify
bash scripts/ai395_catalog_runtime.sh tunnel
```

## Review UI

- Since the 2026-08-09 cutover, AI395 (`ssh ai395`, LAN `192.168.10.90`) is the sole production Review UI and PostgreSQL writer. Desktop is `http://192.168.10.90:8765/`; mobile is `http://192.168.10.90:8766/mobile/`. Per the owner's post-cutover decision, both use trusted-LAN access without application login; keep them bound only to the fixed LAN address.
- The immutable production release is `/srv/ai395/releases/tw-national-exam-catalog/e89c60fd7502a0fce1c47c7b9577a211888504a9`; the clean mutable operator checkout is `/home/tim/src/tw-national-exam-catalog`. Production control is `/srv/ai395/stacks/tw-national-exam-catalog/production/ai395_catalog_production.sh`.
- PostgreSQL is published only on AI395 loopback `127.0.0.1:54329`; use an SSH tunnel for remote DB maintenance. The older AI395 restore drill remains isolated on `8875/8876/54330` and is not a writer.
- The Mac Studio `192.168.10.70` Review UI is stopped. Its PostgreSQL, assets, Compose volume, and freeze snapshot remain intact as rollback evidence through at least 2026-09-08; do not restart its Review UI while AI395 accepts writes.
- The MacBook Compose stack is a legacy fallback during the retirement window ending no earlier than 2026-09-08. Keep it loopback-only, do not start new long-running maintenance jobs there, and never treat its events as production.
- Move code through reviewed Git commits. Keep the AI395 operator checkout clean, build immutable releases from exact SHAs, and never patch an immutable release or leave a server-only change.
- Never run two Review UI writers. Any rollback must first stop AI395; if AI395 has accepted writes, take a fresh AI395 dump before restoring an isolated Mac environment. Never use `rsync --delete` for migration or rollback.
- Production question review writes to the append-only SQL `exam.question_review_events` table.
- Production answer review writes to the append-only SQL `exam.answer_review_events` table.
- AI format audit writes to `exam.question_ai_review_events` and must remain advisory until a human accepts the question. Legacy JSONL files are historical/exchange artifacts, not the production review authority.

## DevSpace / ChatGPT MCP

When editing on the MacBook, use this repository as the workspace root:

```text
/Users/tim/AI workspace/ai_learning_platform/tw-national-exam-catalog
```

The corresponding AI395 operator checkout is `/home/tim/src/tw-national-exam-catalog`; it is a runtime/deployment target, not a second source of uncommitted code.

Prefer small, inspectable edits and summarize verification commands. If a task touches database schema, review UI behavior, parser rules, or large data workflows, explain the data impact before changing files.
