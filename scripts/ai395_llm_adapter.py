#!/usr/bin/env python3
"""Bounded advisory adapter for an Ollama endpoint.

The adapter is deliberately independent of PostgreSQL and Review UI. It sends
only one lane packet at a time, keeps the model output advisory, requires
actual image bytes for the visual lane, and returns a normalized result plus
lineage metadata for the staging runner.
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

from ai395_context_guard import DEFAULT_CONTEXT_LIMIT_TOKENS, ContextBudgetExceeded, enforce_payload
from probe_qwen_mlx_tailscale import normalized_base_url


MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
SUPPORTED_IMAGE_MIME = {"image/png", "image/jpeg", "image/webp"}
PROMPT_VERSION = "ai395_lane_advisory_v2_context_guarded"
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
        # Prefer the parser's verified absolute path when it is still present.
        # Older candidate exports put the project-relative path first; joining
        # that value to an image root that is already ``.../20_mineru_output``
        # would duplicate ``國考題資料夾/20_mineru_output`` and falsely report
        # a missing pixel asset.
        raw_path = raw_ref.get("resolved_path") or raw_ref.get("path") or raw_ref.get("relative_path")
        if not raw_path:
            raise PixelsUnavailable("image reference has no path")
        candidate = Path(str(raw_path))
        if not candidate.is_absolute():
            relative_candidate = root / candidate
            if not relative_candidate.is_file() and str(candidate).startswith("國考題資料夾/"):
                # The portable path is project-root relative while the
                # confinement root is the MinerU asset subtree.
                project_candidate = root.parents[1] / candidate
                if project_candidate.is_file():
                    relative_candidate = project_candidate
            candidate = relative_candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise PixelsUnavailable("image path escapes the immutable fixture root") from exc
        if not resolved.is_file():
            raise PixelsUnavailable(f"image asset is missing: {resolved}")
        mime = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        if mime not in SUPPORTED_IMAGE_MIME:
            raise PixelsUnavailable(f"image asset is not a supported raster format: {resolved.name} ({mime})")
        data = resolved.read_bytes()
        if not data:
            raise PixelsUnavailable(f"image asset is empty: {resolved}")
        if len(data) > MAX_IMAGE_BYTES:
            raise PixelsUnavailable(f"image asset exceeds {MAX_IMAGE_BYTES} bytes: {resolved}")
        resolved_refs.append({
            "path": str(resolved),
            "mime_type": mime,
            "bytes": len(data),
            "data_url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}",
        })
    return resolved_refs


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


def build_request(lane: str, packet: dict[str, Any], image_refs: list[dict[str, Any]], model: str, max_tokens: int) -> dict[str, Any]:
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
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是國考題目審核的保守型 advisory auditor。嚴格遵守 lane，不做人工決策。"},
            {"role": "user", "content": content},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
        # Qwen/MLX may otherwise place the entire answer in a reasoning
        # field and leave message.content empty.  Keep thinking disabled for
        # this bounded JSON-only audit call; the response parser still keeps
        # a conservative fallback for providers that ignore this hint.
        "think": False,
        "response_format": {"type": "json_object"},
    }


def build_native_request(openai_request: dict[str, Any], image_refs: list[dict[str, Any]]) -> dict[str, Any]:
    """Translate the bounded request to Ollama's native /api/chat contract.

    The MacBook's Qwen MLX build exposes both /v1 and the native Ollama API,
    but the native endpoint is the reliable path for ``think:false`` and
    JSON-format responses.  Native vision requests carry raw base64 images in
    the message's ``images`` array rather than OpenAI data URLs.
    """

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


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> tuple[dict[str, Any], str, float]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": "Bearer ollama",
            "User-Agent": "ai395-review-local-qwen/1",
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = exc.read(4_000).decode("utf-8", errors="replace")
        raise LLMAdapterError(f"HTTP {exc.code} from Ollama endpoint: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMAdapterError(f"Ollama request failed: {exc}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise LLMAdapterError("Ollama response exceeded the size limit")
    try:
        response_payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LLMAdapterError("Ollama returned non-JSON data") from exc
    if not isinstance(response_payload, dict):
        raise LLMAdapterError("Ollama returned a non-object JSON response")
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
        f"Ollama response has no text content (message_keys={message_keys}; choice_keys={choice_keys})"
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
            raise LLMAdapterError("Ollama content is not a JSON object")
        unique_candidates = {canonical_json(candidate) for candidate in candidates}
        if len(unique_candidates) != 1:
            raise LLMAdapterError("Ollama content contains invalid JSON") from parse_error
        parsed = candidates[0]
    if not isinstance(parsed, dict):
        raise LLMAdapterError("Ollama JSON result is not an object")
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
        "provider": "local_qwen_mlx",
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
    evidence: Any = None,
    compact_level: int = 0,
    context_limit_tokens: int = DEFAULT_CONTEXT_LIMIT_TOKENS,
    context_safety_margin_tokens: int = 8_192,
) -> dict[str, Any]:
    endpoint, _ = normalized_base_url(base_url)
    packet, image_refs = build_packet(
        lane,
        item,
        revised,
        fixture_root,
        evidence=evidence,
        compact_level=compact_level,
    )
    openai_request = build_request(lane, packet, image_refs, model, max_tokens)
    if transport == "ollama_native":
        request_payload = build_native_request(openai_request, image_refs)
        native_endpoint = endpoint[:-3] if endpoint.endswith("/v1") else endpoint
        response_url = f"{native_endpoint}/api/chat"
    elif transport == "ollama_openai_compatible":
        request_payload = openai_request
        response_url = f"{endpoint}/chat/completions"
    else:
        raise LLMAdapterError(f"unsupported Ollama transport: {transport}")
    context_guard = enforce_payload(
        request_payload,
        output_tokens=max_tokens,
        context_limit_tokens=context_limit_tokens,
        safety_margin_tokens=context_safety_margin_tokens,
    )
    response_payload, raw_response, elapsed_ms = _post_json(response_url, request_payload, timeout)
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
    )
