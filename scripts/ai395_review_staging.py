#!/usr/bin/env python3
"""Retired staging entrypoint.

The previous host-specific staging design is not part of the current catalog
workflow. Use qbr plus the local v2 review UI, or create a new approved task
contract before implementing another staging backend.
"""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "retired: external review staging is disabled; no current backend or writer is configured",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
