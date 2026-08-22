#!/usr/bin/env python3
"""Inspect AI395 local-model packets without making network calls."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from ai395_context_guard import DEFAULT_CONTEXT_LIMIT_TOKENS, ContextBudgetExceeded, inspect_payload
from ai395_llm_adapter import LANE_INSTRUCTIONS, build_native_request, build_packet, build_request


LANES = tuple(LANE_INSTRUCTIONS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-jsonl", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--model", default="qwen3.8:27b-mlx")
    parser.add_argument("--model-max-tokens", type=int, default=256)
    parser.add_argument("--context-limit-tokens", type=int, default=DEFAULT_CONTEXT_LIMIT_TOKENS)
    parser.add_argument("--context-safety-margin-tokens", type=int, default=8_192)
    parser.add_argument("--output-json", type=Path)
    return parser.parse_args()


def load_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    args = parse_args()
    rows = load_rows(args.candidate_jsonl)
    lane_rows: list[dict[str, Any]] = []
    for item in rows:
        for lane in LANES:
            if str((item.get("metadata") or {}).get("parser_status") or "") == "blocked":
                lane_rows.append({"candidate_key": item.get("candidate_key"), "lane": lane, "status": "parser_blocked"})
                continue
            try:
                packet, refs = build_packet(lane, item, item, args.fixture_root)
                request = build_request(lane, packet, refs, args.model, args.model_max_tokens)
                native = build_native_request(request, refs)
                guard = inspect_payload(
                    native,
                    output_tokens=args.model_max_tokens,
                    context_limit_tokens=args.context_limit_tokens,
                    safety_margin_tokens=args.context_safety_margin_tokens,
                )
                lane_rows.append({
                    "candidate_key": item.get("candidate_key"),
                    "lane": lane,
                    "status": "pass" if guard["within_budget"] else "context_budget_exceeded",
                    "image_count": len(refs),
                    "context_guard": guard,
                })
            except ContextBudgetExceeded as exc:
                lane_rows.append({"candidate_key": item.get("candidate_key"), "lane": lane, "status": "context_budget_exceeded", "context_guard": exc.details})
            except Exception as exc:
                lane_rows.append({"candidate_key": item.get("candidate_key"), "lane": lane, "status": type(exc).__name__, "message": str(exc)[:240]})
    statuses = Counter(str(row["status"]) for row in lane_rows)
    passed = [row for row in lane_rows if row["status"] == "pass"]
    report = {
        "status": "pass" if not any(row["status"] == "context_budget_exceeded" for row in lane_rows) else "needs_compaction",
        "candidate_count": len(rows),
        "lane_count": len(lane_rows),
        "status_counts": dict(sorted(statuses.items())),
        "max_input_bytes": max((int((row.get("context_guard") or {}).get("input_bytes") or 0) for row in passed), default=0),
        "context_policy": {
            "limit_tokens": args.context_limit_tokens,
            "safety_margin_tokens": args.context_safety_margin_tokens,
            "output_tokens_reserved": args.model_max_tokens,
            "method": "utf8_json_bytes_upper_bound",
        },
        "rows": lane_rows,
    }
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
