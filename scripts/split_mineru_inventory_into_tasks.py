#!/usr/bin/env python3
"""Split an inventory status into dormant, deterministic MinerU task manifests."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--status", default="incomplete")
    parser.add_argument("--chunk-size", type=int, default=10_000)
    args = parser.parse_args()

    if args.chunk_size <= 0:
        raise SystemExit("--chunk-size must be positive")
    rows = [row for row in read_rows(args.inventory) if row.get("completion_status") == args.status]
    rows.sort(key=lambda row: row["pdf_relative"])
    if not rows:
        raise SystemExit(f"No rows with completion_status={args.status}")

    args.output_root.mkdir(parents=True, exist_ok=True)
    tasks: list[dict[str, object]] = []
    for offset in range(0, len(rows), args.chunk_size):
        task_number = offset // args.chunk_size + 1
        chunk = rows[offset : offset + args.chunk_size]
        task_name = f"task_{task_number:03d}__{len(chunk):05d}"
        task_dir = args.output_root / task_name
        manifest = task_dir / "mineru_task_manifest.csv"
        write_csv(manifest, chunk)
        metadata = {
            "task_number": task_number,
            "task_name": task_name,
            "state": "planned_not_queued",
            "item_count": len(chunk),
            "source_status": args.status,
            "source_inventory": str(args.inventory.resolve()),
            "manifest": str(manifest.resolve()),
            "manifest_sha256": sha256(manifest),
            "first_pdf_relative": chunk[0]["pdf_relative"],
            "last_pdf_relative": chunk[-1]["pdf_relative"],
        }
        (task_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        tasks.append(metadata)

    summary = {
        "state": "planned_not_queued",
        "source_inventory": str(args.inventory.resolve()),
        "source_inventory_sha256": sha256(args.inventory),
        "source_status": args.status,
        "chunk_size": args.chunk_size,
        "total_items": len(rows),
        "task_count": len(tasks),
        "task_item_counts": [task["item_count"] for task in tasks],
        "tasks": tasks,
        "activation_note": "These manifests are dormant. Copy a selected task into the active queue only after revalidating completion status and disk space.",
    }
    (args.output_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    readme_lines = [
        "# MinerU future tasks",
        "",
        f"Source inventory: `{args.inventory.resolve()}`",
        f"Source status: `{args.status}`",
        f"Total items: {len(rows)}",
        f"Task count: {len(tasks)}",
        "",
        "These tasks are planned only. They are not in `outgoing` and will not run automatically.",
        "Revalidate Markdown completion and available disk space before activating any task.",
        "",
        "| Task | Items | State |",
        "|---|---:|---|",
    ]
    readme_lines.extend(
        f"| {task['task_name']} | {task['item_count']} | planned_not_queued |" for task in tasks
    )
    (args.output_root / "README.md").write_text("\n".join(readme_lines) + "\n", encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("total_items", "task_count", "task_item_counts")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
