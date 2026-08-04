#!/usr/bin/env python3
"""Validate model-authored rule proposals without promoting or applying them."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from v4_common import LANES, SKILL_ROOT, read_json, read_jsonl, write_json


RULE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,80}$")
RULE_TYPES = {
    "exact_replacement",
    "contextual_anchor",
    "parser_route",
    "group_route",
    "visual_route",
    "negative_control",
}
AGENT_STATUSES = {"observed", "proposed"}
EVIDENCE_KINDS = {
    "official_pdf",
    "mineru_alignment",
    "human_review",
    "gold_regression",
    "model_observation",
}
SCOPE_KEYS = {"categories", "subjects", "required_context"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposals", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def _is_unique_string_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) for item in value)
        and len(value) == len(set(value))
    )


def proposal_errors(
    proposal: Any,
    *,
    forbidden_pairs: set[tuple[str, str]],
) -> list[str]:
    if not isinstance(proposal, dict):
        return ["proposal_must_be_object"]

    errors: list[str] = []
    rule_id = proposal.get("rule_id")
    rule_type = proposal.get("rule_type")
    lane = proposal.get("lane")
    status = proposal.get("status")
    source = proposal.get("source")
    target = proposal.get("target")
    scope = proposal.get("scope")
    evidence = proposal.get("evidence")
    positive = proposal.get("positive_examples")
    negative = proposal.get("negative_examples")

    if not isinstance(rule_id, str) or not RULE_ID_PATTERN.fullmatch(rule_id):
        errors.append("invalid_rule_id")
    if rule_type not in RULE_TYPES:
        errors.append("invalid_rule_type")
    if lane not in LANES:
        errors.append("invalid_lane")
    if status not in AGENT_STATUSES:
        errors.append("agent_cannot_submit_promoted_status")
    if source is not None and not isinstance(source, str):
        errors.append("source_must_be_string_or_null")
    if target is not None and not isinstance(target, str):
        errors.append("target_must_be_string_or_null")

    if not isinstance(scope, dict):
        errors.append("scope_must_be_object")
    else:
        if set(scope) - SCOPE_KEYS:
            errors.append("scope_has_unknown_keys")
        for key, value in scope.items():
            if key in SCOPE_KEYS and not _is_unique_string_list(value):
                errors.append(f"scope_{key}_must_be_unique_string_list")

    if not isinstance(evidence, list) or not evidence:
        errors.append("evidence_requires_at_least_one_item")
    else:
        for index, item in enumerate(evidence, start=1):
            if not isinstance(item, dict):
                errors.append(f"evidence_{index}_must_be_object")
                continue
            if item.get("kind") not in EVIDENCE_KINDS:
                errors.append(f"evidence_{index}_invalid_kind")
            if not isinstance(item.get("reference"), str) or not item["reference"].strip():
                errors.append(f"evidence_{index}_reference_required")
            if set(item) - {"kind", "reference"}:
                errors.append(f"evidence_{index}_has_unknown_keys")
        if status == "proposed" and not any(
            isinstance(item, dict)
            and item.get("kind") != "model_observation"
            for item in evidence
        ):
            errors.append("proposed_status_requires_source_or_human_evidence")

    if not _is_unique_string_list(positive):
        errors.append("positive_examples_must_be_unique_string_list")
    if not _is_unique_string_list(negative):
        errors.append("negative_examples_must_be_unique_string_list")
    if not isinstance(proposal.get("proposed_by"), str) or not proposal["proposed_by"].strip():
        errors.append("proposed_by_required")
    version = proposal.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        errors.append("invalid_version")

    if rule_type == "exact_replacement":
        if not isinstance(source, str) or not source:
            errors.append("exact_replacement_requires_source")
        if not isinstance(target, str) or not target:
            errors.append("exact_replacement_requires_target")
        if source == target:
            errors.append("exact_replacement_source_equals_target")
        if isinstance(source, str) and isinstance(target, str):
            if (source, target) in forbidden_pairs:
                errors.append("exact_replacement_matches_negative_control")
        if status == "proposed" and not positive:
            errors.append("proposed_exact_rule_requires_positive_example")
        if status == "proposed" and not negative:
            errors.append("proposed_exact_rule_requires_negative_example")

    allowed_keys = {
        "rule_id",
        "rule_type",
        "lane",
        "status",
        "source",
        "target",
        "scope",
        "evidence",
        "positive_examples",
        "negative_examples",
        "proposed_by",
        "version",
        "note",
    }
    if set(proposal) - allowed_keys:
        errors.append("proposal_has_unknown_keys")
    return sorted(set(errors))


def main() -> int:
    args = parse_args()
    controls = read_json(SKILL_ROOT / "rules" / "negative-controls.json")
    forbidden_pairs = {
        (str(row.get("observed") or ""), str(row.get("rejected_target") or ""))
        for row in controls.get("controls") or []
        if isinstance(row, dict) and row.get("observed") and row.get("rejected_target")
    }
    rows = list(read_jsonl(args.proposals))
    errors: list[dict[str, Any]] = []
    rule_ids: dict[str, int] = {}
    for line_no, proposal in rows:
        row_errors = proposal_errors(proposal, forbidden_pairs=forbidden_pairs)
        rule_id = str(proposal.get("rule_id") or "")
        if rule_id in rule_ids:
            row_errors.append(f"duplicate_rule_id_first_seen_line_{rule_ids[rule_id]}")
        elif rule_id:
            rule_ids[rule_id] = line_no
        if row_errors:
            errors.append(
                {
                    "line": line_no,
                    "rule_id": rule_id or None,
                    "errors": sorted(set(row_errors)),
                }
            )

    report = {
        "ok": not errors,
        "proposal_count": len(rows),
        "valid_proposal_count": len(rows) - len(errors),
        "errors": errors,
        "rules_promoted": 0,
        "rules_applied": 0,
    }
    report_path = args.report or args.proposals.with_suffix(".validation.json")
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
