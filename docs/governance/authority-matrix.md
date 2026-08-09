# Agent Authority Matrix

This matrix applies to Codex, OpenClaw, scheduled agents, local models, cloud
models, and future automation unless a stricter rule applies.

Legend:

- `auto`: may run without per-run human approval inside the stated boundary.
- `proposal`: may prepare artifacts, a branch, PR, plan, or dry-run only.
- `approve`: requires exact, contemporaneous G3 human approval.
- `owner`: G4 owner must execute or directly supervise the action.
- `never`: prohibited.

| Action | Dev | Staging | Production | Maximum autonomous authority |
| --- | --- | --- | --- | --- |
| Inspect Git, files, status, logs, and health | auto | auto | read-only auto | G0 |
| Run tests, validators, checksums, and dry-runs | auto | auto | read-only auto | G0 |
| Edit, commit, and push a non-default branch | auto | n/a | n/a | G1 |
| Open or update a PR or issue | auto | n/a | n/a | G1 |
| Merge a PR to `main` | n/a | n/a | owner review | proposal only |
| Generate AI advisory output | auto | auto | proposal unless an approved importer is separately authorized | G2 |
| Import candidates or advisory data | auto | auto in isolated DB | approve | G2/G3 |
| Change parser, normalization, prompt, model, or routing rules | PR | evaluated staging | approve rollout; append resets when required | G2 proposal |
| Change Review UI or database schema | PR | migration test | approve | G2 proposal |
| Build an immutable package from reviewed formal data | auto | auto | read-only source access; package output only | G2 |
| Deploy an exact Git SHA | n/a | auto | approve | G3 |
| Publish/import a package with `--apply` | n/a | dry-run auto | approve | G3 |
| Accept/block a question as a human reviewer | never | never | owner/human | G4 |
| Restore a production database | never | isolated drill | owner | G4 |
| Change the sole production writer | never | never | owner | G4 |
| Repair append-only review history | never | proposal | owner-authorized repair workflow | G4 |
| Delete material assets, backups, events, or releases | never | proposal | owner | G4 |
| Patch an immutable release in place | never | never | never | prohibited |
| Force-push `main` or bypass history | never | never | never | prohibited |

## Role defaults

### Observer

- Maximum G0.
- Read-only workspace and production access.
- May report drift or failures; may not “fix” them in place.

### Maintainer agent

- Maximum G1 autonomously.
- May work only on non-default branches and open draft PRs.
- No production DB, owner token, deployment secret, or writer-control credential.

### Advisory agent

- Maximum G2 in development/staging.
- Writes structured advisory results only through a versioned schema and approved
  importer boundary.
- Cannot write human-review actions or formal promotion decisions.

### Release builder

- Maximum G2 when reading the formally reviewed PostgreSQL layer and producing an
  immutable checksum-backed package.
- Package publication/import remains G3.

### Production operator agent

- No standing autonomous G3 authority.
- May execute one exact G3 operation only after approval has named the target,
  commit/package, preflight evidence, and rollback plan.

### Owner / human reviewer

- Holds G4 authority and responsibility.
- Agent-generated evidence remains advisory and does not substitute for the
  owner’s production or content decision.

## Credential separation

Credentials must be scoped to the role rather than shared from an owner account.
At minimum, separate GitHub, AI395 read-only, staging, and production-operation
identities. A lower-level agent must not be able to obtain higher-level authority
through inherited shell access, a mounted SSH directory, a Docker socket, browser
session reuse, or a shared owner token.
