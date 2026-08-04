#!/usr/bin/env python3
"""Compile minimal, resumable sparse-audit packets without calling a model."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from v4_common import (
    LANE_DISPLAY_NAMES,
    LANES,
    PROMPT_VERSION,
    SKILL_ROOT,
    compact_json,
    compact_task,
    load_profile,
    read_jsonl,
    sha256_json,
    write_json,
)


sys.path.insert(0, str(SKILL_ROOT))
from adapters import build_request  # noqa: E402


LANE_PROMPTS = {
    "ocr_text": "ocr-text.md",
    "semantic_transcription": "semantic-transcription.md",
    "group": "group.md",
    "visual": "visual.md",
}
DEFAULT_MAX_PACKET_BYTES = 49_152


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--lane", choices=sorted(LANES), required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--max-packet-bytes", type=int, default=DEFAULT_MAX_PACKET_BYTES)
    parser.add_argument(
        "--force",
        "--overwrite",
        dest="force",
        action="store_true",
        help="Allow replacing generated files in an existing run directory.",
    )
    return parser.parse_args()


def source_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def packet_batch_id(lane: str, model: str, batch_no: int) -> str:
    return f"{lane}__{model}__{batch_no:05d}"


def build_packet(
    *,
    lane: str,
    model: str,
    batch_no: int,
    tasks: list[dict[str, Any]],
    oversized_singleton: bool = False,
) -> dict[str, Any]:
    packet: dict[str, Any] = {
        "schema_version": "national_exam_sparse_packet_v1",
        "batch_id": packet_batch_id(lane, model, batch_no),
        "lane": lane,
        "lane_display_name": LANE_DISPLAY_NAMES[lane],
        "task_count": len(tasks),
        "tasks": tasks,
    }
    if oversized_singleton:
        packet["oversized_singleton"] = True
    return packet


def compact_packet_bytes(packet: dict[str, Any]) -> int:
    return len(compact_json(packet).encode("utf-8"))


def contiguous_source_blocks(
    raw_tasks: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    blocks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_key: str | None = None
    for index, (raw_task, task) in enumerate(zip(raw_tasks, tasks, strict=True)):
        source_key = str(raw_task.get("source_registry_key") or "")
        # Missing paper identity must not accidentally bind unrelated tasks.
        block_key = source_key if source_key else f"__missing_source_registry_key__{index}"
        if current and block_key != current_key:
            blocks.append(current)
            current = []
        current.append(task)
        current_key = block_key
    if current:
        blocks.append(current)
    return blocks


def pack_tasks(
    *,
    raw_tasks: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    lane: str,
    model: str,
    batch_size: int,
    max_packet_bytes: int,
) -> list[tuple[list[dict[str, Any]], bool]]:
    packed: list[tuple[list[dict[str, Any]], bool]] = []
    current: list[dict[str, Any]] = []

    def size_for(candidate_tasks: list[dict[str, Any]]) -> int:
        return compact_packet_bytes(
            build_packet(
                lane=lane,
                model=model,
                batch_no=len(packed) + 1,
                tasks=candidate_tasks,
            )
        )

    def fits(candidate_tasks: list[dict[str, Any]]) -> bool:
        return (
            len(candidate_tasks) <= batch_size
            and size_for(candidate_tasks) <= max_packet_bytes
        )

    def flush() -> None:
        nonlocal current
        if not current:
            return
        oversized_singleton = len(current) == 1 and size_for(current) > max_packet_bytes
        packed.append((current, oversized_singleton))
        current = []

    def add_one(task: dict[str, Any]) -> None:
        nonlocal current
        if current and not fits([*current, task]):
            flush()
        current.append(task)
        if len(current) == 1 and not fits(current):
            flush()

    if lane != "group":
        for task in tasks:
            add_one(task)
        flush()
        return packed

    for block in contiguous_source_blocks(raw_tasks, tasks):
        if current and fits([*current, *block]):
            current.extend(block)
            continue
        if fits(block):
            flush()
            current = list(block)
            continue

        # The paper cannot fit as one packet under a hard byte/count limit.
        # Split only in this unavoidable case, greedily preserving adjacency.
        flush()
        for task in block:
            add_one(task)
    flush()
    return packed


def guard_output_dir(output_dir: Path, *, force: bool) -> None:
    protected = [output_dir / "manifest.json", output_dir / "packets"]
    existing = [path for path in protected if path.exists()]
    if existing and not force:
        rendered = ", ".join(str(path) for path in existing)
        raise SystemExit(
            "refusing to overwrite an existing compiled run; "
            f"use a new --output-dir or pass --force: {rendered}"
        )


def main() -> int:
    args = parse_args()
    guard_output_dir(args.output_dir, force=args.force)
    profile = load_profile(args.profile)
    if args.lane not in set(profile.get("allowed_lanes") or []):
        raise SystemExit(f"profile {profile.get('model')} is not certified for lane {args.lane}")
    batch_size = args.batch_size or int(profile.get("default_batch_size") or 0)
    max_batch_size = int(profile.get("max_batch_size") or batch_size)
    if batch_size < 1 or batch_size > max_batch_size:
        raise SystemExit(f"batch size must be between 1 and profile max {max_batch_size}")
    if args.max_packet_bytes < 1:
        raise SystemExit("--max-packet-bytes must be at least 1")

    source_tasks_sha256 = source_file_sha256(args.tasks)
    raw_tasks = [row for _, row in read_jsonl(args.tasks)]
    tasks = [compact_task(row, lane=args.lane) for row in raw_tasks]
    if not tasks:
        raise SystemExit("task file is empty")
    keys = [task["candidate_key"] for task in tasks]
    if not all(keys) or len(keys) != len(set(keys)):
        raise SystemExit("candidate keys must be non-empty and unique")

    base_prompt = (SKILL_ROOT / "prompts" / "base-contract.md").read_text(encoding="utf-8")
    lane_prompt = (SKILL_ROOT / "prompts" / LANE_PROMPTS[args.lane]).read_text(encoding="utf-8")
    prompt = base_prompt.rstrip() + "\n\n" + lane_prompt.rstrip() + "\n"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "SYSTEM_PROMPT.md").write_text(prompt, encoding="utf-8")

    batches: list[dict[str, Any]] = []
    packed_batches = pack_tasks(
        raw_tasks=raw_tasks,
        tasks=tasks,
        lane=args.lane,
        model=str(profile["model"]),
        batch_size=batch_size,
        max_packet_bytes=args.max_packet_bytes,
    )
    for batch_tasks, oversized_singleton in packed_batches:
        batch_no = len(batches) + 1
        packet = build_packet(
            lane=args.lane,
            model=str(profile["model"]),
            batch_no=batch_no,
            tasks=batch_tasks,
            oversized_singleton=oversized_singleton,
        )
        batch_id = str(packet["batch_id"])
        packet_bytes = compact_packet_bytes(packet)
        packet_relative = Path("packets") / f"{batch_id}.json"
        request_relative = Path("requests") / f"{batch_id}.json"
        write_json(args.output_dir / packet_relative, packet)
        request = build_request(
            str(profile["adapter"]),
            profile=profile,
            prompt=prompt,
            packet=packet,
        )
        write_json(args.output_dir / request_relative, request)
        batches.append(
            {
                "batch_id": batch_id,
                "task_count": len(batch_tasks),
                "candidate_keys": [task["candidate_key"] for task in batch_tasks],
                "packet_path": str(packet_relative),
                "request_path": str(request_relative),
                "packet_sha256": sha256_json(packet),
                "packet_bytes": packet_bytes,
                **({"oversized_singleton": True} if oversized_singleton else {}),
            }
        )

    if source_file_sha256(args.tasks) != source_tasks_sha256:
        raise SystemExit("source task file changed while packets were being compiled")

    manifest = {
        "schema_version": "national_exam_sparse_run_manifest_v1",
        "prompt_version": PROMPT_VERSION,
        "model": profile["model"],
        "adapter": profile["adapter"],
        "lane": args.lane,
        "lane_display_name": LANE_DISPLAY_NAMES[args.lane],
        "task_count": len(tasks),
        "batch_size": batch_size,
        "max_packet_bytes": args.max_packet_bytes,
        "source_tasks": str(args.tasks),
        "source_tasks_sha256": source_tasks_sha256,
        "compiled_at": datetime.now(timezone.utc).isoformat(),
        "profile": str(args.profile),
        "batches": batches,
        "advisory_only": True,
        "imports_review_events": False,
    }
    write_json(args.output_dir / "manifest.json", manifest)
    print(json.dumps(
        {
            "ok": True,
            "task_count": len(tasks),
            "batch_count": len(batches),
            "output_dir": str(args.output_dir),
        },
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
