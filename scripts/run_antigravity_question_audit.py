#!/usr/bin/env python3
"""Run a resumable, guarded question-text audit through Antigravity CLI.

This program must run within the logged-in macOS desktop session (for example,
from Terminal on the Mac Studio).  It saves raw model responses, operational
metrics, and validation results only.  It never imports AI events or changes
human review state.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

import llmshare_question_audit as audit
from run_llmshare_question_audit import (
    DispatchError,
    append_jsonl,
    archive_rejected,
    existing_complete,
    latency_decision,
    now_iso,
    validate_answer,
    write_json_atomic,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def exclusive_run_lock(root: Path):
    """Prevent concurrent workers from submitting the same audit dispatch."""
    lock_path = root / ".antigravity_question_audit.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit(
                f"Another Antigravity audit runner is already active for {root}. "
                "Wait for it to finish or use its STOP file."
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def shared_traffic_lock():
    """Keep every local AGY audit serial, even when their task roots differ."""

    lock_path = Path(
        os.environ.get(
            "AGY_TRAFFIC_LOCK",
            str(PROJECT_ROOT / "tmp" / ".antigravity_serial_traffic.lock"),
        )
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit(
                "Another local Antigravity request stream is active. "
                "Wait for it to finish or use its STOP file."
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model", default="gemini-3.6-flash-low")
    parser.add_argument("--strategy", default="24", choices=audit.DEFAULT_STRATEGIES)
    parser.add_argument("--effort", default="low", choices=("low", "medium", "high"))
    parser.add_argument("--max-dispatches", type=int, default=0, help="0 runs until completion or a circuit breaker")
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--soft-latency-ms", type=int, default=45_000)
    parser.add_argument("--hard-latency-ms", type=int, default=120_000)
    parser.add_argument("--latency-multiplier", type=float, default=2.5)
    parser.add_argument("--latency-window", type=int, default=10)
    parser.add_argument("--max-consecutive-latency-breaches", type=int, default=2)
    parser.add_argument("--cooldown-seconds", type=float, default=15.0)
    parser.add_argument("--retry-backoff-seconds", type=float, default=5.0)
    parser.add_argument("--state-path", type=Path)
    parser.add_argument("--journal-path", type=Path)
    parser.add_argument("--stop-file", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true", help="Print saved state without making requests")
    return parser.parse_args()


def load_manifest(path: Path, model: str, strategy: str) -> list[dict[str, Any]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    dispatches = [
        row
        for row in manifest.get("dispatches") or []
        if row.get("model") == model and row.get("strategy") == strategy
    ]
    if not dispatches:
        raise SystemExit("manifest has no dispatches matching model and strategy")
    return dispatches


def prompt_for_packet(packet: dict[str, Any], attempt: int) -> str:
    request = packet.get("request") if isinstance(packet.get("request"), dict) else {}
    instruction = str(request.get("system_instruction") or "").strip()
    questions = request.get("questions")
    if not instruction or not isinstance(questions, list) or not questions:
        raise DispatchError("Dispatch packet is missing its instruction or questions.", kind="packet", retryable=False)
    task = (
        "逐題依規則稽核下列 questions。每題都要回傳，candidate_key 必須原樣保留。"
        "只回傳 system instruction 指定的 <FINAL_JSON> JSON 陣列。"
        "不得新增、刪除、重新命名選項 key，也不得由黏連文字推測遺失選項。"
        "若題幹或選項邊界被吃掉、選項缺失或需要重切，這是 parser 問題："
        "recommended_action=fix_parser、correction_applicable=false、suggested_correction=null，"
        "並在 uncorrected_findings 說明需要 parser/PDF；絕不可輸出 options patch。"
    )
    if attempt > 1:
        task += (
            "這是格式修復重試：options 修正必須是完整的 [{\"key\":\"A\",\"text\":\"...\"}]"
            " 陣列；所有可安全修正 finding 必須被同一份 suggested_correction 完整涵蓋。"
        )
    return (
        "<system_instruction>\n"
        + instruction
        + "\n</system_instruction>\n\n<task>\n"
        + task
        + "\n</task>\n\n<context>\n"
        + json.dumps({"questions": questions}, ensure_ascii=False, separators=(",", ":"))
        + "\n</context>"
    )


def int_usage(value: Any) -> dict[str, int]:
    source = value if isinstance(value, dict) else {}
    return {
        key: int(raw)
        for key, raw in source.items()
        if key in {"input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens"}
        and isinstance(raw, (int, float))
    }


def antigravity_request(
    packet: dict[str, Any],
    *,
    model: str,
    effort: str,
    timeout_seconds: int,
    attempt: int,
) -> tuple[str, dict[str, int], str]:
    """Run an isolated, no-tools print request and return the model response."""
    executable = os.environ.get("AGY_BINARY", "agy")
    command = [
        executable,
        "--print",
        prompt_for_packet(packet, attempt),
        "--model",
        model,
        "--effort",
        effort,
        "--output-format",
        "json",
        "--print-timeout",
        f"{timeout_seconds}s",
    ]
    environment = {**os.environ, "PATH": "/opt/homebrew/bin:" + os.environ.get("PATH", "")}
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 15,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        raise DispatchError(
            f"Antigravity hard timeout after {timeout_seconds + 15} seconds.",
            kind="timeout",
            retryable=False,
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Antigravity exited without a diagnostic.").strip()
        raise DispatchError(f"Antigravity CLI failed: {detail[:2_000]}", kind="cli", retryable=False)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DispatchError("Antigravity CLI did not return JSON print output.", kind="response_json", retryable=True) from exc
    if not isinstance(payload, dict) or str(payload.get("status") or "") != "SUCCESS":
        detail = json.dumps(payload, ensure_ascii=False)[:2_000]
        raise DispatchError(f"Antigravity request was not successful: {detail}", kind="cli", retryable=False)
    answer = payload.get("response")
    if not isinstance(answer, str) or not answer.strip():
        raise DispatchError("Antigravity returned an empty model response.", kind="empty_response", retryable=True)
    return answer.strip(), int_usage(payload.get("usage")), str(payload.get("model") or model)


def build_state(root: Path, dispatches: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    completed = [row for row in dispatches if existing_complete(root, row, args.model)]
    return {
        "schema_version": "antigravity_guarded_question_audit_run_v1",
        "updated_at": now_iso(),
        "advisory_only": True,
        "imports_review_events": False,
        "model": args.model,
        "effort": args.effort,
        "strategy": args.strategy,
        "dispatch_total": len(dispatches),
        "dispatch_completed": len(completed),
        "question_total": sum(int(row["task_count"]) for row in dispatches),
        "question_completed": sum(int(row["task_count"]) for row in completed),
        "run_status": "dry_run" if args.dry_run else "running",
        "circuit_reason": None,
        "latency": {
            "successful_ms": [],
            "baseline_ms": None,
            "rolling_median_ms": None,
            "rolling_p95_ms": None,
            "consecutive_breaches": 0,
        },
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "thinking_tokens": 0,
            "cache_read_tokens": 0,
            "total_tokens": 0,
        },
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = args.manifest.resolve().parent
    with exclusive_run_lock(root):
        with shared_traffic_lock():
            return _run_locked(args)


def _run_locked(args: argparse.Namespace) -> dict[str, Any]:
    if args.max_attempts < 1 or args.max_attempts > 3:
        raise SystemExit("--max-attempts must be between 1 and 3")
    root = args.manifest.resolve().parent
    dispatches = load_manifest(args.manifest.resolve(), args.model, args.strategy)
    state_path = (args.state_path or root / "run_state.json").resolve()
    journal_path = (args.journal_path or root / "run_journal.jsonl").resolve()
    stop_file = (args.stop_file or root / "STOP").resolve()
    state = build_state(root, dispatches, args)
    if args.dry_run:
        write_json_atomic(state_path, state)
        return state
    write_json_atomic(state_path, state)
    successful_latencies: list[float] = []
    consecutive_breaches = 0
    completed_this_run = 0
    question_completed = int(state["question_completed"])
    completed_before = int(state["dispatch_completed"])
    for dispatch in dispatches:
        if existing_complete(root, dispatch, args.model):
            continue
        if stop_file.exists():
            state["run_status"] = "stopped"
            state["circuit_reason"] = f"stop_file:{stop_file}"
            break
        if args.max_dispatches and completed_this_run >= args.max_dispatches:
            state["run_status"] = "paused_at_limit"
            break
        packet = json.loads((root / str(dispatch["packet_path"])).read_text(encoding="utf-8"))
        completed = False
        for attempt in range(1, args.max_attempts + 1):
            started = time.monotonic()
            answer = ""
            try:
                answer, usage, response_model = antigravity_request(
                    packet,
                    model=args.model,
                    effort=args.effort,
                    timeout_seconds=args.timeout_seconds,
                    attempt=attempt,
                )
                validation = validate_answer(answer, packet, model=args.model)
                elapsed_ms = round((time.monotonic() - started) * 1000, 1)
                raw_path = root / str(dispatch["raw_result_path"])
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = raw_path.with_suffix(raw_path.suffix + ".tmp")
                temporary.write_text(answer, encoding="utf-8")
                temporary.replace(raw_path)
                successful_latencies.append(elapsed_ms)
                for key in state["usage"]:
                    state["usage"][key] += int(usage.get(key, 0))
                completed_this_run += 1
                question_completed += int(dispatch["task_count"])
                breach, threshold, baseline = latency_decision(
                    successful_latencies,
                    current_ms=elapsed_ms,
                    soft_latency_ms=args.soft_latency_ms,
                    hard_latency_ms=args.hard_latency_ms,
                    multiplier=args.latency_multiplier,
                )
                consecutive_breaches = consecutive_breaches + 1 if breach else 0
                append_jsonl(
                    journal_path,
                    {
                        "created_at": now_iso(),
                        "dispatch_id": dispatch["dispatch_id"],
                        "attempt": attempt,
                        "status": "completed",
                        "task_count": dispatch["task_count"],
                        "elapsed_ms": elapsed_ms,
                        "latency_threshold_ms": threshold,
                        "latency_breach": breach,
                        "response_model": response_model,
                        "usage": usage,
                        "validation": validation,
                    },
                )
                completed = True
                if breach and consecutive_breaches < args.max_consecutive_latency_breaches:
                    time.sleep(args.cooldown_seconds)
                break
            except DispatchError as exc:
                elapsed_ms = round((time.monotonic() - started) * 1000, 1)
                rejected_path = archive_rejected(root, dispatch["dispatch_id"], attempt, answer) if answer else None
                append_jsonl(
                    journal_path,
                    {
                        "created_at": now_iso(),
                        "dispatch_id": dispatch["dispatch_id"],
                        "attempt": attempt,
                        "status": "failed",
                        "kind": exc.kind,
                        "retryable": exc.retryable,
                        "elapsed_ms": elapsed_ms,
                        "error": str(exc)[:2_000],
                        "rejected_path": str(rejected_path.relative_to(root)) if rejected_path else None,
                    },
                )
                if not exc.retryable or attempt >= args.max_attempts:
                    state["run_status"] = "circuit_open"
                    state["circuit_reason"] = f"{dispatch['dispatch_id']}:{exc.kind}"
                    break
                time.sleep(args.retry_backoff_seconds * attempt)
        rolling = successful_latencies[-args.latency_window :]
        state.update(
            {
                "updated_at": now_iso(),
                "dispatch_completed": completed_before + completed_this_run,
                "question_completed": question_completed,
                "current_dispatch_id": dispatch["dispatch_id"],
            }
        )
        state["latency"] = {
            "successful_ms": rolling,
            "baseline_ms": round(statistics.median(successful_latencies[:5]), 1) if len(successful_latencies) >= 5 else None,
            "rolling_median_ms": round(statistics.median(rolling), 1) if rolling else None,
            "rolling_p95_ms": (
                round(statistics.quantiles(rolling, n=20, method="inclusive")[18], 1)
                if len(rolling) >= 2
                else (rolling[0] if rolling else None)
            ),
            "consecutive_breaches": consecutive_breaches,
        }
        write_json_atomic(state_path, state)
        if not completed or consecutive_breaches >= args.max_consecutive_latency_breaches:
            if consecutive_breaches >= args.max_consecutive_latency_breaches:
                state["run_status"] = "circuit_open"
                state["circuit_reason"] = "consecutive_latency_breaches"
                write_json_atomic(state_path, state)
            break
    else:
        state["run_status"] = "completed"
    state["updated_at"] = now_iso()
    write_json_atomic(state_path, state)
    return state


def main() -> int:
    args = parse_args()
    state_path = (args.state_path or args.manifest.resolve().parent / "run_state.json").resolve()
    if args.status:
        if not state_path.exists():
            raise SystemExit(f"state file not found: {state_path}")
        print(state_path.read_text(encoding="utf-8").strip())
        return 0
    result = run(args)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("run_status") in {"completed", "paused_at_limit", "dry_run"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
