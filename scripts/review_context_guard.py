#!/usr/bin/env python3
"""Bounded context admission checks for local model requests.

No tokenizer is assumed. Text is counted as UTF-8 JSON bytes, while images use
an explicit pixel-derived estimate after their dimensions are decoded. The
image estimate is conservative policy, not a guarantee about any provider's
actual vision-tokenization; callers must record it as an estimate.
"""

from __future__ import annotations

import base64
import json
import math
import re
import struct
from typing import Any


# MacBook Qwen staging profile: keep a deliberate margin below the model's
# advertised context. The current local runtime limit is configured
# separately and is not changed by this staging default.
DEFAULT_CONTEXT_LIMIT_TOKENS = 196_608
DEFAULT_SAFETY_MARGIN_TOKENS = 8_192

# A bounded, provider-neutral vision policy. The estimate deliberately does
# not use compressed bytes: a tiny solid PNG can represent many pixels.
MAX_VISION_IMAGE_DIMENSION = 8_192
MAX_VISION_IMAGE_PIXELS = 4_000_000
VISION_PIXELS_PER_ESTIMATED_TOKEN = 256
_DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;,]+);base64,(?P<data>[A-Za-z0-9+/=]*)$")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ContextBudgetExceeded(ValueError):
    """Raised before transport when a request cannot pass context admission."""

    def __init__(self, details: dict[str, Any]):
        self.details = details
        super().__init__(
            "context budget exceeded: "
            f"estimated_upper_bound={details.get('total_estimated_tokens')} "
            f"admitted={details.get('admitted_input_budget_tokens')}"
        )


class VisionPayloadError(ValueError):
    """Raised when a vision payload cannot satisfy the bounded image policy."""


def _decode_base64(value: str, *, label: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise VisionPayloadError(f"{label} is not valid base64") from exc


def _png_dimensions(data: bytes, *, label: str) -> tuple[int, int]:
    if not data.startswith(_PNG_SIGNATURE) or len(data) < 24 or data[12:16] != b"IHDR":
        raise VisionPayloadError(f"{label} must contain a PNG with an IHDR dimension header")
    width, height = struct.unpack(">II", data[16:24])
    if width <= 0 or height <= 0:
        raise VisionPayloadError(f"{label} has invalid image dimensions")
    return width, height


def _image_info(value: Any, *, native: bool = False) -> dict[str, int] | None:
    if not isinstance(value, str):
        return None
    mime = "image/png"
    encoded = value
    if not native:
        match = _DATA_URL_RE.fullmatch(value)
        if not match or not match.group("mime").startswith("image/"):
            return None
        mime = match.group("mime")
        encoded = match.group("data")
    data = _decode_base64(encoded, label="vision image")
    if mime != "image/png":
        raise VisionPayloadError(f"unsupported vision image MIME type: {mime}")
    width, height = _png_dimensions(data, label="vision image")
    pixels = width * height
    if width > MAX_VISION_IMAGE_DIMENSION or height > MAX_VISION_IMAGE_DIMENSION:
        raise VisionPayloadError(
            f"vision image dimensions {width}x{height} exceed {MAX_VISION_IMAGE_DIMENSION}"
        )
    if pixels > MAX_VISION_IMAGE_PIXELS:
        raise VisionPayloadError(
            f"vision image has {pixels} pixels; maximum is {MAX_VISION_IMAGE_PIXELS}"
        )
    estimated_tokens = math.ceil(pixels / VISION_PIXELS_PER_ESTIMATED_TOKEN)
    return {
        "width": width,
        "height": height,
        "pixels": pixels,
        "bytes": len(data),
        "estimated_tokens": estimated_tokens,
    }


def _payload_for_text_accounting(
    value: Any,
    images: list[dict[str, int]],
    *,
    in_native_images: bool = False,
) -> Any:
    if in_native_images and isinstance(value, str):
        info = _image_info(value, native=True)
        assert info is not None
        images.append(info)
        return (
            f"[vision-image:{info['width']}x{info['height']};"
            f"pixels:{info['pixels']};estimated-tokens:{info['estimated_tokens']}]"
        )
    info = _image_info(value)
    if info is not None:
        images.append(info)
        match = _DATA_URL_RE.fullmatch(value)
        assert match is not None
        return (
            f"data:{match.group('mime')};base64,[vision-image:{info['width']}x{info['height']};"
            f"estimated-tokens:{info['estimated_tokens']}]"
        )
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            result[key] = _payload_for_text_accounting(
                item,
                images,
                in_native_images=(key == "images"),
            )
        return result
    if isinstance(value, list):
        return [
            _payload_for_text_accounting(item, images, in_native_images=in_native_images)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _payload_for_text_accounting(item, images, in_native_images=in_native_images)
            for item in value
        )
    return value


def payload_size_breakdown(payload: Any) -> dict[str, int]:
    """Return text bytes and bounded dimension-derived vision estimates."""

    images: list[dict[str, int]] = []
    text_payload = _payload_for_text_accounting(payload, images)
    text_bytes = len(json.dumps(text_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    vision_estimated_tokens = sum(image["estimated_tokens"] for image in images)
    return {
        "text_bytes": text_bytes,
        # Retained as diagnostics only; compressed bytes are not used as tokens.
        "vision_bytes": sum(image["bytes"] for image in images),
        "vision_count": len(images),
        "vision_pixels": sum(image["pixels"] for image in images),
        "vision_estimated_tokens": vision_estimated_tokens,
        "accounted_input_tokens": text_bytes + vision_estimated_tokens,
    }


def serialized_upper_bound_bytes(payload: Any) -> int:
    """Compatibility helper returning the conservative accounted size."""

    return payload_size_breakdown(payload)["accounted_input_tokens"]


def inspect_payload(
    payload: Any,
    *,
    output_tokens: int,
    context_limit_tokens: int = DEFAULT_CONTEXT_LIMIT_TOKENS,
    safety_margin_tokens: int = DEFAULT_SAFETY_MARGIN_TOKENS,
    hard_limit_tokens: int | None = None,
    allow_context_extension: bool = False,
) -> dict[str, Any]:
    """Inspect a payload using conservative text plus vision estimates."""

    if context_limit_tokens <= 0:
        raise ValueError("context_limit_tokens must be positive")
    configured_hard_limit = context_limit_tokens if hard_limit_tokens is None else hard_limit_tokens
    if configured_hard_limit <= 0:
        raise ValueError("hard_limit_tokens must be positive")
    if safety_margin_tokens < 0 or safety_margin_tokens >= configured_hard_limit:
        raise ValueError("safety_margin_tokens must be >= 0 and below the hard context limit")
    if output_tokens < 0:
        raise ValueError("output_tokens must be >= 0")
    effective_limit = context_limit_tokens
    extension_requested = context_limit_tokens > configured_hard_limit
    if extension_requested and not allow_context_extension:
        effective_limit = configured_hard_limit
    breakdown = payload_size_breakdown(payload)
    estimated_input_tokens = breakdown["accounted_input_tokens"]
    admitted_input_budget = effective_limit - safety_margin_tokens - output_tokens
    total_estimated_tokens = estimated_input_tokens + output_tokens
    return {
        "method": "utf8_text_plus_dimension_vision_estimate",
        "estimate_is_not_tokenizer_bound": True,
        "input_bytes": breakdown["text_bytes"],
        "text_bytes": breakdown["text_bytes"],
        "vision_bytes": breakdown["vision_bytes"],
        "vision_count": breakdown["vision_count"],
        "vision_pixels": breakdown["vision_pixels"],
        "vision_estimated_tokens": breakdown["vision_estimated_tokens"],
        "estimated_input_tokens_upper_bound": estimated_input_tokens,
        "output_tokens_reserved": output_tokens,
        "context_limit_tokens": effective_limit,
        "requested_context_limit_tokens": context_limit_tokens,
        "hard_limit_tokens": configured_hard_limit,
        "context_extension_requested": extension_requested,
        "context_extension_allowed": bool(allow_context_extension),
        "safety_margin_tokens": safety_margin_tokens,
        "admitted_input_budget_tokens": admitted_input_budget,
        "total_estimated_tokens": total_estimated_tokens,
        # Legacy field retained for consumers that display it; it is an
        # estimate, not a proof that provider token usage cannot exceed it.
        "total_upper_bound_tokens": total_estimated_tokens,
        "within_budget": estimated_input_tokens <= admitted_input_budget,
    }


def enforce_payload(
    payload: Any,
    *,
    output_tokens: int,
    context_limit_tokens: int = DEFAULT_CONTEXT_LIMIT_TOKENS,
    safety_margin_tokens: int = DEFAULT_SAFETY_MARGIN_TOKENS,
    hard_limit_tokens: int | None = None,
    allow_context_extension: bool = False,
) -> dict[str, Any]:
    """Return admission evidence or fail closed before any network call."""

    details = inspect_payload(
        payload,
        output_tokens=output_tokens,
        context_limit_tokens=context_limit_tokens,
        safety_margin_tokens=safety_margin_tokens,
        hard_limit_tokens=hard_limit_tokens,
        allow_context_extension=allow_context_extension,
    )
    if not details["within_budget"]:
        raise ContextBudgetExceeded(details)
    return details
