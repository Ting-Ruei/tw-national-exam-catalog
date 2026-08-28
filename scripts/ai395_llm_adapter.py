#!/usr/bin/env python3
"""Bounded advisory adapter for Ollama and OpenAI-compatible endpoints.

The adapter is deliberately independent of PostgreSQL and Review UI. It sends
only one lane packet at a time, keeps the model output advisory, requires
actual image bytes for the visual lane, and returns a normalized result plus
lineage metadata for the staging runner. Provider credentials are supplied
only by the caller at runtime and are never included in returned telemetry.
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ai395_context_guard import DEFAULT_CONTEXT_LIMIT_TOKENS, ContextBudgetExceeded, enforce_payload
from probe_qwen_mlx_tailscale import normalized_base_url


MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_DATA_URL_PREFIX = "data:image/png;base64,"
PROMPT_VERSION = "ai395_lane_advisory_v3_provider_neutral_context_guarded"
FEEDBACK_PROMPT_VERSION = "ai395_guardrail_feedback_v1_provider_neutral_context_guarded"
SUPPORTED_REASONING_EFFORTS = {"low", "high", "max"}
ALLOWED_EVIDENCE_KINDS = {
    "question_markdown",
    "answer_markdown",
    "adjacent_questions",
    "pdf_reference",
    "image_manifest",
}
MAX_PACKET_TEXT_CHARS = 12_000
MAX_EVIDENCE_TEXT_CHARS = 16_000


class LLMAdapterError(RuntimeError):
    """Raised when a provider call or response contract fails."""


class PixelsUnavailable(LLMAdapterError):
    """Raised when a visual task has no supported raster pixels to send."""


def normalized_openai_base_url(raw: str, *, allow_insecure_http: bool = False) -> tuple[str, str]:
    """Validate a generic OpenAI-compatible base URL.

    LiteLLM is normally fronted by HTTPS.  Loopback HTTP is allowed for a
    same-host development server; any other HTTP endpoint needs an explicit
    opt-in so a bearer key is not accidentally sent over the network.
    """

    value = str(raw or "").strip().rstrip("/")
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.username or parsed.password:
        raise LLMAdapterError("OpenAI-compatible endpoint must not contain embedded credentials")
    try:
        host.encode("ascii")
    except UnicodeEncodeError as exc:
        raise LLMAdapterError("OpenAI-compatible endpoint contains a non-ASCII hostname") from exc
    if parsed.scheme not in {"http", "https"} or not host:
        raise LLMAdapterError("OpenAI-compatible endpoint must be an http(s) URL")
    if parsed.query or parsed.fragment:
        raise LLMAdapterError("OpenAI-compatible endpoint must not contain a query or fragment")
    if parsed.scheme == "http" and host not in {"127.0.0.1", "localhost", "::1"} and not allow_insecure_http:
        raise LLMAdapterError("remote OpenAI-compatible endpoint must use HTTPS")
    path = parsed.path.rstrip("/")
    if path not in {"", "/v1"}:
        raise LLMAdapterError("OpenAI-compatible endpoint path must be empty or /v1")
    path = "/v1"
    endpoint_class = "localhost" if host in {"127.0.0.1", "localhost", "::1"} else "https_external"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")), endpoint_class


LANE_INSTRUCTIONS: dict[str, str] = {
    "text_evidence": (
        "檢查已擷取文字的可觀察轉寫問題、簡繁字形、標點或明顯 OCR 殘留。"
        "不要重新 OCR、不要改寫官方原文、不要解題。沒有來源證據時只能回 unclear。"
    ),
    "notation": (
        "檢查上下標、希臘字母、公式、單位與 markup 是否疑似轉寫錯誤。"
        "不要憑記憶改公式；沒有足夠證據時只能回 unclear。"
    ),
    "group": (
        "檢查題組共同題幹、承上題標記與範圍線索。只能提出 group dependency，"
        "不能確認題組、不能寫入 group_ref、不能改題目文字。"
    ),
    "vision": (
        "只依照實際附上的 image pixels 判斷圖片是否存在、是否可能裁切錯誤或缺圖。"
        "沒有 pixels 就必須回 unclear；不能只依 image_refs 假裝看過圖片。"
    ),
    "answer": (
        "只檢查提供的答案標記或答案來源線索是否需要人工核對。不要解題、"
        "不要擅自改答案；送分、多答案、# 或答案來源不足時回 unclear。"
    ),
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _truncate_text(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 24)] + "\n[…truncated by context policy…]"


def _compact_metadata(metadata: Any, lane: str, compact_level: int) -> dict[str, Any]:
    """Allow-list metadata; file paths and raw document blocks never go to LLM."""

    if not isinstance(metadata, dict):
        return {}
    common_keys = (
        "group_name",
        "normalized_category_name",
        "normalized_subject_name",
        "year",
        "parser_version",
        "parser_status",
        "ai395_scope_tags",
    )
    lane_keys = {
        "text_evidence": ("text_issue_codes",),
        "notation": ("notation_issue_codes", "stem_markup_status"),
        "group": ("group_boundary", "group_type", "group_sequence_no"),
        "vision": ("visual_expected", "visual_cue", "crop_status"),
        "answer": ("answer_mode", "answer_role_primary", "is_special_correction"),
    }
    result: dict[str, Any] = {}
    for key in (*common_keys, *lane_keys.get(lane, ())):
        value = metadata.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, list):
            result[key] = [str(item)[:120] for item in value[:16]]
        elif isinstance(value, (bool, int, float)):
            result[key] = value
        else:
            result[key] = _truncate_text(value, 600 if compact_level == 0 else 240)
    return result


def _compact_options(options: Any, lane: str) -> list[dict[str, Any]]:
    if not isinstance(options, list):
        return []
    compacted: list[dict[str, Any]] = []
    for option in options[:8]:
        if not isinstance(option, dict):
            continue
        row: dict[str, Any] = {
            "key": str(option.get("key") or "")[:16],
            "text": _truncate_text(option.get("text"), 3000),
        }
        if option.get("markup"):
            row["markup"] = _truncate_text(option.get("markup"), 2000)
        image = option.get("image")
        if isinstance(image, dict):
            row["has_image"] = True
            row["image_bytes"] = int(image.get("bytes") or 0)
        if lane == "vision" and option.get("image") is not None:
            row["visual_slot"] = True
        compacted.append(row)
    return compacted


def _compact_evidence(evidence: Any, compact_level: int) -> list[dict[str, Any]]:
    if not evidence:
        return []
    rows = evidence if isinstance(evidence, list) else [evidence]
    remaining = 8_000 if compact_level else MAX_EVIDENCE_TEXT_CHARS
    compacted: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or remaining <= 0:
            continue
        item: dict[str, Any] = {}
        for key in ("kind", "source", "status", "locator", "page", "family", "tool"):
            value = row.get(key)
            if value is not None and value != "":
                item[key] = _truncate_text(value, 300)
        text = _truncate_text(row.get("text"), min(remaining, 6_000 if compact_level else 8_000))
        if text:
            item["text"] = text
            remaining -= len(text)
        if row.get("error"):
            item["error"] = _truncate_text(row.get("error"), 500)
        if item:
            compacted.append(item)
    return compacted


def _image_refs(item: dict[str, Any], fixture_root: Path) -> list[dict[str, Any]]:
    root = fixture_root.resolve()
    resolved_refs: list[dict[str, Any]] = []
    for raw_ref in item.get("image_refs") or []:
        if not isinstance(raw_ref, dict):
            raise PixelsUnavailable("image reference is not an object")
        raw_paths = [
            raw_ref.get("resolved_path"),
            raw_ref.get("path"),
            raw_ref.get("relative_path"),
            raw_ref.get("raw_ref"),
        ]
        candidates: list[Path] = []
        for raw_path in raw_paths:
            if not raw_path:
                continue
            raw_value = str(raw_path)
            candidate = Path(raw_value).expanduser()
            if candidate.is_absolute():
                candidates.append(candidate)
            else:
                candidates.append(root / candidate)
            # Candidate exports made on another machine can retain an
            # absolute path such as /Users/tim/.../國考題資料夾/20_mineru_output/.
            # Rebind the portable suffix to the immutable runtime image root.
            portable_markers = (
                "國考題資料夾/20_mineru_output/",
                "20_mineru_output/",
            )
            for marker in portable_markers:
                if marker in raw_value:
                    candidates.append(root / raw_value.split(marker, 1)[1])
                    break
        if not candidates:
            raise PixelsUnavailable("image reference has no path")
        resolved = None
        escaped = False
        seen_candidates: set[str] = set()
        for candidate in candidates:
            candidate_resolved = candidate.resolve()
            candidate_key = str(candidate_resolved)
            if candidate_key in seen_candidates:
                continue
            seen_candidates.add(candidate_key)
            try:
                candidate_resolved.relative_to(root)
            except ValueError:
                escaped = True
                continue
            if candidate_resolved.is_file():
                resolved = candidate_resolved
                break
        if resolved is None:
            if escaped:
                raise PixelsUnavailable("image path escapes the immutable fixture root")
            raise PixelsUnavailable(f"image asset is missing under {root}")
        data = resolved.read_bytes()
        if not data:
            raise PixelsUnavailable(f"image asset is empty: {resolved}")
        # GLM's OpenAI-compatible vision route is deliberately normalized to
        # one transport contract. Do not label JPEG/WebP bytes as PNG: if a
        # future ingest source emits another raster format, add an explicit
        # deterministic conversion stage before this boundary.
        if not data.startswith(PNG_SIGNATURE):
            mime = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
            raise PixelsUnavailable(
                "vision asset must contain real PNG bytes for the GLM route; "
                f"got {resolved.name} ({mime}); convert upstream before sending"
            )
        if len(data) > MAX_IMAGE_BYTES:
            raise PixelsUnavailable(f"image asset exceeds {MAX_IMAGE_BYTES} bytes: {resolved}")
        resolved_refs.append({
            "path": str(resolved),
            "mime_type": "image/png",
            "bytes": len(data),
            "data_url": f"{PNG_DATA_URL_PREFIX}{base64.b64encode(data).decode('ascii')}",
        })
    return resolved_refs


def _validated_png_image_refs(image_refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fail closed if an OpenAI-compatible caller supplies a non-PNG URL."""

    validated: list[dict[str, Any]] = []
    for ref in image_refs:
        if not isinstance(ref, dict):
            raise PixelsUnavailable("image reference is not an object")
        data_url = str(ref.get("data_url") or "")
        if not data_url.startswith(PNG_DATA_URL_PREFIX):
            raise PixelsUnavailable("OpenAI-compatible vision images must use data:image/png;base64,...")
        encoded = data_url[len(PNG_DATA_URL_PREFIX):]
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise PixelsUnavailable("PNG data URL has invalid base64") from exc
        if not data.startswith(PNG_SIGNATURE):
            raise PixelsUnavailable("PNG data URL does not contain PNG bytes")
        if len(data) > MAX_IMAGE_BYTES:
            raise PixelsUnavailable(f"PNG data URL exceeds {MAX_IMAGE_BYTES} bytes")
        copy = dict(ref)
        copy["data_url"] = data_url
        copy["mime_type"] = "image/png"
        copy["bytes"] = len(data)
        validated.append(copy)
    return validated


def build_packet(
    lane: str,
    item: dict[str, Any],
    revised: dict[str, Any],
    fixture_root: Path,
    *,
    evidence: Any = None,
    compact_level: int = 0,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if lane not in LANE_INSTRUCTIONS:
        raise LLMAdapterError(f"unsupported lane: {lane}")
    packet: dict[str, Any] = {
        "candidate_key": str(revised.get("candidate_key") or item.get("candidate_key")),
        "lane": lane,
        "question_number": str(revised.get("question_number") or item.get("question_number")),
        "stem": _truncate_text(revised.get("stem"), 12_000),
        "options": _compact_options(revised.get("options") or [], lane),
        "metadata": _compact_metadata(revised.get("metadata") or {}, lane, compact_level),
    }
    if revised.get("stem_markup"):
        packet["stem_markup"] = _truncate_text(revised.get("stem_markup"), 4_000)
    if revised.get("group_ref") and lane == "group":
        packet["group_ref"] = revised.get("group_ref")
    if lane == "answer":
        packet["answer"] = revised.get("answer")
        answer_payload = revised.get("answer_payload")
        if isinstance(answer_payload, dict):
            packet["answer_payload"] = {
                key: value
                for key, value in answer_payload.items()
                if key in {"accepted_values", "answer", "raw_answer", "is_special_correction"}
            }
    image_refs = _image_refs(revised, fixture_root) if lane == "vision" else []
    if lane == "vision" and not image_refs:
        raise PixelsUnavailable("vision lane requires at least one supported raster image")
    if lane == "vision":
        packet["image_slots"] = [
            {"slot": index + 1, "bytes": int(ref.get("bytes") or 0), "mime_type": ref.get("mime_type")}
            for index, ref in enumerate(image_refs)
        ]
    compacted_evidence = _compact_evidence(evidence, compact_level)
    if compacted_evidence:
        packet["evidence"] = compacted_evidence
    return packet, image_refs


def build_request(
    lane: str,
    packet: dict[str, Any],
    image_refs: list[dict[str, Any]],
    model: str,
    max_tokens: int,
    *,
    reasoning_effort: str | None = None,
    clear_thinking: bool | None = None,
) -> dict[str, Any]:
    image_refs = _validated_png_image_refs(image_refs)
    schema_hint = {
        "status": "pass|finding|unclear",
        "confidence": "number 0..1",
        "finding_codes": ["short_code"],
        "reason": "observable reason in 160 characters or fewer",
        "proposed_changes": [],
        "requested_evidence": [
            {
                "kind": "question_markdown|answer_markdown|adjacent_questions|pdf_reference|image_manifest",
                "reason": "why this bounded evidence is needed",
            }
        ],
    }
    user_text = (
        f"LANE_INSTRUCTION={LANE_INSTRUCTIONS[lane]}\n"
        "Return ONLY one JSON object. Do not output markdown. The result is advisory.\n"
        "If the packet is insufficient, request only allow-listed bounded evidence in "
        "requested_evidence; do not guess, do not request arbitrary files, commands, secrets, or full documents.\n"
        f"JSON_SHAPE={canonical_json(schema_hint)}\n"
        f"PACKET={canonical_json(packet)}"
    )
    if image_refs:
        content: str | list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        content.extend({"type": "image_url", "image_url": {"url": ref["data_url"]}} for ref in image_refs)
    else:
        content = user_text
    request = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是國考題目審核的保守型 advisory auditor。嚴格遵守 lane，不做人工決策。"},
            {"role": "user", "content": content},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    if reasoning_effort is not None:
        normalized_effort = str(reasoning_effort).strip().lower()
        if normalized_effort not in SUPPORTED_REASONING_EFFORTS:
            raise LLMAdapterError(f"unsupported reasoning_effort: {reasoning_effort}")
        request["reasoning_effort"] = normalized_effort
    if clear_thinking is not None:
        request["thinking"] = {
            "type": "enabled",
            "clear_thinking": bool(clear_thinking),
        }
    return request


def build_feedback_request(
    feedback_packet: dict[str, Any],
    image_refs: list[dict[str, Any]],
    model: str,
    max_tokens: int,
    *,
    reasoning_effort: str | None = None,
    clear_thinking: bool | None = None,
) -> dict[str, Any]:
    """Build the bounded model request for a before/after guardrail example."""

    image_refs = _validated_png_image_refs(image_refs)

    schema_hint = {
        "status": "candidate|no_generalization|unclear",
        "guardrail_type": "exact_ocr_rule|notation_rule|format_rule|crop_strategy|answer_rule|parser_route|skill_note|no_generalization",
        "confidence": "number 0..1",
        "rule_proposal": "object or null; observed/proposed only",
        "skill_update_candidate": "object or null; prose patch only, never a command",
        "positive_examples": [],
        "negative_controls": [],
        "do_not_generalize": [],
        "rationale": "short observable explanation",
        "requested_evidence": [],
    }
    user_text = (
        "你是國考題目審核的保守型護欄整理器。這是一個已發生的前後修正案例。\n"
        "只判斷是否能形成可重用的 checklist、rule proposal 或 Skill note；不要解題、不要改寫題目、"
        "不要宣告 active、不要執行任何工具或寫檔。證據不足時回 no_generalization。\n"
        "rule_proposal 只能是 observed/proposed，skill_update_candidate 只能是文字草稿；正式啟用一定要人工核准、"
        "負向控制與 gold regression。Return ONLY one JSON object.\n"
        f"JSON_SHAPE={canonical_json(schema_hint)}\n"
        f"FEEDBACK_PACKET={canonical_json(feedback_packet)}"
    )
    if image_refs:
        content: str | list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        content.extend({"type": "image_url", "image_url": {"url": ref["data_url"]}} for ref in image_refs)
    else:
        content = user_text
    request = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你只產生護欄候選，不具有正式規則、Skill 或題目寫入權限。"},
            {"role": "user", "content": content},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    if reasoning_effort is not None:
        normalized_effort = str(reasoning_effort).strip().lower()
        if normalized_effort not in SUPPORTED_REASONING_EFFORTS:
            raise LLMAdapterError(f"unsupported reasoning_effort: {reasoning_effort}")
        request["reasoning_effort"] = normalized_effort
    if clear_thinking is not None:
        request["thinking"] = {
            "type": "enabled",
            "clear_thinking": bool(clear_thinking),
        }
    return request


def build_native_request(openai_request: dict[str, Any], image_refs: list[dict[str, Any]]) -> dict[str, Any]:
    """Translate the bounded request to Ollama's native /api/chat contract.

    The MacBook's Qwen MLX build exposes both /v1 and the native Ollama API,
    but the native endpoint is the reliable path for ``think:false`` and
    JSON-format responses.  Native vision requests carry raw base64 images in
    the message's ``images`` array rather than OpenAI data URLs.
    """

    image_refs = _validated_png_image_refs(image_refs)
    messages = openai_request.get("messages") or []
    native_messages: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, list):
            text_parts = [str(part.get("text")) for part in content if isinstance(part, dict) and part.get("type") == "text"]
            content = "\n".join(part for part in text_parts if part)
        native_message: dict[str, Any] = {
            "role": str(message.get("role") or "user"),
            "content": str(content or ""),
        }
        if native_message["role"] == "user" and image_refs:
            native_message["images"] = [ref["data_url"].split(",", 1)[1] for ref in image_refs]
        native_messages.append(native_message)
    return {
        "model": openai_request["model"],
        "messages": native_messages,
        "stream": False,
        "think": False,
        "format": "json",
        "options": {
            "temperature": openai_request.get("temperature", 0),
            "num_predict": openai_request.get("max_tokens", 512),
        },
    }


def _post_json(
    url: str,
    payload: dict[str, Any],
    timeout: float,
    *,
    provider: str = "ollama",
    api_key: str | None = None,
) -> tuple[dict[str, Any], str, float]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "ai395-review-llm-adapter/1",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    elif provider in {"local_qwen_mlx", "ollama"}:
        # Ollama's OpenAI-compatible endpoint ignores this placeholder.  It
        # keeps the local adapter contract compatible with OpenAI clients.
        headers["Authorization"] = "Bearer ollama"
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = exc.read(4_000).decode("utf-8", errors="replace")
        raise LLMAdapterError(f"HTTP {exc.code} from {provider} endpoint: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMAdapterError(f"{provider} request failed: {exc}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise LLMAdapterError(f"{provider} response exceeded the size limit")
    try:
        response_payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LLMAdapterError(f"{provider} returned non-JSON data") from exc
    if not isinstance(response_payload, dict):
        raise LLMAdapterError(f"{provider} returned a non-object JSON response")
    raw_text = raw.decode("utf-8", errors="replace")
    return response_payload, raw_text, round((time.monotonic() - started) * 1000, 1)


def _text_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for entry in value:
            if isinstance(entry, str):
                parts.append(entry)
            elif isinstance(entry, dict):
                for key in ("text", "content", "value"):
                    text = _text_value(entry.get(key))
                    if text:
                        parts.append(text)
                        break
        return "\n".join(part for part in parts if part).strip()
    return ""


def _extract_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") or {}
        choice_keys = ",".join(sorted(str(key) for key in choice)) or "none"
    else:
        # Ollama's native /api/chat response has a top-level message object.
        choice = payload
        message = payload.get("message") or {}
        choice_keys = ",".join(sorted(str(key) for key in payload)) or "none"
    if not isinstance(message, dict):
        message = {}
    # OpenAI-compatible servers differ on whether multimodal content is a
    # string or a list of text blocks.  Ollama/Qwen builds can also expose a
    # reasoning field even when the final content is empty.
    for key in ("content", "reasoning_content", "reasoning", "thinking"):
        content = _text_value(message.get(key))
        if content:
            return content
    content = _text_value(choice.get("text"))
    if content:
        return content
    message_keys = ",".join(sorted(str(key) for key in message)) or "none"
    raise LLMAdapterError(
        f"provider response has no text content (message_keys={message_keys}; choice_keys={choice_keys})"
    )


def _parse_json_object(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as parse_error:
        # Qwen sometimes obeys the JSON contract but adds a short preface or
        # repeats the same object after a fenced answer.  Recover only from
        # independently decodable root objects; never repair malformed JSON
        # strings or choose between contradictory objects.
        decoder = json.JSONDecoder()
        candidates: list[dict[str, Any]] = []
        cursor = 0
        while cursor < len(cleaned):
            start = cleaned.find("{", cursor)
            if start < 0:
                break
            try:
                candidate, end = decoder.raw_decode(cleaned, start)
            except json.JSONDecodeError:
                cursor = start + 1
                continue
            if isinstance(candidate, dict):
                candidates.append(candidate)
                cursor = end
            else:
                cursor = start + 1
        if not candidates:
            raise LLMAdapterError("provider content is not a JSON object")
        unique_candidates = {canonical_json(candidate) for candidate in candidates}
        if len(unique_candidates) != 1:
            raise LLMAdapterError("provider content contains invalid JSON") from parse_error
        parsed = candidates[0]
    if not isinstance(parsed, dict):
        raise LLMAdapterError("provider JSON result is not an object")
    return parsed


def _normalize_evidence_requests(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    requests: list[dict[str, Any]] = []
    for row in value[:4]:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "").strip()
        if kind not in ALLOWED_EVIDENCE_KINDS:
            continue
        request = {
            "kind": kind,
            "reason": _truncate_text(row.get("reason"), 240),
        }
        if row.get("max_chars") is not None:
            try:
                request["max_chars"] = max(256, min(8_000, int(row["max_chars"])))
            except (TypeError, ValueError):
                pass
        requests.append(request)
    return requests


def _normalize_result(
    payload: dict[str, Any],
    content: str,
    raw_response: str,
    *,
    lane: str,
    model: str,
    endpoint: str,
    transport: str,
    elapsed_ms: float,
    pixels_sent: int,
    context_guard: dict[str, Any],
    compact_level: int,
    provider: str,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    parsed = _parse_json_object(content)
    status = str(parsed.get("status") or "unclear").lower()
    if status not in {"pass", "finding", "unclear"}:
        status = "unclear"
    try:
        confidence = float(parsed.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    codes = parsed.get("finding_codes") or []
    if not isinstance(codes, list):
        codes = [str(codes)]
    finding_codes = [str(code)[:80] for code in codes if str(code).strip()]
    proposals = parsed.get("proposed_changes") or []
    if not isinstance(proposals, list):
        proposals = [proposals]
    requested_evidence = _normalize_evidence_requests(parsed.get("requested_evidence"))
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    return {
        "status": status,
        "confidence": confidence,
        "finding_codes": finding_codes,
        "reason": str(parsed.get("reason") or "未提供可觀察理由")[:160],
        "proposed_changes": proposals,
        "requested_evidence": requested_evidence,
        "advisory_only": True,
        "materialize": False,
        "provider": provider,
        "model": model,
        "lane": lane,
        "endpoint": endpoint,
        "transport": transport,
        "prompt_version": PROMPT_VERSION,
        "elapsed_ms": elapsed_ms,
        "usage": usage,
        "pixels_sent": pixels_sent,
        "context_guard": context_guard,
        "compact_level": compact_level,
        "reasoning_effort": reasoning_effort,
        "raw_response_sha256": sha256_text(raw_response),
        "raw_content": content,
    }


def call_lane(
    *,
    base_url: str,
    model: str,
    lane: str,
    item: dict[str, Any],
    revised: dict[str, Any],
    fixture_root: Path,
    timeout: float = 120.0,
    max_tokens: int = 512,
    transport: str = "ollama_native",
    provider: str = "local_qwen_mlx",
    api_key: str | None = None,
    reasoning_effort: str | None = None,
    clear_thinking: bool | None = None,
    allow_insecure_http: bool = False,
    evidence: Any = None,
    compact_level: int = 0,
    context_limit_tokens: int = DEFAULT_CONTEXT_LIMIT_TOKENS,
    context_safety_margin_tokens: int = 8_192,
) -> dict[str, Any]:
    if transport in {"ollama_native", "ollama_openai_compatible"}:
        endpoint, _ = normalized_base_url(base_url)
    elif transport in {"openai_compatible", "openai_chat_completions", "litellm_openai_compatible"}:
        endpoint, _ = normalized_openai_base_url(base_url, allow_insecure_http=allow_insecure_http)
    else:
        raise LLMAdapterError(f"unsupported provider transport: {transport}")
    packet, image_refs = build_packet(
        lane,
        item,
        revised,
        fixture_root,
        evidence=evidence,
        compact_level=compact_level,
    )
    openai_request = build_request(
        lane,
        packet,
        image_refs,
        model,
        max_tokens,
        reasoning_effort=reasoning_effort,
        clear_thinking=clear_thinking,
    )
    if transport == "ollama_native":
        request_payload = build_native_request(openai_request, image_refs)
        native_endpoint = endpoint[:-3] if endpoint.endswith("/v1") else endpoint
        response_url = f"{native_endpoint}/api/chat"
    elif transport == "ollama_openai_compatible":
        request_payload = openai_request
        response_url = f"{endpoint}/chat/completions"
    else:  # OpenAI-compatible LiteLLM or another explicitly configured gateway.
        request_payload = openai_request
        response_url = f"{endpoint}/chat/completions"
    context_guard = enforce_payload(
        request_payload,
        output_tokens=max_tokens,
        context_limit_tokens=context_limit_tokens,
        safety_margin_tokens=context_safety_margin_tokens,
    )
    response_payload, raw_response, elapsed_ms = _post_json(
        response_url,
        request_payload,
        timeout,
        provider=provider,
        api_key=api_key,
    )
    content = _extract_content(response_payload)
    return _normalize_result(
        response_payload,
        content,
        raw_response,
        lane=lane,
        model=model,
        endpoint=response_url,
        transport=transport,
        elapsed_ms=elapsed_ms,
        pixels_sent=len(image_refs),
        context_guard=context_guard,
        compact_level=compact_level,
        provider=provider,
        reasoning_effort=reasoning_effort,
    )


def call_feedback(
    *,
    base_url: str,
    model: str,
    feedback_packet: dict[str, Any],
    image_refs: list[dict[str, Any]] | None = None,
    timeout: float = 120.0,
    max_tokens: int = 768,
    transport: str = "openai_chat_completions",
    provider: str = "litellm_glm",
    api_key: str | None = None,
    reasoning_effort: str | None = None,
    clear_thinking: bool | None = None,
    allow_insecure_http: bool = False,
    context_limit_tokens: int = DEFAULT_CONTEXT_LIMIT_TOKENS,
    context_safety_margin_tokens: int = 8_192,
) -> dict[str, Any]:
    """Send one bounded correction example for guardrail curation.

    The response is intentionally not a question revision.  The caller must
    validate and store it as an advisory guardrail candidate before any owner
    can promote it to a rule or Skill note.
    """

    if transport in {"ollama_native", "ollama_openai_compatible"}:
        endpoint, _ = normalized_base_url(base_url)
    elif transport in {"openai_compatible", "openai_chat_completions", "litellm_openai_compatible"}:
        endpoint, _ = normalized_openai_base_url(base_url, allow_insecure_http=allow_insecure_http)
    else:
        raise LLMAdapterError(f"unsupported provider transport: {transport}")
    refs = image_refs or []
    request = build_feedback_request(
        feedback_packet,
        refs,
        model,
        max_tokens,
        reasoning_effort=reasoning_effort,
        clear_thinking=clear_thinking,
    )
    if transport == "ollama_native":
        request_payload = build_native_request(request, refs)
        native_endpoint = endpoint[:-3] if endpoint.endswith("/v1") else endpoint
        response_url = f"{native_endpoint}/api/chat"
    else:
        request_payload = request
        response_url = f"{endpoint}/chat/completions"
    context_guard = enforce_payload(
        request_payload,
        output_tokens=max_tokens,
        context_limit_tokens=context_limit_tokens,
        safety_margin_tokens=context_safety_margin_tokens,
    )
    response_payload, raw_response, elapsed_ms = _post_json(
        response_url,
        request_payload,
        timeout,
        provider=provider,
        api_key=api_key,
    )
    content = _extract_content(response_payload)
    parsed = _parse_json_object(content)
    status = str(parsed.get("status") or "unclear").strip().lower()
    if status not in {"candidate", "no_generalization", "unclear"}:
        status = "unclear"
    try:
        confidence = float(parsed.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    guardrail_type = str(parsed.get("guardrail_type") or "no_generalization").strip()
    if guardrail_type not in {
        "exact_ocr_rule",
        "notation_rule",
        "format_rule",
        "crop_strategy",
        "answer_rule",
        "parser_route",
        "skill_note",
        "no_generalization",
    }:
        guardrail_type = "no_generalization"
    rule_proposal = parsed.get("rule_proposal") if isinstance(parsed.get("rule_proposal"), dict) else None
    skill_update_candidate = parsed.get("skill_update_candidate") if isinstance(parsed.get("skill_update_candidate"), dict) else None
    if rule_proposal and str(rule_proposal.get("status") or "").lower() in {"active", "verified", "approved"}:
        # A provider that ignores the prompt is fail-closed.  Keep the raw
        # response hash for diagnosis but do not pass an active proposal on.
        status = "unclear"
        rule_proposal = None
    usage = response_payload.get("usage") if isinstance(response_payload.get("usage"), dict) else {}
    return {
        "status": status,
        "guardrail_type": guardrail_type,
        "confidence": confidence,
        "rule_proposal": rule_proposal,
        "skill_update_candidate": skill_update_candidate,
        "positive_examples": parsed.get("positive_examples") if isinstance(parsed.get("positive_examples"), list) else [],
        "negative_controls": parsed.get("negative_controls") if isinstance(parsed.get("negative_controls"), list) else [],
        "do_not_generalize": parsed.get("do_not_generalize") if isinstance(parsed.get("do_not_generalize"), list) else [],
        "rationale": str(parsed.get("rationale") or "未提供可觀察理由")[:2_000],
        "requested_evidence": _normalize_evidence_requests(parsed.get("requested_evidence")),
        "advisory_only": True,
        "materialize": False,
        "provider": provider,
        "model": model,
        "endpoint": response_url,
        "transport": transport,
        "prompt_version": FEEDBACK_PROMPT_VERSION,
        "elapsed_ms": elapsed_ms,
        "usage": usage,
        "pixels_sent": len(refs),
        "context_guard": context_guard,
        "raw_response_sha256": sha256_text(raw_response),
        "raw_content": content,
    }
