#!/usr/bin/env python3
"""Probe whether an Ollama or LLM Share model actually receives image pixels.

The probe is advisory and read-only.  It sends one explicitly selected image,
records the exact model response and basic usage/latency metadata, and never
touches question candidates or review events.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=("llmshare", "ollama"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument(
        "--prompt",
        default=(
            "Read the image pixels. Return only JSON with keys "
            "vision_received, transcription, layout_summary, confidence."
        ),
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--max-tokens", type=int, default=800)
    parser.add_argument("--ollama-base-url", default="http://127.0.0.1:11434")
    return parser.parse_args()


def image_payload(path: Path) -> tuple[str, str, int]:
    data = path.read_bytes()
    if not data:
        raise ValueError(f"image is empty: {path}")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"image exceeds {MAX_IMAGE_BYTES} bytes: {path}")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    if mime not in {"image/png", "image/jpeg", "image/webp"}:
        raise ValueError(f"unsupported image MIME type: {mime}")
    return base64.b64encode(data).decode("ascii"), mime, len(data)


def build_request(
    *,
    provider: str,
    model: str,
    prompt: str,
    image_base64: str,
    mime_type: str,
    max_tokens: int,
) -> dict[str, Any]:
    if provider == "llmshare":
        return {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{image_base64}",
                                "detail": "high",
                            },
                        },
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
    if provider == "ollama":
        return {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [image_base64],
                }
            ],
            "stream": False,
            "format": "json",
            "think": False,
            "options": {"temperature": 0, "num_ctx": 8192},
        }
    raise ValueError(f"unsupported provider: {provider}")


def _response_content(provider: str, payload: dict[str, Any]) -> str:
    if provider == "ollama":
        message = payload.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"].strip()
        raise ValueError("Ollama response has no message.content")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("LLM Share response has no choices[0]")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("LLM Share response has no assistant message")
    content = message.get("content") or message.get("reasoning_content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM Share response is empty")
    return content.strip()


def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    encoded, mime, image_bytes = image_payload(args.image)
    body = build_request(
        provider=args.provider,
        model=args.model,
        prompt=args.prompt,
        image_base64=encoded,
        mime_type=mime,
        max_tokens=args.max_tokens,
    )
    if args.provider == "llmshare":
        from run_llmshare_question_audit import api_base, resolve_api_key

        url = api_base() + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {resolve_api_key()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "tw-national-exam-multimodal-probe/1.0",
        }
    else:
        url = args.ollama_base_url.rstrip("/") + "/api/chat"
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=args.timeout_seconds) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = exc.read(4_000).decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise RuntimeError("model response exceeded size limit")
    payload = json.loads(raw)
    content = _response_content(args.provider, payload)
    usage = payload.get("usage") if args.provider == "llmshare" else {
        key: payload.get(key)
        for key in ("prompt_eval_count", "eval_count", "load_duration", "total_duration")
        if payload.get(key) is not None
    }
    return {
        "schema_version": "national_exam_multimodal_probe_v1",
        "advisory_only": True,
        "provider": args.provider,
        "requested_model": args.model,
        "response_model": payload.get("model") or args.model,
        "image": str(args.image.resolve()),
        "image_mime_type": mime,
        "image_bytes": image_bytes,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        "usage": usage if isinstance(usage, dict) else {},
        "content": content,
    }


def main() -> int:
    args = parse_args()
    result = run_probe(args)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
