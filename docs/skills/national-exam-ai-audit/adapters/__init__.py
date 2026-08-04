"""Pure request builders for national-exam audit model transports.

Adapters never execute network, browser, database, or review-event writes.  The
orchestrator chooses a certified profile and explicitly performs the returned
request.
"""

from __future__ import annotations

import importlib
from typing import Any


ALLOWED_ADAPTERS = {"codex_agent", "antigravity", "ollama", "llmshare"}


def build_request(
    adapter: str,
    *,
    profile: dict[str, Any],
    prompt: str,
    packet: dict[str, Any],
) -> dict[str, Any]:
    if adapter not in ALLOWED_ADAPTERS:
        raise ValueError(f"unsupported adapter: {adapter}")
    module = importlib.import_module(f"{__name__}.{adapter}")
    return module.build_request(profile=profile, prompt=prompt, packet=packet)
