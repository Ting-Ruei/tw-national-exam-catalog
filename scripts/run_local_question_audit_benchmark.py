#!/usr/bin/env python3
"""Run resumable full-exam local-model batch-size benchmarks.

Each model and chunk delegates to compact_initial_question_audit.py, which is
advisory-only and never writes Review UI events. Runs are deliberately
sequential to avoid congesting the local Ollama server.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_BATCH_SIZES = (16, 24, 40, 80)
PROGRESS_PREFIXES = ("ocr_text:", "meaning:", "visual:", "group:")


def read_task_count(path: Path) -> int:
    with path.open("r", encoding="utf-8") as stream:
        return sum(1 for line in stream if line.strip())


def read_tasks(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            rows.append(value)
    return rows


def chunk_offsets(task_count: int, batch_size: int) -> list[int]:
    if task_count <= 0:
        raise ValueError("task_count must be positive")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    return list(range(0, task_count, batch_size))


def task_paper_key(task: dict[str, Any]) -> str:
    source_key = str(task.get("source_registry_key") or "").strip()
    if source_key:
        return source_key
    exam = task.get("exam") if isinstance(task.get("exam"), dict) else {}
    return "|".join(
        str(exam.get(field) or "")
        for field in ("category", "year", "ordinal", "subject")
    )


def chunk_windows(
    tasks: list[dict[str, Any]],
    batch_size: int,
    paper_aligned: bool,
) -> list[tuple[int, int]]:
    if not tasks:
        raise ValueError("tasks must not be empty")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if not paper_aligned:
        return [
            (offset, min(batch_size, len(tasks) - offset))
            for offset in chunk_offsets(len(tasks), batch_size)
        ]
    windows: list[tuple[int, int]] = []
    seen_papers: set[str] = set()
    start = 0
    while start < len(tasks):
        key = task_paper_key(tasks[start])
        if not key:
            raise ValueError(f"task at offset {start} has no paper identity")
        if key in seen_papers:
            raise ValueError(f"paper is not contiguous in task file: {key}")
        seen_papers.add(key)
        end = start + 1
        while end < len(tasks) and task_paper_key(tasks[end]) == key:
            end += 1
        for offset in range(start, end, batch_size):
            windows.append((offset, min(batch_size, end - offset)))
        start = end
    return windows


def model_slug(model: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-")
    if not slug:
        raise ValueError("model name does not produce a safe directory name")
    return slug


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
    temporary.replace(path)


def chunk_failure_details(output_dir: Path, return_code: int | None) -> dict[str, Any]:
    summary_path = output_dir / "summary.json"
    details: dict[str, Any] = {
        "return_code": return_code,
        "summary_exists": summary_path.is_file(),
        "errors": {},
    }
    if not summary_path.is_file():
        details["reason"] = (
            "process_failed_without_summary"
            if return_code not in {None, 0}
            else "missing_summary"
        )
        return details
    try:
        summary = read_json(summary_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        details["reason"] = "invalid_summary"
        details["summary_error"] = str(exc)
        return details
    details["all_lanes_complete"] = bool(summary.get("all_lanes_complete"))
    details["errors"] = {
        lane: list((validation or {}).get("errors") or [])
        for lane, validation in (summary.get("lane_validation") or {}).items()
        if (validation or {}).get("errors")
    }
    details["reason"] = (
        "complete"
        if details["all_lanes_complete"] and return_code in {None, 0}
        else "validation_failed"
        if not details["all_lanes_complete"]
        else "nonzero_exit_with_complete_summary"
    )
    return details


def archive_failed_output(output_dir: Path, attempt_label: str) -> Path | None:
    if not output_dir.exists():
        return None
    candidate = output_dir.with_name(f"{output_dir.name}__{attempt_label}_failed")
    suffix = 2
    while candidate.exists():
        candidate = output_dir.with_name(
            f"{output_dir.name}__{attempt_label}_failed_{suffix}"
        )
        suffix += 1
    output_dir.rename(candidate)
    return candidate


def merge_recovered_children(
    args: argparse.Namespace,
    model: str,
    batch_size: int,
    offset: int,
    task_limit: int,
    output_dir: Path,
    child_results: list[dict[str, Any]],
) -> None:
    child_summaries = [
        read_json(Path(result["output_dir"]) / "summary.json")
        for result in child_results
    ]
    preview_rows: list[dict[str, Any]] = []
    for result, summary in zip(child_results, child_summaries):
        preview_path = Path(
            str(
                summary.get("review_ui_results_preview")
                or Path(result["output_dir"]) / "review_ui_results_preview.jsonl"
            )
        )
        with preview_path.open(encoding="utf-8") as handle:
            preview_rows.extend(
                json.loads(line) for line in handle if line.strip()
            )
    expected_keys = [
        str(task.get("candidate_key") or "")
        for task in read_tasks(args.tasks)[offset : offset + task_limit]
    ]
    returned_keys = [str(row.get("candidate_key") or "") for row in preview_rows]
    if returned_keys != expected_keys:
        raise ValueError(
            f"recovered child previews do not match source window at offset {offset}"
        )
    output_dir.mkdir(parents=True, exist_ok=False)
    preview_path = output_dir / "review_ui_results_preview.jsonl"
    write_jsonl(preview_path, preview_rows)
    lane_validation: dict[str, Any] = {}
    for lane in ("ocr_text", "meaning", "visual", "group"):
        validations = [
            (summary.get("lane_validation") or {}).get(lane) or {}
            for summary in child_summaries
        ]
        lane_validation[lane] = {
            "complete": all(bool(row.get("complete")) for row in validations),
            "issue_count": sum(int(row.get("issue_count") or 0) for row in validations),
            "errors": [
                error
                for row in validations
                for error in (row.get("errors") or [])
            ],
            "warnings": [
                warning
                for row in validations
                for warning in (row.get("warnings") or [])
            ],
            "selected_count": sum(
                int(row.get("selected_count") or 0) for row in validations
            ),
            "skipped": all(bool(row.get("skipped")) for row in validations),
            "unavailable": any(bool(row.get("unavailable")) for row in validations),
        }
    write_json(
        output_dir / "summary.json",
        {
            "schema_version": "local_question_audit_recovered_batch_v1",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "advisory_only": True,
            "writes_review_events": False,
            "model": model,
            "task_count": task_limit,
            "source_offset": offset,
            "candidate_keys": expected_keys,
            "all_lanes_complete": True,
            "lane_validation": lane_validation,
            "metrics": [
                metric
                for summary in child_summaries
                for metric in (summary.get("metrics") or [])
            ],
            "review_ui_results_preview": str(preview_path),
            "import_gate": (
                "Recovered preview only. Human approval is still required "
                "before any AI event import or reset_review action."
            ),
            "recovery": {
                "strategy": "split_batch_in_half",
                "parent_batch_size": batch_size,
                "children": child_results,
            },
        },
    )


def collect_compatible_model_summaries(
    output_root: Path,
    task_count: int,
    batch_sizes: list[int],
    paper_aligned: bool = False,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for path in sorted(output_root.glob("*/benchmark_summary.json")):
        summary = read_json(path)
        if int(summary.get("task_count") or 0) != task_count:
            continue
        if summary.get("batch_sizes") != batch_sizes:
            continue
        if bool(summary.get("paper_aligned")) != paper_aligned:
            continue
        summaries.append(summary)
    return sorted(summaries, key=lambda summary: str(summary.get("model") or ""))


def aggregate_model(
    model: str,
    model_root: Path,
    batch_sizes: list[int],
    task_count: int,
    windows_by_batch_size: dict[int, list[tuple[int, int]]] | None = None,
    paper_aligned: bool = False,
) -> dict[str, Any]:
    schemes: list[dict[str, Any]] = []
    for batch_size in batch_sizes:
        chunks: list[dict[str, Any]] = []
        windows = (
            windows_by_batch_size[batch_size]
            if windows_by_batch_size is not None
            else [
                (offset, min(batch_size, task_count - offset))
                for offset in chunk_offsets(task_count, batch_size)
            ]
        )
        for offset, expected_task_count in windows:
            chunk_dir = model_root / f"bs{batch_size}-o{offset}"
            summary_path = chunk_dir / "summary.json"
            if not summary_path.exists():
                chunks.append(
                    {
                        "offset": offset,
                        "task_count": expected_task_count,
                        "state": "missing",
                        "output_dir": str(chunk_dir),
                    }
                )
                continue
            summary = read_json(summary_path)
            metrics = summary.get("metrics") or []
            lane_validation = summary.get("lane_validation") or {}
            chunks.append(
                {
                    "offset": offset,
                    "task_count": int(summary.get("task_count") or 0),
                    "state": (
                        "complete"
                        if summary.get("all_lanes_complete")
                        else "validation_failed"
                    ),
                    "all_lanes_complete": bool(summary.get("all_lanes_complete")),
                    "total_latency_ms": sum(
                        float(row.get("total_latency_ms") or 0)
                        for row in metrics
                        if isinstance(row, dict)
                    ),
                    "prompt_eval_count": sum(
                        int(row.get("prompt_eval_count") or 0)
                        for row in metrics
                        if isinstance(row, dict)
                    ),
                    "eval_count": sum(
                        int(row.get("eval_count") or 0)
                        for row in metrics
                        if isinstance(row, dict)
                    ),
                    "ocr_issue_count": int(
                        (lane_validation.get("ocr_text") or {}).get("issue_count")
                        or 0
                    ),
                    "meaning_issue_count": int(
                        (lane_validation.get("meaning") or {}).get("issue_count")
                        or 0
                    ),
                    "errors": {
                        lane: (validation or {}).get("errors") or []
                        for lane, validation in lane_validation.items()
                        if (validation or {}).get("errors")
                    },
                    "preview": summary.get("review_ui_results_preview"),
                    "output_dir": str(chunk_dir),
                }
            )
        present = [chunk for chunk in chunks if chunk["state"] != "missing"]
        complete = [chunk for chunk in chunks if chunk["state"] == "complete"]
        schemes.append(
            {
                "batch_size": batch_size,
                "complete_chunks": len(complete),
                "present_chunks": len(present),
                "total_chunks": len(chunks),
                "all_chunks_complete": len(complete) == len(chunks),
                "total_latency_ms": sum(
                    float(chunk.get("total_latency_ms") or 0)
                    for chunk in present
                ),
                "prompt_eval_count": sum(
                    int(chunk.get("prompt_eval_count") or 0)
                    for chunk in present
                ),
                "eval_count": sum(
                    int(chunk.get("eval_count") or 0)
                    for chunk in present
                ),
                "ocr_issue_count": sum(
                    int(chunk.get("ocr_issue_count") or 0)
                    for chunk in present
                ),
                "meaning_issue_count": sum(
                    int(chunk.get("meaning_issue_count") or 0)
                    for chunk in present
                ),
                "chunks": chunks,
            }
        )
    return {
        "model": model,
        "task_count": task_count,
        "batch_sizes": batch_sizes,
        "paper_aligned": paper_aligned,
        "schemes": schemes,
    }


def run_chunk(
    args: argparse.Namespace,
    model: str,
    batch_size: int,
    offset: int,
    task_limit: int,
    output_dir: Path,
    retry_hint: str | None = None,
) -> int:
    command = [
        sys.executable,
        str(args.runner),
        "run-local",
        "--tasks",
        str(args.tasks),
        "--output-dir",
        str(output_dir),
        "--model",
        model,
        "--offset",
        str(offset),
        "--limit",
        str(task_limit),
        "--num-ctx",
        str(args.num_ctx),
        "--keep-alive",
        args.keep_alive,
        "--timeout-seconds",
        str(args.timeout_seconds),
        "--ollama-format",
        args.ollama_format,
        "--visual-mode",
        args.visual_mode,
    ]
    if args.max_output_tokens is not None:
        command.extend(["--max-output-tokens", str(args.max_output_tokens)])
    if retry_hint:
        command.extend(["--retry-hint", retry_hint[:500]])
    if args.dry_run:
        print("DRY RUN " + " ".join(command), flush=True)
        return 0
    process = subprocess.Popen(
        command,
        cwd=args.project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        stripped = line.rstrip()
        if stripped.startswith(PROGRESS_PREFIXES):
            print(
                f"[{model} bs={batch_size} offset={offset}] {stripped}",
                flush=True,
            )
    return process.wait()


def run_chunk_with_recovery(
    args: argparse.Namespace,
    model: str,
    batch_size: int,
    offset: int,
    task_limit: int,
    output_dir: Path,
    *,
    split_depth: int = 0,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    existing_details = chunk_failure_details(output_dir, None)
    if existing_details.get("reason") == "complete":
        return {
            "state": "complete",
            "output_dir": str(output_dir),
            "attempts": [],
            "reused": True,
            "split_depth": split_depth,
        }
    if output_dir.exists():
        archived = archive_failed_output(output_dir, "preexisting")
        attempts.append(
            {
                "attempt": "preexisting",
                "details": existing_details,
                "archived_output": str(archived) if archived else None,
            }
        )

    total_attempts = args.max_retries + 1
    retry_hint: str | None = None
    for attempt in range(1, total_attempts + 1):
        print(
            f"[{model} bs={batch_size} offset={offset}] "
            f"attempt={attempt}/{total_attempts}",
            flush=True,
        )
        return_code = run_chunk(
            args,
            model,
            batch_size,
            offset,
            task_limit,
            output_dir,
            retry_hint,
        )
        if args.dry_run:
            attempts.append(
                {
                    "attempt": attempt,
                    "details": {"reason": "dry_run", "return_code": return_code},
                }
            )
            return {
                "state": "dry_run",
                "output_dir": str(output_dir),
                "attempts": attempts,
                "reused": False,
                "split_depth": split_depth,
            }
        details = chunk_failure_details(output_dir, return_code)
        record = {
            "attempt": attempt,
            "details": details,
        }
        if details.get("reason") == "complete":
            attempts.append(record)
            return {
                "state": "complete",
                "output_dir": str(output_dir),
                "attempts": attempts,
                "reused": False,
                "split_depth": split_depth,
            }
        archived = archive_failed_output(output_dir, f"attempt{attempt:02d}")
        record["archived_output"] = str(archived) if archived else None
        attempts.append(record)
        print(
            f"[{model} bs={batch_size} offset={offset}] "
            f"attempt={attempt} failed: "
            f"{json.dumps(details, ensure_ascii=False, sort_keys=True)}",
            flush=True,
        )
        error_codes = [
            str(error)
            for lane_errors in (details.get("errors") or {}).values()
            for error in lane_errors
        ]
        retry_hint = "; ".join(error_codes[:8]) or str(details.get("reason") or "validation_failed")

    if task_limit > 1 and split_depth < args.max_split_depth:
        left_limit = task_limit // 2
        right_limit = task_limit - left_limit
        child_specs = [
            (offset, left_limit),
            (offset + left_limit, right_limit),
        ]
        print(
            f"[{model} bs={batch_size} offset={offset}] "
            f"split_after_failures={child_specs}",
            flush=True,
        )
        child_results: list[dict[str, Any]] = []
        for child_offset, child_limit in child_specs:
            child_dir = output_dir.with_name(
                f"{output_dir.name}__split{split_depth + 1}"
                f"-o{child_offset}-n{child_limit}"
            )
            child_result = run_chunk_with_recovery(
                args,
                model,
                child_limit,
                child_offset,
                child_limit,
                child_dir,
                split_depth=split_depth + 1,
            )
            child_results.append(child_result)
        if all(result.get("state") == "complete" for result in child_results):
            merge_recovered_children(
                args,
                model,
                batch_size,
                offset,
                task_limit,
                output_dir,
                child_results,
            )
            return {
                "state": "complete_after_split",
                "output_dir": str(output_dir),
                "attempts": attempts,
                "children": child_results,
                "reused": False,
                "split_depth": split_depth,
            }
        return {
            "state": "failed_after_split",
            "output_dir": str(output_dir),
            "attempts": attempts,
            "children": child_results,
            "reused": False,
            "split_depth": split_depth,
        }
    return {
        "state": "failed",
        "output_dir": str(output_dir),
        "attempts": attempts,
        "reused": False,
        "split_depth": split_depth,
    }


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description=(
            "Run sequential, resumable 16/24/40/80 full-exam benchmarks. "
            "Never imports Review UI events."
        )
    )
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--model", action="append", required=True)
    parser.add_argument(
        "--batch-sizes",
        type=int,
        nargs="+",
        default=list(DEFAULT_BATCH_SIZES),
    )
    parser.add_argument(
        "--only-offsets",
        type=int,
        nargs="+",
        help=(
            "Run only these source offsets for every selected batch size. "
            "This is intended for retrying failed chunks without repeating "
            "already valid inference."
        ),
    )
    parser.add_argument("--num-ctx", type=int, default=65536)
    parser.add_argument("--keep-alive", default="30m")
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        help=(
            "Override the compact runner's per-lane output limit. Leave unset "
            "for the lane defaults."
        ),
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Retries after the initial attempt for a failed batch.",
    )
    parser.add_argument(
        "--max-split-depth",
        type=int,
        default=2,
        help=(
            "After exhausting retries, split a failed batch in half up to this "
            "many levels. Zero disables splitting."
        ),
    )
    parser.add_argument(
        "--paper-aligned",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Never combine tasks from different source papers in one chunk.",
    )
    parser.add_argument(
        "--ollama-format",
        choices=["schema", "json", "prompt-only"],
        default="schema",
    )
    parser.add_argument(
        "--visual-mode",
        choices=["vision", "unavailable"],
        default="unavailable",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip chunks that already contain summary.json.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.set_defaults(
        project_root=project_root,
        runner=project_root / "scripts" / "compact_initial_question_audit.py",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.tasks.is_file():
        raise FileNotFoundError(args.tasks)
    if any(batch_size <= 0 for batch_size in args.batch_sizes):
        raise ValueError("batch sizes must be positive")
    if args.max_retries < 0:
        raise ValueError("max-retries must be non-negative")
    if args.max_split_depth < 0:
        raise ValueError("max-split-depth must be non-negative")
    if args.max_output_tokens is not None and args.max_output_tokens <= 0:
        raise ValueError("max-output-tokens must be positive")
    tasks = read_tasks(args.tasks)
    task_count = len(tasks)
    if not task_count:
        raise ValueError("tasks file is empty")
    requested_offsets = set(args.only_offsets or [])
    invalid_offsets = sorted(
        offset for offset in requested_offsets if offset < 0 or offset >= task_count
    )
    if invalid_offsets:
        raise ValueError(f"only-offsets outside task range: {invalid_offsets}")
    windows_by_batch_size = {
        batch_size: chunk_windows(tasks, batch_size, args.paper_aligned)
        for batch_size in args.batch_sizes
    }
    args.output_root.mkdir(parents=True, exist_ok=True)
    for model in args.model:
        slug = model_slug(model)
        model_root = args.output_root / slug
        model_root.mkdir(parents=True, exist_ok=True)
        recovery_records: list[dict[str, Any]] = []
        print(f"[{model}] start task_count={task_count}", flush=True)
        for batch_size in args.batch_sizes:
            for offset, task_limit in windows_by_batch_size[batch_size]:
                if requested_offsets and offset not in requested_offsets:
                    continue
                output_dir = model_root / f"bs{batch_size}-o{offset}"
                summary_path = output_dir / "summary.json"
                if args.resume and summary_path.exists():
                    existing = chunk_failure_details(output_dir, None)
                    if existing.get("reason") == "complete":
                        print(
                            f"[{model} bs={batch_size} offset={offset}] "
                            "resume: skip complete",
                            flush=True,
                        )
                        continue
                if output_dir.exists() and not args.resume:
                    print(
                        f"[{model} bs={batch_size} offset={offset}] "
                        "blocked: output exists without summary",
                        flush=True,
                    )
                    continue
                print(
                    f"[{model} bs={batch_size} offset={offset}] start",
                    flush=True,
                )
                recovery = run_chunk_with_recovery(
                    args,
                    model,
                    batch_size,
                    offset,
                    task_limit,
                    output_dir,
                )
                recovery_record = {
                    "batch_size": batch_size,
                    "offset": offset,
                    "task_count": task_limit,
                    **recovery,
                }
                recovery_records.append(recovery_record)
                write_json(
                    model_root / "recovery_summary.json",
                    {
                        "schema_version": "local_question_audit_recovery_v1",
                        "generated_at": datetime.now().astimezone().isoformat(
                            timespec="seconds"
                        ),
                        "model": model,
                        "max_retries": args.max_retries,
                        "max_split_depth": args.max_split_depth,
                        "records": recovery_records,
                        "unresolved": [
                            row
                            for row in recovery_records
                            if row.get("state") in {"failed", "failed_after_split"}
                        ],
                    },
                )
                print(
                    f"[{model} bs={batch_size} offset={offset}] "
                    f"recovery_state={recovery['state']}",
                    flush=True,
                )
                model_summary = aggregate_model(
                    model,
                    model_root,
                    args.batch_sizes,
                    task_count,
                    windows_by_batch_size,
                    args.paper_aligned,
                )
                write_json(model_root / "benchmark_summary.json", model_summary)
        model_summary = aggregate_model(
            model,
            model_root,
            args.batch_sizes,
            task_count,
            windows_by_batch_size,
            args.paper_aligned,
        )
        write_json(model_root / "benchmark_summary.json", model_summary)
        print(f"[{model}] complete", flush=True)
    combined = collect_compatible_model_summaries(
        args.output_root,
        task_count,
        args.batch_sizes,
        args.paper_aligned,
    )
    write_json(
        args.output_root / "benchmark_summary.json",
        {
            "schema_version": "local_question_audit_benchmark_v1",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "advisory_only": True,
            "writes_review_events": False,
            "tasks": str(args.tasks),
            "task_count": task_count,
            "paper_aligned": args.paper_aligned,
            "models": combined,
        },
    )


if __name__ == "__main__":
    main()
