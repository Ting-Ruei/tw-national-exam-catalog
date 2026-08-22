#!/usr/bin/env python3
"""Conservative context admission checks for local model requests.

The repository does not assume that a tokenizer for every local runtime is
installed.  The guard therefore uses UTF-8 request bytes as an upper bound for
input token count: a byte-level upper bound can reject too much work, but it
cannot silently admit a request above the configured safety budget.  The
actual provider context limit is still recorded separately in the run
manifest.
"""

from __future__ import annotations

import json
from typing import Any


DEFAULT_CONTEXT_LIMIT_TOKENS = 131_072
DEFAULT_SAFETY_MARGIN_TOKENS = 8_192


class ContextBudgetExceeded(ValueError):
    """Raised before transport when a request cannot pass context admission."""

    def __init__(self, details: dict[str, Any]):
        self.details = details
        super().__init__(
            "context budget exceeded: "
            f"upper_bound={details.get('total_upper_bound_tokens')} "
            f"admitted={details.get('admitted_input_budget_tokens')}"
        )


def serialized_upper_bound_bytes(payload: Any) -> int:
    """Return a deterministic byte-level upper bound for a JSON payload."""

    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def inspect_payload(
    payload: Any,
    *,
    output_tokens: int,
    context_limit_tokens: int = DEFAULT_CONTEXT_LIMIT_TOKENS,
    safety_margin_tokens: int = DEFAULT_SAFETY_MARGIN_TOKENS,
) -> dict[str, Any]:
    """Inspect a payload without raising, using a conservative upper bound."""

    if context_limit_tokens <= 0:
        raise ValueError("context_limit_tokens must be positive")
    if safety_margin_tokens < 0 or safety_margin_tokens >= context_limit_tokens:
        raise ValueError("safety_margin_tokens must be >= 0 and below the context limit")
    if output_tokens < 0:
        raise ValueError("output_tokens must be >= 0")
    input_bytes = serialized_upper_bound_bytes(payload)
    admitted_input_budget = context_limit_tokens - safety_margin_tokens - output_tokens
    total_upper_bound = input_bytes + output_tokens
    return {
        "method": "utf8_json_bytes_upper_bound",
        "input_bytes": input_bytes,
        "estimated_input_tokens_upper_bound": input_bytes,
        "output_tokens_reserved": output_tokens,
        "context_limit_tokens": context_limit_tokens,
        "safety_margin_tokens": safety_margin_tokens,
        "admitted_input_budget_tokens": admitted_input_budget,
        "total_upper_bound_tokens": total_upper_bound,
        "within_budget": input_bytes <= admitted_input_budget,
    }


def enforce_payload(
    payload: Any,
    *,
    output_tokens: int,
    context_limit_tokens: int = DEFAULT_CONTEXT_LIMIT_TOKENS,
    safety_margin_tokens: int = DEFAULT_SAFETY_MARGIN_TOKENS,
) -> dict[str, Any]:
    """Return admission evidence or fail closed before any network call."""

    details = inspect_payload(
        payload,
        output_tokens=output_tokens,
        context_limit_tokens=context_limit_tokens,
        safety_margin_tokens=safety_margin_tokens,
    )
    if not details["within_budget"]:
        raise ContextBudgetExceeded(details)
    return details
