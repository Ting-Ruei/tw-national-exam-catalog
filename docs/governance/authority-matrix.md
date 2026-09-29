# Agent Authority Matrix

This is an interim authority matrix approved on 2026-09-26. The detailed review
workflow is expected to be redesigned. These permissions do not select a new
model/provider, expand data-transfer scope, or authorize website production changes.

Legend:

- `auto`: may run without per-run human approval inside the stated boundary.
- `proposal`: may prepare artifacts, a branch, PR, plan, or dry-run only.
- `approve`: requires exact, contemporaneous G3 human approval.
- `owner`: G4 owner must execute or directly supervise the action.
- `never`: prohibited.

| Action | Local development | Isolated run | QBR review workflow | Website production | Maximum agent authority |
| --- | --- | --- | --- | --- | --- |
| Inspect files, status, logs, and health | auto | auto | auto | read-only | G0 |
| Run tests, validators, checksums, and dry-runs | auto | auto | auto | read-only | G0 |
| Edit code/docs on a non-default branch | auto | n/a | n/a | n/a | G1 |
| Open or update a PR or issue | auto | n/a | n/a | n/a | G1 |
| Merge a PR to `main` | n/a | n/a | owner review | owner review | proposal only |
| Generate AI evidence within the declared task scope | auto | auto | auto | not authorized | G2 |
| Set AI workflow status to pass, return, or block in the selected QBR queue | n/a | auto | auto | not authorized | G2 |
| Update the agent's own AI result, retaining revisions and provenance | n/a | auto | auto | not authorized | G2 |
| Write, replace, or delete a human review decision/event | never | never | never | never | Human reviewer / G4 |
| Change parser, normalization, prompt, model, or routing rules | PR | evaluated run | proposal | not authorized | G2 proposal |
| Change Review UI code or schema | PR | validation only | separate gate pending redesign | not authorized | G2 proposal |
| Build an immutable package | auto | auto | auto | read-only source | G2 |
| Deploy website, migrate its database, or publish/import a package | never | never | not authorized | exact approval | G3 |
| Repair human review history or restore a database | never | proposal | owner only | owner only | G4 |
| Delete material assets, backups, human events, or releases | never | proposal | owner only | owner only | G4 |
| Patch an immutable release in place or force-push history | never | never | prohibited | prohibited | prohibited |
## Role defaults

### Observer

- Maximum G0.
- Read-only workspace access; no external runtime access.
- May report drift or failures; may not “fix” them in place.

### Maintainer agent

- Maximum G1 autonomously.
- May work only on non-default branches and open draft PRs.
- No database, owner token, deployment secret, or writer-control credential.

### Advisory agent

- Maximum G2 in local development and isolated runs.
- Within the selected QBR workflow, may set AI workflow status and update its own
  AI result while preserving source, actor, model/run version, and prior revisions.
- Cannot write or change a human-review action, present an AI status as a human
  decision, or publish a formal package.

### Release builder

- Maximum G2 when reading explicitly selected local review evidence and producing an
  immutable checksum-backed package.
- Package publication/import remains G3.

### Production operator agent

- No standing G3 authority. Website production actions still need exact approval
  naming the target, release, preflight evidence, and rollback plan.

### Owner / human reviewer

- Holds G4 authority and responsibility.
- Human decisions remain attributable to a human. Website production and formal
  package publication retain separate approval gates.

## Credential separation

Credentials must be scoped to the role rather than shared from an owner account.
No external credentials are part of the current contract. A lower-level agent must
not obtain higher authority through inherited shell access, mounted SSH directories,
Docker sockets, browser session reuse, or shared owner tokens.
