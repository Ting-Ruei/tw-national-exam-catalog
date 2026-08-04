#!/usr/bin/env python3
"""Run resumable, latency-guarded LLM Share question-audit dispatches.

The runner writes raw model responses and operational metrics only.  It never
imports AI events or changes human review state.  Run ``llmshare_question_audit
normalize`` after completion, validate that output, and use the explicit SQL
import gate separately.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import statistics
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

import llmshare_question_audit as audit


DEFAULT_API_BASE = "https://llm-share.duotify.com/v1"
KEYCHAIN_SERVICE = "codex-llmshare"
MAX_RESPONSE_BYTES = 2_000_000
SCHEMA_GATE_WARNINGS = {
    "invalid_or_excess_issue_families",
    "unsafe_or_empty_suggested_correction_removed",
    "findings_missing_or_invalid",
    "explicit_finding_without_complete_correction",
    "complete_coverage_without_complete_patch",
    "omitted_correction_without_itemized_reason",
    "partial_coverage_without_correction",
}
SAFE_TRAFFIC_HEADERS = {
    "x-litellm-response-cost",
    "x-litellm-key-spend",
    "x-litellm-key-max-budget",
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-reset-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-tokens",
}
SAFE_KEY_INFO_FIELDS = {
    "spend",
    "max_budget",
    "soft_budget",
    "budget_duration",
    "budget_reset_at",
    "expires",
    "rpm_limit",
    "tpm_limit",
    "model_spend",
}


class DispatchError(RuntimeError):
    """A bounded transport or validation failure."""

    def __init__(self, message: str, *, kind: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable


@contextlib.contextmanager
def wall_clock_timeout(seconds: int) -> Any:
    """Enforce a process-side deadline even if the HTTP stack remains blocked."""
    if seconds <= 0 or not hasattr(signal, "setitimer"):
        yield
        return

    def raise_timeout(_signum: int, _frame: Any) -> None:
        raise TimeoutError(f"wall-clock timeout after {seconds} seconds")

    previous_handler = signal.signal(signal.SIGALRM, raise_timeout)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model", default="gemma4:31b")
    parser.add_argument("--strategy", default="24", choices=audit.DEFAULT_STRATEGIES)
    parser.add_argument("--max-dispatches", type=int, default=0, help="0 runs until completion or a circuit breaker")
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--max-tokens", type=int, default=12_000)
    parser.add_argument("--temperature", type=float, default=0.0)
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
    parser.add_argument("--budget-check-every", type=int, default=10)
    parser.add_argument("--max-budget-utilization", type=float, default=0.9)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true", help="Print the saved state without making requests")
    parser.add_argument("--budget-status", action="store_true", help="Print a sanitized /key/info snapshot and exit")
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(audit.canonical_json(value) + "\n")


def resolve_api_key() -> str:
    key = os.environ.get("LLMSHARE_API_KEY", "").strip()
    if key:
        return key
    account = os.environ.get("USER", "").strip()
    if not account:
        account = subprocess.check_output(
            ["/usr/bin/id", "-un"],
            text=True,
            timeout=5,
        ).strip()
    command = [
        "/usr/bin/security",
        "find-generic-password",
        "-s",
        KEYCHAIN_SERVICE,
        "-a",
        account,
        "-w",
    ]
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    key = result.stdout.strip()
    if result.returncode != 0 or not key:
        raise DispatchError(
            "LLM Share API key is unavailable in the environment or macOS Keychain.",
            kind="authentication",
            retryable=False,
        )
    return key


def api_base() -> str:
    value = os.environ.get("LLMSHARE_API_BASE", DEFAULT_API_BASE).strip().rstrip("/")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise DispatchError("LLMSHARE_API_BASE is invalid.", kind="configuration", retryable=False)
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise DispatchError("Remote LLM Share endpoints must use HTTPS.", kind="configuration", retryable=False)
    return value


def key_usage_snapshot(timeout_seconds: int = 10) -> dict[str, Any]:
    root = api_base()
    if root.endswith("/v1"):
        root = root[:-3]
    request_object = urllib.request.Request(
        root + "/key/info",
        headers={
            "Authorization": f"Bearer {resolve_api_key()}",
            "Accept": "application/json",
            "User-Agent": "tw-national-exam-llmshare-runner/1.0",
        },
        method="GET",
    )
    try:
        with wall_clock_timeout(timeout_seconds):
            with urllib.request.urlopen(request_object, timeout=timeout_seconds) as response:
                raw = response.read(200_001)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise DispatchError(
            f"Unable to read LLM Share key usage: {exc}",
            kind="usage_monitor",
            retryable=False,
        ) from exc
    if len(raw) > 200_000:
        raise DispatchError(
            "LLM Share key usage response exceeded the size limit.",
            kind="usage_monitor",
            retryable=False,
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DispatchError(
            "LLM Share key usage response was not JSON.",
            kind="usage_monitor",
            retryable=False,
        ) from exc
    info = payload.get("info") if isinstance(payload, dict) else None
    if not isinstance(info, dict):
        info = payload if isinstance(payload, dict) else {}
    safe = {key: info[key] for key in SAFE_KEY_INFO_FIELDS if key in info}
    spend = safe.get("spend")
    max_budget = safe.get("max_budget")
    utilization = None
    if (
        isinstance(spend, (int, float))
        and isinstance(max_budget, (int, float))
        and max_budget > 0
    ):
        utilization = round(float(spend) / float(max_budget), 6)
    return {
        "checked_at": now_iso(),
        "available": True,
        "utilization": utilization,
        **safe,
    }


def completion_request(
    packet: dict[str, Any],
    *,
    model: str,
    max_tokens: int,
    temperature: float,
    timeout_seconds: int,
    attempt: int,
) -> tuple[str, dict[str, int], str, dict[str, str]]:
    request = packet.get("request") if isinstance(packet.get("request"), dict) else {}
    instruction = str(request.get("system_instruction") or "").strip()
    questions = request.get("questions")
    if not instruction or not isinstance(questions, list) or not questions:
        raise DispatchError("Dispatch packet is missing its instruction or questions.", kind="packet", retryable=False)
    task = (
        "逐題依規則稽核下列 questions。每題都要回傳，candidate_key 必須原樣保留。"
        "只回傳 system instruction 指定的 <FINAL_JSON> JSON 陣列。"
    )
    if attempt > 1:
        task += (
            "這是格式修復重試：options 修正必須是完整的 [{\"key\":\"A\",\"text\":\"...\"}]"
            " 陣列；所有可安全修正 finding 必須被同一份 suggested_correction 完整涵蓋。"
        )
    user_content = (
        f"<task>\n{task}\n</task>\n\n<context>\n"
        + json.dumps({"questions": questions}, ensure_ascii=False, separators=(",", ":"))
        + "\n</context>"
    )
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": user_content},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request_object = urllib.request.Request(
        api_base() + "/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {resolve_api_key()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "tw-national-exam-llmshare-runner/1.0",
        },
        method="POST",
    )
    try:
        with wall_clock_timeout(timeout_seconds):
            with urllib.request.urlopen(request_object, timeout=timeout_seconds) as response:
                traffic_headers = {
                    key.lower(): value
                    for key, value in response.headers.items()
                    if key.lower() in SAFE_TRAFFIC_HEADERS
                }
                raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = exc.read(2_000).decode("utf-8", errors="replace")
        if exc.code == 429:
            raise DispatchError(
                f"LLM Share rate limit reached: HTTP 429: {detail}",
                kind="rate_limit",
                retryable=False,
            ) from exc
        raise DispatchError(
            f"LLM Share HTTP {exc.code}: {detail}",
            kind="http",
            retryable=500 <= exc.code < 600,
        ) from exc
    except TimeoutError as exc:
        raise DispatchError(str(exc), kind="timeout", retryable=False) from exc
    except urllib.error.URLError as exc:
        raise DispatchError(str(exc), kind="transport", retryable=True) from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise DispatchError("LLM Share response exceeded the size limit.", kind="response_size", retryable=False)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DispatchError("LLM Share returned invalid response JSON.", kind="response_json", retryable=True) from exc
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise DispatchError("LLM Share response has no choices[0].", kind="response_shape", retryable=True)
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise DispatchError("LLM Share response has no assistant message.", kind="response_shape", retryable=True)
    answer = message.get("content")
    if not isinstance(answer, str) or not answer.strip():
        answer = message.get("reasoning_content")
    if not isinstance(answer, str) or not answer.strip():
        raise DispatchError("LLM Share returned an empty answer.", kind="empty_response", retryable=True)
    raw_usage = payload.get("usage")
    usage = {
        key: int(value)
        for key, value in (raw_usage.items() if isinstance(raw_usage, dict) else [])
        if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
        and isinstance(value, (int, float))
    }
    return answer.strip(), usage, str(payload.get("model") or model), traffic_headers


def validate_answer(
    answer: str,
    packet: dict[str, Any],
    *,
    model: str,
) -> dict[str, Any]:
    expected_keys = [str(value) for value in packet.get("candidate_keys") or []]
    expected_set = set(expected_keys)
    questions = {
        str(row.get("candidate_key") or ""): row
        for row in ((packet.get("request") or {}).get("questions") or [])
        if isinstance(row, dict)
    }
    try:
        raw_rows = audit.parse_raw_response(answer)
    except (ValueError, json.JSONDecodeError) as exc:
        raise DispatchError(str(exc), kind="audit_json", retryable=True) from exc
    returned_keys = [str(row.get("candidate_key") or "") for row in raw_rows]
    if len(returned_keys) != len(expected_keys) or set(returned_keys) != expected_set:
        raise DispatchError(
            f"candidate coverage mismatch: expected={len(expected_keys)}, returned={len(returned_keys)}",
            kind="candidate_coverage",
            retryable=True,
        )
    if len(set(returned_keys)) != len(returned_keys):
        raise DispatchError("duplicate candidate_key in model response", kind="candidate_coverage", retryable=True)
    warning_counts: dict[str, int] = {}
    gate_failures: list[str] = []
    flagged = 0
    corrections = 0
    for raw_row in raw_rows:
        key = str(raw_row.get("candidate_key") or "")
        try:
            normalized, warnings = audit.normalize_model_row(raw_row, model, audit.PROMPT_VERSION)
        except ValueError as exc:
            raise DispatchError(f"{key}: {exc}", kind="audit_schema", retryable=True) from exc
        if normalized["status"] != "pass":
            flagged += 1
        if normalized.get("suggested_correction"):
            corrections += 1
        for warning in warnings:
            warning_counts[warning] = warning_counts.get(warning, 0) + 1
            if warning in SCHEMA_GATE_WARNINGS:
                gate_failures.append(f"{key}:{warning}")
        correction = normalized.get("suggested_correction")
        if isinstance(correction, dict) and isinstance(correction.get("options"), list):
            source_options = ((questions.get(key) or {}).get("content") or {}).get("options") or []
            expected_option_keys = [
                str(option.get("key") or "")
                for option in source_options
                if isinstance(option, dict)
            ]
            returned_option_keys = [
                str(option.get("key") or "")
                for option in correction["options"]
                if isinstance(option, dict)
            ]
            if returned_option_keys != expected_option_keys:
                gate_failures.append(f"{key}:options_patch_not_complete")
    if gate_failures:
        preview = ", ".join(gate_failures[:8])
        raise DispatchError(
            f"schema gate rejected {len(gate_failures)} issue(s): {preview}",
            kind="audit_schema_gate",
            retryable=True,
        )
    return {
        "returned_count": len(raw_rows),
        "flagged_count": flagged,
        "suggested_correction_count": corrections,
        "warning_counts": warning_counts,
    }


def archive_rejected(root: Path, dispatch_id: str, attempt: int, answer: str) -> Path:
    path = root / "raw_attempts" / f"{dispatch_id}__attempt{attempt:02d}_rejected.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(answer, encoding="utf-8")
    return path


def latency_decision(
    successful_latencies: list[float],
    *,
    current_ms: float,
    soft_latency_ms: int,
    hard_latency_ms: int,
    multiplier: float,
) -> tuple[bool, float, float | None]:
    baseline = (
        statistics.median(successful_latencies[:5])
        if len(successful_latencies) >= 5
        else None
    )
    threshold = max(float(soft_latency_ms), (baseline or 0.0) * multiplier)
    return current_ms >= hard_latency_ms or current_ms > threshold, threshold, baseline


def load_manifest(path: Path, model: str, strategy: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    dispatches = [
        row
        for row in manifest.get("dispatches") or []
        if row.get("model") == model and row.get("strategy") == strategy
    ]
    if not dispatches:
        raise SystemExit("manifest has no dispatches matching model and strategy")
    return manifest, dispatches


def existing_complete(root: Path, dispatch: dict[str, Any], model: str) -> bool:
    raw_path = root / str(dispatch["raw_result_path"])
    if not raw_path.exists():
        return False
    packet = json.loads((root / str(dispatch["packet_path"])).read_text(encoding="utf-8"))
    try:
        validate_answer(raw_path.read_text(encoding="utf-8"), packet, model=model)
    except (OSError, DispatchError):
        return False
    return True


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.max_attempts < 1 or args.max_attempts > 3:
        raise SystemExit("--max-attempts must be between 1 and 3")
    manifest_path = args.manifest.resolve()
    root = manifest_path.parent
    _manifest, dispatches = load_manifest(manifest_path, args.model, args.strategy)
    state_path = (args.state_path or root / "run_state.json").resolve()
    journal_path = (args.journal_path or root / "run_journal.jsonl").resolve()
    stop_file = (args.stop_file or root / "STOP").resolve()
    completed_before = sum(existing_complete(root, row, args.model) for row in dispatches)
    state: dict[str, Any] = {
        "schema_version": "llmshare_guarded_question_audit_run_v1",
        "updated_at": now_iso(),
        "advisory_only": True,
        "imports_review_events": False,
        "model": args.model,
        "strategy": args.strategy,
        "dispatch_total": len(dispatches),
        "dispatch_completed": completed_before,
        "question_total": sum(int(row["task_count"]) for row in dispatches),
        "question_completed": sum(
            int(row["task_count"])
            for row in dispatches
            if existing_complete(root, row, args.model)
        ),
        "run_status": "dry_run" if args.dry_run else "running",
        "circuit_reason": None,
        "latency": {
            "successful_ms": [],
            "baseline_ms": None,
            "rolling_median_ms": None,
            "rolling_p95_ms": None,
            "consecutive_breaches": 0,
        },
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "budget": None,
    }
    if args.dry_run:
        write_json_atomic(state_path, state)
        return state
    resolve_api_key()
    try:
        state["budget"] = key_usage_snapshot()
    except DispatchError as exc:
        state["budget"] = {
            "checked_at": now_iso(),
            "available": False,
            "error": str(exc)[:500],
        }
    if (
        isinstance(state["budget"], dict)
        and isinstance(state["budget"].get("utilization"), (int, float))
        and state["budget"]["utilization"] >= args.max_budget_utilization
    ):
        state["run_status"] = "circuit_open"
        state["circuit_reason"] = "budget_utilization_limit"
        write_json_atomic(state_path, state)
        return state
    write_json_atomic(state_path, state)
    successful_latencies: list[float] = []
    consecutive_breaches = 0
    completed_this_run = 0
    question_completed = int(state["question_completed"])
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
        packet_path = root / str(dispatch["packet_path"])
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        completed = False
        for attempt in range(1, args.max_attempts + 1):
            started = time.monotonic()
            answer = ""
            try:
                answer, usage, response_model, traffic_headers = completion_request(
                    packet,
                    model=args.model,
                    max_tokens=args.max_tokens,
                    temperature=args.temperature,
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
                        "traffic_headers": traffic_headers,
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
                rejected_path = (
                    archive_rejected(root, dispatch["dispatch_id"], attempt, answer)
                    if answer
                    else None
                )
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
            "baseline_ms": (
                round(statistics.median(successful_latencies[:5]), 1)
                if len(successful_latencies) >= 5
                else None
            ),
            "rolling_median_ms": round(statistics.median(rolling), 1) if rolling else None,
            "rolling_p95_ms": (
                round(statistics.quantiles(rolling, n=20, method="inclusive")[18], 1)
                if len(rolling) >= 2
                else (rolling[0] if rolling else None)
            ),
            "consecutive_breaches": consecutive_breaches,
        }
        if (
            args.budget_check_every > 0
            and completed_this_run > 0
            and completed_this_run % args.budget_check_every == 0
        ):
            try:
                state["budget"] = key_usage_snapshot()
            except DispatchError as exc:
                state["budget"] = {
                    "checked_at": now_iso(),
                    "available": False,
                    "error": str(exc)[:500],
                }
            if (
                isinstance(state["budget"], dict)
                and isinstance(state["budget"].get("utilization"), (int, float))
                and state["budget"]["utilization"] >= args.max_budget_utilization
            ):
                state["run_status"] = "circuit_open"
                state["circuit_reason"] = "budget_utilization_limit"
        write_json_atomic(state_path, state)
        if (
            not completed
            or state.get("circuit_reason") == "budget_utilization_limit"
            or consecutive_breaches >= args.max_consecutive_latency_breaches
        ):
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
    if args.budget_status:
        print(json.dumps(key_usage_snapshot(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
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
