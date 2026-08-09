# Project Governance

Status: approved baseline v1

Effective date: 2026-08-09 (Asia/Taipei)

This directory is the human-readable governance authority for code changes,
AI-agent work, and production operations in `tw-national-exam-catalog`.
`governance/policy.json` is the machine-readable companion. If the two disagree,
stop the affected automation and resolve the mismatch in a reviewed pull request.

## Governance goals

- Keep `main` reproducible and reviewable.
- Let agents perform useful low-risk work without receiving owner-level authority.
- Preserve official-source provenance and append-only human review history.
- Keep AI advisory output separate from human decisions.
- Make every production mutation attributable, bounded, reversible, and approved.

## Authorities by domain

These authorities cover different domains; one does not silently overwrite another.

| Domain | Authority |
| --- | --- |
| Official facts | MOEX pages and official Q / ANS / MOD PDFs, identified by source URL and hash |
| Code and policy | Reviewed commits on GitHub `main` |
| Workflow state | AI395 PostgreSQL job state and artifact manifests |
| Human review | AI395 append-only review event tables |
| Formal question bank | AI395 formal tables derived through approved gates |
| Published data | Immutable packages, manifests, checksums, release IDs, and import evidence |

Git history is not a storage authority for official PDFs, MinerU output, candidate
JSONL, review-event exports, manual assets, database dumps, or other large derived
artifacts.

## Environment boundaries

| Environment | Purpose | Write boundary |
| --- | --- | --- |
| Development | Local code, tests, disposable data | Must not write production review events or formal tables |
| Staging | Isolated workflow, parser, migration, and import validation | Must use separate DB, ports, assets, and credentials |
| Production | AI395 Review UI, PostgreSQL, formal tables, and immutable releases | Single writer; G3 approval or G4 owner action as defined below |
| Rollback standby | Stopped Mac Studio evidence retained during the retirement window | Must remain stopped while AI395 accepts writes |

## GitHub change flow

The normal path is:

```text
task or issue
  -> codex/* or agent/* branch
  -> inspectable commits
  -> pull request
  -> required checks
  -> human review
  -> merge to main
  -> immutable release from an exact SHA when needed
```

Agents must not push new work directly to `main`. An owner may use a direct push
only as a break-glass recovery when the PR path is unavailable and delay would
materially increase harm. The owner must record the reason, exact commits,
validation, and follow-up in an issue or PR afterward. Force pushes to `main` are
never part of the recovery path.

Opening a PR is a proposal, not production approval. Merging a code PR does not
implicitly approve a later production deploy, schema migration, restore, publish,
or writer-authority change.

## Agent authority levels

| Level | Meaning | Default examples |
| --- | --- | --- |
| G0 Observe | Autonomous, read-only inspection | status, verify, diff, tests, reports |
| G1 Develop | Reversible work on a non-default branch | edit code/docs, commit, push branch, open draft PR |
| G2 Advise / stage | Versioned, isolated, non-authoritative processing | AI advisory generation, staging ingest, parser or rule proposal |
| G3 Approved production operation | Exact action requires contemporaneous human approval | deploy, production migration, advisory import, publish/import apply |
| G4 Owner-only authority | Agent may prepare evidence but may not execute | human accept/block, restore, writer switch, event repair, material deletion |

Detailed permissions are in [authority-matrix.md](authority-matrix.md). Change
classes and evidence requirements are in [change-control.md](change-control.md).

## Non-negotiable invariants

1. AI output alone cannot accept or block a question.
2. Human review events are append-only; repair is an owner-authorized G4 workflow.
3. AI395 is the only production Review UI and PostgreSQL writer.
4. Parser changes that alter reviewed candidate content append `reset_review` and
   preserve prior notes and provenance.
5. Immutable releases are built from exact Git SHAs and are never patched in place.
6. Secrets, credentials, owner tokens, large official assets, and local data roots
   stay out of Git.
7. Migration and rollback never use `rsync --delete` and never run two writers.

## Approval semantics

Valid G3 approval must identify:

- the exact action and target environment;
- the commit, package, migration, or release ID;
- the expected impact and validation evidence;
- the rollback or stop condition;
- the approver and approval time.

General statements such as “keep going” or approval of a different PR/run are not
reusable production authorization. Unattended automation cannot obtain approval
mid-run and must stop after producing evidence, an issue, a package, or a PR.

## Audit evidence

Agent and operator workflows should preserve, where applicable:

- `run_id`, actor/agent identity, session or job ID, and timestamps;
- input hashes, tool/profile/model/prompt versions, and source commit;
- commands or deterministic action identifiers and exit status;
- PR, commit, package, release, backup, and approval identifiers;
- preflight, dry-run, smoke-test, and rollback evidence.

Agent operational logs are separate from human question-review events. An agent
must never impersonate a human reviewer to satisfy a gate.

## Related policy

- `AGENTS.md`: concise repository instructions for coding agents.
- `governance/policy.json`: machine-readable levels, roles, actions, and invariants.
- `docs/ai395-runtime-maintenance.md`: read-only maintenance entrypoints.
- `docs/ai395-production-cutover-2026-08-09.md`: production authority and rollback evidence.
- `docs/review-automation-strategy.md`: deterministic QA, AI advisory, and risk routing.
- `deploy/openclaw/README.md`: OpenClaw trust-boundary and configuration guidance.
