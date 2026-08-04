#!/usr/bin/env python3
"""Join read-only Group Review routes to safe question tasks for shadow auditing."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from v4_common import read_json, read_jsonl, sha256_json, write_json, write_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group-api-response", type=Path, required=True)
    parser.add_argument("--question-tasks", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit-groups", type=int, default=1)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.limit_groups < 1:
        raise SystemExit("--limit-groups must be positive")
    source_tasks = {
        str(row["candidate_key"]): row for _, row in read_jsonl(args.question_tasks)
    }
    response = read_json(args.group_api_response)
    sheets = response.get("candidates")
    if not isinstance(sheets, list):
        raise SystemExit("group API response has no candidates array")
    if int(response.get("returned_count") or len(sheets)) != len(sheets):
        raise SystemExit("group API response is truncated")

    selected_sheets: list[dict[str, Any]] = []
    for sheet in sheets:
        if not isinstance(sheet, dict):
            continue
        inferred = str(sheet.get("inferred_group_ref") or sheet.get("group_ref") or "")
        rows = sheet.get("rows") or []
        if inferred and isinstance(rows, list) and len(rows) >= 2:
            selected_sheets.append(sheet)
        if len(selected_sheets) >= args.limit_groups:
            break
    if not selected_sheets:
        raise SystemExit("no inferred or explicit group sheet is available")

    tasks: list[dict[str, Any]] = []
    sheet_manifest: list[dict[str, Any]] = []
    for sheet in selected_sheets:
        group_ref = str(sheet.get("inferred_group_ref") or sheet.get("group_ref") or "")
        rows = [row for row in sheet.get("rows") or [] if isinstance(row, dict)]
        keys = [str(row.get("candidate_key") or "") for row in rows]
        missing = [key for key in keys if key not in source_tasks]
        if missing:
            raise SystemExit(f"group rows are absent from safe question tasks: {missing}")
        for index, key in enumerate(keys):
            task = copy.deepcopy(source_tasks[key])
            content = task.setdefault("content", {})
            content["group_ref"] = group_ref

            def neighbor(position: int) -> dict[str, Any] | None:
                if position < 0 or position >= len(keys):
                    return None
                value = source_tasks[keys[position]]
                return {
                    "candidate_key": value["candidate_key"],
                    "question_number": (value.get("exam") or {}).get("question_number"),
                    "stem": str((value.get("content") or {}).get("stem") or ""),
                }

            task["neighbors"] = {
                "previous": neighbor(index - 1),
                "next": neighbor(index + 1),
            }
            task["source_fingerprint"] = sha256_json(
                {
                    "content": task["content"],
                    "neighbors": task["neighbors"],
                    "group_ref": group_ref,
                }
            )
            tasks.append(task)
        sheet_manifest.append(
            {
                "group_sheet_key": sheet.get("group_sheet_key"),
                "group_ref": group_ref,
                "inferred_group_kind": sheet.get("inferred_group_kind"),
                "gate_status": sheet.get("gate_status"),
                "candidate_keys": keys,
            }
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    task_path = args.output_dir / "group_tasks.jsonl"
    write_jsonl(task_path, tasks)
    manifest = {
        "schema_version": "national_exam_group_shadow_controls_v1",
        "advisory_only": True,
        "production_writes": False,
        "group_count": len(sheet_manifest),
        "task_count": len(tasks),
        "task_hash": sha256_json(tasks),
        "source_group_api_response": str(args.group_api_response.resolve()),
        "source_question_tasks": str(args.question_tasks.resolve()),
        "groups": sheet_manifest,
        "tasks": str(task_path),
    }
    write_json(args.output_dir / "manifest.json", manifest)
    print(
        json.dumps(
            {
                "ok": True,
                "group_count": manifest["group_count"],
                "task_count": manifest["task_count"],
                "output_dir": str(args.output_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
