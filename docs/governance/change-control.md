# Change Control

## Change classes

| Class | Scope | Minimum path |
| --- | --- | --- |
| A | Documentation or tests with no runtime/data impact | branch, checks, PR, human merge |
| B | Reversible code, UI, or automation behavior | branch, tests, PR with impact and rollback |
| C | Parser, normalization, AI routing, schema, or review semantics | Class B plus data-impact analysis, staging evidence, reset-review assessment |
| D | Production deploy, migration, advisory import, or package publish/import | merged commit plus separate G3 approval, preflight/dry-run, smoke test, rollback evidence |
| E | Restore, writer-authority change, append-only repair, or material deletion | G4 owner workflow with backup and exact recovery evidence |

Agents classify conservatively. If a change spans classes, the highest class
controls. Uncertainty about whether reviewed content changes makes the work Class C
until proven otherwise.

## Pull request evidence

Every PR must state:

1. what changed and why;
2. the change class and affected paths;
3. code, data, review-authority, schema, and production impact;
4. validation commands and results;
5. rollout and rollback or why neither applies;
6. whether parser output or reviewed candidate content changes;
7. whether secrets, large assets, credentials, or external model data are involved;
8. the agent/tool identity when AI assisted.

A PR touching `AGENTS.md`, `governance/`, `.github/`, database schema, production
deployment, or AI395 control scripts requires owner review through CODEOWNERS.

## Merge policy

- `main` is the code and governance authority.
- New work uses `codex/*` or `agent/*` branches.
- Required checks must pass before merge.
- An agent may prepare and update the PR but does not self-approve its work.
- Force pushes and history rewrites on `main` are prohibited.
- Prefer small PRs with one coherent change and inspectable data impact.

GitHub server-side rulesets should immediately require a PR, resolved
conversations, `governance` and `unit-tests` checks, and block force pushes. After
agents use a distinct GitHub App/bot identity, also require one CODEOWNER approval
and dismiss stale approvals. Agent or automation identities must not have bypass
permission. See `docs/governance/github-ruleset.md` for the staged checklist.

## Parser and review-impact policy

Before merging a parser, normalization, grouping, or candidate-building change:

1. compare representative output before and after;
2. identify already-reviewed candidates whose effective content changes;
3. append per-question `reset_review` events through an approved repair/migration
   script while preserving previous state, notes, versions, and reasons;
4. never rewrite existing human review events in place;
5. keep AI findings advisory until a human accepts the question.

## Production approval separation

Code merge and production execution are separate decisions. A Class D PR can be
merged without authorizing an immediate production run. The production approval
must name the exact merged SHA, migration/package/release ID, target, preflight,
stop conditions, and rollback path.

Unattended scheduled jobs may perform G0 reporting and approved G2 staging work.
They must fail closed before G3/G4 actions because no human is present to clarify
or approve.

## Break-glass and incidents

When the normal PR path is unavailable and delay materially increases harm, the
owner may apply the smallest non-force direct fix. Preserve:

- the incident and reason the normal path was unavailable;
- exact before/after SHAs and affected environment;
- backup or recovery evidence;
- commands, validation, and result;
- a follow-up PR or issue that reconciles documentation and tests.

Break-glass never authorizes a second production writer, in-place immutable
release patch, silent review-event rewrite, secret commit, or `rsync --delete`.
