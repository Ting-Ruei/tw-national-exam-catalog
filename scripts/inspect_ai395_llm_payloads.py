#!/usr/bin/env python3
"""Retired payload inspector; no host-specific model route is active."""

from __future__ import annotations

import sys


def main() -> int:
    print("retired: model payload inspection requires a new local task contract", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
