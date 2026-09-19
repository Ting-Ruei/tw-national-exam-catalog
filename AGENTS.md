# tw-national-exam-catalog Agent Instructions

This repository catalogs Taiwan national exam PDFs, MinerU outputs, parsed question candidates, review events, and PostgreSQL schema drafts.

## Ground Rules

- Do not commit secrets, API keys, owner tokens, database passwords, or copyrighted textbook content.
- Keep official exam PDFs, MinerU outputs, candidate JSONL, review JSONL, manual assets, and other large derived artifacts out of Git unless the user explicitly asks for a small sample.
- Treat `國考題資料夾/` and `國考題資料夾_其他類型/` as local data roots; inspect carefully before writing.
- Human review events are append-only. Do not rewrite existing `question_review_events.jsonl`, `answer_review_events.jsonl`, or future AI review logs unless the user explicitly asks for a repair script.
- If parser changes alter already-reviewed candidate content, append a per-question `reset_review` event and preserve previous notes.
- Do not auto-accept or auto-block questions from AI output alone. AI review is advisory.

## Two tracks, each with its own AGENTS.md and skill

This repository now holds two actively maintained tracks. **Read the track's own AGENTS.md before
changing anything in it** — this file is the repository-wide floor, not the working procedure.

| Track | AGENTS.md | Skill |
|---|---|---|
| Question-bank build pipeline | [`qbr/AGENTS.md`](qbr/AGENTS.md) | [`docs/skills/build-exam-question-bank/SKILL.md`](docs/skills/build-exam-question-bank/SKILL.md) |
| Review UI (v2) | [`review_ui/AGENTS.md`](review_ui/AGENTS.md) | [`docs/skills/review-ui-v2/SKILL.md`](docs/skills/review-ui-v2/SKILL.md) |

### `qbr/` — the build pipeline

`qbr/` turns an official PDF into a platform-verifiable package. It reads the corpus and **writes no
database**. Its own venv is `qbr/.venv`; dependencies are `requirements/qbr.txt`.

- **Acceptance is two engines agreeing on the paper, not a green suite.** A green test can prove a
  wrong rule.
- **Scripts keep only the properties of the paper** (pages, character counts, font families, ink,
  block geometry) — the things that measuring the same page twice gives the same answer for.
  Everything that is *reading what the text means* is a **prompt** (`qbr/prompts/`), not a script.
- **Never state a rule in terms of the parse.** A rule whose input is the parser's output makes the
  parser and the check confirm each other.
- **Every check ships with a negative control** — the case that must fail on the old behaviour.
- **Relative paths only** in assets: no `/Users/`, `/Volumes/`, `file://`, drive letters.
  Two tests enforce it.
- **`registry_key` role suffixes are normalised by `package.paper_key()`.** A doubled suffix
  (`...:question:question`) was an 80/80 defect, and that key is the primary key a reviewer's
  decision is recorded against.
- **Do not edit `qbr/tests/golden/`** unless the paper itself changed. It is the proof that the code
  still reads a paper the same way.
- `qbr/data/`, `qbr/.venv/`, `qbr/runs/`, `qbr/tmp/` are gitignored; `qbr/tests/golden/` is tracked.

### `review_ui/` — v2 is the baseline

**`review_ui/v2.html` (route `/v2`) is the baseline. v1 is reference-only and no longer maintained**
(`review_ui/v1-reference/`: `mobile.html`, `workflow.html`). v1 still *answers* on its old routes,
because an existing bookmark and an installed PWA are contracts a better UI does not get to break;
fix v1 only to keep it working, never add a feature.

- **Improve by replacing, never by forking.** Two consoles showing the same questions is two places a
  reviewer's records can be lost.
- **One source for navigation and content** (charter: 導覽與內容必須來自同一個來源). `S.rows` is both
  what is drawn and what `W`/`S` walk. "A filter narrows what is drawn, not what is walked" was a
  real defect — measured, pressing `S` on group question 80 jumped to the next paper.
- **Keyboard is all left hand**: `W` previous, `S` next, `A` accept, `R` needs review, `B` block,
  `E` fix/save.
- **The right-hand PDF is the question sheet only**, never the answer sheet, and it is fully
  decoupled from the left-hand list.
- Verify navigation with `node scripts/test_v2_navigation.mjs`, not by clicking. Read
  [`docs/ROUTE_HISTORY.md`](docs/ROUTE_HISTORY.md) before redesigning anything — it records why the
  previous designs were abandoned.

### Carrying review records across a rebuild

The reviewer's decisions live in `question_review_events.jsonl`, **append-only**. A rebuild must not
silently discard or duplicate them. Carrying is **automatic** (no flag to forget), deduplication is
by **whole record** (not `candidate_key` — one question legitimately carries `block` then `accept`),
and orphans are kept and counted. If a rebuild would carry 0 records while records exist nearby, it
**refuses to start (exit 2)** before writing anything.

> **Known unsolved:** rebuilding into the directory the server is serving leaves the list and the
> candidate file inconsistent for a moment. **Pause the service during a rebuild.**
> Detail: [`qbr/reports/review_record_safety.md`](qbr/reports/review_record_safety.md).


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

# the question-bank build pipeline (its own venv)
cd qbr && .venv/bin/python -m pytest tests/ -q
# the review UI's navigation contract (drive the real script, not the pixels)
node scripts/test_v2_navigation.mjs review_ui/v2.html <workdir>/review-ui/candidates.jsonl
```

## Review UI

- **Interface baseline: `review_ui/v2.html` at `/v2`** (linear single-question review). v1 (`mobile.html`, `workflow.html`) moved to `review_ui/v1-reference/` and is **reference-only, no longer maintained**; it still answers on `/mobile`, `/workflow` and `/mobile/workflow` because existing bookmarks and installed PWAs are contracts. Fix v1 only to keep it working. Rationale and the abandoned designs: [`docs/ROUTE_HISTORY.md`](docs/ROUTE_HISTORY.md).
- The v2 console is served locally over a built queue (`scripts/review_run.sh <workdir> 8774`, route `/v2`) and needs no PostgreSQL. `/v2` is served `no-store`, so editing `v2.html` is live without a restart; the review log is opened append-only.
- **Navigation follows the drawn list** (charter: 導覽與內容必須來自同一個來源). `S.rows` is both what is drawn and what `W`/`S` walk; verify with `node scripts/test_v2_navigation.mjs`, which drives the real script and ships with the negative control.
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
