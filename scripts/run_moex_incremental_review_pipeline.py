#!/usr/bin/env python3
"""Retired database-backed incremental review pipeline.

The active path is the source-to-package qbr pipeline and local review UI. This
entrypoint intentionally fails closed so an old scheduler or database command
cannot be mistaken for a current contract.
"""

from retired_model_entrypoint import main

if __name__ == "__main__":
    raise SystemExit(main("database-backed incremental review pipeline"))
