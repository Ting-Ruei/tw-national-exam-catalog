#!/usr/bin/env python3
"""Retired model-batch runner; no provider transport is active."""

from __future__ import annotations

import sys


def main() -> int:
    print("retired: model batch execution is disabled", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
