"""Build a generic OpenAI-compatible request without sending it."""

from __future__ import annotations

import json
from typing import Any


def build_request(
    *,
    profile: dict[str, Any],
    prompt: str,
    packet: dict[str, Any],
) -> dict[str, Any]:
    return {
        "transport": str((profile.get("transport") or {}).get("protocol") or "openai_chat_completions"),
        "endpoint": str((profile.get("transport") or {}).get("completion_path") or "/chat/completions"),
        "model": profile.get("model_default") or profile.get("model"),
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(packet, ensure_ascii=False, separators=(",", ":"))},
        ],
        "temperature": 0,
        "max_tokens": 512,
        "stream": False,
        "response_format": {"type": "json_object"},
        "advisory_only": True,
        "credentials": "runtime_env_only",
    }
