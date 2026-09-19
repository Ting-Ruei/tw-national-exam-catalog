#!/usr/bin/env python3
"""Build a bounded three-source pilot spec from an AI395 staging bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_TAGS = {
    "parser_warning",
    "parser_blocked",
    "notation",
    "option_anomaly",
    "visual_missing",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def build_spec(rows: list[dict[str, Any]], *, scope_id: str, tags: set[str]) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        row_tags = {str(value) for value in metadata.get("ai395_scope_tags") or []}
        matched = sorted(row_tags & tags)
        if not matched:
            continue
        group_name = str(metadata.get("group_name") or "")
        category_scope = "__pharmacist_track__" if group_name == "藥師" else "醫事檢驗師"
        cases.append(
            {
                "id": f"{scope_id}_{index:03d}",
                "candidate_key": str(row.get("candidate_key") or ""),
                "lane": "text_evidence",
                "category_scope": category_scope,
                "scope_tags": matched,
                "expected": [],
            }
        )
    if not cases:
        raise ValueError("no candidate matched the requested scope tags")
    return {
        "schema_version": "ai395_three_source_spec_v1",
        "scope_id": scope_id,
        "selection_tags": sorted(tags),
        "candidate_count": len(rows),
        "case_count": len(cases),
        "cases": cases,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-jsonl", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scope-id", required=True)
    parser.add_argument("--tags", nargs="+", default=sorted(DEFAULT_TAGS))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    spec = build_spec(read_jsonl(args.candidate_jsonl), scope_id=args.scope_id, tags=set(args.tags))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "case_count": spec["case_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
