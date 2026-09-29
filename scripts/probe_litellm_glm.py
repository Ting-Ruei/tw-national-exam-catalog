#!/usr/bin/env python3
"""Retired provider probe; no external model provider is active."""

from __future__ import annotations

import sys


def main() -> int:
    print("retired: provider probing requires a new approved local contract", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
