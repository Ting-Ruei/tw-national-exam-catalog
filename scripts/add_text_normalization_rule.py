#!/usr/bin/env python3
"""Append one confirmed OCR phrase correction to the editable rule registry."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = PROJECT_ROOT / "configs" / "text_normalization_rules.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Exact OCR text to replace")
    parser.add_argument("--target", required=True, help="Correct text verified against the official PDF")
    parser.add_argument("--category", action="append", default=[], help="Limit to a category; repeat for a category group")
    parser.add_argument("--subject", action="append", default=[], help="Limit to a subject; repeat for subjects")
    parser.add_argument("--note", default="", help="Short evidence or review note")
    parser.add_argument("--id", default="", help="Stable rule id; defaults to a timestamped id")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.source == args.target:
        raise SystemExit("source and target must differ")
    payload = json.loads(REGISTRY.read_text(encoding="utf-8")) if REGISTRY.exists() else {"version": 1, "rules": []}
    rules = payload.setdefault("rules", [])
    scope = {}
    if args.category:
        scope["categories"] = args.category
    if args.subject:
        scope["subjects"] = args.subject
    def scope_covers(existing: dict, requested: dict) -> bool:
        for field in ("categories", "subjects"):
            existing_values = {str(value) for value in (existing.get(field) or [])}
            requested_values = {str(value) for value in (requested.get(field) or [])}
            if existing_values and not requested_values.issubset(existing_values):
                return False
            if not existing_values and requested_values:
                continue
        return True

    duplicate = next(
        (
            rule
            for rule in rules
            if rule.get("kind") == "phrase"
            and rule.get("source") == args.source
            and rule.get("target") == args.target
            and scope_covers(rule.get("scope") or {}, scope)
        ),
        None,
    )
    if duplicate:
        print(f"already exists: {duplicate.get('id')}")
        return 0
    conflicting = next(
        (
            rule
            for rule in rules
            if rule.get("kind") == "phrase"
            and rule.get("source") == args.source
            and scope_covers(rule.get("scope") or {}, scope)
            and rule.get("target") != args.target
        ),
        None,
    )
    if conflicting:
        raise SystemExit(f"conflicting rule already exists: {conflicting.get('id')}")
    rule_id = args.id or f"ocr-phrase-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    rules.append(
        {
            "id": rule_id,
            "kind": "phrase",
            "source": args.source,
            "target": args.target,
            "scope": scope,
            "confidence": "high",
            "status": "active",
            "note": args.note,
        }
    )
    REGISTRY.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"added: {rule_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
