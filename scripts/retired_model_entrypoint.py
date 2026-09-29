"""Fail-closed helper for retired model execution entrypoints."""

from __future__ import annotations

import sys


def main(label: str = "model execution") -> int:
    print(
        f"retired: {label} is disabled; create a new approved local task contract first",
        file=sys.stderr,
    )
    return 2
