#!/usr/bin/env python3
"""Prepare, normalize, compare, and report advisory LLM Share question audits.

This command deliberately does not call a model or change review state.  It
turns a SQL worklist into paper-preserving packets for LLM Share, records the
expected raw-response locations, normalizes the returned JSON, and creates a
multi-model advisory result.  A separate explicit approval workflow is required
before any question can be reset for human re-review.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


DEFAULT_MODELS = (
    "gemma4:31b",
    "minimax-m2.7",
    "deepseek-v4-flash",
    "qwen3.5:397b",
    "gpt-oss:120b",
)
EXPERIMENTAL_MODELS = ("minimax-m3",)
# These are accepted by the shared normalizer, but are dispatched by their
# dedicated runner rather than through the LLM Share API.
EXTERNAL_AUDIT_MODELS = ("gemini-3.6-flash-low",)
SUPPORTED_MODELS = DEFAULT_MODELS + EXPERIMENTAL_MODELS + EXTERNAL_AUDIT_MODELS
DEFAULT_STRATEGIES = ("8", "16", "24", "half-paper", "full-paper")
PROMPT_VERSION = "llmshare_five_model_text_audit_v2"
ISSUE_FAMILIES = {
    "non_question_header",
    "boundary_merge",
    "boundary_missing",
    "empty_stem",
    "option_structure",
    "ocr_character",
    "notation_markup",
    "semantic_disfluency",
    "visual_dependency",
    "group_dependency",
}
STATUS_VALUES = {"pass", "needs_review", "block"}
RECOMMENDED_ACTIONS = {
    "none",
    "human_review_text",
    "human_review_pdf",
    "fix_parser",
    "add_manual_asset",
    "review_group",
    "review_answer",
}
FINDING_LOCATIONS = {"stem", "options", "group_ref", "group_sequence_no"} | {
    f"option_{key}" for key in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
}
CORRECTION_COVERAGE_VALUES = {"complete", "partial", "none"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")


def jsonl_paths(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(candidate for candidate in path.rglob("*.jsonl") if candidate.is_file())
    return [path]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for file_path in jsonl_paths(path):
        with file_path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"{file_path}:{line_no} is not a JSON object")
                rows.append(value)
    return rows


def question_sort_key(task: dict[str, Any]) -> tuple[Any, ...]:
    exam = task.get("exam") if isinstance(task.get("exam"), dict) else {}
    question = str(exam.get("question_number") or "")
    occurrence = str(exam.get("occurrence") or "1")
    return (
        int(question) if question.isdigit() else 10**9,
        question,
        int(occurrence) if occurrence.isdigit() else 10**9,
        str(task.get("candidate_key") or ""),
    )


def paper_key(task: dict[str, Any]) -> str:
    source_key = str(task.get("source_registry_key") or "").strip()
    if source_key:
        return source_key
    exam = task.get("exam") if isinstance(task.get("exam"), dict) else {}
    return "|".join(str(exam.get(name) or "") for name in ("category", "subject", "year", "ordinal"))


def grouped_tasks(tasks: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        key = str(task.get("candidate_key") or "")
        if not key:
            raise ValueError("task is missing candidate_key")
        groups[paper_key(task)].append(task)
    return [(key, sorted(rows, key=question_sort_key)) for key, rows in sorted(groups.items())]


def make_batches(tasks: list[dict[str, Any]], strategy: str) -> list[list[dict[str, Any]]]:
    """Keep every batch inside a source paper; never join unrelated papers."""
    if strategy not in DEFAULT_STRATEGIES:
        raise ValueError(f"unsupported strategy: {strategy}")
    batches: list[list[dict[str, Any]]] = []
    for _paper, rows in grouped_tasks(tasks):
        if strategy == "full-paper":
            batches.append(rows)
        elif strategy == "half-paper":
            midpoint = (len(rows) + 1) // 2
            batches.extend(chunk for chunk in (rows[:midpoint], rows[midpoint:]) if chunk)
        else:
            size = int(strategy)
            batches.extend(rows[index:index + size] for index in range(0, len(rows), size))
    return batches


def model_slug(model: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", model).strip("_") or "model"


def audit_instruction() -> str:
    return """你是臺灣國家考試題目文字稽核員。只檢查 OCR／轉寫品質：簡體字、錯別字、符號與公式標記、選項結構、題幹與選項語意因轉寫而不通順。不要判斷專業答案正確性，不要因專業知識改寫題幹或干擾選項，不要推測原卷不存在的文字，也不要改變任何人工審核標記。

每一題都必須回傳一個結果。只輸出 <FINAL_JSON> 與 </FINAL_JSON> 之間的一個 JSON 陣列，不能有 Markdown 或其他文字。每列必須含 candidate_key、stage="question"、status（pass|needs_review|block）、issue_families（最多三個；可用 non_question_header、boundary_merge、boundary_missing、empty_stem、option_structure、ocr_character、notation_markup、semantic_disfluency、visual_dependency、group_dependency）、confidence（0 到 1）、reason（繁中短句）、evidence（非 pass 至少一項可核對的欄位/原文/建議）、recommended_action（pass 必為 none；其餘用 human_review_text、human_review_pdf、fix_parser 或 add_manual_asset）、findings、correction_coverage、uncorrected_findings、suggested_correction。

findings 是最多三個逐項問題；每項必須含 issue_family、location（stem、option_A、option_B…、options、group_ref 或 group_sequence_no）、observed（原文最小片段）、suggested（可安全逐字修正時填最小替代片段，否則 null）、confidence、correction_applicable（布林值）、correction_omission_reason（能修時為 null；不能修時明確說明需 PDF、題組、圖片或人工判斷的原因）。

只要你指出明確的 observed→suggested 修正，correction_applicable 必須為 true，而且 suggested_correction 不得為 null。若同一題指出兩個或更多可安全修正的錯誤，suggested_correction 必須在同一份 patch 中完整修正全部錯誤，correction_coverage 必須為 "complete"。不得只修其中一處。

只有真的無法安全決定文字時才可省略 correction：correction_coverage 用 "partial" 或 "none"，並在 uncorrected_findings 逐項列出 {"finding_index":從 1 開始的序號,"reason":"無法修正原因"}。不能只說「需人工確認」而不指出不確定點。pass 題的 findings、evidence、uncorrected_findings 必須是空陣列，correction_coverage 必須為 "none"，suggested_correction 必須為 null。

suggested_correction 只可含 stem、options、group_ref、group_sequence_no。stem 必須是修正後完整題幹。options 必須是 JSON 陣列，每項只能是 {"key":"A","text":"修正後完整選項文字"}；絕不可輸出 key→text 的 JSON object。只修改實際有文字錯誤的欄位，禁止修改答案或用醫學／藥學知識「修正」本來就是干擾選項的內容。"""


def model_question(task: dict[str, Any]) -> dict[str, Any]:
    """Send only text needed for this audit; exclude answers and human history."""
    content = task.get("content") if isinstance(task.get("content"), dict) else {}
    signals = task.get("signals") if isinstance(task.get("signals"), dict) else {}
    raw_options = content.get("options") if isinstance(content.get("options"), list) else []
    safe_options = [
        {"key": option.get("key"), "text": option.get("text") or ""}
        for option in raw_options
        if isinstance(option, dict)
    ]
    raw_issues = signals.get("parser_issues") if isinstance(signals.get("parser_issues"), list) else []
    safe_issues = [
        {
            "code": issue.get("code") or issue.get("issue_code") or "",
            "severity": issue.get("severity") or "",
            "message": str(issue.get("message") or "")[:300],
        }
        for issue in raw_issues
        if isinstance(issue, dict)
    ]
    return {
        "candidate_key": task.get("candidate_key"),
        "question_number": (task.get("exam") or {}).get("question_number") if isinstance(task.get("exam"), dict) else None,
        "content": {
            "stem": content.get("stem") or "",
            "options": safe_options,
            "group_ref": content.get("group_ref"),
            "group_sequence_no": content.get("group_sequence_no"),
        },
        "parser_signals": {
            "issues": safe_issues,
            "exam_header_false_question": bool(signals.get("exam_header_false_question")),
        },
    }


def build_plan(tasks: list[dict[str, Any]], output_dir: Path, models: list[str], strategies: list[str]) -> dict[str, Any]:
    if not tasks:
        raise ValueError("no tasks supplied")
    keys = [str(task.get("candidate_key") or "") for task in tasks]
    if len(set(keys)) != len(keys):
        duplicates = [key for key, count in Counter(keys).items() if count > 1]
        raise ValueError(f"duplicate candidate_key in tasks: {duplicates[:5]}")
    prompt = audit_instruction()
    dispatches: list[dict[str, Any]] = []
    for strategy in strategies:
        for model in models:
            for index, batch in enumerate(make_batches(tasks, strategy), start=1):
                dispatch_id = f"{strategy.replace('-', '_')}__{model_slug(model)}__{index:05d}"
                request = {
                    "prompt_version": PROMPT_VERSION,
                    "system_instruction": prompt,
                    "questions": [model_question(task) for task in batch],
                }
                packet = {
                    "dispatch_id": dispatch_id,
                    "model": model,
                    "strategy": strategy,
                    "task_count": len(batch),
                    "candidate_keys": [str(task["candidate_key"]) for task in batch],
                    "paper_keys": sorted({paper_key(task) for task in batch}),
                    "input_hash": stable_hash(request),
                    "request": request,
                }
                packet_path = output_dir / "dispatches" / strategy / model_slug(model) / f"{dispatch_id}.json"
                write_json(packet_path, packet)
                dispatches.append(
                    {
                        **{key: packet[key] for key in ("dispatch_id", "model", "strategy", "task_count", "candidate_keys", "paper_keys", "input_hash")},
                        "packet_path": str(packet_path.relative_to(output_dir)),
                        "raw_result_path": str((Path("raw") / strategy / model_slug(model) / f"{dispatch_id}.txt")),
                    }
                )
    manifest = {
        "schema_version": "llmshare_question_audit_dispatch_v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "prompt_version": PROMPT_VERSION,
        "advisory_only": True,
        "models": models,
        "strategies": strategies,
        "task_count": len(tasks),
        "paper_count": len(grouped_tasks(tasks)),
        "task_hash": stable_hash(tasks),
        "dispatch_count": len(dispatches),
        "dispatches": dispatches,
        "usage": "Send each packet.request to the named LLM Share model. Save its exact response at raw_result_path, then run normalize and ensemble.",
    }
    write_json(output_dir / "manifest.json", manifest)
    write_json(output_dir / "SYSTEM_PROMPT.json", {"prompt_version": PROMPT_VERSION, "instruction": prompt})
    return manifest


def representative_dispatches(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Select the first packet for every model/strategy/paper tuple."""
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for dispatch in manifest.get("dispatches") or []:
        paper = str((dispatch.get("paper_keys") or [""])[0])
        group = (str(dispatch.get("model") or ""), str(dispatch.get("strategy") or ""), paper)
        if group in seen:
            continue
        seen.add(group)
        selected.append(dispatch)
    return selected


def benchmark_failure_ids(root: Path) -> set[str]:
    timings_path = root / "benchmark_timings.json"
    if not timings_path.exists():
        return set()
    payload = json.loads(timings_path.read_text(encoding="utf-8"))
    latest: dict[str, str] = {}
    for row in payload.get("runs") or []:
        dispatch_id = str(row.get("dispatch_id") or "")
        if dispatch_id:
            latest[dispatch_id] = str(row.get("status") or "completed")
    return {dispatch_id for dispatch_id, status in latest.items() if status != "completed"}


def next_benchmark_packet(manifest_path: Path, defer_failures: bool = False) -> dict[str, Any]:
    root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    deferred = benchmark_failure_ids(root) if defer_failures else set()
    for dispatch in representative_dispatches(manifest):
        if str(dispatch.get("dispatch_id") or "") in deferred:
            continue
        raw_path = root / str(dispatch["raw_result_path"])
        if raw_path.exists():
            try:
                rows = parse_raw_response(raw_path.read_text(encoding="utf-8"))
                returned = [str(row.get("candidate_key") or "") for row in rows]
                if len(returned) == len(dispatch["candidate_keys"]) and set(returned) == set(dispatch["candidate_keys"]):
                    continue
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        packet_path = root / str(dispatch["packet_path"])
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        return {
            "ok": True,
            "done": False,
            "remaining_count": sum(
                not _raw_dispatch_complete(root, item)
                for item in representative_dispatches(manifest)
            ),
            "dispatch": dispatch,
            "request": packet["request"],
        }
    remaining = sum(not _raw_dispatch_complete(root, item) for item in representative_dispatches(manifest))
    return {
        "ok": True,
        "done": True,
        "remaining_count": remaining,
        "deferred_failure_count": len(deferred),
    }


def _raw_dispatch_complete(root: Path, dispatch: dict[str, Any]) -> bool:
    raw_path = root / str(dispatch["raw_result_path"])
    if not raw_path.exists():
        return False
    try:
        rows = parse_raw_response(raw_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    returned = [str(row.get("candidate_key") or "") for row in rows]
    return len(returned) == len(dispatch["candidate_keys"]) and set(returned) == set(dispatch["candidate_keys"])


def save_raw_response(
    manifest_path: Path,
    dispatch_id: str,
    response_base64: str,
    elapsed_ms: int | float | None,
    replace_invalid: bool = False,
) -> dict[str, Any]:
    """Persist one exact external response and an idempotent local timing row."""
    root = manifest_path.parent.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    matches = [
        item for item in representative_dispatches(manifest)
        if str(item.get("dispatch_id") or "") == dispatch_id
    ]
    if len(matches) != 1:
        raise ValueError("dispatch_id is not a unique representative benchmark dispatch")
    dispatch = matches[0]
    try:
        response = base64.b64decode(response_base64, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("response_base64 is not valid UTF-8 base64") from exc
    raw_path = (root / str(dispatch["raw_result_path"])).resolve()
    if root not in raw_path.parents:
        raise ValueError("raw result path escapes manifest root")
    if raw_path.exists():
        if raw_path.read_text(encoding="utf-8") != response:
            if not replace_invalid or _raw_dispatch_complete(root, dispatch):
                raise FileExistsError(f"refusing to overwrite different raw response: {raw_path}")
            attempts_dir = root / "raw_attempts"
            attempts_dir.mkdir(parents=True, exist_ok=True)
            attempt_number = len(list(attempts_dir.glob(f"{dispatch_id}__attempt*.txt"))) + 1
            archived = attempts_dir / f"{dispatch_id}__attempt{attempt_number:02d}.txt"
            raw_path.replace(archived)
            raw_path.write_text(response, encoding="utf-8")
            state = "replaced_invalid"
        else:
            archived = None
            state = "already_saved"
    else:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(response, encoding="utf-8")
        archived = None
        state = "saved"
    timings_path = root / "benchmark_timings.json"
    timing_payload = (
        json.loads(timings_path.read_text(encoding="utf-8"))
        if timings_path.exists()
        else {"schema_version": "llmshare_benchmark_timings_v1", "runs": []}
    )
    runs = timing_payload.get("runs")
    if not isinstance(runs, list):
        raise ValueError("benchmark timings runs must be a list")
    existing = [row for row in runs if row.get("dispatch_id") == dispatch_id]
    if state != "already_saved":
        runs.append(
            {
                "dispatch_id": dispatch_id,
                "model": dispatch["model"],
                "strategy": dispatch["strategy"],
                "paper_key": (dispatch.get("paper_keys") or [""])[0],
                "task_count": dispatch["task_count"],
                "elapsed_ms": elapsed_ms,
                "attempt": len(existing) + 1,
                "status": "completed",
                "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
        )
        write_json(timings_path, timing_payload)
    return {
        "ok": True,
        "state": state,
        "dispatch_id": dispatch_id,
        "raw_result_path": str(raw_path),
        "archived_invalid_path": str(archived) if archived else None,
        "timing_recorded": state != "already_saved",
    }


def record_benchmark_failure(
    manifest_path: Path,
    dispatch_id: str,
    error_base64: str,
    elapsed_ms: int | float | None,
) -> dict[str, Any]:
    root = manifest_path.parent.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    matches = [
        item for item in representative_dispatches(manifest)
        if str(item.get("dispatch_id") or "") == dispatch_id
    ]
    if len(matches) != 1:
        raise ValueError("dispatch_id is not a unique representative benchmark dispatch")
    dispatch = matches[0]
    try:
        error = base64.b64decode(error_base64, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("error_base64 is not valid UTF-8 base64") from exc
    attempts_dir = root / "raw_attempts"
    attempts_dir.mkdir(parents=True, exist_ok=True)
    existing_attempts = list(attempts_dir.glob(f"{dispatch_id}__failure*.txt"))
    failure_path = attempts_dir / f"{dispatch_id}__failure{len(existing_attempts) + 1:02d}.txt"
    failure_path.write_text(error, encoding="utf-8")
    timings_path = root / "benchmark_timings.json"
    payload = (
        json.loads(timings_path.read_text(encoding="utf-8"))
        if timings_path.exists()
        else {"schema_version": "llmshare_benchmark_timings_v1", "runs": []}
    )
    runs = payload.get("runs")
    if not isinstance(runs, list):
        raise ValueError("benchmark timings runs must be a list")
    attempt = sum(row.get("dispatch_id") == dispatch_id for row in runs) + 1
    runs.append(
        {
            "dispatch_id": dispatch_id,
            "model": dispatch["model"],
            "strategy": dispatch["strategy"],
            "paper_key": (dispatch.get("paper_keys") or [""])[0],
            "task_count": dispatch["task_count"],
            "elapsed_ms": elapsed_ms,
            "attempt": attempt,
            "status": "transport_error",
            "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "error_path": str(failure_path.relative_to(root)),
        }
    )
    write_json(timings_path, payload)
    return {
        "ok": True,
        "dispatch_id": dispatch_id,
        "status": "transport_error",
        "failure_path": str(failure_path),
    }


def _json_value_from_text(text: str) -> Any:
    tagged = re.search(r"<FINAL_JSON>\s*(.*?)\s*</FINAL_JSON>", text, re.S | re.I)
    candidate = tagged.group(1) if tagged else text.strip()
    candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate.strip(), flags=re.I)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        value, _end = decoder.raw_decode(candidate.lstrip())
        return value


def parse_raw_response(text: str) -> list[dict[str, Any]]:
    value = _json_value_from_text(text)
    if isinstance(value, dict):
        value = value.get("results") or value.get("items") or value.get("data")
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError("response must contain a JSON array of result objects")
    return value


def valid_suggested_correction(row: dict[str, Any]) -> dict[str, Any] | None:
    """Return a UI-compatible safe patch, or None for malformed/empty patches."""
    correction = row.get("suggested_correction")
    if not isinstance(correction, dict) or not correction:
        return None
    if set(correction) - {"stem", "options", "group_ref", "group_sequence_no"}:
        return None
    safe: dict[str, Any] = {}
    if "stem" in correction:
        if not isinstance(correction["stem"], str):
            return None
        safe["stem"] = correction["stem"]
    if "options" in correction:
        options = correction["options"]
        if not isinstance(options, list) or not options:
            return None
        safe_options: list[dict[str, str]] = []
        seen_keys: set[str] = set()
        for option in options:
            if not isinstance(option, dict) or set(option) - {"key", "text"}:
                return None
            key = str(option.get("key") or "").strip()
            text = option.get("text")
            if not key or key in seen_keys or not isinstance(text, str):
                return None
            seen_keys.add(key)
            safe_options.append({"key": key, "text": text})
        safe["options"] = safe_options
    if "group_ref" in correction:
        group_ref = correction["group_ref"]
        if group_ref is not None and not isinstance(group_ref, str):
            return None
        safe["group_ref"] = group_ref
    if "group_sequence_no" in correction:
        sequence = correction["group_sequence_no"]
        if sequence is not None and (not isinstance(sequence, int) or isinstance(sequence, bool)):
            return None
        safe["group_sequence_no"] = sequence
    return safe or None


def normalize_findings(value: Any, status: str) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    if status == "pass":
        if value not in (None, []):
            warnings.append("pass_findings_not_empty")
        return [], warnings
    if not isinstance(value, list):
        return [], ["findings_missing_or_invalid"]
    findings: list[dict[str, Any]] = []
    if len(value) > 3:
        warnings.append("too_many_findings")
    for index, item in enumerate(value[:3], start=1):
        if not isinstance(item, dict):
            warnings.append(f"finding_{index}_not_object")
            continue
        family = str(item.get("issue_family") or "")
        location = str(item.get("location") or "")
        observed = item.get("observed")
        suggested = item.get("suggested")
        confidence = item.get("confidence")
        applicable = item.get("correction_applicable")
        omission_reason = item.get("correction_omission_reason")
        if family not in ISSUE_FAMILIES:
            warnings.append(f"finding_{index}_invalid_issue_family")
        if location not in FINDING_LOCATIONS:
            warnings.append(f"finding_{index}_invalid_location")
        if not isinstance(observed, str) or not observed.strip():
            warnings.append(f"finding_{index}_observed_required")
            observed = str(observed or "")
        if suggested is not None and not isinstance(suggested, str):
            warnings.append(f"finding_{index}_suggested_invalid")
            suggested = None
        if not isinstance(applicable, bool):
            warnings.append(f"finding_{index}_correction_applicable_required")
            applicable = False
        try:
            finding_confidence = float(confidence)
        except (TypeError, ValueError):
            warnings.append(f"finding_{index}_confidence_repaired")
            finding_confidence = 0.5
        if not 0 <= finding_confidence <= 1:
            warnings.append(f"finding_{index}_confidence_clamped")
            finding_confidence = max(0.0, min(finding_confidence, 1.0))
        if applicable and (not isinstance(suggested, str) or not suggested.strip() or suggested == observed):
            warnings.append(f"finding_{index}_applicable_without_replacement")
        if not applicable and not str(omission_reason or "").strip():
            warnings.append(f"finding_{index}_omission_reason_required")
        findings.append(
            {
                "issue_family": family,
                "location": location,
                "observed": observed,
                "suggested": suggested,
                "confidence": finding_confidence,
                "correction_applicable": applicable,
                "correction_omission_reason": None if omission_reason is None else str(omission_reason),
            }
        )
    if not findings:
        warnings.append("findings_missing_or_invalid")
    return findings, warnings


def correction_text_values(correction: dict[str, Any] | None) -> list[str]:
    if not correction:
        return []
    values: list[str] = []
    if isinstance(correction.get("stem"), str):
        values.append(correction["stem"])
    if isinstance(correction.get("options"), list):
        values.extend(
            str(option.get("text") or "")
            for option in correction["options"]
            if isinstance(option, dict)
        )
    if isinstance(correction.get("group_ref"), str):
        values.append(correction["group_ref"])
    if correction.get("group_sequence_no") is not None:
        values.append(str(correction["group_sequence_no"]))
    return values


def uncovered_correctable_findings(
    findings: list[dict[str, Any]],
    correction: dict[str, Any] | None,
) -> list[int]:
    patch_text = "\n".join(correction_text_values(correction))
    uncovered: list[int] = []
    for index, finding in enumerate(findings, start=1):
        if not finding.get("correction_applicable"):
            continue
        suggested = finding.get("suggested")
        if not isinstance(suggested, str) or not suggested.strip() or suggested not in patch_text:
            uncovered.append(index)
    return uncovered


def normalize_model_row(raw_row: dict[str, Any], model: str, prompt_version: str) -> tuple[dict[str, Any], list[str]]:
    """Make harmless shape repairs while retaining warnings for benchmarking.

    This never invents a correction.  It only turns common JSON-shape slips
    (such as evidence being a string) into the documented result contract.
    """
    row = dict(raw_row)
    warnings: list[str] = []
    status = str(row.get("status") or "")
    if status not in STATUS_VALUES:
        raise ValueError(f"invalid status: {status!r}")
    issues = row.get("issue_families")
    if not isinstance(issues, list):
        warnings.append("issue_families_not_list")
        issues = []
    valid_issues = [str(issue) for issue in issues if str(issue) in ISSUE_FAMILIES][:3]
    if len(valid_issues) != len(issues):
        warnings.append("invalid_or_excess_issue_families")
    evidence = row.get("evidence")
    if status == "pass":
        if issues or evidence not in (None, []):
            warnings.append("pass_payload_not_empty")
        valid_issues, evidence, action = [], [], "none"
    else:
        if not isinstance(evidence, list):
            warnings.append("evidence_not_list")
            evidence = [{"field": "model_output", "text": str(evidence or row.get("reason") or "模型未提供可核對證據。")}]
        if not evidence:
            warnings.append("evidence_missing")
            evidence = [{"field": "model_output", "text": str(row.get("reason") or "模型未提供可核對證據。")}]
        action = str(row.get("recommended_action") or "")
        if action not in RECOMMENDED_ACTIONS or action == "none":
            warnings.append("recommended_action_repaired")
            action = "human_review_text"
    try:
        confidence = float(row.get("confidence"))
    except (TypeError, ValueError):
        warnings.append("confidence_repaired")
        confidence = 0.5
    if not 0 <= confidence <= 1:
        warnings.append("confidence_clamped")
        confidence = max(0.0, min(confidence, 1.0))
    raw_correction = row.get("suggested_correction")
    if (
        isinstance(raw_correction, dict)
        and isinstance(raw_correction.get("options"), dict)
        and raw_correction["options"]
        and all(
            isinstance(key, str)
            and key.strip()
            and isinstance(text, str)
            for key, text in raw_correction["options"].items()
        )
    ):
        repaired_correction = dict(raw_correction)
        repaired_correction["options"] = [
            {"key": key, "text": raw_correction["options"][key]}
            for key in sorted(raw_correction["options"])
        ]
        row["suggested_correction"] = repaired_correction
        raw_correction = repaired_correction
        warnings.append("suggested_correction_options_object_repaired")
    correction = valid_suggested_correction(row)
    if raw_correction is not None and correction != raw_correction:
        warnings.append("unsafe_or_empty_suggested_correction_removed")
    findings, finding_warnings = normalize_findings(row.get("findings"), status)
    warnings.extend(finding_warnings)
    raw_coverage = str(row.get("correction_coverage") or "")
    raw_uncorrected = row.get("uncorrected_findings")
    if status == "pass":
        if raw_coverage not in {"", "none"}:
            warnings.append("pass_correction_coverage_not_none")
        if raw_uncorrected not in (None, []):
            warnings.append("pass_uncorrected_findings_not_empty")
        if correction is not None:
            warnings.append("pass_suggested_correction_not_null")
            correction = None
        coverage = "none"
        uncorrected: list[dict[str, Any]] = []
    else:
        if raw_coverage not in CORRECTION_COVERAGE_VALUES:
            warnings.append("invalid_correction_coverage")
            coverage = "none"
        else:
            coverage = raw_coverage
        if not isinstance(raw_uncorrected, list):
            warnings.append("uncorrected_findings_not_list")
            uncorrected = []
        else:
            uncorrected = [item for item in raw_uncorrected if isinstance(item, dict)]
            if len(uncorrected) != len(raw_uncorrected):
                warnings.append("uncorrected_findings_contains_non_object")
        uncovered = uncovered_correctable_findings(findings, correction)
        if uncovered:
            warnings.append("explicit_finding_without_complete_correction")
        if coverage == "complete" and (correction is None or uncovered):
            warnings.append("complete_coverage_without_complete_patch")
        if coverage in {"partial", "none"} and any(
            finding.get("correction_applicable") for finding in findings
        ) and not uncorrected:
            warnings.append("omitted_correction_without_itemized_reason")
        if coverage == "none" and correction is not None:
            warnings.append("none_coverage_with_correction")
        if coverage == "partial" and correction is None:
            warnings.append("partial_coverage_without_correction")
    return {
        **row,
        "stage": "question",
        "status": status,
        "issue_families": valid_issues,
        "confidence": confidence,
        "reason": str(row.get("reason") or "模型未提供理由。"),
        "evidence": evidence,
        "recommended_action": action,
        "findings": findings,
        "correction_coverage": coverage,
        "uncorrected_findings": uncorrected,
        "suggested_correction": correction,
        "model": model,
        "prompt_version": prompt_version,
    }, warnings


def normalize_results(
    manifest_path: Path,
    strategy: str,
    model: str,
    output_path: Path,
    dispatch_ids: list[str] | None = None,
) -> dict[str, Any]:
    root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selected = [
        dispatch for dispatch in manifest.get("dispatches") or []
        if dispatch.get("strategy") == strategy and dispatch.get("model") == model
    ]
    if not selected:
        raise ValueError("no matching dispatches in manifest")
    if dispatch_ids:
        requested = set(dispatch_ids)
        selected = [dispatch for dispatch in selected if dispatch.get("dispatch_id") in requested]
        unknown = requested - {str(dispatch.get("dispatch_id") or "") for dispatch in selected}
        if unknown:
            raise ValueError(f"dispatch ids do not match model/strategy: {sorted(unknown)}")
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for dispatch in selected:
        raw_path = root / str(dispatch["raw_result_path"])
        expected = set(dispatch["candidate_keys"])
        if not raw_path.exists():
            errors.append({"dispatch_id": dispatch["dispatch_id"], "error": "raw_response_missing", "path": str(raw_path)})
            continue
        try:
            raw_rows = parse_raw_response(raw_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append({"dispatch_id": dispatch["dispatch_id"], "error": "raw_response_invalid", "detail": str(exc)})
            continue
        received = set()
        for raw_row in raw_rows:
            key = str(raw_row.get("candidate_key") or "")
            if not key or key not in expected:
                errors.append({"dispatch_id": dispatch["dispatch_id"], "error": "unexpected_candidate_key", "candidate_key": key})
                continue
            if key in seen or key in received:
                errors.append({"dispatch_id": dispatch["dispatch_id"], "error": "duplicate_candidate_key", "candidate_key": key})
                continue
            received.add(key)
            seen.add(key)
            try:
                row, row_warnings = normalize_model_row(raw_row, model, manifest.get("prompt_version") or PROMPT_VERSION)
            except ValueError as exc:
                errors.append({"dispatch_id": dispatch["dispatch_id"], "error": "invalid_result_row", "candidate_key": key, "detail": str(exc)})
                continue
            if row_warnings:
                warnings.append({"dispatch_id": dispatch["dispatch_id"], "candidate_key": key, "warnings": row_warnings})
            row["candidate_key"] = key
            row["dispatch_id"] = dispatch["dispatch_id"]
            rows.append(row)
        for missing in sorted(expected - received):
            errors.append({"dispatch_id": dispatch["dispatch_id"], "error": "missing_candidate_result", "candidate_key": missing})
    write_jsonl(output_path, rows)
    report = {
        "ok": not errors,
        "model": model,
        "strategy": strategy,
        "expected_count": sum(int(item["task_count"]) for item in selected),
        "normalized_count": len(rows),
        "errors": errors,
        "warnings": warnings,
        "results_path": str(output_path),
    }
    write_json(output_path.with_suffix(".normalization.json"), report)
    return report


def benchmark_run(manifest_root: Path, dispatch: dict[str, Any], elapsed_ms: int | float | None) -> dict[str, Any]:
    raw_path = manifest_root / str(dispatch["raw_result_path"])
    expected = set(dispatch["candidate_keys"])
    errors: list[str] = []
    warning_counts: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    candidate_summaries: list[dict[str, Any]] = []
    if not raw_path.exists():
        errors.append("raw_response_missing")
    else:
        try:
            raw_rows = parse_raw_response(raw_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"raw_response_invalid:{exc}")
            raw_rows = []
        seen: set[str] = set()
        for raw_row in raw_rows:
            key = str(raw_row.get("candidate_key") or "")
            if key not in expected:
                errors.append("unexpected_candidate_key")
                continue
            if key in seen:
                errors.append("duplicate_candidate_key")
                continue
            seen.add(key)
            try:
                normalized, row_warnings = normalize_model_row(
                    raw_row,
                    str(dispatch["model"]),
                    PROMPT_VERSION,
                )
                warning_counts.update(row_warnings)
                rows.append(normalized)
                findings = normalized.get("findings") if isinstance(normalized.get("findings"), list) else []
                correctable_count = sum(bool(item.get("correction_applicable")) for item in findings if isinstance(item, dict))
                uncovered = uncovered_correctable_findings(findings, normalized.get("suggested_correction"))
                candidate_summaries.append(
                    {
                        "candidate_key": key,
                        "status": normalized.get("status"),
                        "issue_families": normalized.get("issue_families") or [],
                        "finding_count": len(findings),
                        "correctable_finding_count": correctable_count,
                        "covered_correctable_finding_count": max(0, correctable_count - len(uncovered)),
                        "correction_coverage": normalized.get("correction_coverage"),
                        "has_suggested_correction": bool(normalized.get("suggested_correction")),
                        "suggested_correction": normalized.get("suggested_correction"),
                        "warnings": row_warnings,
                    }
                )
            except ValueError as exc:
                errors.append(f"invalid_result_row:{key}:{exc}")
        if expected - seen:
            errors.append(f"missing_candidate_result:{len(expected - seen)}")
    correctable_findings = sum(item["correctable_finding_count"] for item in candidate_summaries)
    covered_findings = sum(item["covered_correctable_finding_count"] for item in candidate_summaries)
    return {
        "dispatch_id": dispatch["dispatch_id"],
        "model": dispatch["model"],
        "strategy": dispatch["strategy"],
        "paper_key": (dispatch.get("paper_keys") or [""])[0],
        "expected_count": len(expected),
        "returned_count": len(rows),
        "complete": not errors and len(rows) == len(expected),
        "elapsed_ms": elapsed_ms,
        "latency_per_question_ms": round(float(elapsed_ms) / len(expected), 1) if elapsed_ms is not None and expected else None,
        "normalization_warning_count": sum(warning_counts.values()),
        "normalization_warning_rate": round(sum(warning_counts.values()) / len(rows), 3) if rows else None,
        "normalization_warning_counts": dict(warning_counts),
        "needs_review_count": sum(row.get("status") != "pass" for row in rows),
        "suggested_correction_count": sum(bool(row.get("suggested_correction")) for row in rows),
        "finding_count": sum(len(row.get("findings") or []) for row in rows),
        "correctable_finding_count": correctable_findings,
        "covered_correctable_finding_count": covered_findings,
        "correction_completion_rate": (
            round(covered_findings / correctable_findings, 3) if correctable_findings else None
        ),
        "flagged_candidate_keys": sorted(
            str(row.get("candidate_key") or "") for row in rows if row.get("status") != "pass"
        ),
        "candidate_summaries": sorted(candidate_summaries, key=lambda item: item["candidate_key"]),
        "errors": errors,
    }


def build_benchmark_report(manifest_path: Path, timings_path: Path, output_path: Path, max_latency_ms: int) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    timing_payload = json.loads(timings_path.read_text(encoding="utf-8"))
    timings = {
        (str(row.get("model") or ""), str(row.get("strategy") or ""), str(row.get("dispatch_id") or "")): row.get("elapsed_ms")
        for row in timing_payload.get("runs") or []
        if isinstance(row, dict)
    }
    representative = representative_dispatches(manifest)
    runs = [
        benchmark_run(
            manifest_path.parent,
            dispatch,
            timings.get((str(dispatch["model"]), str(dispatch["strategy"]), str(dispatch["dispatch_id"]))),
        )
        for dispatch in representative
    ]
    paired_comparisons: list[dict[str, Any]] = []
    run_index = {
        (str(row["model"]), str(row["paper_key"]), str(row["strategy"])): row
        for row in runs
    }
    paired_strategies = ("24", "half-paper", "full-paper")
    for model in manifest.get("models") or []:
        papers = sorted({str(row["paper_key"]) for row in runs if row["model"] == model})
        for paper in papers:
            selected_runs = [run_index.get((str(model), paper, strategy)) for strategy in paired_strategies]
            if not all(selected_runs):
                continue
            summaries = {
                strategy: {
                    str(item["candidate_key"]): item
                    for item in run["candidate_summaries"]
                }
                for strategy, run in zip(paired_strategies, selected_runs)
                if run is not None
            }
            common_keys = set.intersection(*(set(rows) for rows in summaries.values()))
            flagged = {
                strategy: {
                    key for key in common_keys if rows[key].get("status") != "pass"
                }
                for strategy, rows in summaries.items()
            }
            corrections = {
                strategy: {
                    key for key in common_keys if rows[key].get("has_suggested_correction")
                }
                for strategy, rows in summaries.items()
            }
            base_flags = flagged["24"]
            strategy_rows: dict[str, Any] = {}
            for strategy in paired_strategies:
                union = base_flags | flagged[strategy]
                strategy_rows[strategy] = {
                    "common_question_count": len(common_keys),
                    "flagged_count": len(flagged[strategy]),
                    "correction_count": len(corrections[strategy]),
                    "flag_jaccard_vs_24": round(len(base_flags & flagged[strategy]) / len(union), 3) if union else 1.0,
                    "new_flags_vs_24": sorted(flagged[strategy] - base_flags),
                    "missed_24_flags": sorted(base_flags - flagged[strategy]),
                }
            paired_comparisons.append(
                {
                    "model": model,
                    "paper_key": paper,
                    "common_candidate_count": len(common_keys),
                    "strategies": strategy_rows,
                }
            )
    recommendation: dict[str, Any] = {}
    for model in manifest.get("models") or []:
        recommendation[model] = {
            "recommended_batch_size": None,
            "reason": "延遲只記錄、不作淘汰門檻；需依人工 gold precision/recall、修正完整率與跨策略穩定性決定。",
            "latency_is_observational_only": True,
        }
    report = {
        "schema_version": "llmshare_question_batch_benchmark_v2",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": {
            "paper_count": len({str(row["paper_key"]) for row in runs}),
            "models": manifest.get("models") or [],
            "strategies": manifest.get("strategies") or [],
            "selection": "每一模型、策略、試卷的第一個 packet；24/半份/全份以共同 candidate 做配對比較。",
        },
        "legacy_latency_reference_ms": max_latency_ms,
        "latency_policy": "只觀察，不因合理延遲排除模型或大批次。",
        "runs": runs,
        "paired_24_half_full": paired_comparisons,
        "recommendation": recommendation,
        "accuracy_note": "沒有人工黃金標記時，flag 數、跨策略重疊與 correction 完整率只能比較穩定性，不能宣稱為真實糾錯 precision/recall。",
    }
    write_json(output_path, report)
    return report


def result_by_key(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(path):
        key = str(row.get("candidate_key") or "")
        if not key or key in result:
            raise ValueError(f"duplicate or missing candidate_key in {path}: {key!r}")
        result[key] = row
    return result


def ensemble_row(candidate_key: str, members: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    non_pass = [(model, row) for model, row in members if row.get("status") != "pass"]
    issue_votes: Counter[str] = Counter()
    evidence: list[Any] = []
    for model, row in non_pass:
        for issue in row.get("issue_families") or []:
            if issue in ISSUE_FAMILIES:
                issue_votes[str(issue)] += 1
        for item in row.get("evidence") or []:
            evidence.append({"model": model, "evidence": item})
    correction_votes: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for model, row in members:
        correction = valid_suggested_correction(row)
        if correction:
            correction_votes[canonical_json(correction)].append((model, correction))
    shared = max(correction_votes.values(), key=len) if correction_votes else []
    suggested = shared[0][1] if len(shared) >= 2 else None
    status = "pass" if not non_pass else "needs_review"
    issue_families = [issue for issue, _count in issue_votes.most_common(3)]
    agreement = {
        "model_count": len(members),
        "non_pass_count": len(non_pass),
        "issue_votes": dict(issue_votes),
        "shared_correction_models": [model for model, _correction in shared] if suggested else [],
        "full_pass": len(non_pass) == 0,
    }
    if status == "pass":
        reason = "所有模型均未發現可核對的轉寫或文字格式異常。"
        action = "none"
        evidence = []
    else:
        reason = "LLM Share 模型標出可能的轉寫或文字格式異常，請人工依證據與原卷核對。"
        action = "human_review_text"
        if not evidence:
            evidence = [{"model": model, "reason": row.get("reason") or "模型回報需複核。"} for model, row in non_pass]
    return {
        "candidate_key": candidate_key,
        "stage": "question",
        "status": status,
        "issue_families": issue_families,
        "confidence": round(sum(float(row.get("confidence") or 0) for _model, row in members) / max(len(members), 1), 3),
        "reason": reason,
        "evidence": evidence[:12],
        "recommended_action": action,
        "suggested_correction": suggested,
        "model": "llmshare-multi-model-ensemble",
        "prompt_version": PROMPT_VERSION,
        "ensemble": agreement,
        "member_results": [{"model": model, **row} for model, row in members],
    }


def build_ensemble(tasks_path: Path, result_specs: list[str], output_path: Path) -> dict[str, Any]:
    tasks = read_jsonl(tasks_path)
    task_keys = [str(task.get("candidate_key") or "") for task in tasks]
    if len(set(task_keys)) != len(task_keys) or not all(task_keys):
        raise ValueError("tasks must contain unique non-empty candidate_key values")
    model_results: dict[str, dict[str, dict[str, Any]]] = {}
    for spec in result_specs:
        if "=" not in spec:
            raise ValueError("--result must use MODEL=PATH")
        model, raw_path = spec.split("=", 1)
        model_results[model] = result_by_key(Path(raw_path))
    missing_models = sorted(set(DEFAULT_MODELS) - set(model_results))
    extras = sorted(set(model_results) - set(DEFAULT_MODELS))
    if missing_models or extras:
        raise ValueError(f"results must be exactly {DEFAULT_MODELS}; missing={missing_models}; extra={extras}")
    missing = {
        model: sorted(set(task_keys) - set(rows))
        for model, rows in model_results.items()
        if set(task_keys) - set(rows)
    }
    if missing:
        raise ValueError(f"model results do not cover every task: { {model: len(keys) for model, keys in missing.items()} }")
    rows = [ensemble_row(key, [(model, model_results[model][key]) for model in DEFAULT_MODELS]) for key in task_keys]
    write_jsonl(output_path, rows)
    report = {
        "ok": True,
        "task_count": len(rows),
        "pass_count": sum(row["status"] == "pass" for row in rows),
        "needs_review_count": sum(row["status"] != "pass" for row in rows),
        "shared_correction_count": sum(bool(row.get("suggested_correction")) for row in rows),
        "issue_counts": dict(Counter(issue for row in rows for issue in row.get("issue_families") or [])),
        "results_path": str(output_path),
    }
    write_json(output_path.with_suffix(".ensemble.json"), report)
    return report


def build_catch_report(tasks_path: Path, ensemble_path: Path, output_path: Path) -> dict[str, Any]:
    tasks = {str(row.get("candidate_key") or ""): row for row in read_jsonl(tasks_path)}
    ensemble = read_jsonl(ensemble_path)
    candidates = []
    for result in ensemble:
        if result.get("status") == "pass":
            continue
        key = str(result.get("candidate_key") or "")
        task = tasks.get(key) or {}
        human = task.get("human_state") if isinstance(task.get("human_state"), dict) else {}
        action = str(human.get("action") or "")
        requires_reset = action not in {"", "unreviewed", "reset_review"}
        candidates.append(
            {
                "candidate_key": key,
                "effective_content_hash": task.get("effective_content_hash"),
                "exam": task.get("exam") or {},
                "current_human_action": action or "unreviewed",
                "current_human_notes": human.get("notes") or "",
                "requires_reset": requires_reset,
                "ai_issue_families": result.get("issue_families") or [],
                "ai_summary": result.get("reason") or "",
                "model_agreement": result.get("ensemble") or {},
                "suggested_correction": result.get("suggested_correction"),
            }
        )
    report = {
        "schema_version": "llmshare_question_catch_report_v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "advisory_only": True,
        "requires_explicit_human_approval_before_reset": True,
        "scope": {
            "task_count": len(tasks),
            "catch_count": len(candidates),
            "already_reviewed_reset_candidates": sum(item["requires_reset"] for item in candidates),
            "unreviewed_or_already_reset_candidates": sum(not item["requires_reset"] for item in candidates),
        },
        "candidates": candidates,
    }
    write_json(output_path, report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="Create model packets for all requested strategies.")
    plan.add_argument("--tasks", type=Path, required=True)
    plan.add_argument("--output-dir", type=Path, required=True)
    plan.add_argument("--model", action="append", dest="models")
    plan.add_argument("--strategy", action="append", dest="strategies", choices=DEFAULT_STRATEGIES)
    normalize = commands.add_parser("normalize", help="Extract strict JSON results from saved raw LLM Share responses.")
    normalize.add_argument("--manifest", type=Path, required=True)
    normalize.add_argument("--strategy", choices=DEFAULT_STRATEGIES, required=True)
    normalize.add_argument("--model", choices=SUPPORTED_MODELS, required=True)
    normalize.add_argument("--output", type=Path, required=True)
    normalize.add_argument("--dispatch-id", action="append", dest="dispatch_ids", help="Normalize only benchmark packet(s), not the full strategy.")
    ensemble = commands.add_parser("ensemble", help="Build an advisory multi-model result from validated result JSONL files.")
    ensemble.add_argument("--tasks", type=Path, required=True)
    ensemble.add_argument("--result", action="append", required=True, dest="results")
    ensemble.add_argument("--output", type=Path, required=True)
    report = commands.add_parser("catch-report", help="Report the AI catch scope; never reset review state.")
    report.add_argument("--tasks", type=Path, required=True)
    report.add_argument("--ensemble", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    benchmark = commands.add_parser("benchmark-report", help="Measure representative 8/16/24/half/full responses without importing them.")
    benchmark.add_argument("--manifest", type=Path, required=True)
    benchmark.add_argument("--timings", type=Path, required=True)
    benchmark.add_argument("--output", type=Path, required=True)
    benchmark.add_argument("--max-fixed-batch-latency-ms", type=int, default=60000)
    next_packet = commands.add_parser("next-benchmark", help="Return the next missing representative benchmark packet.")
    next_packet.add_argument("--manifest", type=Path, required=True)
    next_packet.add_argument("--defer-failures", action="store_true", help="Skip dispatches whose latest attempt was a transport error.")
    save_raw = commands.add_parser("save-raw", help="Save one exact representative response without importing it.")
    save_raw.add_argument("--manifest", type=Path, required=True)
    save_raw.add_argument("--dispatch-id", required=True)
    save_raw.add_argument("--response-base64", required=True)
    save_raw.add_argument("--elapsed-ms", type=float)
    save_raw.add_argument("--replace-invalid", action="store_true")
    record_failure = commands.add_parser("record-failure", help="Record a transport/tool failure without marking the dispatch complete.")
    record_failure.add_argument("--manifest", type=Path, required=True)
    record_failure.add_argument("--dispatch-id", required=True)
    record_failure.add_argument("--error-base64", required=True)
    record_failure.add_argument("--elapsed-ms", type=float)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "plan":
        result = build_plan(read_jsonl(args.tasks), args.output_dir.resolve(), args.models or list(DEFAULT_MODELS), args.strategies or list(DEFAULT_STRATEGIES))
        result = {
            "ok": True,
            "output_dir": str(args.output_dir.resolve()),
            "task_count": result["task_count"],
            "paper_count": result["paper_count"],
            "dispatch_count": result["dispatch_count"],
            "models": result["models"],
            "strategies": result["strategies"],
        }
    elif args.command == "normalize":
        result = normalize_results(args.manifest.resolve(), args.strategy, args.model, args.output.resolve(), args.dispatch_ids)
    elif args.command == "ensemble":
        result = build_ensemble(args.tasks, args.results, args.output.resolve())
    elif args.command == "catch-report":
        result = build_catch_report(args.tasks, args.ensemble, args.output.resolve())
    elif args.command == "next-benchmark":
        result = next_benchmark_packet(args.manifest.resolve(), args.defer_failures)
    elif args.command == "save-raw":
        result = save_raw_response(
            args.manifest.resolve(),
            args.dispatch_id,
            args.response_base64,
            args.elapsed_ms,
            args.replace_invalid,
        )
    elif args.command == "record-failure":
        result = record_benchmark_failure(
            args.manifest.resolve(),
            args.dispatch_id,
            args.error_base64,
            args.elapsed_ms,
        )
    else:
        result = build_benchmark_report(args.manifest.resolve(), args.timings.resolve(), args.output.resolve(), args.max_fixed_batch_latency_ms)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
