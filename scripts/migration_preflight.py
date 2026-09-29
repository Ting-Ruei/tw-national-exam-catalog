#!/usr/bin/env python3
"""Retired external-runtime migration preflight."""

from __future__ import annotations

import sys


def main() -> int:
    print("retired: no external runtime migration is configured", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
