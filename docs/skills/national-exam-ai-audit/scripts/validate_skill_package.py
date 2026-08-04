#!/usr/bin/env python3
"""Validate the self-contained national-exam audit Skill package."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from v4_common import LANES, PROJECT_ROOT, SKILL_ROOT, load_profile, read_json, read_jsonl


sys.path.insert(0, str(SKILL_ROOT))
from adapters import ALLOWED_ADAPTERS, build_request  # noqa: E402


REQUIRED_FILES = [
    "SKILL.md",
    "agents/openai.yaml",
    "references/automation-strategy.md",
    "references/ai-max-395-deployment.md",
    "contracts/task.schema.json",
    "contracts/sparse-result.schema.json",
    "contracts/rule-proposal.schema.json",
    "rules/ocr-exact.json",
    "rules/semantic-anchors.json",
    "rules/group-patterns.json",
    "rules/visual-cues.json",
    "rules/negative-controls.json",
    "prompts/base-contract.md",
    "prompts/ocr-text.md",
    "prompts/semantic-transcription.md",
    "prompts/group.md",
    "prompts/visual.md",
    "scripts/build_group_shadow_controls.py",
    "scripts/build_prior_pass_recheck_scope.py",
    "scripts/build_rule_observations.py",
    "scripts/build_shadow_pilot_scope.py",
    "scripts/build_validated_partial_run.py",
    "scripts/build_coverage_proof.py",
    "scripts/adjudicate_sparse_results.py",
    "scripts/build_review_ui_advisory_results.py",
    "scripts/compile_agent_packets.py",
    "scripts/evaluate_shadow_pilot.py",
    "scripts/validate_sparse_results.py",
    "scripts/materialize_sparse_corrections.py",
    "scripts/import_advisory_results.py",
    "scripts/run_ollama_shadow_batches.py",
    "scripts/select_tasks_by_key.py",
    "scripts/validate_rule_proposals.py",
    "profiles/gpt-5.6-luna.yaml",
    "profiles/gemini-flash-low.yaml",
    "profiles/qwen3.6-27b.yaml",
    "profiles/gemma4-31b.yaml",
    "profiles/ai-max-qwen3.6-27b.yaml",
    "runtimes/ai-max-395.yaml",
    "benchmarks/gold-corpus.jsonl",
]


def validate_frontmatter(errors: list[str]) -> None:
    text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        errors.append("skill_frontmatter_missing")
        return
    try:
        frontmatter = text.split("---\n", 2)[1]
    except IndexError:
        errors.append("skill_frontmatter_invalid")
        return
    keys = {
        line.split(":", 1)[0].strip()
        for line in frontmatter.splitlines()
        if ":" in line
    }
    if keys != {"name", "description"}:
        errors.append("skill_frontmatter_must_only_contain_name_and_description")
    if "name: national-exam-ai-audit" not in frontmatter:
        errors.append("skill_name_mismatch")
    if len(text.splitlines()) > 500:
        errors.append("skill_body_exceeds_500_lines")


def validate_contracts(errors: list[str]) -> None:
    for path in sorted((SKILL_ROOT / "contracts").glob("*.json")):
        payload = read_json(path)
        if payload.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            errors.append(f"{path.name}:invalid_json_schema_version")
        if payload.get("type") != "object":
            errors.append(f"{path.name}:root_type_must_be_object")


def validate_profiles(errors: list[str]) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    models: set[str] = set()
    for path in sorted((SKILL_ROOT / "profiles").glob("*.yaml")):
        profile = load_profile(path)
        profiles.append(profile)
        model = str(profile.get("model") or "")
        if not model or model in models:
            errors.append(f"{path.name}:missing_or_duplicate_model")
        models.add(model)
        adapter = str(profile.get("adapter") or "")
        if adapter not in ALLOWED_ADAPTERS:
            errors.append(f"{path.name}:invalid_adapter")
        lanes = set(profile.get("allowed_lanes") or [])
        if not lanes or lanes - LANES:
            errors.append(f"{path.name}:invalid_allowed_lanes")
        batch = profile.get("default_batch_size")
        maximum = profile.get("max_batch_size")
        if (
            not isinstance(batch, int)
            or isinstance(batch, bool)
            or not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or batch < 1
            or maximum < batch
        ):
            errors.append(f"{path.name}:invalid_batch_sizes")
        if profile.get("may_materialize_without_active_rule") is not False:
            errors.append(f"{path.name}:unsafe_materialization_policy")
        try:
            build_request(
                adapter,
                profile=profile,
                prompt="test",
                packet={"batch_id": "test", "tasks": []},
            )
        except Exception as exc:
            errors.append(f"{path.name}:adapter_smoke_failed:{type(exc).__name__}")
    return profiles


def validate_rules(errors: list[str]) -> dict[str, int]:
    exact_index = read_json(SKILL_ROOT / "rules" / "ocr-exact.json")
    registry_path = PROJECT_ROOT / str(exact_index.get("canonical_registry") or "")
    if not registry_path.is_file():
        errors.append("canonical_ocr_registry_missing")
        active_count = 0
    else:
        registry = read_json(registry_path)
        rules = registry.get("rules") or []
        ids = [str(rule.get("id") or "") for rule in rules if isinstance(rule, dict)]
        if not all(ids) or len(ids) != len(set(ids)):
            errors.append("canonical_ocr_rule_ids_missing_or_duplicate")
        for rule in rules:
            if (
                isinstance(rule, dict)
                and rule.get("status", "active") == "active"
                and rule.get("source") == rule.get("target")
            ):
                errors.append(f"canonical_ocr_rule_same_source_target:{rule.get('id')}")
        active_count = sum(
            1
            for rule in rules
            if isinstance(rule, dict) and rule.get("status", "active") == "active"
        )
    negative = read_json(SKILL_ROOT / "rules" / "negative-controls.json")
    controls = negative.get("controls") or []
    control_ids = [str(row.get("id") or "") for row in controls if isinstance(row, dict)]
    if not all(control_ids) or len(control_ids) != len(set(control_ids)):
        errors.append("negative_control_ids_missing_or_duplicate")
    required_keys = {
        "moex:109100:301:22:1:question:q026",
        "moex:104090:301:55:1:question:q049",
        "moex:115090:308:0501:1:question:q072",
    }
    present_keys = {str(row.get("candidate_key") or "") for row in controls}
    if not required_keys.issubset(present_keys):
        errors.append("required_source_original_negative_controls_missing")
    return {"active_exact_rules": active_count, "negative_controls": len(controls)}


def validate_benchmark(errors: list[str]) -> dict[str, int]:
    rows = [row for _, row in read_jsonl(SKILL_ROOT / "benchmarks" / "gold-corpus.jsonl")]
    ids = [str(row.get("case_id") or "") for row in rows]
    if not all(ids) or len(ids) != len(set(ids)):
        errors.append("gold_case_ids_missing_or_duplicate")
    counts = {
        "cases": len(rows),
        "negative_controls": sum(
            1 for row in rows if "negative_control" in set(row.get("tags") or [])
        ),
        "true_defects": sum(
            1 for row in rows if "true_defect" in set(row.get("tags") or [])
        ),
        "route_controls": sum(
            1 for row in rows if "route_control" in set(row.get("tags") or [])
        ),
    }
    if counts["negative_controls"] < 3:
        errors.append("gold_requires_at_least_three_negative_controls")
    if counts["true_defects"] < 2:
        errors.append("gold_requires_true_defects")
    if counts["route_controls"] < 2:
        errors.append("gold_requires_group_and_visual_routes")
    return counts


def main() -> int:
    errors: list[str] = []
    for relative in REQUIRED_FILES:
        if not (SKILL_ROOT / relative).is_file():
            errors.append(f"missing_required_file:{relative}")
    validate_frontmatter(errors)
    validate_contracts(errors)
    profiles = validate_profiles(errors)
    rule_counts = validate_rules(errors)
    benchmark_counts = validate_benchmark(errors)
    report = {
        "ok": not errors,
        "skill_root": str(SKILL_ROOT),
        "profile_count": len(profiles),
        "rule_counts": rule_counts,
        "benchmark_counts": benchmark_counts,
        "errors": errors,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
