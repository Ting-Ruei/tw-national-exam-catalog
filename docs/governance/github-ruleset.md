# GitHub `main` Ruleset Checklist

Repository files cannot enable a GitHub ruleset by themselves. Roll it out in two
stages so a single-owner repository is not accidentally locked when PRs are still
created under the owner’s own GitHub identity.

## Stage 1: current single-owner setup

After this PR is merged, create or update a server-side ruleset for `main` with:

- enforcement status: active;
- target branch: `main`;
- require a pull request before merging;
- require all conversations to be resolved;
- require status checks `governance` and `unit-tests`;
- block force pushes and branch deletion;
- do not grant agent, OpenClaw, bot, or routine automation bypass permission.

At this stage, the owner manually inspects and merges the PR after checks pass.
Do not require a formal approving review if the PR author and sole owner are the
same GitHub user, because GitHub does not treat self-approval as an independent
review.

## Stage 2: distinct agent identity available

After OpenClaw/Codex PRs are created by a dedicated GitHub App or bot identity,
add:

- require one approving review;
- require review from CODEOWNERS;
- dismiss stale approvals when new commits are pushed.

The human owner is then the reviewer, and the agent identity remains unable to
approve, merge, administer, or bypass the ruleset.

The owner may retain a narrowly controlled break-glass bypass only if its use is
recorded under `docs/governance/change-control.md`. A normal production deploy is
not a reason to bypass the PR rule.

GitHub Actions in this repository must:

- use read-only `GITHUB_TOKEN` permissions unless a reviewed workflow needs more;
- avoid `pull_request_target` for untrusted PR code;
- pin third-party actions to full commit SHAs;
- keep production secrets out of PR validation jobs;
- never deploy merely because a PR test succeeded.
