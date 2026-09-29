#!/usr/bin/env python3
"""Retired remote-model probe; no remote provider is active."""

from __future__ import annotations

import sys


def main() -> int:
    print("retired: remote model probing is disabled", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
