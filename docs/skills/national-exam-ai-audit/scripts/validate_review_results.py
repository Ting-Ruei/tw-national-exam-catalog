#!/usr/bin/env python3
"""Retired model-result validator.

The current audit path validates deterministic evidence and package manifests;
there is no active model-result schema or importer.
"""

from __future__ import annotations


def validate_review_results(*args, **kwargs) -> list[str]:
    return ["retired_model_result_validator"]


def main() -> int:
    raise SystemExit("retired: no model-result validator is active")


if __name__ == "__main__":
    main()
