#!/usr/bin/env python3
"""Build UI-safe correction previews from validated active-rule sparse issues."""

from __future__ import annotations

import argparse
import copy
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from v4_common import (
    get_field_text,
    read_json,
    read_jsonl,
    set_field_text,
    write_json,
    write_jsonl,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--validation-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    validation_path = args.validation_report or args.results.with_suffix(".validation.json")
    validation = read_json(validation_path)
    if not validation.get("ok"):
        raise SystemExit("sparse validation report is not successful")

    manifest = read_json(args.manifest)
    run_dir = args.manifest.parent
    task_by_key: dict[str, dict[str, Any]] = {}
    batch_keys: dict[str, set[str]] = {}
    for batch in manifest.get("batches") or []:
        packet = read_json(run_dir / str(batch["packet_path"]))
        keys: set[str] = set()
        for task in packet.get("tasks") or []:
            key = str(task["candidate_key"])
            task_by_key[key] = task
            keys.add(key)
        batch_keys[str(batch["batch_id"])] = keys

    deterministic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for _, row in read_jsonl(args.results):
        batch_id = str(row.get("batch_id") or "")
        for issue in row.get("issues") or []:
            if not isinstance(issue, dict) or issue.get("route") != "deterministic":
                continue
            key = str(issue.get("candidate_key") or "")
            if key not in batch_keys.get(batch_id, set()):
                raise SystemExit(f"issue candidate is not in batch: {key}")
            deterministic[key].append(issue)

    previews: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for key, issues in deterministic.items():
        original = task_by_key[key]
        updated = copy.deepcopy(original)
        changes: list[str] = []
        rule_ids: list[str] = []
        for issue in issues:
            field = str(issue["field"])
            before = str(issue["before"])
            after = str(issue["after"])
            current = get_field_text(updated, field)
            if current is None or current.count(before) != 1:
                errors.append(
                    {
                        "candidate_key": key,
                        "field": field,
                        "error": "exact_unique_match_failed",
                    }
                )
                continue
            set_field_text(updated, field, current.replace(before, after, 1))
            changes.append(f"{field}: {before} → {after}")
            rule_ids.append(str(issue["rule_id"]))
        if any(error["candidate_key"] == key for error in errors):
            continue
        correction: dict[str, Any] = {}
        if get_field_text(updated, "stem") != get_field_text(original, "stem"):
            correction["stem"] = get_field_text(updated, "stem")
        original_options = (original.get("content") or {}).get("options") or []
        updated_options = (updated.get("content") or {}).get("options") or []
        if updated_options != original_options:
            correction["options"] = [
                {"key": str(option["key"]), "text": str(option["text"])}
                for option in updated_options
            ]
        previews.append(
            {
                "candidate_key": key,
                "stage": original.get("stage", "question"),
                "source_fingerprint": original.get("source_fingerprint"),
                "rule_ids": sorted(set(rule_ids)),
                "suggested_correction": correction,
                "suggested_changes": changes,
                "advisory_only": True,
            }
        )

    write_jsonl(args.output, previews)
    report = {
        "ok": not errors,
        "candidate_count": len(previews),
        "deterministic_issue_count": sum(len(value) for value in deterministic.values()),
        "errors": errors,
        "output": str(args.output),
        "database_written": False,
        "review_events_written": False,
    }
    write_json(args.report or args.output.with_suffix(".report.json"), report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
