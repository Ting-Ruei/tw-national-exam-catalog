# OpenClaw Governance Baseline

This directory contains non-secret examples for a future OpenClaw integration.
It does not authorize installation, start a Gateway, create credentials, or grant
production access.

The repository governance authority is `docs/governance/README.md` and
`governance/policy.json`.

## Trust boundary

Run project agents under a dedicated OS account and a dedicated OpenClaw state
directory. Do not reuse the owner’s personal shell, browser profile, SSH directory,
GitHub token, password manager, or cloud credentials. A future production operator
should use a separate Gateway/OS identity from the observer/maintainer boundary.

Do not mount any of the following into an agent sandbox:

- the Docker or Podman socket;
- `~/.ssh`, `~/.config`, password-manager data, or an owner browser profile;
- the project `.env` or AI395 production environment;
- PostgreSQL data directories or backup roots.

Use a dedicated GitHub App or fine-grained identity. The routine maintainer needs
repository contents on non-default branches plus PR/issue access; it does not need
repository administration, secrets, Actions administration, or a `main` ruleset
bypass.

## Configuration example

`openclaw.catalog-agents.example.json5` is a review starting point, not a complete
deployment file. It defines:

- an observer with a read-only workspace;
- a maintainer with a sandboxed read/write checkout but no production credentials;
- sandboxing for every session;
- allowlist-only exec with strict inline-eval review;
- no browser, messaging, cron creation, elevated execution, or sub-agent spawning.

The maintainer can prepare a branch and PR only. Production access is deliberately
absent. Add bindings, model providers, delivery channels, and credentials outside
Git only after reviewing their trust boundaries.

## Host approvals

OpenClaw host approvals are local state and must not be committed. Keep the default
fail-closed posture:

```json
{
  "version": 1,
  "defaults": {
    "security": "deny",
    "ask": "on-miss",
    "askFallback": "deny",
    "autoAllowSkills": false
  }
}
```

Create per-agent allowlist entries only for exact reviewed executables and, when
possible, constrained arguments. Do not place interpreters such as Python, Node,
Bash, Zsh, Ruby, or `osascript` in a broad safe-bin list. Prefer repository wrapper
commands with fixed arguments for recurring checks.

The initial observer allowlist should be limited to read-only repository/runtime
checks, for example a wrapper around:

```text
git status --short --branch
git fetch --prune origin
python3 scripts/validate_agent_governance.py
bash scripts/ai395_catalog_runtime.sh status
bash scripts/ai395_catalog_runtime.sh verify
```

`git fetch` writes local refs but does not mutate GitHub code or production data.
Use a disposable observer clone if even local-ref mutation is undesirable.

## Scheduled jobs

OpenClaw cron/hook runs are unattended. Limit them to G0 reporting and explicitly
approved G2 staging workflows. A scheduled job must not attempt G3/G4 work because
there is no human present to clarify the target or approve an escalation.

Recommended first jobs:

- repository and AI395 read-only drift report;
- `status` / `verify` health report;
- test and governance-policy report;
- backup-age, disk-watermark, and stalled-job report after dedicated read-only
  wrappers exist.

On failure, create a report or notification containing evidence and stop. Do not
automatically repair production, restart a writer, restore a database, delete data,
or broaden permissions.

## Skills and delegated agents

- Version project skills in Git and review their source like code.
- Pin external skill/plugin versions and inspect them before enabling.
- Keep implicit skill CLI allowlisting disabled.
- Deny sub-agent spawning initially. If delegation is later needed, allow only
  named sandboxed agents with an equal or lower governance level.
- Do not rely on prompt text or an agent name as an authorization boundary.

## Before enabling an OpenClaw agent

1. Merge the governance baseline and enable the GitHub `main` ruleset.
2. Create the dedicated OS and GitHub identities.
3. Review the effective sandbox, tool policy, mounts, and local approvals.
4. Run `openclaw security audit --deep` and archive the non-secret result.
5. Verify the agent cannot access `.env`, owner SSH/browser state, production DB
   credentials, the container socket, or ruleset bypass.
6. Run only G0 jobs for an observation period.
7. Add G1/G2 capabilities one bounded workflow at a time.

Any G3 integration requires a separate reviewed change and an exact per-run human
approval path. No standing G3 credential is approved by this baseline.
