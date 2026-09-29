# tw-national-exam-catalog Agent Instructions

This repository catalogs Taiwan national exam PDFs, MinerU outputs, parsed question candidates, review events, and PostgreSQL schema drafts.

## 你在哪裡開 pi？（工作界線）

**在 `tw-national-exam-catalog/` 打開 pi，就是「做題目匯入、審核、優化這一條線」。**
使用者在這邊審完一批題，就會在這一層開 session 說「跑審核迴圈」。

| 起點 | 工作範圍 |
|---|---|
| `ai_learning_platform/`（傘層） | **所有子專案**都能調（考題匯入、審核、優化，以及 `platform-app/` 等） |
| **`tw-national-exam-catalog/`（本層）** | **只做題目這一條線**：匯入、審核、優化、建規則、重掃 |

指引：[`docs/skills/run-question-review-loop/SKILL.md`](docs/skills/run-question-review-loop/SKILL.md)
—— 那是在這一層指揮的**操作程序**（每一步打什麼、判準是什麼）。
模型只可在當次任務明確核准 local endpoint、資料範圍與 budget 後使用；不得把遠端主機、
舊模型或未核准的 runtime 寫入現行流程。缺少契約時，deterministic-only 並停止。

## Ground Rules

- Do not commit secrets, API keys, owner tokens, database passwords, or copyrighted textbook content.
- Keep official exam PDFs, MinerU outputs, candidate JSONL, review JSONL, manual assets, and other large derived artifacts out of Git unless the user explicitly asks for a small sample.
- Treat `國考題資料夾/` and `國考題資料夾_其他類型/` as local data roots; inspect carefully before writing.

<!-- project-map:data-root path="國考題資料夾" reason="official exam corpus and MinerU artifacts" -->
<!-- project-map:data-root path="國考題資料夾_其他類型" reason="official exam corpus and MinerU artifacts" -->
<!-- project-map:data-root path="國考題資料夾_非醫學剩餘全集" reason="official exam corpus and MinerU artifacts" -->
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

Two working procedures cross those tracks and are worth having open while doing the work:

| When | Skill |
|---|---|
| A paper reads the wrong number of questions, shipped text is doubled/truncated, or you are adding a rule to `extract.py`/`repair.py` | [`docs/skills/repair-qbr-extraction/SKILL.md`](docs/skills/repair-qbr-extraction/SKILL.md) |
| Bringing the review server up on the LAN, opened from another device, verified, or restarted after a rebuild | [`docs/skills/deploy-qbr-review/SKILL.md`](docs/skills/deploy-qbr-review/SKILL.md) |
| Classifying accepted formal questions into a versioned curriculum taxonomy | [`docs/skills/classify-exam-curriculum/SKILL.md`](docs/skills/classify-exam-curriculum/SKILL.md) |
| Recovering paper structure and image/option crops from an official PDF | [`docs/skills/extract-exam-paper-structure/SKILL.md`](docs/skills/extract-exam-paper-structure/SKILL.md) |
| Auditing candidates and producing sparse advisory repairs behind validation gates | [`docs/skills/national-exam-ai-audit/SKILL.md`](docs/skills/national-exam-ai-audit/SKILL.md) |
| A review-UI pane renders wrong, a dropdown moves another dropdown, a button does nothing, or the 錯題討論區 is empty / loses a question | [`docs/skills/repair-review-ui-v2/SKILL.md`](docs/skills/repair-review-ui-v2/SKILL.md) |

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
- Unattended jobs cannot obtain approval mid-run. They must stop after producing evidence, a report, an issue, a package, or a PR; they may not continue into G3/G4 work.
- Changes to governance, GitHub workflows, database schema, or production deployment require owner review through CODEOWNERS.
- Keep agent operational evidence separate from human question-review events. An agent must never impersonate a human reviewer.

## Useful Commands

```bash
python3 scripts/validate_agent_governance.py
python3 -m unittest discover -s tests
python3 -m py_compile scripts/serve_question_review_ui.py scripts/build_question_candidates_from_mineru.py
# the question-bank build pipeline (its own venv)
cd qbr && .venv/bin/python -m pytest tests/ -q
# the review UI's navigation contract (drive the real script, not the pixels)
node scripts/test_v2_navigation.mjs review_ui/v2.html <workdir>/review-ui/candidates.jsonl
```

## Review UI

> **介面基準是 `review_ui/v2.html`。** Review records are append-only JSONL in the
> review store explicitly selected by the current local task; no external writer or
> production service is configured.

- **改完要推上常駐機，否則使用者看不到。** 審題介面服務在 `192.168.10.70:8765`，跑的是
  **常駐機的容器**，不是這台筆電的工作樹。改過 `review_ui/`、`scripts/serve_question_review_ui.py`
  或 `qbr/src/qbr/review_ui/` 之後，執行：

  ```sh
  scripts/deploy_station.sh --restart     # --restart 不能省：只同步檔案不會讓跑著的程式換版本
  ```

  然後用瀏覽器打開 `http://192.168.10.70:8765/v2` 確認。**「我改好了」與「使用者看得到」
  是兩件事**，2026-09-23 就是少了這一步，介面與題目改了一整天而站上停在 6 小時前。
- **伺服器程式已拆分。** `scripts/serve_question_review_ui.py` 是 347 行的 composition root，
  實作在 `qbr/src/qbr/review_ui/`（`ai_audit`／`review_state`／`handlers`／`queue_view` 等）。
  它仍**再匯出**所有符號，因為約 50 個測試檔直接載入該路徑；詳細契約見
  [`review_ui/AGENTS.md`](review_ui/AGENTS.md)。
- **四個入口（首頁／題目／答案／討論）是同一頁的四個模式**，不是四頁。
- v1（`mobile.html`、`workflow.html`）位於 `review_ui/v1-reference/`，只作 compatibility
  reference；不要從它恢復 backend、writer 或 deployment。
- `/v2` 以 `no-store` 服務時，改檔後可由當次 local server 重新讀取。
- **導覽跟隨被畫出的清單**；用 `node scripts/test_v2_navigation.mjs` 驗證真 script。
- 介面契約：skill [`docs/skills/review-ui-v2/SKILL.md`](docs/skills/review-ui-v2/SKILL.md)。
- Local serving skill [`docs/skills/deploy-qbr-review/SKILL.md`](docs/skills/deploy-qbr-review/SKILL.md)
  說明常駐機的部署與驗證程序。

舊 SQL 審核文件與 deployment copies 只作考古；不要從它們推導 writer、服務主機、資料庫
或 production 依賴。若未來需要新的審核 backend，必須另立 owner-approved contract。

## DevSpace / ChatGPT MCP

When editing on the MacBook, use this repository as the workspace root:

```text
/Users/tim/AI workspace/ai_learning_platform/tw-national-exam-catalog
```

Runtime/deployment checkouts are not second sources of uncommitted code; use the
current catalog checkout and its reviewed branches only.

Prefer small, inspectable edits and summarize verification commands. If a task touches database schema, review UI behavior, parser rules, or large data workflows, explain the data impact before changing files.
