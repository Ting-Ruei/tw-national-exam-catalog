#!/usr/bin/env python3
"""Record a resumable checkpoint after pausing a local split MinerU queue.

This script does not terminate processes itself and does not move batch
directories.  Stop the queue process group first, then use this script to
verify its PID is gone, archive the stale PID record, inspect actual Markdown
outputs, and write a machine-readable handoff report.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATES = ("outgoing", "local_running", "assigned", "local_done", "local_partial", "local_failed")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def markdown_for_pdf(asset_root: Path, pdf_path: Path) -> Path | None:
    pdf_root = asset_root / "10_official_pdf" / "by_official_catalog"
    output_root = asset_root / "20_mineru_output" / "by_official_catalog"
    try:
        relative = pdf_path.resolve().relative_to(pdf_root.resolve())
    except ValueError:
        return None
    parent = output_root / relative.parent
    exact = parent / pdf_path.stem
    for kind in ("vlm", "ocr"):
        matches = sorted((exact / kind).glob("*.md"))
        if matches:
            return matches[0]
    if not parent.exists():
        return None
    candidates: list[tuple[int, Path]] = []
    for child in parent.iterdir():
        if not child.is_dir() or len(child.name) < 200 or not pdf_path.stem.startswith(child.name):
            continue
        for kind in ("vlm", "ocr"):
            for markdown in (child / kind).glob("*.md"):
                candidates.append((len(child.name), markdown))
    if not candidates:
        return None
    return sorted(candidates, reverse=True)[0][1]


def manifest_pdf_path(asset_root: Path, row: dict[str, str]) -> Path:
    relative = (row.get("pdf_relative") or row.get("relative_asset_path") or "").strip()
    if relative:
        return asset_root / relative
    value = (row.get("pdf_path") or row.get("asset_path") or "").strip()
    path = Path(value)
    return path if path.is_absolute() else asset_root / path


def batch_number(path: Path) -> int:
    match = re.search(r"_part(\d+)$", path.name)
    return int(match.group(1)) if match else -1


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-root", type=Path, required=True)
    parser.add_argument("--task-stamp", required=True, help="Batch name prefix stamp, e.g. 20260717-20000-verified.")
    parser.add_argument("--stopped-process-group", type=int, default=0)
    parser.add_argument("--stop-reason", default="paused_by_operator")
    parser.add_argument("--mineru-bin", type=Path, default=Path(os.environ.get("MINERU_BIN", "")))
    parser.add_argument("--stamp", default=dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    asset_root = args.asset_root.expanduser().resolve()
    batch_root = asset_root / "Registry" / "mineru_remote_batches"
    prefix = f"mineru_remote_batch_{args.task_stamp}_part"
    pid_path = batch_root / "local_queue__active.pid"
    recorded_pid = 0
    if pid_path.exists():
        try:
            recorded_pid = int(pid_path.read_text(encoding="utf-8").strip())
        except ValueError as exc:
            raise SystemExit(f"Invalid active PID record: {pid_path}") from exc
        if pid_running(recorded_pid):
            raise SystemExit(f"Queue PID is still running: {recorded_pid}")

    by_part: dict[int, tuple[str, Path]] = {}
    duplicate_parts: dict[int, list[str]] = {}
    state_batches: Counter[str] = Counter()
    state_manifest_items: Counter[str] = Counter()
    state_complete_items: Counter[str] = Counter()
    batch_details: list[dict[str, Any]] = []

    for state in STATES:
        for batch_dir in sorted((batch_root / state).glob(f"{prefix}*")):
            part = batch_number(batch_dir)
            if part in by_part:
                duplicate_parts.setdefault(part, [f"{by_part[part][0]}:{by_part[part][1]}"]).append(f"{state}:{batch_dir}")
            else:
                by_part[part] = (state, batch_dir)
            manifest = batch_dir / "batch_manifest.csv"
            rows = read_csv(manifest) if manifest.exists() else []
            complete_paths: list[str] = []
            unfinished_paths: list[str] = []
            for row in rows:
                pdf_path = manifest_pdf_path(asset_root, row)
                if markdown_for_pdf(asset_root, pdf_path):
                    complete_paths.append(str(pdf_path))
                else:
                    unfinished_paths.append(str(pdf_path))
            state_batches[state] += 1
            state_manifest_items[state] += len(rows)
            state_complete_items[state] += len(complete_paths)
            batch_details.append(
                {
                    "part": part,
                    "batch_name": batch_dir.name,
                    "state": state,
                    "batch_dir": str(batch_dir),
                    "manifest_items": len(rows),
                    "complete_items": len(complete_paths),
                    "unfinished_items": len(unfinished_paths),
                    "complete_paths": complete_paths,
                    "unfinished_paths": unfinished_paths,
                }
            )

    if duplicate_parts:
        raise SystemExit(f"Duplicate task parts across states: {json.dumps(duplicate_parts, ensure_ascii=False)}")
    if not batch_details:
        raise SystemExit(f"No batches found for task stamp: {args.task_stamp}")

    local_running = sorted((item for item in batch_details if item["state"] == "local_running"), key=lambda item: item["part"], reverse=True)
    outgoing = sorted((item for item in batch_details if item["state"] == "outgoing"), key=lambda item: item["part"], reverse=True)
    next_batch = (local_running or outgoing or [None])[0]

    archived_pid = ""
    if pid_path.exists():
        archive = batch_root / f"local_queue__paused__{args.stamp}.pid"
        pid_path.replace(archive)
        archived_pid = str(archive.relative_to(asset_root))

    total_items = sum(item["manifest_items"] for item in batch_details)
    complete_items = sum(item["complete_items"] for item in batch_details)
    unfinished_items = total_items - complete_items
    report_dir = asset_root / "Registry" / "reports" / f"mineru_queue_pause__{args.stamp}"
    report_dir.mkdir(parents=True, exist_ok=False)

    if next_batch:
        (report_dir / "next_batch_complete_paths.txt").write_text(
            "".join(f"{path}\n" for path in next_batch["complete_paths"]), encoding="utf-8"
        )
        (report_dir / "next_batch_unfinished_paths.txt").write_text(
            "".join(f"{path}\n" for path in next_batch["unfinished_paths"]), encoding="utf-8"
        )

    checkpoint = {
        "schema_version": 1,
        "generated_at": dt.datetime.now().astimezone().isoformat(),
        "state": "paused_resumable",
        "stop_reason": args.stop_reason,
        "asset_root": str(asset_root),
        "shutdown": {
            "stopped_process_group": args.stopped_process_group or None,
            "recorded_queue_pid": recorded_pid or None,
            "process_verified_stopped": not recorded_pid or not pid_running(recorded_pid),
            "archived_pid_record": archived_pid,
            "batch_directories_moved": 0,
            "pdfs_deleted": 0,
            "mineru_outputs_deleted": 0,
        },
        "formal_task": {
            "stamp": args.task_stamp,
            "total_batches": len(batch_details),
            "total_items": total_items,
            "complete_items": complete_items,
            "unfinished_items": unfinished_items,
            "next_batch": next_batch["batch_name"] if next_batch else None,
            "next_batch_state": next_batch["state"] if next_batch else None,
            "next_batch_complete_items": next_batch["complete_items"] if next_batch else 0,
            "next_batch_unfinished_items": next_batch["unfinished_items"] if next_batch else 0,
            "batch_counts": dict(sorted(state_batches.items())),
            "manifest_item_counts": dict(sorted(state_manifest_items.items())),
            "actual_complete_item_counts": dict(sorted(state_complete_items.items())),
        },
        "resume": {
            "behavior": "local_running is selected first; completed Markdown outputs are skipped automatically",
            "workers": 1,
            "timeout_seconds": 0,
            "mineru_bin": str(args.mineru_bin.expanduser()) if str(args.mineru_bin) else "set MINERU_BIN on destination",
            "command": (
                f"ASSET_ROOT='{asset_root}' MINERU_BIN='{args.mineru_bin.expanduser()}' "
                "WORKERS=1 TIMEOUT_SECONDS=0 python3 scripts/start_local_split_batch_queue.py"
            ),
        },
    }
    write_json(report_dir / "checkpoint.json", checkpoint)
    write_json(report_dir / "batch_summary.json", [{key: value for key, value in item.items() if not key.endswith("_paths")} for item in sorted(batch_details, key=lambda row: row["part"], reverse=True)])

    readme = [
        "# Local MinerU Queue Pause Checkpoint",
        "",
        f"Generated: `{checkpoint['generated_at']}`",
        "",
        f"State: `{checkpoint['state']}`",
        "",
        f"- Formal task: `{args.task_stamp}`",
        f"- Batches: {len(batch_details)}",
        f"- Manifest items: {total_items}",
        f"- Actual Markdown complete: {complete_items}",
        f"- Actual unfinished: {unfinished_items}",
        f"- Next batch: `{checkpoint['formal_task']['next_batch']}` in `{checkpoint['formal_task']['next_batch_state']}`",
        f"- Next batch complete/unfinished: {checkpoint['formal_task']['next_batch_complete_items']} / {checkpoint['formal_task']['next_batch_unfinished_items']}",
        f"- Batch states: `{json.dumps(dict(sorted(state_batches.items())), ensure_ascii=False)}`",
        "- No batch directory was moved; no PDF or MinerU output was deleted.",
        "",
        "## Resume",
        "",
        "The queue selects `local_running` first. Existing Markdown is skipped, so the current batch resumes from its unfinished files.",
        "",
        "```bash",
        checkpoint["resume"]["command"],
        "```",
        "",
    ]
    (report_dir / "README.md").write_text("\n".join(readme), encoding="utf-8")
    print(json.dumps({"report_dir": str(report_dir), **checkpoint["formal_task"], "shutdown": checkpoint["shutdown"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
