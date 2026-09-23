"""Review-UI AI audit interpretation.

Extracted verbatim from `scripts/serve_question_review_ui.py` so one file is no longer 10,700 lines.
`serve_question_review_ui` re-exports every name here; that indirection is deliberate and is why the
test files that load the server by path keep working unchanged. No behaviour was changed.
"""

from __future__ import annotations



import sys as _sys
from pathlib import Path as _Path

# The server's guarded imports fall back to `from scripts.x import ...`. `scripts/` has no
# `__init__.py`, so that is a namespace-package import and needs the *repository root* on `sys.path`
# — not the `scripts/` directory. Both are added: the root for `scripts.x`, the directory for the
# plain `import x` form that a bare `sys.path` entry would otherwise be needed for.
_REPO_ROOT = str(_Path(__file__).resolve().parents[4])
for _p in (_REPO_ROOT, _REPO_ROOT + "/scripts"):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

try:
    from question_group_detection import (
        GROUP_CONTINUATION_RE,
        GROUP_COUNT_RE,
        GROUP_COUNT_SQL_RE,
        GROUP_PREFIX_RANGE_RE,
        group_count_from_text,
    )
except ModuleNotFoundError:  # pragma: no cover - importlib-based test loading
    from scripts.question_group_detection import (
        GROUP_CONTINUATION_RE,
        GROUP_COUNT_RE,
        GROUP_COUNT_SQL_RE,
        GROUP_PREFIX_RANGE_RE,
        group_count_from_text,
    )
from typing import Any
import hashlib
import json
import os
import re
import urllib.request
from .constants import ABBREVIATED_BINOMIAL_RE, AI_ANSWER_DEFER_LABELS, AI_OCR_CHAR_REPLACEMENTS, AI_OCR_TEXT_REPLACEMENTS, AI_REVIEW_ACTIONS_WITH_WORK, CAPSULE_COMPOUND_RE, CAPSULE_EXACT_REPLACEMENTS, DEFAULT_AI_MODEL, HUMAN_SUPERSEDES_AI_ACTIONS, LATIN_BINOMIAL_RE, OPENAI_API_BASE, STRUCTURED_TABLE_OPEN_RE, TABLE_DEPENDENCY_RE, VISUAL_DEPENDENCY_RE
from .events import event_timestamp
from .paths import display_path, safe_file_path
from .queue_view import review_projection

def ai_review_reference(event: dict[str, Any] | None) -> str:
    """Return a stable reference to the exact AI audit being rated.

    SQL rows expose their numeric event id.  JSONL-only/test backends do not,
    so they use a content hash over the immutable audit envelope instead.
    The reference prevents a late click from rating a newer model run.
    """
    if not isinstance(event, dict):
        return ""
    event_id = event.get("event_id")
    if event_id not in (None, ""):
        return f"sql:{event_id}"
    identity = {
        key: event.get(key)
        for key in (
            "candidate_key",
            "created_at",
            "input_hash",
            "provider",
            "model",
            "model_name",
            "prompt_version",
            "audit",
        )
    }
    if not any(value not in (None, "", {}, []) for value in identity.values()):
        return ""
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def issue_quality_status(issues: list[dict[str, Any]]) -> str:
    severities = {issue.get("severity") for issue in issues}
    if "blocked" in severities or "error" in severities:
        return "blocked"
    if "warning" in severities:
        return "needs_review"
    return "pass"


def int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def has_structured_table_evidence(text: str) -> bool:
    """Identify actual table cues without treating an English word fragment as a table."""

    return bool(STRUCTURED_TABLE_OPEN_RE.search(text) or TABLE_DEPENDENCY_RE.search(text))


def candidate_visual_profile(candidate: dict[str, Any]) -> dict[str, Any]:
    visual_review_status = str(candidate.get("visual_review") or "").strip()
    no_visual_required = visual_review_status == "no_visual_required"
    image_refs = [
        ref for ref in (candidate.get("image_refs") or [])
        if isinstance(ref, dict)
    ]
    stem_image = candidate.get("stem_image") if isinstance(candidate.get("stem_image"), dict) else None
    option_images = [
        option.get("image")
        for option in (candidate.get("options") or [])
        if isinstance(option, dict) and isinstance(option.get("image"), dict)
    ]
    existing_refs = [ref for ref in image_refs if ref.get("exists") is not False]
    if stem_image and stem_image.get("exists") is not False:
        existing_refs.append(stem_image)
    existing_refs.extend(ref for ref in option_images if ref and ref.get("exists") is not False)
    text = "\n".join(
        [
            # The display layer hides table markup. Retain it for classification
            # so a genuine table cannot disappear before image review.
            str(candidate.get("stem_with_tables") or candidate.get("stem") or ""),
            str((candidate.get("metadata") or {}).get("raw_block") or ""),
        ]
    )
    has_visual_dependency = bool(VISUAL_DEPENDENCY_RE.search(text)) and not no_visual_required
    has_structured_table = has_structured_table_evidence(text)
    roles = sorted(
        {
            str(ref.get("asset_role") or ref.get("role") or "image")
            for ref in existing_refs
            if isinstance(ref, dict)
        }
    )
    has_manual_asset = any(
        str(ref.get("manual_asset") or "").lower() in {"true", "1"}
        or "manual" in str(ref.get("asset_role") or ref.get("role") or "").lower()
        or str(ref.get("asset_role") or ref.get("role") or "") == "table_manual_screenshot"
        for ref in existing_refs
        if isinstance(ref, dict)
    )
    return {
        "has_visual_asset": bool(existing_refs),
        "visual_asset_count": len(existing_refs),
        "has_visual_dependency": has_visual_dependency,
        "has_structured_table": has_structured_table,
        "needs_visual_asset_review": has_visual_dependency and not existing_refs,
        "visual_asset_roles": roles,
        "has_manual_asset": has_manual_asset,
        "no_visual_required": no_visual_required,
        "visual_review_status": visual_review_status,
        "visual_reviewed": visual_review_status in {"no_visual_required", "visual_asset_ok", "visual_asset_problem"},
    }


def answer_payload_values(answer_payload: Any, answer: Any) -> list[str]:
    values: list[str] = []
    if isinstance(answer_payload, dict):
        accepted = answer_payload.get("accepted_values")
        if isinstance(accepted, list):
            values.extend(str(value).strip() for value in accepted if str(value).strip())
        for key in ["answer", "raw_answer"]:
            value = str(answer_payload.get(key) or "").strip()
            if value:
                values.append(value)
    answer_value = str(answer or "").strip()
    if answer_value:
        values.append(answer_value)
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        if value not in seen:
            unique_values.append(value)
            seen.add(value)
    return unique_values


def ai_visual_status(ai_audit: Any) -> str:
    if not isinstance(ai_audit, dict):
        return ""
    direct = str(ai_audit.get("visual_status") or "").strip()
    if direct in {"visual_required_likely", "visual_not_required_likely", "visual_uncertain"}:
        return direct
    labels = ai_audit.get("labels") if isinstance(ai_audit.get("labels"), list) else []
    for label in labels:
        value = str(label or "").strip()
        if value in {"visual_required_likely", "visual_not_required_likely", "visual_uncertain"}:
            return value
    return ""


def answer_choice_letters(value: str) -> list[str]:
    return re.findall(r"[A-D]", value.upper())


def answer_review_hint(role: str, answer: Any, answer_payload: Any) -> dict[str, Any]:
    values = answer_payload_values(answer_payload, answer)
    raw_answer = ""
    is_special_correction = False
    if isinstance(answer_payload, dict):
        raw_answer = str(answer_payload.get("raw_answer") or "").strip()
        is_special_correction = bool(answer_payload.get("is_special_correction"))
    answer_text = str(answer or "").strip()
    unresolved_marker = any(value == "#" for value in values)
    multi_choice = any("|" in value or "+" in value or "/" in value for value in values)
    accepted_count = len([value for value in values if answer_choice_letters(value)])
    is_ans_single = role == "answer" and bool(re.fullmatch(r"[A-D]", answer_text.upper()))
    is_mod = role == "correction"
    needs_manual_choice = is_mod and (unresolved_marker or multi_choice or is_special_correction or accepted_count > 1)
    flags: list[str] = []
    message = ""
    severity = ""
    if is_ans_single:
        flags.append("ans_single_choice_trusted")
        message = "ANS 單選答案，若無其他疑點可沿用 parser 結果。"
        severity = "info"
    if is_mod and unresolved_marker:
        flags.append("mod_unresolved_marker")
        message = "MOD 答案仍含 #，需看答案 PDF 後點選正確答案。"
        severity = "warning"
    elif is_mod and needs_manual_choice:
        flags.append("mod_multi_answer")
        message = "MOD 多答案或特殊更正，建議看答案 PDF 後用點選確認。"
        severity = "warning"
    return {
        "flags": flags,
        "message": message,
        "severity": severity,
        "trusted_single": is_ans_single,
        "needs_manual_choice": needs_manual_choice,
        "unresolved_marker": unresolved_marker,
        "raw_answer": raw_answer,
        "values": values,
    }


def repair_event_info(
    latest_review: dict[str, Any] | None,
    latest_reset_review: dict[str, Any] | None,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    latest_action = str((latest_review or {}).get("action") or "")
    if latest_action in {"accept", "unblock"}:
        return {"active": False}

    event = latest_review or latest_reset_review
    projection = review_projection(latest_review, latest_reset_review, metadata)
    reviewer = str((event or {}).get("reviewer") or "")
    metadata_sources = [
        str(metadata.get(key) or "")
        for key in ("review_block_repair", "backfill_repair", "backfill_source")
        if metadata.get(key)
    ]
    is_repair_event = bool(projection.get("repair_event"))
    is_reset_waiting = bool(latest_reset_review and not latest_review)
    if not (is_repair_event or is_reset_waiting or metadata_sources):
        return {"active": False}

    notes = str(
        (event or {}).get("reset_notes")
        or (event or {}).get("notes")
        or (event or {}).get("previous_notes")
        or ""
    )
    return {
        "active": True,
        "label": (
            projection.get("display_label")
            if is_reset_waiting
            else "已修待複核"
        ),
        "reviewer": reviewer,
        "action": latest_action or str((latest_reset_review or {}).get("action") or "reset_review"),
        "notes": notes,
        "sources": metadata_sources,
        "updated_at": (event or {}).get("created_at"),
        "queue_bucket": projection.get("queue_bucket"),
        "previous_action": projection.get("previous_action"),
        "was_previously_accepted": projection.get("was_previously_accepted", False),
    }


def compact_candidate_for_ai(candidate: dict[str, Any]) -> dict[str, Any]:
    metadata = candidate.get("metadata") or {}
    return {
        "candidate_key": candidate.get("candidate_key"),
        "category": metadata.get("normalized_category_name") or metadata.get("group_name"),
        "subject": metadata.get("normalized_subject_name"),
        "year": metadata.get("year"),
        "exam_ordinal": metadata.get("exam_ordinal"),
        "question_number": candidate.get("question_number"),
        "stem": candidate.get("stem"),
        "options": [
            {
                "key": option.get("key"),
                "text": option.get("text"),
                "has_image": bool(isinstance(option.get("image"), dict) and option["image"].get("exists")),
            }
            for option in (candidate.get("options") or [])
            if isinstance(option, dict)
        ],
        "answer": candidate.get("answer"),
        "group_ref": candidate.get("group_ref"),
        "image_count": len(candidate.get("non_option_image_refs") or candidate.get("image_refs") or []),
        "question_issues": [
            {
                "issue_code": issue.get("issue_code"),
                "severity": issue.get("severity"),
                "message": issue.get("message"),
            }
            for issue in (candidate.get("question_issues") or candidate.get("issues") or [])
        ],
    }


def local_question_ai_audit(candidate: dict[str, Any]) -> dict[str, Any]:
    payload = compact_candidate_for_ai(candidate)
    text_parts = [str(payload.get("stem") or "")]
    text_parts.extend(str(option.get("text") or "") for option in payload.get("options") or [])
    combined = "\n".join(text_parts)
    findings: list[dict[str, Any]] = []

    def add(code: str, severity: str, field: str, message: str, evidence: str = "", suggestion: str = "") -> None:
        findings.append(
            {
                "code": code,
                "severity": severity,
                "field": field,
                "message": message,
                "evidence": evidence[:160],
                "suggestion": suggestion,
            }
        )

    suspicious_variant_map = {
        "黄": "黃",
        "氢": "氫",
        "脱": "脫",
        "铵": "銨",
        "巯": "巰",
        "题": "題",
        "临": "臨",
        "验": "驗",
        "药": "藥",
        "麸": "麩",
        "羟": "羥",
        "钠": "鈉",
        "钾": "鉀",
        "钙": "鈣",
        "镁": "鎂",
        "铁": "鐵",
        "锌": "鋅",
        "铜": "銅",
        "铅": "鉛",
    }
    simplified_hits = sorted({char for char in combined if char in suspicious_variant_map})
    if simplified_hits:
        evidence = "、".join(f"{char}→{suspicious_variant_map[char]}" for char in simplified_hits)
        add(
            "possible_simplified_or_ocr_char",
            "warning",
            "text",
            "偵測到疑似簡化字或 OCR 字形，建議人工比對 PDF。",
            evidence,
            "若 PDF 原文為繁體，請修 parser 正規化或人工校正；若原文即如此，請保留。",
        )
    if re.search(r"\\[A-Za-z]+|_\{|<sub>|<sup>|\^\{", combined):
        add(
            "science_markup_present",
            "info",
            "text",
            "題文含公式、上下標或 LaTeX/HTML markup，前端顯示與入庫時需確認。",
            re.search(r"\\[A-Za-z]+|_\{|<sub>|<sup>|\^\{", combined).group(0),
            "確認畫面是否已正確渲染希臘字母、上下標與科學符號。",
        )
    bracket_pairs = [("(", ")"), ("（", "）"), ("[", "]"), ("【", "】")]
    for left, right in bracket_pairs:
        if combined.count(left) != combined.count(right):
            add("unbalanced_bracket", "warning", "text", f"括號數量不一致：{left}{right}", f"{left}:{combined.count(left)} {right}:{combined.count(right)}")
    options = payload.get("options") or []
    option_keys = [option.get("key") for option in options]
    if len(options) not in {0, 4, 5}:
        add("unexpected_option_count", "warning", "options", "選項數量不是常見的 4 或 5 個。", str(option_keys), "檢查是否串題、漏選項或題組文字被切進選項。")
    if len(option_keys) != len(set(option_keys)):
        add("duplicate_option_key", "error", "options", "選項代號重複。", str(option_keys), "需要修 parser 或人工校正。")
    if re.search(r"(下列圖|附圖|圖中|表中|下表|附表)", combined) and not payload.get("image_count") and not any(option.get("has_image") for option in options):
        add("image_or_table_cue_without_asset", "warning", "assets", "題文提到圖表，但 candidate 沒有圖片或表格資產。", "圖表 cue", "比對 MinerU layout；必要時用 manual asset 掛圖。")
    if "<table" in combined.lower():
        add("structured_table_markup", "info", "stem", "題幹含結構化 table markup。", "<table>", "若網頁顯示不完整，改用 manual table image 並保留 raw table 追溯。")
    status = "pass"
    if any(item["severity"] == "error" for item in findings):
        status = "block"
    elif any(item["severity"] == "warning" for item in findings):
        status = "needs_review"
    return {
        "status": status,
        "confidence": 0.45 if findings else 0.55,
        "summary": "本機規則稽核完成；未使用 OpenAI API。" if findings else "本機規則未發現明顯格式疑點；未使用 OpenAI API。",
        "findings": findings,
        "recommended_action": "needs_review" if status != "pass" else "no_action",
    }


def extract_response_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    chunks: list[str] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict):
                text = content.get("text")
                if isinstance(text, str):
                    chunks.append(text)
    return "\n".join(chunks).strip()


def openai_question_ai_audit(candidate: dict[str, Any]) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        audit = local_question_ai_audit(candidate)
        audit["provider"] = "local"
        audit["model"] = "heuristic"
        return audit
    payload = compact_candidate_for_ai(candidate)
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "status": {"type": "string", "enum": ["pass", "needs_review", "block"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "summary": {"type": "string"},
            "recommended_action": {"type": "string", "enum": ["no_action", "needs_review", "block", "manual_correction"]},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "code": {"type": "string"},
                        "severity": {"type": "string", "enum": ["info", "warning", "error"]},
                        "field": {"type": "string"},
                        "message": {"type": "string"},
                        "evidence": {"type": "string"},
                        "suggestion": {"type": "string"},
                    },
                    "required": ["code", "severity", "field", "message", "evidence", "suggestion"],
                },
            },
        },
        "required": ["status", "confidence", "summary", "recommended_action", "findings"],
    }
    request_body = {
        "model": DEFAULT_AI_MODEL,
        "input": [
            {
                "role": "system",
                "content": (
                    "你是台灣國考題 OCR/parser 審核助理。只做格式與字形稽核，不判斷學科答案正確性。"
                    "請檢查疑似 OCR 字形錯誤、簡繁混用、希臘字母/上下標/科學符號、選項數量、題組/圖表線索、表格或圖片引用是否可能缺漏。"
                    "科學符號必須有原文證據才可建議修正；不要從英文單字片段推導數字或下標。hypo、hypothyroidism 與單獨的 PO 必須原樣保留，只有獨立且原文已出現 PO2/PO₂ 或 P_{O_2} 時才可建議 PO₂。"
                    "不要自動改題，不要宣稱一定錯；用繁體中文回覆 JSON。"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "question_format_audit",
                "strict": True,
                "schema": schema,
            }
        },
    }
    data = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{OPENAI_API_BASE}/responses",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=int(os.environ.get("OPENAI_REVIEW_TIMEOUT", "45"))) as response:
        raw_response = json.loads(response.read().decode("utf-8"))
    text = extract_response_text(raw_response)
    try:
        audit = json.loads(text)
    except json.JSONDecodeError:
        audit = {
            "status": "needs_review",
            "confidence": 0,
            "summary": "OpenAI 回傳不是有效 JSON，已保留原始文字供排查。",
            "recommended_action": "needs_review",
            "findings": [
                {
                    "code": "invalid_model_json",
                    "severity": "error",
                    "field": "model_output",
                    "message": "模型回傳無法解析。",
                    "evidence": text[:500],
                    "suggestion": "檢查模型與 response format 支援度。",
                }
            ],
        }
    audit["provider"] = "openai"
    audit["model"] = DEFAULT_AI_MODEL
    audit["response_id"] = raw_response.get("id")
    audit["usage"] = raw_response.get("usage")
    return audit


def normalized_correction(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    correction: dict[str, Any] = {}
    for key in ("stem", "answer", "group_ref", "visual_review"):
        if key in value:
            # Empty group/visual fields are omitted from text-only correction
            # events.  Clearing a group is a group-review action, not a side
            # effect of accepting a typo correction.
            if key in {"group_ref", "visual_review"} and not str(value.get(key) or "").strip():
                continue
            correction[key] = "" if value[key] is None else str(value[key])
    if "group_sequence_no" in value:
        try:
            correction["group_sequence_no"] = int(value["group_sequence_no"])
        except (TypeError, ValueError):
            correction["group_sequence_no"] = None
    if "stem_image" in value and value.get("stem_image") is None:
        correction["stem_image"] = None
    elif isinstance(value.get("stem_image"), dict):
        stem_image = normalized_asset_ref(value["stem_image"])
        if stem_image:
            correction["stem_image"] = stem_image
    if isinstance(value.get("image_refs"), list):
        image_refs = []
        for ref in value["image_refs"]:
            if not isinstance(ref, dict):
                continue
            normalized = normalized_asset_ref(ref)
            if normalized:
                image_refs.append(normalized)
        correction["image_refs"] = image_refs
    if isinstance(value.get("answer_image_refs"), list):
        answer_image_refs = []
        for ref in value["answer_image_refs"]:
            if not isinstance(ref, dict):
                continue
            normalized = normalized_asset_ref(ref)
            if normalized:
                answer_image_refs.append(normalized)
        correction["answer_image_refs"] = answer_image_refs
    if isinstance(value.get("options"), list):
        options = []
        for option in value["options"]:
            if not isinstance(option, dict):
                continue
            label = str(option.get("key") or "").strip().upper()
            if not label:
                continue
            image = option.get("image")
            normalized_image = normalized_asset_ref(image) if isinstance(image, dict) else None
            normalized_option = {
                "key": label[:1],
                "text": "" if option.get("text") is None else str(option.get("text")),
            }
            # A text-only AI patch must not erase an existing option image or
            # markup simply because the compact audit packet omitted it.
            if normalized_image:
                normalized_option["image"] = normalized_image
            if "markup" in option and option.get("markup") is not None:
                normalized_option["markup"] = option.get("markup")
            options.append(normalized_option)
        correction["options"] = options
    return correction


def correction_changes_candidate(candidate: dict[str, Any], correction: dict[str, Any]) -> bool:
    """Return whether an AI correction would change the visible candidate.

    AI events can outlive a deterministic parser repair.  Do not keep showing
    an "apply suggestion" button when the suggested text is already present;
    the AI event remains historical and advisory, but the operator has no
    useful action left to take.
    """
    if not isinstance(candidate, dict) or not isinstance(correction, dict):
        return False
    for field in ("stem", "answer", "group_ref", "visual_review"):
        if field in correction and str(candidate.get(field) or "") != str(correction.get(field) or ""):
            return True
    for field in ("image_refs", "answer_image_refs", "stem_image"):
        if field in correction and candidate.get(field) != correction.get(field):
            return True
    if isinstance(correction.get("options"), list):
        current_options = {
            str(row.get("key") or "").upper(): str(row.get("text") or "")
            for row in candidate.get("options") or []
            if isinstance(row, dict)
        }
        suggested_options = {
            str(row.get("key") or "").upper(): str(row.get("text") or "")
            for row in correction["options"]
            if isinstance(row, dict)
        }
        if current_options != suggested_options:
            return True
    return False


def normalized_asset_ref(value: dict[str, Any]) -> dict[str, Any]:
    path_value = str(value.get("path") or value.get("path_relative") or "").strip()
    path = safe_file_path(path_value)
    if path is None:
        return {}
    normalized: dict[str, Any] = {
        "raw_ref": str(value.get("raw_ref") or value.get("label") or path.name),
        "path": display_path(path),
        "path_relative": display_path(path),
        "exists": path.exists(),
    }
    for key in ("asset_key", "asset_role", "source", "caption", "description", "manual_asset", "mime_type", "sha256", "bytes", "placement", "target_option"):
        if key in value:
            normalized[key] = value[key]
    return normalized


def apply_ai_ocr_replacements(value: str) -> tuple[str, list[str]]:
    if not value:
        return value, []
    updated = value
    changes: list[str] = []
    for source, target in AI_OCR_TEXT_REPLACEMENTS:
        if source in updated:
            updated = updated.replace(source, target)
            changes.append(f"{source} -> {target}")
    translated = updated.translate(AI_OCR_CHAR_REPLACEMENTS)
    if translated != updated:
        for old, new in zip(updated, translated):
            if old != new:
                changes.append(f"{old} -> {new}")
        updated = translated
    return updated, sorted(set(changes))


def ai_audit_has_work(audit: dict[str, Any], suggested_correction: dict[str, Any] | None = None) -> bool:
    if ai_audit_is_answer_deferred_only(audit):
        return False
    recommended_action = str(audit.get("recommended_action") or audit.get("skill_recommended_action") or "")
    findings = audit.get("findings") or []
    labels = set(audit.get("labels") or [])
    if suggested_correction:
        return True
    if recommended_action in AI_REVIEW_ACTIONS_WITH_WORK:
        return True
    if findings:
        return True
    return bool(labels - {"pass_likely"})


def ai_audit_is_answer_deferred_only(audit: dict[str, Any]) -> bool:
    recommended_actions = {
        str(audit.get("recommended_action") or ""),
        str(audit.get("skill_recommended_action") or ""),
    }
    labels = set(audit.get("labels") or [])
    findings = audit.get("findings") or []
    has_answer_defer = "defer_to_answer_audit" in recommended_actions
    if not has_answer_defer:
        has_answer_defer = any(
            str(finding.get("suggestion") or finding.get("recommended_action") or "") == "defer_to_answer_audit"
            for finding in findings
            if isinstance(finding, dict)
        )
    if not has_answer_defer:
        return False
    if labels - AI_ANSWER_DEFER_LABELS:
        return False
    non_answer_findings = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        finding_text = " ".join(
            str(finding.get(field) or "")
            for field in ("field", "code", "message", "evidence", "suggestion", "recommended_action")
        )
        if "answer" not in finding_text.lower() and "答案" not in finding_text:
            non_answer_findings.append(finding)
    return not non_answer_findings


def split_ai_audit_scopes(
    candidate: dict[str, Any],
    audit: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Separate group-ownership findings from the question audit surface.

    The AI event table is shared for operational simplicity, but the Review UI
    has distinct ownership: text/notation belongs to 題目, while continuation
    markers and ranges belong to 題組.  Older LUNA rows stored a group finding
    in ``field=stem`` with ``recommended_action=review_group``.  Keep that
    event for history, expose it as ``group_ai_review``, and make the question
    surface see a pass when the event contains no question-owned finding.
    """
    if not isinstance(audit, dict):
        return audit, None
    findings = [item for item in audit.get("findings") or [] if isinstance(item, dict)]
    labels = {str(value) for value in audit.get("labels") or []}
    group_findings: list[dict[str, Any]] = []
    question_findings: list[dict[str, Any]] = []
    stem = str(candidate.get("stem") or "")
    marker_present = bool(GROUP_CONTINUATION_RE.search(stem))
    for finding in findings:
        family = str(finding.get("issue_family") or finding.get("code") or "")
        route = str(finding.get("route") or "")
        field = str(finding.get("field") or finding.get("location") or "")
        observed = str(finding.get("observed") or finding.get("before") or "")
        is_group = (
            family == "group_dependency"
            or route == "group"
            or (marker_present and bool(re.search(r"承上題|呈上題|上題|前述", observed)))
        )
        (group_findings if is_group else question_findings).append(finding)
    group_only_label = "group_dependency" in labels and not question_findings
    if not group_findings and not group_only_label:
        return audit, None

    group_audit = dict(audit)
    group_audit["audit_scope"] = "group"
    group_audit["status"] = "needs_review" if group_findings else audit.get("status") or "needs_review"
    group_audit["recommended_action"] = "review_group"
    group_audit["findings"] = group_findings
    group_audit["labels"] = sorted(labels | {"group_dependency"})
    group_audit.pop("suggested_correction", None)
    group_audit.pop("suggested_changes", None)

    question_audit = dict(audit)
    question_audit["audit_scope"] = "question"
    question_audit["findings"] = question_findings
    question_audit["labels"] = sorted(labels - {"group_dependency"})
    if not question_findings:
        question_audit["status"] = "pass"
        question_audit["recommended_action"] = "no_action"
        question_audit["summary"] = "題組延續線索已移交題組審核；題目文字層沒有待處理疑點。"
        question_audit["reason"] = question_audit["summary"]
        question_audit.pop("suggested_correction", None)
        question_audit.pop("suggested_changes", None)
        question_audit.pop("uncorrected_findings", None)
    return question_audit, group_audit


def effective_ai_audit_status(audit: dict[str, Any] | None, suggested_correction: dict[str, Any] | None = None) -> str | None:
    if not isinstance(audit, dict):
        return None
    status = str(audit.get("status") or "")
    if status == "blocked":
        status = "block"
    if ai_audit_is_answer_deferred_only(audit):
        return "pass"
    if status == "pass" and ai_audit_has_work(audit, suggested_correction):
        return "needs_review"
    return status or None


def human_review_supersedes_ai(
    human_event: dict[str, Any] | None,
    ai_event: dict[str, Any] | None,
) -> bool:
    if not isinstance(human_event, dict) or not isinstance(ai_event, dict):
        return False
    if human_event.get("action") not in HUMAN_SUPERSEDES_AI_ACTIONS:
        return False
    human_at = event_timestamp(human_event)
    ai_at = event_timestamp(ai_event)
    return human_at is not None and ai_at is not None and human_at >= ai_at


def ai_patch_safety_reason(
    candidate: dict[str, Any],
    audit: dict[str, Any] | None,
) -> str | None:
    """Prevent historical advisory rows from exposing unsafe one-click patches.

    This gate protects old events written before the current sparse-result
    guardrails.  It is intentionally stricter than the model status: valid
    scientific abbreviations and translated organism names are source-owned
    until a deterministic rule or official-PDF mismatch is attached.
    """
    if not isinstance(audit, dict):
        return None
    content_values = [str(candidate.get("stem") or "")]
    content_values.extend(
        str(option.get("text") or "")
        for option in candidate.get("options") or []
        if isinstance(option, dict)
    )
    fields = "\n".join(content_values)
    findings = [item for item in audit.get("findings") or [] if isinstance(item, dict)]
    for finding in findings:
        route = str(finding.get("route") or "")
        rule_id = str(finding.get("rule_id") or "").strip()
        if route == "deterministic" and rule_id:
            continue
        field = str(finding.get("field") or finding.get("location") or "")
        before = str(finding.get("before") or finding.get("observed") or "")
        after = str(finding.get("after") or finding.get("suggested") or "")
        if field == "stem":
            field_value = str(candidate.get("stem") or "")
        elif field.startswith("option_"):
            option_key = field[-1].upper()
            field_value = next(
                (
                    str(option.get("text") or "")
                    for option in candidate.get("options") or []
                    if isinstance(option, dict)
                    and str(option.get("key") or "").upper() == option_key
                ),
                "",
            )
        else:
            field_value = fields
        local = "\n".join(
            [
                field_value,
                before,
                after,
            ]
        )
        verified_capsule_exact = any(
            source in field_value
            and (
                after in {"莢膜", target}
                or target in after
                or target in json.dumps(audit.get("suggested_correction") or {}, ensure_ascii=False)
            )
            for source, target in CAPSULE_EXACT_REPLACEMENTS.items()
        )
        if CAPSULE_COMPOUND_RE.search(local) and not verified_capsule_exact:
            return (
                "「莢膜」出現在 Histoplasma capsulatum/組織漿菌完整詞組中；"
                "只有 active exact rule 已確認的完整詞組可一鍵修正；其他變體仍需核對官方 PDF。"
            )
        if not verified_capsule_exact and (
            ABBREVIATED_BINOMIAL_RE.search(local) or LATIN_BINOMIAL_RE.search(local)
        ):
            return "欄位含完整或縮寫拉丁學名；沒有官方 PDF/active exact rule，暫不提供一鍵修正。"
    return None


def ai_suggested_correction(candidate: dict[str, Any], audit: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(audit, dict):
        return None, []
    if ai_patch_safety_reason(candidate, audit):
        return None, []
    if isinstance(audit.get("suggested_correction"), dict):
        normalized = normalized_correction(audit["suggested_correction"])
        if normalized and not correction_changes_candidate(candidate, normalized):
            return None, []
        explicit_changes = audit.get("suggested_changes")
        if isinstance(explicit_changes, list):
            changes = [str(item) for item in explicit_changes if str(item).strip()]
        else:
            changes = ["AI 提供 explicit suggested_correction。"] if normalized else []
        return (normalized or None), changes

    status = effective_ai_audit_status(audit)
    audit_text = " ".join(
        str(part or "")
        for part in [
            audit.get("summary"),
            audit.get("reason"),
            audit.get("recommended_action"),
            json.dumps(audit.get("labels") or [], ensure_ascii=False),
            json.dumps(audit.get("findings") or [], ensure_ascii=False),
        ]
    )
    if status == "pass" and not re.search(r"(ocr|字形|簡|麸|麩|胰岛|肾|氢|去氢|麗胺|麃胺)", audit_text, re.I):
        return None, []

    correction: dict[str, Any] = {}
    changes: list[str] = []
    stem, stem_changes = apply_ai_ocr_replacements(str(candidate.get("stem") or ""))
    if stem_changes and stem != candidate.get("stem"):
        correction["stem"] = stem
        changes.extend(f"題幹：{change}" for change in stem_changes)

    option_rows = []
    option_changed = False
    for option in candidate.get("options") or []:
        if not isinstance(option, dict):
            continue
        copy = dict(option)
        text, option_changes = apply_ai_ocr_replacements(str(copy.get("text") or ""))
        if option_changes and text != copy.get("text"):
            copy["text"] = text
            option_changed = True
            changes.extend(f"選項 {copy.get('key')}: {change}" for change in option_changes)
        option_rows.append(copy)
    if option_changed:
        correction["options"] = option_rows

    return (correction or None), sorted(set(changes))


def compact_ai_lane_results(audit: Any) -> list[dict[str, Any]]:
    """Expose lane telemetry without returning raw model responses to the UI.

    The staging exporter keeps the complete model result for auditability.  A
    browser queue only needs the decision path, latency, context guard and
    evidence request.  In particular, never send ``raw_content`` through the
    dashboard API; it is both noisy and easy to mistake for an approved edit.
    """
    if not isinstance(audit, dict):
        return []
    compact: list[dict[str, Any]] = []
    for row in audit.get("lane_results") or []:
        if not isinstance(row, dict):
            continue
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        model_result = result.get("model_result") if isinstance(result.get("model_result"), dict) else {}
        context_guard = model_result.get("context_guard") if isinstance(model_result.get("context_guard"), dict) else {}
        context_policy = model_result.get("context_policy") if isinstance(model_result.get("context_policy"), dict) else {}
        raw_trace = model_result.get("agentic_trace") if isinstance(model_result.get("agentic_trace"), list) else []
        trace = []
        for trace_row in raw_trace[:4]:
            if not isinstance(trace_row, dict):
                continue
            trace.append(
                {
                    key: trace_row.get(key)
                    for key in ("turn", "latency_ms", "latency_status", "compact_level", "adaptive_action")
                    if key in trace_row
                }
            )
        compact.append(
            {
                "lane": str(row.get("lane") or ""),
                "status": str(row.get("status") or result.get("status") or "unknown"),
                "revision_id": str(row.get("revision_id") or ""),
                "provider": str(row.get("provider") or result.get("provider") or ""),
                "model": str(row.get("model") or result.get("model") or ""),
                "created_at": row.get("created_at"),
                "checked_count": result.get("checked_count"),
                "model_called": bool(result.get("model_called")),
                "model_action": str(result.get("model_action") or ""),
                "finding_codes": [str(value) for value in (result.get("finding_codes") or [])],
                "reason": str(model_result.get("reason") or result.get("reason") or ""),
                "requested_evidence": model_result.get("requested_evidence") or [],
                "proposed_changes": model_result.get("proposed_changes") or [],
                "error": str(model_result.get("error") or result.get("error") or ""),
                "latency_ms": model_result.get("latency_ms"),
                "latency_status": str(model_result.get("latency_status") or ""),
                "tool_turn_count": model_result.get("tool_turn_count"),
                "pixels_sent": model_result.get("pixels_sent"),
                "transport": str(model_result.get("transport") or ""),
                "context_policy": {
                    key: context_policy.get(key)
                    for key in ("hard_limit_tokens", "effective_limit_tokens", "context_extension_used", "slow_threshold_seconds")
                    if key in context_policy
                },
                "agentic_trace": trace,
                "context_guard": {
                    key: context_guard.get(key)
                    for key in (
                        "context_limit_tokens",
                        "safety_margin_tokens",
                        "admitted_input_budget_tokens",
                        "estimated_input_tokens_upper_bound",
                        "total_upper_bound_tokens",
                        "within_budget",
                    )
                    if key in context_guard
                },
            }
        )
    return compact
