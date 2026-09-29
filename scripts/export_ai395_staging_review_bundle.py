#!/usr/bin/env python3
"""Retired staging bundle exporter; no external staging database is configured."""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "retired: staging bundle export is disabled; use the local review workflow or a new contract",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
