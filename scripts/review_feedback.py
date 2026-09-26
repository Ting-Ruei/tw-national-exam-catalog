#!/usr/bin/env python3
"""Build bounded correction feedback packets for the local audit workflow.

This module is intentionally pure: it does not call a model, write a file, or
touch a database.  Human corrections and deterministic three-evidence repairs
are converted into immutable before/after examples.  A model may later turn
the example into a rule or Skill-note proposal, but this module never promotes
or applies that proposal.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
from datetime import datetime, timezone
from typing import Any


FEEDBACK_SCHEMA_VERSION = "question_correction_feedback_v1"
GUARDRAIL_SCHEMA_VERSION = "question_guardrail_candidate_v1"
FEEDBACK_SOURCE_KINDS = {"human_correction", "three_evidence_correction"}
FEEDBACK_SCOPES = {"question", "group", "visual", "answer"}
GUARDRAIL_TYPES = {
    "exact_ocr_rule",
    "notation_rule",
    "format_rule",
    "crop_strategy",
    "answer_rule",
    "parser_route",
    "skill_note",
    "no_generalization",
}
VISIBLE_FIELDS = (
    "stem",
    "stem_markup",
    "options",
    "answer",
    "answer_payload",
    "group_ref",
    "group_sequence_no",
    "image_refs",
    "stem_image",
    "answer_image_refs",
    "visual_review",
)
MAX_TEXT_CHARS = 12_000
MAX_OPTION_TEXT_CHARS = 3_000
MAX_EXAMPLES = 12


class FeedbackContractError(ValueError):
    """Raised when a feedback event cannot be safely represented."""


class NoVisibleChange(FeedbackContractError):
    """Raised when a human event contains no visible before/after change."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _truncate(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 28)] + "\n[…feedback truncated…]"


def _asset_snapshot(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    # Paths and raw references are deliberately excluded.  The feedback model
    # receives the stable asset identity and the human-visible placement, not
    # a filesystem path or an arbitrary file locator.
    for key in (
        "asset_key",
        "asset_role",
        "placement",
        "target_option",
        "source",
        "caption",
        "manual_asset",
        "mime_type",
        "sha256",
        "bytes",
        "exists",
    ):
        if value.get(key) not in (None, ""):
            result[key] = value[key]
    return result or None


def _options_snapshot(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for option in value[:8]:
        if not isinstance(option, dict):
            continue
        row: dict[str, Any] = {
            "key": _truncate(option.get("key"), 16),
            "text": _truncate(option.get("text"), MAX_OPTION_TEXT_CHARS),
        }
        if option.get("markup") is not None:
            row["markup"] = _truncate(option.get("markup"), 2_000)
        image = _asset_snapshot(option.get("image"))
        if image:
            row["image"] = image
        rows.append(row)
    return rows


def question_snapshot(value: Any) -> dict[str, Any]:
    """Return only the visible, bounded part of a candidate revision."""

    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key in ("candidate_key", "question_number", "source_registry_key"):
        if value.get(key) not in (None, ""):
            result[key] = _truncate(value.get(key), 300)
    for key in ("stem", "stem_markup"):
        if value.get(key) is not None:
            result[key] = _truncate(value.get(key))
    result["options"] = _options_snapshot(value.get("options"))
    for key in ("answer", "group_ref", "group_sequence_no", "visual_review"):
        if value.get(key) not in (None, ""):
            result[key] = copy.deepcopy(value[key])
    for key in ("answer_payload",):
        if isinstance(value.get(key), dict):
            result[key] = {
                str(entry): copy.deepcopy(entry_value)
                for entry, entry_value in value[key].items()
                if entry in {"answer", "accepted_values", "raw_answer", "is_special_correction"}
            }
    for key in ("image_refs", "answer_image_refs"):
        refs = value.get(key)
        if isinstance(refs, list):
            result[key] = [ref for ref in (_asset_snapshot(row) for row in refs[:8]) if ref]
    for key in ("stem_image",):
        image = _asset_snapshot(value.get(key))
        if image:
            result[key] = image
    return result


def apply_correction(before: dict[str, Any], correction: Any) -> dict[str, Any]:
    """Apply a partial UI correction to a visible snapshot without mutation."""

    result = copy.deepcopy(before)
    if not isinstance(correction, dict):
        return result
    normalized = question_snapshot({**result, **correction})
    for key in VISIBLE_FIELDS:
        if key in correction:
            if key == "options":
                result[key] = _options_snapshot(correction.get(key))
            elif key in {"image_refs", "answer_image_refs"}:
                result[key] = [ref for ref in (_asset_snapshot(row) for row in (correction.get(key) or [])[:8]) if ref]
            elif key == "stem_image":
                result[key] = _asset_snapshot(correction.get(key))
            elif key in {"stem", "stem_markup"}:
                result[key] = _truncate(correction.get(key))
            else:
                result[key] = copy.deepcopy(correction.get(key))
    # A correction may be a sparse patch; normalize only fields explicitly
    # supplied by the caller, while retaining the untouched before state.
    for key in VISIBLE_FIELDS:
        if key in normalized and key in correction and key not in result:
            result[key] = normalized[key]
    return result


def diff_snapshots(before: Any, after: Any) -> list[dict[str, Any]]:
    before_snapshot = question_snapshot(before)
    after_snapshot = question_snapshot(after)
    differences: list[dict[str, Any]] = []
    for field in VISIBLE_FIELDS:
        left = before_snapshot.get(field)
        right = after_snapshot.get(field)
        if left != right:
            differences.append({"field": field, "before": left, "after": right})
    return differences


def _string_change_is_single_glyph(before: Any, after: Any) -> bool:
    left = str(before or "")
    right = str(after or "")
    if left == right or not left or not right:
        return False
    matcher = difflib.SequenceMatcher(a=left, b=right, autojunk=False)
    opcodes = [op for op in matcher.get_opcodes() if op[0] != "equal"]
    if len(opcodes) != 1:
        return False
    tag, i1, i2, j1, j2 = opcodes[0]
    return tag == "replace" and (i2 - i1) == 1 and (j2 - j1) == 1


def classify_change(before: dict[str, Any], after: dict[str, Any], changed_fields: list[dict[str, Any]]) -> str:
    fields = {str(row.get("field")) for row in changed_fields}
    if fields <= {"stem", "stem_markup"} and _string_change_is_single_glyph(
        before.get("stem"), after.get("stem")
    ):
        return "exact_ocr_rule"
    if fields & {"image_refs", "stem_image", "answer_image_refs"}:
        return "crop_strategy"
    if fields & {"answer", "answer_payload"}:
        return "answer_rule"
    if fields & {"group_ref", "group_sequence_no"}:
        return "parser_route"
    if fields & {"stem_markup", "options"}:
        return "format_rule"
    if fields & {"stem"}:
        return "format_rule"
    return "skill_note"


def _evidence_rows(value: Any) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else [value]
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item: dict[str, Any] = {}
        for key in ("kind", "family", "source", "reference", "locator", "page", "sha256", "status"):
            if row.get(key) not in (None, ""):
                item[key] = _truncate(row[key], 500)
        if item:
            result.append(item)
    return result[:MAX_EXAMPLES]


def _independent_evidence_count(evidence: list[dict[str, Any]]) -> int:
    identities = {
        str(row.get("family") or row.get("kind") or row.get("source") or row.get("reference") or "")
        for row in evidence
    }
    return len({value for value in identities if value})


def build_ai_task(event: dict[str, Any]) -> dict[str, Any]:
    """Build the bounded packet sent to GLM or another configured provider."""

    diff = event.get("diff") if isinstance(event.get("diff"), list) else []
    return {
        "task_type": "guardrail_candidate_review",
        "feedback_schema_version": FEEDBACK_SCHEMA_VERSION,
        "feedback_id": event.get("feedback_id"),
        "candidate_key": event.get("candidate_key"),
        "question_number": event.get("question_number"),
        "source_kind": event.get("source_kind"),
        "scope": event.get("scope"),
        "change_class": event.get("change_class"),
        "changed_fields": [str(row.get("field")) for row in diff if isinstance(row, dict)],
        "before": event.get("before"),
        "after": event.get("after"),
        "diff": diff,
        "evidence": event.get("evidence") or [],
        "instruction": (
            "判斷這次前後差異是否可形成可重用護欄。只能提出候選，不得直接修改題目、"
            "不得宣告 active、不得寫檔。若證據不足，回 no_generalization。"
        ),
        "output_shape": {
            "status": "candidate|no_generalization|unclear",
            "guardrail_type": "exact_ocr_rule|notation_rule|format_rule|crop_strategy|answer_rule|parser_route|skill_note|no_generalization",
            "confidence": "number 0..1",
            "rule_proposal": "object or null; observed/proposed only",
            "skill_update_candidate": "object or null; prose patch only",
            "positive_examples": "array of feedback ids or bounded examples",
            "negative_controls": "array",
            "do_not_generalize": "array",
            "rationale": "short observable explanation",
        },
    }


def build_feedback_event(
    *,
    candidate_key: str,
    before: Any,
    after: Any,
    source_kind: str,
    scope: str = "question",
    lane: str | None = None,
    reviewer: str | None = None,
    run_id: str | None = None,
    revision_id: str | None = None,
    event_ref: str | None = None,
    evidence: Any = None,
    question_number: Any = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    candidate_key = str(candidate_key or "").strip()
    if not candidate_key:
        raise FeedbackContractError("candidate_key is required")
    if source_kind not in FEEDBACK_SOURCE_KINDS:
        raise FeedbackContractError(f"unsupported feedback source_kind: {source_kind}")
    if scope not in FEEDBACK_SCOPES:
        raise FeedbackContractError(f"unsupported feedback scope: {scope}")
    before_snapshot = question_snapshot(before)
    after_snapshot = question_snapshot(after)
    diff = diff_snapshots(before_snapshot, after_snapshot)
    if not diff:
        raise NoVisibleChange("feedback must contain a visible before/after change")
    evidence_rows = _evidence_rows(evidence)
    independent_count = _independent_evidence_count(evidence_rows)
    if source_kind == "three_evidence_correction" and independent_count < 3:
        raise FeedbackContractError(
            "three_evidence_correction requires at least three independent evidence families"
        )
    created = created_at or now_iso()
    identity = canonical_json(
        {
            "candidate_key": candidate_key,
            "source_kind": source_kind,
            "scope": scope,
            "event_ref": event_ref,
            "before": before_snapshot,
            "after": after_snapshot,
        }
    )
    feedback_id = f"feedback:{sha256_text(identity)[:24]}"
    change_class = classify_change(before_snapshot, after_snapshot, diff)
    event = {
        "schema_version": FEEDBACK_SCHEMA_VERSION,
        "feedback_id": feedback_id,
        "candidate_key": candidate_key,
        "question_number": str(question_number or after_snapshot.get("question_number") or before_snapshot.get("question_number") or ""),
        "run_id": str(run_id or ""),
        "revision_id": str(revision_id or ""),
        "source_kind": source_kind,
        "scope": scope,
        "lane": str(lane or ""),
        "actor_kind": "human" if source_kind == "human_correction" else "deterministic_system",
        "reviewer": str(reviewer or ""),
        "event_ref": str(event_ref or ""),
        "change_class": change_class,
        "changed_fields": [str(row["field"]) for row in diff],
        "before": before_snapshot,
        "after": after_snapshot,
        "diff": diff,
        "evidence": evidence_rows,
        "evidence_summary": {
            "independent_family_count": independent_count,
            "minimum_required": 3 if source_kind == "three_evidence_correction" else 0,
        },
        "ai_task": {},
        "promotion_policy": {
            "advisory_only": True,
            "rule_candidate_only": True,
            "requires_negative_controls": True,
            "requires_owner_approval": True,
            "may_write_skill_directly": False,
            "may_activate_rule_directly": False,
        },
        "created_at": created,
    }
    event["ai_task"] = build_ai_task(event)
    return event


def build_guardrail_candidate(
    feedback_event: dict[str, Any],
    *,
    ai_result: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
    attempt: int = 0,
) -> dict[str, Any]:
    """Create a non-active guardrail row from feedback and optional AI output."""

    if not isinstance(feedback_event, dict) or not feedback_event.get("feedback_id"):
        raise FeedbackContractError("feedback_event with feedback_id is required")
    result = ai_result if isinstance(ai_result, dict) else {}
    status = "observed"
    ai_status = str(result.get("status") or "").strip().lower()
    if ai_status == "candidate":
        status = "proposed"
    elif ai_status in {"no_generalization", "unclear"}:
        status = "no_generalization"
    elif result.get("error"):
        status = "ai_failed"
    guardrail_type = str(result.get("guardrail_type") or feedback_event.get("change_class") or "skill_note")
    if guardrail_type not in GUARDRAIL_TYPES:
        guardrail_type = "no_generalization"
    identity = canonical_json(
        {
            "feedback_id": feedback_event["feedback_id"],
            "attempt": int(attempt),
            "ai_result": result,
        }
    )
    guardrail_id = f"guardrail:{sha256_text(identity)[:24]}"
    rule_proposal = result.get("rule_proposal") if isinstance(result.get("rule_proposal"), dict) else None
    skill_candidate = result.get("skill_update_candidate") if isinstance(result.get("skill_update_candidate"), dict) else None
    if rule_proposal and str(rule_proposal.get("status") or "").lower() in {"active", "verified"}:
        status = "rejected"
        rule_proposal = None
    if skill_candidate:
        skill_candidate = {
            key: value
            for key, value in skill_candidate.items()
            if key in {"path", "operation", "content", "section", "reason"}
        }
        if len(str(skill_candidate.get("content") or "")) > 8_000:
            skill_candidate["content"] = _truncate(skill_candidate["content"], 8_000)
    return {
        "schema_version": GUARDRAIL_SCHEMA_VERSION,
        "guardrail_id": guardrail_id,
        "feedback_id": feedback_event["feedback_id"],
        "candidate_key": feedback_event.get("candidate_key"),
        "scope": feedback_event.get("scope"),
        "lane": feedback_event.get("lane"),
        "change_class": feedback_event.get("change_class"),
        "guardrail_type": guardrail_type,
        "status": status,
        "rule_proposal": rule_proposal,
        "skill_update_candidate": skill_candidate,
        "positive_examples": result.get("positive_examples") if isinstance(result.get("positive_examples"), list) else [],
        "negative_controls": result.get("negative_controls") if isinstance(result.get("negative_controls"), list) else [],
        "do_not_generalize": result.get("do_not_generalize") if isinstance(result.get("do_not_generalize"), list) else [],
        "rationale": _truncate(result.get("rationale"), 2_000),
        "ai_result": result,
        "validation": validation if isinstance(validation, dict) else {"status": "not_run"},
        "promotion_policy": {
            "advisory_only": True,
            "requires_owner_approval": True,
            "requires_negative_controls": True,
            "active": False,
            "direct_file_write": False,
        },
        "created_at": now_iso(),
    }
