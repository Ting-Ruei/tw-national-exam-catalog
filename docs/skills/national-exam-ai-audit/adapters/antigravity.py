"""Build an Antigravity browser-agent request manifest."""

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
        "transport": "antigravity",
        "model": profile["model"],
        "effort": profile.get("thinking", "low"),
        "prompt": prompt,
        "packet_json": json.dumps(packet, ensure_ascii=False, separators=(",", ":")),
        "advisory_only": True,
        "imports_review_events": False,
    }
