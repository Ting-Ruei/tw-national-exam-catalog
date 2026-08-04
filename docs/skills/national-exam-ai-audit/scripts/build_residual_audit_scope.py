#!/usr/bin/env python3
"""Build a low-token residual audit scope with one immutable route per task.

This script joins safe compact tasks to the latest AI metadata retained in
frozen Review UI shards.  It never calls a model, materializes a correction,
imports an advisory, or changes human review state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, TextIO

from v4_common import (
    SKILL_ROOT,
    compact_json,
    load_active_exact_rules,
    read_json,
    read_jsonl,
    scope_matches,
    sha256_json,
    task_text,
    write_json,
)


SCHEMA_VERSION = "national_exam_residual_audit_scope_v1"
LEDGER_SCHEMA_VERSION = "national_exam_residual_coverage_row_v1"
TERMINAL_SCHEMA_VERSION = "national_exam_prior_ai_pass_terminal_v1"
ROUTING_METADATA_KEY = "residual_routing"

MODEL_LANES = (
    "group",
    "visual",
    "semantic_transcription",
    "ocr_text",
)
PRIMARY_ROUTES = (
    "parser_queue",
    "active_rule_exact",
    *MODEL_LANES,
    "terminal_prior_codex54_pass",
)
ROUTE_PATHS = {
    "parser_queue": Path("queues/parser.jsonl"),
    "active_rule_exact": Path("queues/active_rule_exact.jsonl"),
    "group": Path("lanes/group.jsonl"),
    "visual": Path("lanes/visual.jsonl"),
    "semantic_transcription": Path("lanes/semantic_transcription.jsonl"),
    "ocr_text": Path("lanes/ocr_text.jsonl"),
    "terminal_prior_codex54_pass": Path("terminal/prior_pass.jsonl"),
}

PARSER_CONTROL_RE = re.compile(
    r"boundary|option|non_question|missing|header|merge|split",
    re.IGNORECASE,
)
PARSER_AI_RE = re.compile(
    r"boundary|option|non_question|missing|header|merge|split",
    re.IGNORECASE,
)
SEMANTIC_RE = re.compile(
    r"translation|semantic|transcription|terminology|amino_acid",
    re.IGNORECASE,
)
GROUP_RE = re.compile(
    r"承上題|呈上題|回答(?:下列|以下).{0,8}題|請依序回答下列"
)
GROUP_AI_RE = re.compile(r"group|題組", re.IGNORECASE)
VISUAL_RE = re.compile(
    r"如下圖|如圖所示|根據所附影像|依下表|附圖|圖中|圖示"
)
VISUAL_AI_RE = re.compile(r"visual|image|table|圖像|圖片|圖表", re.IGNORECASE)
ANSWER_OWNED_ISSUE_CODES = {
    "missing_answer",
    "missing_answer_markdown",
    "unexpected_answer_value",
}
VISUAL_NEGATIVE_STATUSES = {
    "",
    "visual_not_required_likely",
    "no_visual_required",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--api-response-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def meaningful_suggestion(ai_review: dict[str, Any]) -> bool:
    correction = ai_review.get("suggested_correction")
    changes = ai_review.get("suggested_changes")
    return bool(correction) or bool(changes)


def minimal_human_review(raw_candidate: dict[str, Any]) -> dict[str, Any]:
    review = raw_candidate.get("review")
    if not isinstance(review, dict):
        review = {}
    return {
        "status": str(review.get("status") or ""),
        "action": (
            str(review.get("action"))
            if review.get("action") is not None
            else None
        ),
        "event_count": review.get("event_count"),
        "updated_at": (
            str(review.get("updated_at"))
            if review.get("updated_at") is not None
            else None
        ),
    }


def frozen_human_review_is_unreviewed(review: dict[str, Any]) -> bool:
    return bool(
        review.get("status") == "unreviewed"
        and review.get("action") in {None, "", "unreviewed", "reset_review"}
    )


def normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({str(item) for item in value if item is not None and str(item)})


def minimal_ai_evidence(raw_candidate: dict[str, Any]) -> dict[str, Any]:
    ai_review = raw_candidate.get("ai_review")
    if not isinstance(ai_review, dict):
        ai_review = {}
    findings = ai_review.get("findings")
    finding_codes = (
        sorted(
            {
                str(row.get("code") or "")
                for row in findings
                if isinstance(row, dict) and str(row.get("code") or "")
            }
        )
        if isinstance(findings, list)
        else []
    )
    return {
        "frozen_human_review": minimal_human_review(raw_candidate),
        "provider": (
            str(ai_review.get("provider"))
            if ai_review.get("provider") is not None
            else None
        ),
        "model": (
            str(ai_review.get("model"))
            if ai_review.get("model") is not None
            else None
        ),
        "audit_status": str(
            ai_review.get("audit_status")
            or ai_review.get("raw_audit_status")
            or ""
        ),
        "active": ai_review.get("active") is True,
        "superseded_by_human": ai_review.get("superseded_by_human") is True,
        "has_suggestion": meaningful_suggestion(ai_review),
        "recommended_action": (
            str(ai_review.get("recommended_action"))
            if ai_review.get("recommended_action") is not None
            else None
        ),
        "visual_status": str(ai_review.get("visual_status") or ""),
        "labels": normalize_string_list(ai_review.get("labels")),
        "finding_codes": finding_codes,
        "event_id": ai_review.get("event_id"),
        "event_count": ai_review.get("event_count"),
        "input_hash": (
            str(ai_review.get("input_hash"))
            if ai_review.get("input_hash") is not None
            else None
        ),
        "updated_at": (
            str(ai_review.get("updated_at"))
            if ai_review.get("updated_at") is not None
            else None
        ),
    }


def load_raw_ai_evidence(
    response_dir: Path,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    paths = sorted(response_dir.glob("shard_*.json"))
    if not paths:
        raise ValueError(f"no shard_*.json files found in {response_dir}")
    evidence_by_key: dict[str, dict[str, Any]] = {}
    shard_manifest: list[dict[str, Any]] = []
    for path in paths:
        payload = read_json(path)
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            raise ValueError(f"{path}: API response has no candidates array")
        returned = int(payload.get("returned_count") or len(candidates))
        filtered = int(payload.get("filtered_count") or returned)
        if returned != len(candidates) or filtered != returned:
            raise ValueError(
                f"{path}: truncated API response: "
                f"filtered={filtered}, returned={returned}, rows={len(candidates)}"
            )
        for raw_candidate in candidates:
            if not isinstance(raw_candidate, dict):
                raise ValueError(f"{path}: candidate row must be an object")
            key = str(raw_candidate.get("candidate_key") or "")
            if not key:
                raise ValueError(f"{path}: raw candidate key must be non-empty")
            if key in evidence_by_key:
                raise ValueError(f"duplicate raw candidate key: {key}")
            evidence = minimal_ai_evidence(raw_candidate)
            human_review = evidence["frozen_human_review"]
            if not frozen_human_review_is_unreviewed(human_review):
                raise ValueError(
                    "raw candidate is not frozen human-unreviewed: "
                    f"{key}: status={human_review.get('status')!r}, "
                    f"action={human_review.get('action')!r}"
                )
            evidence_by_key[key] = evidence
        shard_manifest.append(
            {
                "path": str(path.resolve()),
                "row_count": len(candidates),
                "sha256": sha256_file(path),
            }
        )
    return evidence_by_key, shard_manifest


def load_semantic_terms() -> set[str]:
    payload = read_json(SKILL_ROOT / "rules" / "semantic-anchors.json")
    terms: set[str] = set()
    for row in payload.get("anchors") or []:
        if not isinstance(row, dict):
            continue
        anchor = str(row.get("anchor") or "")
        if anchor and anchor != "chemical_formula":
            terms.add(anchor.casefold())
        terms.update(
            str(value).casefold()
            for value in row.get("suspect_terms") or []
            if str(value)
        )
    return terms


def load_negative_controls() -> list[dict[str, Any]]:
    payload = read_json(SKILL_ROOT / "rules" / "negative-controls.json")
    return [
        row
        for row in payload.get("controls") or []
        if isinstance(row, dict)
    ]


def parser_issue_codes(task: dict[str, Any]) -> list[str]:
    return [
        str(issue.get("code") or issue.get("issue_code") or "")
        for issue in ((task.get("signals") or {}).get("parser_issues") or [])
        if isinstance(issue, dict)
        and str(issue.get("code") or issue.get("issue_code") or "")
        not in ANSWER_OWNED_ISSUE_CODES
    ]


def ai_signal_values(ai_evidence: dict[str, Any]) -> list[str]:
    return [
        *[str(value) for value in ai_evidence.get("labels") or []],
        *[str(value) for value in ai_evidence.get("finding_codes") or []],
    ]


def is_answer_owned_signal(value: str) -> bool:
    return any(code in value for code in ANSWER_OWNED_ISSUE_CODES)


def option_structure_reasons(task: dict[str, Any]) -> list[str]:
    content = task.get("content")
    if not isinstance(content, dict):
        return ["content_not_object"]
    options = content.get("options")
    if not isinstance(options, list) or not options:
        return ["options_missing_or_empty"]
    reasons: list[str] = []
    keys: list[str] = []
    for index, option in enumerate(options, start=1):
        if not isinstance(option, dict):
            reasons.append(f"option_{index}_not_object")
            continue
        key = str(option.get("key") or "").strip().upper()
        if not key:
            reasons.append(f"option_{index}_missing_key")
        else:
            keys.append(key)
        text = str(option.get("text") or "").strip()
        if not text and not isinstance(option.get("image"), dict):
            reasons.append(f"option_{key or index}_empty_without_image")
    if len(keys) != len(set(keys)):
        reasons.append("duplicate_option_keys")
    return reasons


def parser_reasons(
    task: dict[str, Any],
    ai_evidence: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if str(task.get("stage") or "question") != "question":
        reasons.append("answer_or_non_question_stage")
    content = task.get("content")
    if not isinstance(content, dict):
        content = {}
    if not str(content.get("stem") or "").strip():
        reasons.append("empty_stem")
    reasons.extend(option_structure_reasons(task))
    codes = parser_issue_codes(task)
    reasons.extend(
        f"parser_issue:{code}" for code in codes if PARSER_CONTROL_RE.search(code)
    )
    if bool((task.get("signals") or {}).get("exam_header_false_question")):
        reasons.append("exam_header_false_question")
    reasons.extend(
        f"prior_ai_parser_signal:{value}"
        for value in ai_signal_values(ai_evidence)
        if PARSER_AI_RE.search(value) and not is_answer_owned_signal(value)
    )
    return sorted(set(reasons))


def materializable_fields(task: dict[str, Any]) -> list[tuple[str, str]]:
    content = task.get("content") or {}
    fields = [("stem", str(content.get("stem") or ""))]
    for option in content.get("options") or []:
        if not isinstance(option, dict):
            continue
        key = str(option.get("key") or "").strip().upper()
        if len(key) == 1 and key.isalpha():
            fields.append((f"option_{key}", str(option.get("text") or "")))
    return fields


def matching_negative_controls(
    task: dict[str, Any],
    controls: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    key = str(task.get("candidate_key") or "")
    text = task_text(task)
    matches: list[dict[str, Any]] = []
    for control in controls:
        control_key = str(control.get("candidate_key") or "")
        observed = str(control.get("observed") or "")
        if control_key and control_key != key:
            continue
        if observed and observed not in text:
            continue
        matches.append(control)
    return matches


def active_rule_analysis(
    task: dict[str, Any],
    active_rules: Iterable[dict[str, Any]],
    negative_controls: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    fields = materializable_fields(task)
    controls = matching_negative_controls(task, negative_controls)
    exact_matches: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for rule in active_rules:
        if not scope_matches(rule, task):
            continue
        source = str(rule.get("source") or "")
        target = str(rule.get("target") or "")
        if not source:
            continue
        occurrences: list[dict[str, Any]] = []
        for field, value in fields:
            count = value.count(source)
            if count:
                occurrences.append({"field": field, "count": count})
        total_count = sum(row["count"] for row in occurrences)
        if not total_count:
            continue
        blocked_by = [
            str(control.get("id") or "")
            for control in controls
            if str(control.get("observed") or "") == source
            and str(control.get("rejected_target") or "") == target
        ]
        row = {
            "rule_id": str(rule.get("id") or ""),
            "source": source,
            "target": target,
            "occurrences": occurrences,
            "total_occurrence_count": total_count,
        }
        if blocked_by:
            unresolved.append(
                {
                    **row,
                    "reason": "negative_control_conflict",
                    "negative_control_ids": sorted(blocked_by),
                }
            )
        elif total_count != 1 or len(occurrences) != 1:
            unresolved.append({**row, "reason": "non_unique_materializable_match"})
        else:
            exact_matches.append(
                {
                    **row,
                    "field": occurrences[0]["field"],
                }
            )
    return {
        "exact_matches": exact_matches,
        "unresolved_matches": unresolved,
        "has_any_match": bool(exact_matches or unresolved),
    }


def has_group_signal(task: dict[str, Any], ai_evidence: dict[str, Any]) -> bool:
    content = task.get("content") or {}
    return bool(
        content.get("group_ref")
        or GROUP_RE.search(task_text(task))
        or any(GROUP_AI_RE.search(value) for value in ai_signal_values(ai_evidence))
    )


def has_visual_asset(task: dict[str, Any]) -> bool:
    content = task.get("content") or {}
    if content.get("image_refs") or content.get("stem_image"):
        return True
    return any(
        isinstance(option, dict) and isinstance(option.get("image"), dict)
        for option in content.get("options") or []
    )


def has_visual_signal(task: dict[str, Any], ai_evidence: dict[str, Any]) -> bool:
    visual_status = str(ai_evidence.get("visual_status") or "")
    return bool(
        has_visual_asset(task)
        or VISUAL_RE.search(task_text(task))
        or visual_status not in VISUAL_NEGATIVE_STATUSES
        or any(VISUAL_AI_RE.search(value) for value in ai_signal_values(ai_evidence))
    )


def semantic_reasons(
    task: dict[str, Any],
    ai_evidence: dict[str, Any],
    semantic_terms: set[str],
) -> list[str]:
    reasons = [
        f"semantic_parser_signal:{code}"
        for code in parser_issue_codes(task)
        if SEMANTIC_RE.search(code)
    ]
    reasons.extend(
        f"prior_ai_semantic_signal:{value}"
        for value in ai_signal_values(ai_evidence)
        if SEMANTIC_RE.search(value)
    )
    folded = task_text(task).casefold()
    matched_terms = sorted(term for term in semantic_terms if term in folded)
    reasons.extend(f"semantic_anchor:{term}" for term in matched_terms)
    return sorted(set(reasons))


def exact_prior_codex54_pass(ai_evidence: dict[str, Any]) -> bool:
    return bool(
        frozen_human_review_is_unreviewed(
            ai_evidence.get("frozen_human_review") or {}
        )
        and ai_evidence.get("provider") == "codex"
        and ai_evidence.get("model") == "codex-5.4"
        and ai_evidence.get("audit_status") == "pass"
        and ai_evidence.get("active") is True
        and ai_evidence.get("superseded_by_human") is not True
        and ai_evidence.get("has_suggestion") is not True
    )


def classify_task(
    task: dict[str, Any],
    ai_evidence: dict[str, Any],
    *,
    active_rules: list[dict[str, Any]],
    semantic_terms: set[str],
    negative_controls: list[dict[str, Any]],
) -> dict[str, Any]:
    structural_reasons = parser_reasons(task, ai_evidence)
    if structural_reasons:
        return {
            "primary_route": "parser_queue",
            "route_reasons": structural_reasons,
        }

    active = active_rule_analysis(task, active_rules, negative_controls)
    if active["exact_matches"] and not active["unresolved_matches"]:
        return {
            "primary_route": "active_rule_exact",
            "route_reasons": ["active_rule_unique_materializable_match"],
            "active_rule_matches": active["exact_matches"],
        }

    if has_group_signal(task, ai_evidence):
        return {
            "primary_route": "group",
            "route_reasons": ["group_signal"],
            **(
                {"unresolved_active_rules": active["unresolved_matches"]}
                if active["unresolved_matches"]
                else {}
            ),
        }

    if has_visual_signal(task, ai_evidence):
        return {
            "primary_route": "visual",
            "route_reasons": ["visual_signal"],
            **(
                {"unresolved_active_rules": active["unresolved_matches"]}
                if active["unresolved_matches"]
                else {}
            ),
        }

    semantic = semantic_reasons(task, ai_evidence, semantic_terms)
    if semantic:
        return {
            "primary_route": "semantic_transcription",
            "route_reasons": semantic,
            **(
                {"unresolved_active_rules": active["unresolved_matches"]}
                if active["unresolved_matches"]
                else {}
            ),
        }

    if not exact_prior_codex54_pass(ai_evidence) or active["has_any_match"]:
        residual_reasons: list[str] = []
        if ai_evidence.get("provider") != "codex":
            residual_reasons.append("prior_ai_provider_not_exact_codex")
        if ai_evidence.get("model") != "codex-5.4":
            residual_reasons.append("prior_ai_model_not_exact_codex_5_4")
        if ai_evidence.get("audit_status") != "pass":
            residual_reasons.append("prior_ai_not_pass")
        if ai_evidence.get("active") is not True:
            residual_reasons.append("prior_ai_not_active")
        if ai_evidence.get("superseded_by_human") is True:
            residual_reasons.append("prior_ai_superseded")
        if ai_evidence.get("has_suggestion") is True:
            residual_reasons.append("prior_ai_has_suggestion")
        if active["has_any_match"]:
            residual_reasons.append("active_rule_match_not_safely_terminal")
        return {
            "primary_route": "ocr_text",
            "route_reasons": sorted(set(residual_reasons))
            or ["residual_text_control"],
            **(
                {"unresolved_active_rules": active["unresolved_matches"]}
                if active["unresolved_matches"]
                else {}
            ),
        }

    return {
        "primary_route": "terminal_prior_codex54_pass",
        "route_reasons": ["carry_forward_exact_latest_codex54_pass"],
        "carry_forward_evidence": {
            "evidence_type": "latest_ai_review",
            "provider": ai_evidence["provider"],
            "model": ai_evidence["model"],
            "audit_status": ai_evidence["audit_status"],
            "event_id": ai_evidence.get("event_id"),
            "input_hash": ai_evidence.get("input_hash"),
            "updated_at": ai_evidence.get("updated_at"),
        },
        "not_independent_luna_review": True,
        "deterministic_proof": False,
    }


def source_fingerprint(task: dict[str, Any]) -> str:
    value = str(
        task.get("source_fingerprint")
        or task.get("effective_content_hash")
        or ""
    )
    return value or sha256_json(
        {
            "content": task.get("content") or {},
            "exam": task.get("exam") or {},
        }
    )


def routing_metadata(
    decision: dict[str, Any],
    ai_evidence: dict[str, Any],
) -> dict[str, Any]:
    frozen_human_review = ai_evidence.get("frozen_human_review") or {}
    latest_ai_evidence = {
        key: value
        for key, value in ai_evidence.items()
        if key != "frozen_human_review"
    }
    metadata = {
        "primary_route": decision["primary_route"],
        "route_reasons": decision["route_reasons"],
        "frozen_human_review": frozen_human_review,
        "latest_ai_evidence": latest_ai_evidence,
    }
    for key in (
        "active_rule_matches",
        "unresolved_active_rules",
        "carry_forward_evidence",
        "not_independent_luna_review",
        "deterministic_proof",
    ):
        if key in decision:
            metadata[key] = decision[key]
    return metadata


def ledger_row(
    task: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    row = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "candidate_key": str(task.get("candidate_key") or ""),
        "source_fingerprint": source_fingerprint(task),
        "stage": str(task.get("stage") or "question"),
        **metadata,
    }
    return row


def terminal_row(
    task: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": TERMINAL_SCHEMA_VERSION,
        "candidate_key": str(task.get("candidate_key") or ""),
        "source_fingerprint": source_fingerprint(task),
        "primary_route": metadata["primary_route"],
        "route_reasons": metadata["route_reasons"],
        "frozen_human_review": metadata["frozen_human_review"],
        "carry_forward_evidence": metadata["carry_forward_evidence"],
        "not_independent_luna_review": True,
        "deterministic_proof": False,
    }


def routed_task(
    task: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        **task,
        ROUTING_METADATA_KEY: metadata,
    }


def write_jsonl_row(handle: TextIO, row: dict[str, Any]) -> None:
    handle.write(compact_json(row) + "\n")


def verify_source_hash(path: Path, expected_sha256: str) -> None:
    actual_sha256 = sha256_file(path)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            "source task file changed during build: "
            f"expected_sha256={expected_sha256}, actual_sha256={actual_sha256}"
        )


def output_file_metadata(
    staging_dir: Path,
    route_counts: Counter[str],
) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    for route, relative in ROUTE_PATHS.items():
        if route == "terminal_prior_codex54_pass":
            schema = TERMINAL_SCHEMA_VERSION
        elif route in MODEL_LANES:
            schema = "compact_audit_task_snapshot_v1"
        else:
            schema = "compact_audit_task_with_residual_routing_v1"
        metadata[route] = {
            "path": str(relative),
            "row_schema": schema,
            "row_count": route_counts[route],
            "sha256": sha256_file(staging_dir / relative),
        }
    ledger_path = Path("coverage_ledger.jsonl")
    metadata["coverage_ledger"] = {
        "path": str(ledger_path),
        "row_schema": LEDGER_SCHEMA_VERSION,
        "row_count": sum(route_counts.values()),
        "sha256": sha256_file(staging_dir / ledger_path),
    }
    return metadata


def build_scope(
    *,
    tasks_path: Path,
    api_response_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    if output_dir.exists():
        raise ValueError(f"output directory must not already exist: {output_dir}")
    if not tasks_path.is_file():
        raise ValueError(f"task file does not exist: {tasks_path}")

    initial_tasks_sha256 = sha256_file(tasks_path)
    ai_by_key, shard_manifest = load_raw_ai_evidence(api_response_dir)
    active_rules = load_active_exact_rules()
    semantic_terms = load_semantic_terms()
    negative_controls = load_negative_controls()

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_dir.name}.staging-",
        dir=output_dir.parent,
    ) as temporary:
        staging_dir = Path(temporary)
        for relative in ROUTE_PATHS.values():
            (staging_dir / relative).parent.mkdir(parents=True, exist_ok=True)
        handles = {
            route: (staging_dir / relative).open("w", encoding="utf-8")
            for route, relative in ROUTE_PATHS.items()
        }
        ledger_handle = (staging_dir / "coverage_ledger.jsonl").open(
            "w",
            encoding="utf-8",
        )
        seen_task_keys: set[str] = set()
        route_key_sets = {route: set() for route in PRIMARY_ROUTES}
        route_counts: Counter[str] = Counter()
        try:
            for line_no, task in read_jsonl(tasks_path):
                key = str(task.get("candidate_key") or "")
                if not key:
                    raise ValueError(f"{tasks_path}:{line_no}: empty task candidate key")
                if key in seen_task_keys:
                    raise ValueError(f"duplicate task candidate key: {key}")
                seen_task_keys.add(key)
                if key not in ai_by_key:
                    raise ValueError(f"task candidate key missing from raw shards: {key}")
                evidence = ai_by_key[key]
                decision = classify_task(
                    task,
                    evidence,
                    active_rules=active_rules,
                    semantic_terms=semantic_terms,
                    negative_controls=negative_controls,
                )
                route = str(decision["primary_route"])
                if route not in ROUTE_PATHS:
                    raise AssertionError(f"unknown primary route: {route}")
                if key in route_key_sets[route]:
                    raise AssertionError(f"duplicate route assignment: {key}")
                route_key_sets[route].add(key)
                route_counts[route] += 1
                metadata = routing_metadata(decision, evidence)
                write_jsonl_row(ledger_handle, ledger_row(task, metadata))
                if route == "terminal_prior_codex54_pass":
                    write_jsonl_row(
                        handles[route],
                        terminal_row(task, metadata),
                    )
                elif route in MODEL_LANES:
                    write_jsonl_row(handles[route], task)
                else:
                    write_jsonl_row(
                        handles[route],
                        routed_task(task, metadata),
                    )
        finally:
            ledger_handle.close()
            for handle in handles.values():
                handle.close()

        raw_keys = set(ai_by_key)
        extra_raw_keys = sorted(raw_keys - seen_task_keys)
        if extra_raw_keys:
            raise ValueError(
                "raw candidate keys missing from tasks: "
                f"{extra_raw_keys[:10]} (count={len(extra_raw_keys)})"
            )
        verify_source_hash(tasks_path, initial_tasks_sha256)

        union: set[str] = set()
        disjoint = True
        for keys in route_key_sets.values():
            if union.intersection(keys):
                disjoint = False
            union.update(keys)
        task_count = len(seen_task_keys)
        invariant_values = {
            "task_keys_nonempty_unique": True,
            "raw_keys_nonempty_unique": True,
            "exact_task_raw_key_join": seen_task_keys == raw_keys,
            "exactly_one_primary_route_per_task": (
                sum(route_counts.values()) == task_count
            ),
            "route_files_disjoint": disjoint,
            "route_file_union_matches_tasks": union == seen_task_keys,
            "route_count_sum_matches_task_count": (
                sum(route_counts.values()) == task_count
            ),
        }
        failed = [
            name for name, passed in invariant_values.items() if not passed
        ]
        if failed:
            raise AssertionError(f"coverage invariants failed: {failed}")

        output_files = output_file_metadata(staging_dir, route_counts)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "advisory_only": True,
            "materializes_corrections": False,
            "imports_review_events": False,
            "production_writes": False,
            "source_tasks": str(tasks_path.resolve()),
            "source_tasks_sha256": initial_tasks_sha256,
            "source_api_response_dir": str(api_response_dir.resolve()),
            "source_api_shards": shard_manifest,
            "source_api_shard_set_sha256": sha256_json(shard_manifest),
            "task_count": task_count,
            "raw_candidate_count": len(raw_keys),
            "active_rule_count": len(active_rules),
            "semantic_anchor_term_count": len(semantic_terms),
            "negative_control_count": len(negative_controls),
            "primary_route_priority": list(PRIMARY_ROUTES),
            "route_counts": {
                route: route_counts[route] for route in PRIMARY_ROUTES
            },
            "model_lane_counts": {
                lane: route_counts[lane] for lane in MODEL_LANES
            },
            "queue_counts": {
                "parser": route_counts["parser_queue"],
                "active_rule_exact": route_counts["active_rule_exact"],
            },
            "terminal_counts": {
                "prior_codex54_pass": route_counts[
                    "terminal_prior_codex54_pass"
                ],
            },
            "terminal_semantics": {
                "basis": "carry_forward_latest_exact_codex54_pass_evidence",
                "not_independent_luna_review": True,
                "deterministic_proof": False,
            },
            "output_files": output_files,
            "row_schemas": {
                "coverage_ledger": (
                    "minimal key/fingerprint/route/frozen-human/latest-AI "
                    "evidence metadata"
                ),
                "model_lanes": (
                    "full compact task only; routing and prior AI evidence remain "
                    "in the coverage ledger and are not copied into model lanes"
                ),
                "queues": "full compact task plus residual_routing",
                "terminal_prior_pass": (
                    "minimal key/fingerprint/frozen-human/carry-forward "
                    "evidence metadata; no stem or options"
                ),
            },
            "invariants": invariant_values,
        }
        write_json(staging_dir / "manifest.json", manifest)
        staging_dir.replace(output_dir)
    return manifest


def main() -> int:
    args = parse_args()
    manifest = build_scope(
        tasks_path=args.tasks,
        api_response_dir=args.api_response_dir,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "task_count": manifest["task_count"],
                "route_counts": manifest["route_counts"],
                "output_dir": str(args.output_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
