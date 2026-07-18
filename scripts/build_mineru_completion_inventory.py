#!/usr/bin/env python3
"""Build a per-PDF MinerU completion inventory from filesystem outputs."""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from collections import Counter, defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = Path(os.environ.get("ASSET_ROOT", PROJECT_ROOT / "國考題資料夾")).expanduser().resolve()
PDF_ROOT = ASSET_ROOT / "10_official_pdf" / "by_official_catalog"
OUTPUT_ROOT = ASSET_ROOT / "20_mineru_output" / "by_official_catalog"
REGISTRY_ROOT = ASSET_ROOT / "Registry"
BATCH_ROOT = REGISTRY_ROOT / "mineru_remote_batches"


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def output_markdown(pdf_path: Path) -> tuple[Path | None, str]:
    relative = pdf_path.relative_to(PDF_ROOT)
    parent = OUTPUT_ROOT / relative.parent
    exact_dir = parent / pdf_path.stem
    for kind in ("vlm", "ocr"):
        matches = sorted((exact_dir / kind).glob("*.md"))
        if matches:
            return matches[0], f"exact_{kind}"
    if not parent.exists():
        return None, "missing_output_parent"
    try:
        children = [child for child in parent.iterdir() if child.is_dir()]
    except OSError:
        return None, "unreadable_output_parent"
    candidates: list[tuple[int, Path, str]] = []
    for child in children:
        if len(child.name) < 200 or not pdf_path.stem.startswith(child.name):
            continue
        for kind in ("vlm", "ocr"):
            for markdown in (child / kind).glob("*.md"):
                candidates.append((len(child.name), markdown, f"truncated_{kind}"))
    if not candidates:
        return None, "no_matching_markdown"
    candidates.sort(key=lambda item: (item[0], str(item[1])), reverse=True)
    return candidates[0][1], candidates[0][2]


def queued_relatives() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    states: dict[str, set[str]] = defaultdict(set)
    batches: dict[str, set[str]] = defaultdict(set)
    for state in ("outgoing", "assigned", "local_running"):
        for manifest in (BATCH_ROOT / state).glob("*/batch_manifest.csv"):
            for row in read_csv(manifest):
                relative = (row.get("pdf_relative") or row.get("relative_asset_path") or "").strip()
                if relative:
                    states[relative].add(state)
                    batches[relative].add(manifest.parent.name)
    return states, batches


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-index", type=Path, required=True)
    parser.add_argument("--stamp", default=time.strftime("%Y%m%d-%H%M%S"))
    args = parser.parse_args()

    queue_states, queue_batches = queued_relatives()
    rows: list[dict[str, object]] = []
    counts: Counter[str] = Counter()
    for source in read_csv(args.pdf_index):
        relative = source["relative_asset_path"]
        pdf_path = ASSET_ROOT / relative
        markdown, strategy = output_markdown(pdf_path) if pdf_path.is_file() else (None, "pdf_missing")
        active_states = queue_states.get(relative, set())
        if markdown:
            status = "complete"
        elif "local_running" in active_states:
            status = "running"
        elif active_states:
            status = "queued"
        elif not pdf_path.is_file():
            status = "pdf_missing"
        else:
            status = "incomplete"
        counts[status] += 1
        rows.append({
            "completion_status": status,
            "output_match_strategy": strategy,
            "group_name": source.get("group_name", ""),
            "year": source.get("year", ""),
            "exam_ordinal": source.get("exam_ordinal", ""),
            "document_role": source.get("document_role", ""),
            "official_subject_name": source.get("official_subject_name", ""),
            "registry_key": source.get("registry_key", ""),
            "pdf_relative": relative,
            "pdf_path": str(pdf_path),
            "markdown_path": str(markdown or ""),
            "queue_states": "|".join(sorted(active_states)),
            "queue_batches": "|".join(sorted(queue_batches.get(relative, set()))),
        })

    report_dir = REGISTRY_ROOT / "reports" / f"mineru_completion_inventory__{args.stamp}"
    detail = report_dir / "mineru_completion_inventory.csv"
    write_csv(detail, rows)
    for status in ("complete", "incomplete", "queued", "running", "pdf_missing"):
        path = report_dir / f"{status}_pdf_paths.txt"
        path.write_text("".join(f"{row['pdf_path']}\n" for row in rows if row["completion_status"] == status), encoding="utf-8")
    (report_dir / "not_complete_pdf_paths.txt").write_text(
        "".join(f"{row['pdf_path']}\n" for row in rows if row["completion_status"] != "complete"),
        encoding="utf-8",
    )
    summary = {
        "generated_at": args.stamp,
        "asset_root": str(ASSET_ROOT),
        "pdf_index": str(args.pdf_index.resolve()),
        "total": len(rows),
        "counts": dict(sorted(counts.items())),
        "detail_csv": str(detail),
    }
    (report_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
