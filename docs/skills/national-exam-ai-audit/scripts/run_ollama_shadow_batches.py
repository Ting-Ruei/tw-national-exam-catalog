#!/usr/bin/env python3
"""Run compiled Ollama shadow batches with resumable raw and token journals."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from v4_common import read_json, read_jsonl, write_json, write_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
    )
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def atomic_write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    write_jsonl(temporary, rows)
    temporary.replace(path)


def post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Ollama response must be an object")
    return value


def main() -> int:
    args = parse_args()
    if args.timeout < 1 or args.max_attempts < 1:
        raise SystemExit("timeout and max-attempts must be positive")
    manifest = read_json(args.run_dir / "manifest.json")
    if manifest.get("adapter") != "ollama":
        raise SystemExit("run manifest is not compiled for the ollama adapter")

    results_path = args.run_dir / "results.jsonl"
    existing = (
        [row for _, row in read_jsonl(results_path)]
        if args.resume and results_path.is_file()
        else []
    )
    completed = {str(row.get("batch_id") or "") for row in existing}
    if len(completed) != len(existing):
        raise SystemExit("existing results contain empty or duplicate batch ids")
    journal_path = args.run_dir / "run_journal.jsonl"
    journal = (
        [row for _, row in read_jsonl(journal_path)]
        if args.resume and journal_path.is_file()
        else []
    )
    pending = [
        batch
        for batch in manifest.get("batches") or []
        if str(batch["batch_id"]) not in completed
    ]
    if args.dry_run:
        print(
            json.dumps(
                {
                    "ok": True,
                    "dry_run": True,
                    "completed_batches": len(completed),
                    "pending_batches": len(pending),
                    "model": manifest.get("model"),
                },
                ensure_ascii=False,
            )
        )
        return 0

    raw_dir = args.run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    base_url = args.base_url.rstrip("/")
    for batch in pending:
        batch_id = str(batch["batch_id"])
        request_manifest = read_json(args.run_dir / str(batch["request_path"]))
        if request_manifest.get("transport") != "ollama":
            raise SystemExit(f"request is not Ollama transport: {batch_id}")
        endpoint = str(request_manifest.get("endpoint") or "/api/generate")
        url = base_url + (endpoint if endpoint.startswith("/") else f"/{endpoint}")
        started = time.monotonic()
        response: dict[str, Any] | None = None
        error: str | None = None
        attempts = 0
        for attempts in range(1, args.max_attempts + 1):
            try:
                response = post_json(
                    url,
                    request_manifest["payload"],
                    timeout=args.timeout,
                )
                error = None
                break
            except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
                error = f"{type(exc).__name__}: {exc}"
        elapsed = time.monotonic() - started
        journal_row = {
            "batch_id": batch_id,
            "attempts": attempts,
            "elapsed_seconds": round(elapsed, 3),
            "ok": response is not None,
            "error": error,
            "prompt_eval_count": response.get("prompt_eval_count") if response else None,
            "eval_count": response.get("eval_count") if response else None,
            "total_duration": response.get("total_duration") if response else None,
            "load_duration": response.get("load_duration") if response else None,
        }
        journal.append(journal_row)
        atomic_write_jsonl(journal_path, journal)
        if response is None:
            raise SystemExit(f"Ollama batch failed: {batch_id}: {error}")
        write_json(raw_dir / f"{batch_id}.json", response)
        response_text = response.get("response")
        if not isinstance(response_text, str):
            raise SystemExit(f"Ollama response has no text payload: {batch_id}")
        try:
            result = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Ollama output is not strict JSON: {batch_id}: {exc}") from exc
        if not isinstance(result, dict):
            raise SystemExit(f"Ollama output is not an object: {batch_id}")
        existing.append(result)
        atomic_write_jsonl(results_path, existing)

    summary = {
        "ok": True,
        "model": manifest.get("model"),
        "batch_count": len(manifest.get("batches") or []),
        "completed_batch_count": len(existing),
        "prompt_eval_count": sum(
            int(row.get("prompt_eval_count") or 0) for row in journal if row.get("ok")
        ),
        "eval_count": sum(
            int(row.get("eval_count") or 0) for row in journal if row.get("ok")
        ),
        "results": str(results_path),
        "database_written": False,
        "review_events_written": False,
    }
    write_json(args.run_dir / "runner_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
