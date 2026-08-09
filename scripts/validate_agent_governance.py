#!/usr/bin/env python3
"""Validate machine-readable agent governance and Git repository boundaries."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "governance" / "policy.json"

REQUIRED_FILES = {
    ".github/CODEOWNERS",
    ".github/ISSUE_TEMPLATE/agent-task.yml",
    ".github/pull_request_template.md",
    ".github/workflows/ci.yml",
    "AGENTS.md",
    "deploy/openclaw/README.md",
    "docs/governance/README.md",
    "docs/governance/authority-matrix.md",
    "docs/governance/change-control.md",
    "docs/governance/github-ruleset.md",
    "governance/policy.json",
}

REQUIRED_ACTION_LEVELS = {
    "repository.inspect": "G0",
    "validation.run": "G0",
    "branch.modify": "G1",
    "github.pull_request.create": "G1",
    "advisory.generate": "G2",
    "staging.ingest": "G2",
    "parser_or_rule_change.propose": "G2",
    "reviewed_package.build": "G2",
    "production.deploy": "G3",
    "production.schema_migrate": "G3",
    "production.advisory_import": "G3",
    "package.publish_or_import_apply": "G3",
    "review.human_decision": "G4",
    "production.restore": "G4",
    "production.writer_authority_change": "G4",
    "append_only_event.repair": "G4",
    "material_artifact.delete": "G4",
}

REQUIRED_INVARIANTS = {
    "ai_output_cannot_accept_or_block_questions",
    "human_review_events_are_append_only",
    "ai395_is_the_single_production_writer",
    "reviewed_candidate_changes_require_reset_review_events",
    "immutable_releases_are_never_patched_in_place",
    "secrets_and_large_derived_artifacts_stay_out_of_git",
    "migration_and_rollback_never_use_rsync_delete",
    "agents_never_force_push_main",
}

FORBIDDEN_PREFIXES = (
    "datasets/",
    "images/",
    "mineru/",
    "migration_snapshots/",
    "pdf/",
    "tmp/",
    "國考題資料夾/",
    "國考題資料夾_",
)
FORBIDDEN_SUFFIXES = (".db", ".parquet", ".pdf", ".sqlite")
FORBIDDEN_EVENT_EXPORTS = {
    "answer_review_events.jsonl",
    "question_ai_review_events.jsonl",
    "question_review_events.jsonl",
}

SECRET_PATTERNS = {
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(rb"(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    "OpenAI-style secret": re.compile(rb"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}"),
}


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("policy root must be an object")
    return data


def validate_policy(policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required_top = {
        "schema_version",
        "policy_version",
        "effective_date",
        "project",
        "default_branch",
        "allowed_work_branch_patterns",
        "authorities",
        "environments",
        "levels",
        "roles",
        "actions",
        "invariants",
        "protected_paths",
        "github_controls",
        "required_pr_checks",
        "large_file_policy",
    }
    missing_top = required_top - set(policy)
    if missing_top:
        errors.append(f"policy missing top-level keys: {sorted(missing_top)}")

    if policy.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if policy.get("default_branch") != "main":
        errors.append("default_branch must be main")

    levels = policy.get("levels")
    if not isinstance(levels, dict) or set(levels) != {"G0", "G1", "G2", "G3", "G4"}:
        errors.append("levels must define exactly G0 through G4")
        levels = {}
    else:
        ranks = [levels[f"G{index}"].get("rank") for index in range(5)]
        if ranks != list(range(5)):
            errors.append("G0 through G4 ranks must be 0 through 4")
        for level_id in ("G3", "G4"):
            if levels[level_id].get("autonomous") is not False:
                errors.append(f"{level_id} must not be autonomous")
            if levels[level_id].get("production_mutation") is not True:
                errors.append(f"{level_id} must be marked as production mutation authority")

    roles = policy.get("roles")
    if not isinstance(roles, dict):
        errors.append("roles must be an object")
        roles = {}
    for role_id, role in roles.items():
        if not isinstance(role, dict) or role.get("max_level") not in levels:
            errors.append(f"role {role_id!r} has an invalid max_level")
    for role_id in ("observer", "maintainer_agent", "advisory_agent", "release_builder"):
        role = roles.get(role_id, {})
        if role.get("production_credentials") is not False:
            errors.append(f"role {role_id!r} must not have production credentials")
    if roles.get("production_operator_agent", {}).get("standing_g3_authority") is not False:
        errors.append("production_operator_agent must not have standing G3 authority")

    actions = policy.get("actions")
    if not isinstance(actions, list):
        errors.append("actions must be an array")
        actions = []
    by_id: dict[str, dict[str, Any]] = {}
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            errors.append(f"action at index {index} must be an object")
            continue
        action_id = action.get("id")
        if not isinstance(action_id, str) or not action_id:
            errors.append(f"action at index {index} has no valid id")
            continue
        if action_id in by_id:
            errors.append(f"duplicate action id: {action_id}")
        by_id[action_id] = action
        if action.get("level") not in levels:
            errors.append(f"action {action_id!r} has an invalid level")
        if not isinstance(action.get("environments"), list) or not action["environments"]:
            errors.append(f"action {action_id!r} must name at least one environment")
        if not isinstance(action.get("agent_may_initiate"), bool):
            errors.append(f"action {action_id!r} must define agent_may_initiate")
        if not isinstance(action.get("approval"), str) or not action["approval"]:
            errors.append(f"action {action_id!r} must define approval")
        if not isinstance(action.get("constraints"), list) or not action["constraints"]:
            errors.append(f"action {action_id!r} must define constraints")

    for action_id, expected_level in REQUIRED_ACTION_LEVELS.items():
        action = by_id.get(action_id)
        if action is None:
            errors.append(f"required action missing: {action_id}")
        elif action.get("level") != expected_level:
            errors.append(
                f"action {action_id!r} must be {expected_level}, got {action.get('level')!r}"
            )
    for action_id, action in by_id.items():
        if action.get("level") in {"G3", "G4"} and action.get("agent_may_initiate") is not False:
            errors.append(f"{action_id!r} must not be agent-initiated")
    for action_id in (
        "review.human_decision",
        "production.restore",
        "production.writer_authority_change",
        "append_only_event.repair",
        "material_artifact.delete",
    ):
        if by_id.get(action_id, {}).get("approval") != "owner_only":
            errors.append(f"{action_id!r} must remain owner_only")

    invariants = policy.get("invariants")
    if not isinstance(invariants, list):
        errors.append("invariants must be an array")
    else:
        missing_invariants = REQUIRED_INVARIANTS - set(invariants)
        if missing_invariants:
            errors.append(f"required invariants missing: {sorted(missing_invariants)}")

    if set(policy.get("required_pr_checks", [])) != {"governance", "unit-tests"}:
        errors.append("required_pr_checks must be governance and unit-tests")

    github_controls = policy.get("github_controls")
    if not isinstance(github_controls, dict):
        errors.append("github_controls must be an object")
    else:
        expected_controls = {
            "require_pull_request": True,
            "block_force_push": True,
            "agent_ruleset_bypass": False,
            "current_single_owner_required_approvals": 0,
            "distinct_agent_identity_required_approvals": 1,
            "distinct_agent_identity_requires_codeowner_review": True,
        }
        for key, expected in expected_controls.items():
            if github_controls.get(key) != expected:
                errors.append(f"github_controls.{key} must be {expected!r}")
        if set(github_controls.get("required_checks", [])) != {"governance", "unit-tests"}:
            errors.append("github_controls.required_checks must name governance and unit-tests")

    large_file_policy = policy.get("large_file_policy")
    if not isinstance(large_file_policy, dict):
        errors.append("large_file_policy must be an object")
    else:
        if not isinstance(large_file_policy.get("default_max_bytes"), int):
            errors.append("large_file_policy.default_max_bytes must be an integer")
        if not isinstance(large_file_policy.get("exceptions"), dict):
            errors.append("large_file_policy.exceptions must be an object")

    patterns = policy.get("allowed_work_branch_patterns")
    if not isinstance(patterns, list) or not patterns:
        errors.append("allowed_work_branch_patterns must be a non-empty array")
    else:
        for pattern in patterns:
            try:
                re.compile(pattern)
            except (TypeError, re.error) as exc:
                errors.append(f"invalid branch pattern {pattern!r}: {exc}")

    return errors


def repository_paths() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return sorted(
        {
            ROOT / raw.decode("utf-8", errors="surrogateescape")
            for raw in completed.stdout.split(b"\0")
            if raw
        }
    )


def validate_repository(policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    paths = repository_paths()
    relative_paths = {path.relative_to(ROOT).as_posix() for path in paths if path.is_file()}

    missing_files = REQUIRED_FILES - relative_paths
    if missing_files:
        errors.append(f"required governance files missing: {sorted(missing_files)}")

    large_policy = policy["large_file_policy"]
    default_limit = large_policy["default_max_bytes"]
    exceptions = large_policy["exceptions"]

    for path in paths:
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT).as_posix()
        basename = path.name

        if relative == ".env":
            errors.append("local .env must not be tracked or staged")
        if relative.startswith(FORBIDDEN_PREFIXES):
            errors.append(f"large/local artifact path must not be tracked: {relative}")
        if relative.lower().endswith(FORBIDDEN_SUFFIXES):
            errors.append(f"forbidden artifact type must not be tracked: {relative}")
        if basename in FORBIDDEN_EVENT_EXPORTS:
            errors.append(f"review event export must not be tracked: {relative}")

        limit = exceptions.get(relative, default_limit)
        size = path.stat().st_size
        if size > limit:
            errors.append(f"tracked file exceeds {limit} bytes ({size}): {relative}")

        if size > default_limit:
            continue
        content = path.read_bytes()
        if b"\0" in content:
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(content):
                errors.append(f"possible {label} in tracked file: {relative}")

    return errors


def main() -> int:
    try:
        policy = load_policy()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"governance validation failed: cannot load policy: {exc}", file=sys.stderr)
        return 1

    errors = validate_policy(policy)
    if not errors:
        errors.extend(validate_repository(policy))

    if errors:
        print("agent governance validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("agent governance validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
