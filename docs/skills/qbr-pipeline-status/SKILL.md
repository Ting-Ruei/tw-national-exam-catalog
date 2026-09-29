---
name: qbr-pipeline-status
description: Read the current question-bank pipeline status, distinguish stable contracts from open work, and report evidence without reviving retired deployment or model assumptions.
---

# qbr pipeline status

`qbr/` is the active question-bank build line: official PDF → source-aligned candidate →
validated package. It reads corpus material and writes no production database.

## Current contract

- `qbr/AGENTS.md` and the architecture charter are the authority.
- `review_ui/v2.html` is the review interface baseline.
- Human review records are append-only and belong to the review store explicitly selected for
  the current run.
- Deterministic checks validate paper properties: pages, text counts, fonts, ink, geometry,
  source hashes, candidate structure, and package integrity.
- Model findings, when a task explicitly approves a local model, remain advisory evidence. Separately,
  the interim QBR permission allows a scoped agent to set its own `pass` / `return` / `block` workflow
  status and update its own AI result with revision provenance. This does not configure an external
  inference host or authorize a new model, provider, or data-transfer scope.
- A missing model or endpoint is not a reason to invent a fallback. Stop at the packet boundary
  and report the missing contract.

## Pipeline stages

```text
S0 intake      freeze official source and hashes
S1 triage      classify measurable paper properties
S2 extraction  parse and compare independent readings
S3 gate        reject missing, contradictory, or unverifiable evidence
S4 records     emit one candidate record per question
S5 package     write immutable package and manifest
S6 verify      read the package back and verify every checksum
```

Acceptance is two independent readings and source evidence agreeing on the same paper boundary;
a green test suite alone is not acceptance.

## What to run

From `tw-national-exam-catalog/`:

```bash
python3 scripts/validate_agent_governance.py
python3 -m unittest discover -s tests
cd qbr
.venv/bin/python -m pytest tests/ -q
```

For a real package, use the task's approved local corpus root and package version:

```bash
.venv/bin/python scripts/golden_path.py run \
  --registry-key <registry-key> --year <year> --ordinal <ordinal> \
  --category <category> --subject <subject> \
  --asset-root "../國考題資料夾" --out <run-dir> \
  --package-version <version>
```

Keep corpus and derived output outside Git. Never overwrite a run directory; create a new run
for a new input or rule version.

## Review loop

1. Pull or open the review store selected by the current task.
2. Inspect deterministic findings and the source PDF.
3. Ask a locally approved model only about residual ambiguity, if the task contract allows it.
4. Record the model result as advisory evidence.
5. Let a human decide accept, block, needs review, or correction.
6. Append a review event; never rewrite an existing human event.
7. Rebuild and verify a new package after parser or source changes.

Do not use silence as approval. Do not turn a repeated model suggestion into an active rule without
source evidence, negative controls, regression tests, and owner approval.

## Open work

- Define the next representative paper sample and independent-source evidence.
- Keep parser rules general and paper-measurable; move semantic interpretation to prompts.
- Characterize large scripts before modularizing them.
- Establish a new model contract only if a concrete task requires model assistance.
- Resolve package and review-store ownership before any database import or publication.

## Retired material

Older host deployments, remote workers, model benchmark reports, SQL staging copies, and migration
notes are archaeology only. They do not identify a current endpoint, writer, production database,
or rollback target. A future capability must start with a new owner-approved contract rather than
reusing an old command or artifact.

<!-- project-map:belongs-to -->
## 這一層在哪（回上層的路）

> **這是本子專屬技能**：只服務這個子專案。其他子專案要用同一件事時，先確認是不是該變成全域共通技能。

- 本層入口：[`../../../AGENTS.md`](../../../AGENTS.md)
- 不確定從哪開始：[`project_map`](../../../../project_map) 是整棵樹的可點擊地圖
- 卡住時的回溯路徑：技能 → 本層 `AGENTS.md` → `project_map` 入口文件鏈 → 傘層 → charter
<!-- /project-map:belongs-to -->
