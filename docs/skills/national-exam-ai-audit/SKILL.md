---
name: national-exam-ai-audit
description: Run deterministic source-aligned checks over national-exam candidates and produce advisory evidence without changing human review decisions.
---

# National exam audit

Use this skill for source-aligned candidate checks: text, options, question
boundaries, groups, answers, images, hashes, and package integrity.

## Current contract

- Official PDF/source material is source truth; the candidate package records its
  provenance and hashes.
- Deterministic checks run before any interpretation.
- Human review events are append-only and remain the only human accept/block authority. Under the
  interim QBR permission, a scoped agent may separately set an AI-owned `pass`, `return`, or `block`
  workflow status and update its own AI result with revision provenance.
- There is no active model profile, external inference host, remote worker, review
  writer, database import path, or production deployment in this skill.
- A future model task must explicitly approve one local endpoint, data scope,
  budget, output schema, permissions, and rollback/evidence plan.

## Audit sequence

1. Freeze the source and candidate manifests in a new run directory.
2. Run deterministic source, numbering, option, asset, and package checks.
3. Classify each finding to one owner stage: parser, question text, image, group,
   answer, or UI.
4. Stop on missing, contradictory, or unverified evidence.
5. Present findings in the local v2 review UI or the task's selected review store.
6. Let a human choose the review decision and append one event.
7. Rebuild a new package after source or parser changes; never overwrite a prior run.

## Advisory boundary

If a current task approves local model assistance, send only residual ambiguity in
an immutable packet. The result must be sparse advisory evidence with source key,
packet hash, observed field, confidence, and reason. It may not write a human
`accept` / `block` action, `exclude`, `reset_review`, a formal correction, or a
rule activation. A separately recorded AI workflow `block` is not a human action.

This skill's audit result remains advisory evidence; it does not implement the separate
AI workflow-status writer. If another declared QBR workflow sets `pass`, `return`, or
`block`, that machine status is not a human review event, formal question-bank approval,
or permission to modify candidate text.

Do not turn repeated model observations into a rule without independent source or
human evidence, negative controls, regression tests, and owner approval. No model
may propose and activate its own rule.

## Evidence gates

A candidate correction is materializable only when a deterministic exact rule and
source evidence identify one unambiguous location. Source-original wording,
parser boundaries, images, groups, and ambiguous findings remain manual/PDF work.
A green test suite is not acceptance; the official source and two independent
readings must agree on the paper boundary.

## Required artifacts

Keep these in a non-Git run directory:

- source manifest and source hash;
- candidate manifest and parser version;
- deterministic findings and unresolved disagreements;
- review events and reviewer identity;
- final package manifest and checksums.

Do not infer a provider, host, database, or deployment target from filenames or
historical reports. Missing ownership or endpoint information is a blocker, not a
fallback selection.

## Active entrypoints

- `qbr/AGENTS.md` — source-to-package pipeline.
- `docs/skills/run-question-review-loop/SKILL.md` — human review loop.
- `docs/skills/qbr-pipeline-status/SKILL.md` — status and verification.
- `scripts/manage_local_review.py` — deterministic local review orchestration.
- `review_ui/v2.html` — review interface baseline.

Legacy SQL, provider, profile, adapter, and deployment material is retained only
for archaeology and is not an active dependency.

<!-- project-map:belongs-to -->
## 這一層在哪（回上層的路）

> **這是本子專屬技能**：只服務這個子專案。其他子專案要用同一件事時，先確認是不是該變成全域共通技能。

- 本層入口：[`../../../AGENTS.md`](../../../AGENTS.md)
- 不確定從哪開始：[`project_map`](../../../../project_map) 是整棵樹的可點擊地圖
- 卡住時的回溯路徑：技能 → 本層 `AGENTS.md` → `project_map` 入口文件鏈 → 傘層 → charter
<!-- /project-map:belongs-to -->
