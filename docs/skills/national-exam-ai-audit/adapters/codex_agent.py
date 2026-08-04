"""Build a Codex task request without creating or mutating a thread."""

from __future__ import annotations

import json
from typing import Any


def build_request(
    *,
    profile: dict[str, Any],
    prompt: str,
    packet: dict[str, Any],
) -> dict[str, Any]:
    task = (
        f"{prompt}\n\n"
        "Audit this frozen packet and return one sparse batch JSON object:\n"
        f"{json.dumps(packet, ensure_ascii=False, separators=(',', ':'))}"
    )
    return {
        "transport": "codex_thread",
        "model": profile["model"],
        "thinking": profile.get("thinking", "low"),
        "prompt": task,
        "advisory_only": True,
    }
