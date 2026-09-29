# Project Governance

Status: interim authority decisions (2026-09-26); workflow redesign pending

Effective date: 2026-09-26 (Asia/Taipei)

This directory is the human-readable governance authority for code changes,
AI-agent work, and production operations in `tw-national-exam-catalog`.
`governance/policy.json` is the machine-readable companion. If the two disagree,
stop the affected automation and resolve the mismatch in a reviewed pull request.

## Governance goals

- Keep `main` reproducible and reviewable.
- Let agents perform useful low-risk work without receiving owner-level authority.
- Preserve official-source provenance and human review history.
- Let QBR agents update their own machine review results and workflow status without impersonating a human.
- Make every production mutation attributable, bounded, reversible, and approved.

## Authorities by domain

These authorities cover different domains; one does not silently overwrite another.

| Domain | Authority |
| --- | --- |
| Official facts | MOEX pages and official Q / ANS / MOD PDFs, identified by source URL and hash |
| Code and policy | Reviewed commits on GitHub `main` |
| Workflow state | The selected QBR queue plus AI-owned review results and their revision provenance |
| Human review | The selected human review-event store; agents cannot modify these events |
| Formal question bank | Immutable packages derived through approved gates |
| Published data | Immutable packages, manifests, checksums, release IDs, and import evidence |

Git history is not a storage authority for official PDFs, MinerU output, candidate
JSONL, review-event exports, manual assets, database dumps, or other large derived
artifacts.

## Environment boundaries

| Environment | Purpose | Write boundary |
| --- | --- | --- |
| Development | Local code, tests, disposable data | Must not write website production or formal question-bank data |
| Staging | Isolated workflow, parser, migration, and import validation | Must use separate DB, ports, assets, and credentials |
| QBR review workflow | Mac Studio `192.168.10.70`, LAN Review UI v2 and queue | Agents may update their own AI result and pass/return/block workflow status; human events remain separate |
| Website production | Mac Studio platform runtime | Existing website production gates remain in force; QBR AI status grants no website write or publish authority |
| Catalog PostgreSQL reference | MacBook Pro, retired and read-only | Reference lookup only; no active writer or Mac Studio copy |

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
| G2 Advise / stage | Versioned workflow processing within the declared QBR scope | AI review status, updates to the agent's own results, staging ingest, parser or rule proposal |
| G3 Approved production operation | Exact action requires contemporaneous human approval | deploy, production migration, advisory import, publish/import apply |
| G4 Owner-only authority | Agent may prepare evidence but may not execute | human accept/block, restore, writer switch, event repair, material deletion |

Detailed permissions are in [authority-matrix.md](authority-matrix.md). Change
classes and evidence requirements are in [change-control.md](change-control.md).

## Non-negotiable invariants

1. QBR agents may automatically pass, return, or block a candidate's workflow status. The status is an AI decision, not a human accept/block decision or formal publication approval.
2. Agents may update their own AI results while preserving revisions and provenance. They may not write, replace, or delete human review events; human decisions remain attributable to humans.
3. QBR Review UI v2 is an active LAN workflow on Mac Studio. It is separate from website production and the retired catalog PostgreSQL reference copy.
4. Parser changes that alter reviewed candidate content append `reset_review` and
   preserve prior notes and provenance.
5. Immutable releases are built from exact Git SHAs and are never patched in place.
6. Secrets, credentials, owner tokens, large official assets, and local data roots
   stay out of Git.
7. No migration or rollback operation is authorized without a new owner-approved contract.

## Approval semantics

Valid G3 approval must identify:

- the exact action and target environment;
- the commit, package, migration, or release ID;
- the expected impact and validation evidence;
- the rollback or stop condition;
- the approver and approval time.

General statements such as “keep going” or approval of a different PR/run are not
reusable production authorization. An unattended QBR workflow may complete its
declared G0-G2 work, including AI workflow status updates, then must stop before G3
or G4 actions. This interim permission does not approve a new model/provider or
data-transfer scope; the detailed workflow will be redesigned.

## Audit evidence

Agent and operator workflows should preserve, where applicable:

- `run_id`, actor/agent identity, session or job ID, and timestamps;
- input hashes, tool/profile/model/prompt versions, and source commit;
- commands or deterministic action identifiers and exit status;
- PR, commit, package, release, backup, and approval identifiers;
- preflight, dry-run, smoke-test, and rollback evidence.

Agent operational logs and AI result revisions are separate from human
question-review events. An agent must never impersonate a human reviewer or
present an AI workflow status as a human decision.

## Related policy

- `AGENTS.md`: concise repository instructions for coding agents.
- `governance/policy.json`: machine-readable levels, roles, actions, and invariants.
- `docs/local-review-workflow.md`: deterministic local review boundary.
- `docs/skills/qbr-pipeline-status/SKILL.md`: current package/run status.
- `docs/review-automation-strategy.md`: deterministic QA, AI advisory, and risk routing.
- `deploy/openclaw/README.md`: retired/future proposal only; it does not authorize installation or access.
