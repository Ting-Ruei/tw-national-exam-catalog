## Summary

<!-- What changed, why, and what outcome should the reviewer expect? -->

## Change class

<!-- See docs/governance/change-control.md. Select the highest applicable class. -->

- [ ] A — documentation/tests only, no runtime or data impact
- [ ] B — reversible code, UI, or automation behavior
- [ ] C — parser, normalization, AI routing, schema, or review semantics
- [ ] D — production deploy/migration/import/publish capability
- [ ] E — restore, writer authority, append-only repair, or material deletion

## Impact

- Code/runtime impact:
- Data/candidate impact:
- Human-review or authority impact:
- Schema/migration impact:
- Production impact:

### Safety declarations

- [ ] No secret, credential, owner token, database password, or private key is included.
- [ ] No official PDF, MinerU output, candidate/review JSONL, manual asset, DB dump, or other large derived artifact is included.
- [ ] AI output is advisory and does not impersonate a human accept/block decision.
- [ ] If reviewed candidate content changes, the reset-review impact and preservation of previous notes are documented below.

## Validation

<!-- Exact commands and results. Include staging/preflight/dry-run evidence when relevant. -->

```text

```

## Rollout and rollback

<!-- State “not applicable” only when neither applies. A merged PR is not G3/G4 approval. -->

- Rollout:
- Stop conditions:
- Rollback:

## Agent provenance

- Agent/tool identity:
- Run/task/issue ID:
- Human decisions still required:

## Reviewer checklist

- [ ] The change class matches the highest-risk effect.
- [ ] Required checks passed and evidence is inspectable.
- [ ] Production execution, if any, has a separate exact G3/G4 approval path.
- [ ] No agent identity can bypass the `main` ruleset or act as a human reviewer.
