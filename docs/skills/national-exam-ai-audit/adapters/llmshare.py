"""Build a bounded LLM Share delegate payload without sending it."""

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
        "transport": "llmshare",
        "tool": "delegate_task",
        "model": profile["model"],
        "task": "Return one sparse national-exam audit batch JSON object.",
        "system_prompt": prompt,
        "context": json.dumps(packet, ensure_ascii=False, separators=(",", ":")),
        "temperature": 0,
        "advisory_only": True,
    }
