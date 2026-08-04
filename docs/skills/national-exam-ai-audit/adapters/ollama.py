"""Build a local Ollama /api/generate payload without sending it."""

from __future__ import annotations

import json
from typing import Any


def build_request(
    *,
    profile: dict[str, Any],
    prompt: str,
    packet: dict[str, Any],
) -> dict[str, Any]:
    combined = (
        f"{prompt}\n\nPACKET\n"
        f"{json.dumps(packet, ensure_ascii=False, separators=(',', ':'))}"
    )
    return {
        "transport": "ollama",
        "endpoint": "/api/generate",
        "payload": {
            "model": profile["model"],
            "prompt": combined,
            "stream": False,
            "format": "json",
            "think": bool(profile.get("thinking", False)),
            "options": {
                "temperature": 0,
                "num_ctx": 65536,
            },
        },
        "advisory_only": True,
    }
