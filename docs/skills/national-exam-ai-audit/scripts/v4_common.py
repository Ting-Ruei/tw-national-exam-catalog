"""Shared dependency-free helpers for the sparse v4 audit workflow."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable


SKILL_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SKILL_ROOT.parents[2]
PROMPT_VERSION = "national_exam_sparse_audit_v4"
LANES = {"ocr_text", "semantic_transcription", "group", "visual"}
LANE_DISPLAY_NAMES = {
    "ocr_text": "post_mineru_text_audit",
    "semantic_transcription": "semantic_text_audit",
    "group": "group_binding_audit",
    "visual": "visual_asset_audit",
}
SOURCE_CLASSES = {
    "candidate_mismatch",
    "mineru_ocr_mismatch",
    "parser_error",
    "source_original",
    "source_original_suspected_typo",
    "source_unverified",
}
ROUTES = {
    "none",
    "deterministic",
    "propose_rule",
    "human_text",
    "human_pdf",
    "parser",
    "group",
    "visual",
}
ISSUE_FAMILIES = {
    "non_question_header",
    "boundary_merge",
    "boundary_missing",
    "empty_stem",
    "option_structure",
    "ocr_character",
    "notation_markup",
    "semantic_disfluency",
    "semantic_ocr",
    "visual_dependency",
    "group_dependency",
}
LATIN_BINOMIAL_RE = re.compile(r"\b[A-Z][a-z]{3,}\s+[a-z][a-z-]{2,}\b")
ABBREVIATED_BINOMIAL_RE = re.compile(r"\b[A-Z]\.\s+[a-z][a-z-]{2,}\b")


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(compact_json(value).encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_no}: JSONL row must be an object")
            yield line_no, value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(compact_json(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def load_profile(path: Path) -> dict[str, Any]:
    # Profile files use JSON syntax, which is a strict subset of YAML.  This
    # avoids a runtime PyYAML dependency in the Review UI container.
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"profile must be an object: {path}")
    return value


def task_text(task: dict[str, Any]) -> str:
    content = task.get("content") or {}
    values = [str(content.get("stem") or "")]
    values.extend(
        str(option.get("text") or "")
        for option in content.get("options") or []
        if isinstance(option, dict)
    )
    return "\n".join(values)


def task_scope(task: dict[str, Any]) -> tuple[str, str]:
    exam = task.get("exam") or {}
    return str(exam.get("category") or ""), str(exam.get("subject") or "")


def scope_matches(rule: dict[str, Any], task: dict[str, Any]) -> bool:
    category, subject = task_scope(task)
    scope = rule.get("scope") or {}
    categories = {str(value) for value in scope.get("categories") or []}
    subjects = {str(value) for value in scope.get("subjects") or []}
    return (not categories or category in categories) and (not subjects or subject in subjects)


def load_active_exact_rules() -> list[dict[str, Any]]:
    index = read_json(SKILL_ROOT / "rules" / "ocr-exact.json")
    registry_path = PROJECT_ROOT / str(index["canonical_registry"])
    registry = read_json(registry_path)
    return [
        rule
        for rule in registry.get("rules") or []
        if isinstance(rule, dict)
        and rule.get("status", "active") == "active"
        and rule.get("kind") == "phrase"
        and rule.get("id")
        and rule.get("source")
        and rule.get("target")
    ]


def matching_active_rules(task: dict[str, Any]) -> list[dict[str, Any]]:
    text = task_text(task)
    return [
        {
            "id": str(rule["id"]),
            "source": str(rule["source"]),
            "target": str(rule["target"]),
        }
        for rule in load_active_exact_rules()
        if scope_matches(rule, task) and str(rule["source"]) in text
    ]


def matching_negative_controls(task: dict[str, Any]) -> list[dict[str, Any]]:
    payload = read_json(SKILL_ROOT / "rules" / "negative-controls.json")
    key = str(task.get("candidate_key") or "")
    text = task_text(task)
    matches: list[dict[str, Any]] = []
    for control in payload.get("controls") or []:
        control_key = str(control.get("candidate_key") or "")
        observed = str(control.get("observed") or "")
        if control_key and control_key != key:
            continue
        if observed and observed not in text:
            continue
        matches.append(
            {
                field: control.get(field)
                for field in ("id", "observed", "rejected_target", "source_class", "route", "reason")
                if control.get(field) is not None
            }
        )
    return matches


def matching_semantic_anchors(task: dict[str, Any]) -> list[dict[str, Any]]:
    """Attach only anchors whose local text actually contains both sides."""
    payload = read_json(SKILL_ROOT / "rules" / "semantic-anchors.json")
    text = task_text(task).lower()
    matches: list[dict[str, Any]] = []
    for anchor in payload.get("anchors") or []:
        if not isinstance(anchor, dict):
            continue
        anchor_text = str(anchor.get("anchor") or "").lower()
        suspects = [str(value) for value in anchor.get("suspect_terms") or [] if str(value)]
        if not anchor_text or anchor_text not in text:
            continue
        if suspects and not any(suspect in task_text(task) for suspect in suspects):
            continue
        matches.append(
            {
                field: anchor.get(field)
                for field in (
                    "id",
                    "anchor",
                    "suspect_terms",
                    "candidate_target",
                    "status",
                    "route",
                    "auto_materialize",
                    "reason",
                )
                if anchor.get(field) is not None
            }
        )
    return matches


def matching_scientific_name_policy(task: dict[str, Any]) -> dict[str, Any] | None:
    """Return the small policy object only for tasks containing a binomial.

    Genus-initial notation (for example ``B. cereus``) is included; it is a
    valid scientific-name form, not an OCR punctuation error.
    """
    text = task_text(task)
    if not (LATIN_BINOMIAL_RE.search(text) or ABBREVIATED_BINOMIAL_RE.search(text)):
        return None
    payload = read_json(SKILL_ROOT / "rules" / "scientific-names.json")
    policy = payload.get("policy")
    return dict(policy) if isinstance(policy, dict) else None


def compact_task(task: dict[str, Any], *, lane: str) -> dict[str, Any]:
    content = task.get("content") or {}
    options: list[dict[str, Any]] = []
    for option in content.get("options") or []:
        if not isinstance(option, dict) or not str(option.get("key") or "").strip():
            continue
        compact_option: dict[str, Any] = {
            "key": str(option.get("key") or "").strip().upper(),
            "text": str(option.get("text") or ""),
        }
        if lane == "visual" and isinstance(option.get("image"), dict):
            asset = option["image"]
            compact_option["image"] = {
                field: asset.get(field)
                for field in (
                    "raw_ref",
                    "relative_path",
                    "path_relative",
                    "exists",
                    "source_exists",
                    "local_exists",
                    "bytes",
                    "placement",
                    "target_option",
                )
                if asset.get(field) is not None
            }
        options.append(compact_option)
    compact: dict[str, Any] = {
        "candidate_key": str(task.get("candidate_key") or ""),
        "stage": str(task.get("stage") or "question"),
        "lane": lane,
        "content": {
            "stem": str(content.get("stem") or ""),
            "options": options,
        },
        "exam": {
            key: str((task.get("exam") or {}).get(key) or "")
            for key in ("category", "subject", "year", "question_number")
        },
    }
    if lane == "group":
        compact["content"]["group_ref"] = content.get("group_ref")
        compact["neighbors"] = task.get("neighbors") or {}
    if lane == "visual":
        compact["content"]["image_refs"] = [
            {
                field: asset.get(field)
                for field in (
                    "raw_ref",
                    "relative_path",
                    "path_relative",
                    "exists",
                    "source_exists",
                    "local_exists",
                    "bytes",
                    "kind",
                    "placement",
                )
                if isinstance(asset, dict) and asset.get(field) is not None
            }
            for asset in content.get("image_refs") or []
        ]
        stem_image = content.get("stem_image")
        if isinstance(stem_image, dict):
            compact["content"]["stem_image"] = {
                field: stem_image.get(field)
                for field in (
                    "raw_ref",
                    "relative_path",
                    "path_relative",
                    "exists",
                    "source_exists",
                    "local_exists",
                    "bytes",
                    "placement",
                )
                if stem_image.get(field) is not None
            }
    parser_issues = ((task.get("signals") or {}).get("parser_issues") or [])
    if parser_issues:
        compact["signals"] = {"parser_issues": parser_issues}
    fingerprint = str(task.get("source_fingerprint") or task.get("effective_content_hash") or "")
    compact["source_fingerprint"] = fingerprint or sha256_json(
        {"content": compact["content"], "exam": compact["exam"]}
    )
    active_rules = matching_active_rules(compact)
    negative_controls = matching_negative_controls(compact)
    semantic_anchors = matching_semantic_anchors(compact)
    scientific_name_policy = matching_scientific_name_policy(compact)
    if active_rules:
        compact["active_rules"] = active_rules
    if negative_controls:
        compact["negative_controls"] = negative_controls
    if semantic_anchors:
        compact["semantic_anchors"] = semantic_anchors
    if scientific_name_policy:
        compact["scientific_name_policy"] = scientific_name_policy
    return compact


def get_field_text(task: dict[str, Any], field: str) -> str | None:
    content = task.get("content") or {}
    if field == "stem":
        return str(content.get("stem") or "")
    if field.startswith("option_") and len(field) == 8:
        key = field[-1]
        for option in content.get("options") or []:
            if str(option.get("key") or "").strip().upper() == key:
                return str(option.get("text") or "")
    return None


def set_field_text(task: dict[str, Any], field: str, value: str) -> None:
    content = task.setdefault("content", {})
    if field == "stem":
        content["stem"] = value
        return
    if field.startswith("option_") and len(field) == 8:
        key = field[-1]
        for option in content.get("options") or []:
            if str(option.get("key") or "").strip().upper() == key:
                option["text"] = value
                return
    raise ValueError(f"field is not text-materializable: {field}")
