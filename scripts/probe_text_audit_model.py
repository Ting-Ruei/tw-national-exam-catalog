#!/usr/bin/env python3
"""Run a read-only, bounded text-evidence probe against blind audit packets."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path
from typing import Any


MAX_RESPONSE_BYTES = 2 * 1024 * 1024


SYSTEM_PROMPT = """You are a conservative Taiwan exam OCR diff classifier.
Never solve questions. PDF text proves source fidelity only. Exact source repair
requires matching nonempty text from at least two independent source_family
values. Empty question-level text means insufficient_evidence. 承上題 routes
to group review. Do not flag scientific genus abbreviations such as B. subtilis
or source wording such as 胜肽 without evidence. Never reverse an editorial
human normalization merely to reproduce an obvious official-source typo.
Return compact JSON only."""


TASK_PROMPT = """Audit every case. Return {cases:[{case_id,status,findings:[{
field,before,after,evidence_class,auto_repair_eligible}]}]}. status is one of
pass, exact_repair, editorial_rule_candidate, group_route,
insufficient_evidence. Whitespace/HTML/subscript cleanup is editorial. A change
that would turn a normal phrase into malformed language is never auto repair."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("ollama", "llmshare"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lanes", default="text,group")
    parser.add_argument(
        "--case-ids",
        default="",
        help="Optional comma-separated case ids for bounded one-case probes.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--max-tokens", type=int, default=1800)
    parser.add_argument("--ollama-base-url", default="http://127.0.0.1:11434")
    return parser.parse_args()


def read_packets(path: Path, lanes: set[str]) -> list[dict[str, Any]]:
    packets: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if str(row.get("lane") or "") not in lanes:
            continue
        pdf = row.get("official_pdf_second_source") or {}
        current = row.get("effective_candidate")
        mineru = row.get("mineru_or_parser")
        packet: dict[str, Any] = {
            "case_id": row.get("case_id"),
            "lane": row.get("lane"),
            "current": current,
            "current_and_mineru_same": current == mineru,
            "pdf": compact_pdf_evidence(pdf),
        }
        if current != mineru:
            packet["mineru"] = mineru
        packets.append(packet)
    return packets


def compact_pdf_evidence(pdf: dict[str, Any]) -> dict[str, Any]:
    """Collapse extractor outputs without pretending same-family variants are votes."""

    by_family: dict[str, list[str]] = {}
    empty_engines: list[str] = []
    engines = pdf.get("question_text_by_engine") or {}
    if isinstance(engines, dict):
        for engine, raw in engines.items():
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text") or "").strip()
            if not text:
                empty_engines.append(str(engine))
                continue
            family = str(raw.get("source_family") or engine)
            variants = by_family.setdefault(family, [])
            if text not in variants:
                variants.append(text)
    return {
        "question_consensus": pdf.get("question_consensus"),
        "family_variants": [
            {"source_family": family, "texts": texts}
            for family, texts in sorted(by_family.items())
        ],
        "empty_engines": sorted(empty_engines),
    }


def build_request(provider: str, model: str, packets: list[dict[str, Any]], max_tokens: int) -> dict[str, Any]:
    context = json.dumps(packets, ensure_ascii=False, separators=(",", ":"))
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": TASK_PROMPT + "\nCONTEXT=" + context},
    ]
    if provider == "ollama":
        return {
            "model": model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "think": False,
            "options": {"temperature": 0, "num_ctx": 16384, "num_predict": max_tokens},
        }
    return {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
        "response_format": {"type": "json_object"},
    }


def response_content(provider: str, payload: dict[str, Any]) -> tuple[str, str]:
    if provider == "ollama":
        message = payload.get("message") or {}
        content = str(message.get("content") or "").strip()
        if content:
            return content, "content"
        return str(message.get("thinking") or "").strip(), "thinking"
    choices = payload.get("choices") or []
    if not choices:
        return "", "missing"
    message = (choices[0] or {}).get("message") or {}
    content = str(message.get("content") or "").strip()
    if content:
        return content, "content"
    return str(message.get("reasoning_content") or "").strip(), "reasoning_content"


def main() -> int:
    args = parse_args()
    lanes = {lane.strip() for lane in args.lanes.split(",") if lane.strip()}
    packets = read_packets(args.packets, lanes)
    case_ids = {case_id.strip() for case_id in args.case_ids.split(",") if case_id.strip()}
    if case_ids:
        packets = [packet for packet in packets if str(packet.get("case_id") or "") in case_ids]
    if not packets:
        raise SystemExit("no packets matched the requested lanes")
    body = build_request(args.provider, args.model, packets, args.max_tokens)
    if args.provider == "ollama":
        url = args.ollama_base_url.rstrip("/") + "/api/chat"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
    else:
        from run_llmshare_question_audit import api_base, resolve_api_key

        url = api_base() + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {resolve_api_key()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=args.timeout_seconds) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise RuntimeError("model response exceeded size limit")
    payload = json.loads(raw)
    content, content_source = response_content(args.provider, payload)
    try:
        parsed_content: Any = json.loads(content)
    except json.JSONDecodeError:
        parsed_content = None
    usage = payload.get("usage") if args.provider == "llmshare" else {
        key: payload.get(key)
        for key in ("prompt_eval_count", "eval_count", "load_duration", "total_duration")
        if payload.get(key) is not None
    }
    result = {
        "schema_version": "national_exam_text_probe_v1",
        "advisory_only": True,
        "provider": args.provider,
        "requested_model": args.model,
        "case_count": len(packets),
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        "usage": usage,
        "strict_json": parsed_content is not None,
        "content_source": content_source,
        "finish_reason": (
            ((payload.get("choices") or [{}])[0] or {}).get("finish_reason")
            if args.provider == "llmshare"
            else payload.get("done_reason")
        ),
        "result": parsed_content,
        "raw_content": content if parsed_content is None else None,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
