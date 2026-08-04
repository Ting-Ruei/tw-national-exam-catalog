#!/usr/bin/env python3
"""Run a resumable, advisory-only visual audit through Antigravity CLI.

The task directory must have been created by ``export_visual_ai_audit_batch.py``
with ``--chunk-size``.  Every task JSONL becomes one serial model dispatch.
Validated JSONL is written only after the response contains exactly one visual
advisory per candidate.  This runner never writes review events or visual
review decisions.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
import re
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

from run_llmshare_question_audit import append_jsonl, latency_decision, now_iso, write_json_atomic


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALID_VISUAL_STATUSES = {"visual_required_likely", "visual_not_required_likely", "visual_uncertain"}
VALID_ACTIONS = {"review_pdf_visual", "confirm_no_visual_required", "keep_visual_candidate"}
FINAL_JSON_RE = re.compile(r"^\s*<FINAL_JSON>\s*(\[.*\])\s*</FINAL_JSON>\s*$", re.S)


class VisualDispatchError(RuntimeError):
    def __init__(self, message: str, *, kind: str, retryable: bool) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable


@contextmanager
def exclusive_run_lock(root: Path):
    """Prevent two visual workers from sending the same task directory."""

    lock_path = root / ".antigravity_visual_audit.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit(f"Another visual audit runner is active for {root}.") from exc
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


@contextmanager
def audit_locks(root: Path):
    with exclusive_run_lock(root):
        with shared_traffic_lock():
            yield


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-dir", type=Path, required=True)
    parser.add_argument("--model", default="gemini-3.6-flash-low")
    parser.add_argument("--effort", default="low", choices=("low", "medium", "high"))
    parser.add_argument("--max-dispatches", type=int, default=0, help="0 runs until complete or circuit opens.")
    parser.add_argument("--max-attempts", type=int, default=3)
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
    parser.add_argument("--status", action="store_true")
    return parser.parse_args()


def task_paths(root: Path) -> list[Path]:
    chunks = root / "chunks"
    source = chunks if chunks.is_dir() else root
    paths = sorted(source.glob("visual_ai_audit_tasks__*.jsonl"))
    if not paths:
        raise SystemExit(f"No visual_ai_audit_tasks JSONL files found under {root}.")
    return paths


def result_path(task_path: Path) -> Path:
    name = task_path.name.replace("visual_ai_audit_tasks__", "visual_ai_audit_results__", 1)
    if name == task_path.name:
        raise ValueError(f"Unexpected visual task filename: {task_path.name}")
    return task_path.with_name(name)


def raw_path(root: Path, task_path: Path) -> Path:
    return root / "raw_responses" / task_path.with_suffix(".txt").name


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise VisualDispatchError(f"Invalid JSONL task row {number} in {path.name}.", kind="task_json", retryable=False) from exc
        if not isinstance(row, dict) or not str(row.get("candidate_key") or "").strip():
            raise VisualDispatchError(f"Task row {number} in {path.name} has no candidate_key.", kind="task_schema", retryable=False)
        fingerprint = str(row.get("source_fingerprint") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{32}", fingerprint):
            raise VisualDispatchError(
                f"Task row {number} in {path.name} has no valid source_fingerprint.",
                kind="task_schema",
                retryable=False,
            )
        row["source_fingerprint"] = fingerprint
        rows.append(row)
    keys = [str(row["candidate_key"]) for row in rows]
    if not rows or len(keys) != len(set(keys)):
        raise VisualDispatchError(f"Task {path.name} is empty or repeats candidate keys.", kind="task_schema", retryable=False)
    return rows


def visual_prompt(rows: list[dict[str, Any]], attempt: int) -> str:
    task = (
        "逐題判斷是否真的需要圖片或表格資產才能作答或審題。只做 advisory，"
        "不得修改題幹、選項、答案、題組或任何人工審核標記。每題都必須回傳一次。"
        "只有以下三種 visual_status：visual_required_likely、visual_not_required_likely、visual_uncertain。"
        "輸出必須且只能是 <FINAL_JSON> 與 </FINAL_JSON> 包住的一個 JSON 陣列，不能輸出 Markdown。"
        "陣列每項只能包含 candidate_key、visual_status、confidence、reason、evidence、recommended_human_action。"
        "recommended_human_action 只能是 review_pdf_visual、confirm_no_visual_required、keep_visual_candidate。"
        "真正 `<table>...</table>`、表中、下表、附表、如下表，或題文明確指向圖／表時，"
        "visual_status 應為 visual_required_likely；正式人工流程會以官方 PDF 截圖顯示表格。"
        "tablet、tablets、stable、metastable、tabletting、tablespoon 只是英文單字，絕不是表格線索，"
        "不得因為它們含有 table 字串而判定需圖。沒有明確圖片指向時，應標 visual_not_required_likely。"
        "若無法確定 PDF 是否另有圖表，標 visual_uncertain，不得猜測。"
    )
    if attempt > 1:
        task += "這是格式重試：candidate_key 必須與輸入完全一致，且不得漏題或重複題。"
    return (
        "<task>\n" + task + "\n</task>\n\n<context>\n"
        + json.dumps({"questions": rows}, ensure_ascii=False, separators=(",", ":"))
        + "\n</context>"
    )


def request(rows: list[dict[str, Any]], *, model: str, effort: str, timeout_seconds: int, attempt: int) -> tuple[str, dict[str, int], str]:
    command = [
        os.environ.get("AGY_BINARY", "agy"),
        "--print",
        visual_prompt(rows, attempt),
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
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 15,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        raise VisualDispatchError("Antigravity hard timeout.", kind="timeout", retryable=False) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "Antigravity exited without a diagnostic.").strip()
        raise VisualDispatchError(f"Antigravity CLI failed: {detail[:2_000]}", kind="cli", retryable=False)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise VisualDispatchError("Antigravity CLI did not return JSON print output.", kind="response_json", retryable=True) from exc
    if not isinstance(payload, dict) or payload.get("status") != "SUCCESS":
        raise VisualDispatchError(f"Antigravity request was not successful: {json.dumps(payload, ensure_ascii=False)[:2_000]}", kind="cli", retryable=False)
    answer = payload.get("response")
    if not isinstance(answer, str) or not answer.strip():
        raise VisualDispatchError("Antigravity returned an empty model response.", kind="empty_response", retryable=True)
    usage = {
        key: int(value)
        for key, value in (payload.get("usage") or {}).items()
        if key in {"input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens"}
        and isinstance(value, (int, float))
    }
    return answer.strip(), usage, str(payload.get("model") or model)


def validate_response(answer: str, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    match = FINAL_JSON_RE.fullmatch(answer)
    if not match:
        raise VisualDispatchError("Response must contain only one <FINAL_JSON> array.", kind="visual_schema_gate", retryable=True)
    try:
        output = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise VisualDispatchError("FINAL_JSON is not valid JSON.", kind="visual_schema_gate", retryable=True) from exc
    if not isinstance(output, list):
        raise VisualDispatchError("FINAL_JSON must be an array.", kind="visual_schema_gate", retryable=True)
    expected_keys = [str(row["candidate_key"]) for row in rows]
    fingerprints = {str(row["candidate_key"]): str(row["source_fingerprint"]) for row in rows}
    expected_set = set(expected_keys)
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for entry in output:
        if not isinstance(entry, dict):
            raise VisualDispatchError("Every visual result must be an object.", kind="visual_schema_gate", retryable=True)
        key = str(entry.get("candidate_key") or "").strip()
        if key not in expected_set or key in seen:
            raise VisualDispatchError("Response has an unknown or duplicate candidate_key.", kind="visual_schema_gate", retryable=True)
        status = str(entry.get("visual_status") or "").strip()
        action = str(entry.get("recommended_human_action") or "").strip()
        confidence = entry.get("confidence")
        if status not in VALID_VISUAL_STATUSES or action not in VALID_ACTIONS or not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
            raise VisualDispatchError(f"Invalid visual advisory fields for {key}.", kind="visual_schema_gate", retryable=True)
        reason = str(entry.get("reason") or "").strip()
        evidence = str(entry.get("evidence") or "").strip()
        if not reason or not evidence:
            raise VisualDispatchError(f"Visual advisory for {key} needs reason and evidence.", kind="visual_schema_gate", retryable=True)
        normalized.append(
            {
                "candidate_key": key,
                "source_fingerprint": fingerprints[key],
                "visual_status": status,
                "confidence": round(float(confidence), 4),
                "reason": reason,
                "evidence": evidence,
                "recommended_human_action": action,
            }
        )
        seen.add(key)
    if seen != expected_set:
        raise VisualDispatchError("Response omitted one or more task candidates.", kind="visual_schema_gate", retryable=True)
    normalized.sort(key=lambda item: expected_keys.index(item["candidate_key"]))
    metrics = {
        "returned_count": len(normalized),
        "required_count": sum(item["visual_status"] == "visual_required_likely" for item in normalized),
        "not_required_count": sum(item["visual_status"] == "visual_not_required_likely" for item in normalized),
        "uncertain_count": sum(item["visual_status"] == "visual_uncertain" for item in normalized),
    }
    return normalized, metrics


def completed_result(path: Path, rows: list[dict[str, Any]]) -> bool:
    if not path.exists():
        return False
    try:
        output = read_jsonl(path)
        expected = [str(row["candidate_key"]) for row in rows]
        return [str(row.get("candidate_key") or "") for row in output] == expected and all(
            str(row.get("visual_status") or "") in VALID_VISUAL_STATUSES
            and str(row.get("source_fingerprint") or "") == str(task_row.get("source_fingerprint") or "")
            for row, task_row in zip(output, rows)
        )
    except (OSError, VisualDispatchError):
        return False


def build_state(root: Path, paths: list[Path], args: argparse.Namespace) -> dict[str, Any]:
    rows_by_path = {path: read_jsonl(path) for path in paths}
    completed = [path for path, rows in rows_by_path.items() if completed_result(result_path(path), rows)]
    return {
        "schema_version": "antigravity_guarded_visual_audit_run_v1",
        "updated_at": now_iso(),
        "advisory_only": True,
        "imports_review_events": False,
        "model": args.model,
        "effort": args.effort,
        "dispatch_total": len(paths),
        "dispatch_completed": len(completed),
        "question_total": sum(len(rows) for rows in rows_by_path.values()),
        "question_completed": sum(len(rows_by_path[path]) for path in completed),
        "run_status": "running",
        "circuit_reason": None,
        "latency": {"successful_ms": [], "baseline_ms": None, "rolling_median_ms": None, "rolling_p95_ms": None, "consecutive_breaches": 0},
        "usage": {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "cache_read_tokens": 0, "total_tokens": 0},
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.max_attempts < 1 or args.max_attempts > 3:
        raise SystemExit("--max-attempts must be between 1 and 3")
    root = args.task_dir.resolve()
    with audit_locks(root):
        paths = task_paths(root)
        state_path = (args.state_path or root / "run_state.json").resolve()
        journal_path = (args.journal_path or root / "run_journal.jsonl").resolve()
        stop_file = (args.stop_file or root / "STOP").resolve()
        state = build_state(root, paths, args)
        write_json_atomic(state_path, state)
        successful_latencies: list[float] = []
        completed_this_run = 0
        completed_before = int(state["dispatch_completed"])
        question_completed = int(state["question_completed"])
        consecutive_breaches = 0
        for task_path in paths:
            rows = read_jsonl(task_path)
            output_path = result_path(task_path)
            if completed_result(output_path, rows):
                continue
            if stop_file.exists():
                state["run_status"] = "stopped"
                state["circuit_reason"] = f"stop_file:{stop_file}"
                break
            if args.max_dispatches and completed_this_run >= args.max_dispatches:
                state["run_status"] = "paused_at_limit"
                break
            completed = False
            for attempt in range(1, args.max_attempts + 1):
                started = time.monotonic()
                answer = ""
                try:
                    answer, usage, response_model = request(rows, model=args.model, effort=args.effort, timeout_seconds=args.timeout_seconds, attempt=attempt)
                    output, validation = validate_response(answer, rows)
                    target_raw = raw_path(root, task_path)
                    target_raw.parent.mkdir(parents=True, exist_ok=True)
                    target_raw.write_text(answer, encoding="utf-8")
                    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
                    temporary.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in output), encoding="utf-8")
                    temporary.replace(output_path)
                    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
                    successful_latencies.append(elapsed_ms)
                    for key in state["usage"]:
                        state["usage"][key] += int(usage.get(key, 0))
                    completed_this_run += 1
                    question_completed += len(rows)
                    breach, threshold, _baseline = latency_decision(successful_latencies, current_ms=elapsed_ms, soft_latency_ms=args.soft_latency_ms, hard_latency_ms=args.hard_latency_ms, multiplier=args.latency_multiplier)
                    consecutive_breaches = consecutive_breaches + 1 if breach else 0
                    append_jsonl(journal_path, {"created_at": now_iso(), "dispatch": task_path.name, "attempt": attempt, "status": "completed", "task_count": len(rows), "elapsed_ms": elapsed_ms, "latency_threshold_ms": threshold, "latency_breach": breach, "response_model": response_model, "usage": usage, "validation": validation})
                    completed = True
                    if breach and consecutive_breaches < args.max_consecutive_latency_breaches:
                        time.sleep(args.cooldown_seconds)
                    break
                except VisualDispatchError as exc:
                    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
                    rejected = root / "raw_rejected" / f"{task_path.stem}__attempt{attempt:02d}.txt"
                    if answer:
                        rejected.parent.mkdir(parents=True, exist_ok=True)
                        rejected.write_text(answer, encoding="utf-8")
                    append_jsonl(journal_path, {"created_at": now_iso(), "dispatch": task_path.name, "attempt": attempt, "status": "failed", "kind": exc.kind, "retryable": exc.retryable, "elapsed_ms": elapsed_ms, "error": str(exc)[:2_000], "rejected_path": str(rejected.relative_to(root)) if answer else None})
                    if not exc.retryable or attempt >= args.max_attempts:
                        state["run_status"] = "circuit_open"
                        state["circuit_reason"] = f"{task_path.name}:{exc.kind}"
                        break
                    time.sleep(args.retry_backoff_seconds * attempt)
            rolling = successful_latencies[-args.latency_window:]
            state.update({"updated_at": now_iso(), "dispatch_completed": completed_before + completed_this_run, "question_completed": question_completed, "current_dispatch": task_path.name})
            state["latency"] = {
                "successful_ms": rolling,
                "baseline_ms": round(statistics.median(successful_latencies[:5]), 1) if len(successful_latencies) >= 5 else None,
                "rolling_median_ms": round(statistics.median(rolling), 1) if rolling else None,
                "rolling_p95_ms": round(statistics.quantiles(rolling, n=20, method="inclusive")[18], 1) if len(rolling) >= 2 else (rolling[0] if rolling else None),
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
    state_path = (args.state_path or args.task_dir.resolve() / "run_state.json").resolve()
    if args.status:
        if not state_path.exists():
            raise SystemExit(f"state file not found: {state_path}")
        print(state_path.read_text(encoding="utf-8").strip())
        return 0
    result = run(args)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("run_status") in {"completed", "paused_at_limit"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
