#!/usr/bin/env python3
"""Retired staging HTTP entrypoint; no service is configured."""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "retired: external review staging HTTP is disabled; create a new approved contract first",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
