#!/usr/bin/env python3
"""Probe the optional Mac-hosted Qwen MLX Ollama endpoint.

The default command is a read-only GET /v1/models check.  Generation is
explicitly opt-in with --live; image transport is additionally opt-in with
--image.  The probe accepts localhost for a same-host test and HTTPS
*.ts.net endpoints for Tailscale Serve.  It rejects public HTTP endpoints and
does not modify Tailscale, Ollama, the database, or repository artifacts.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from typing import Any


class ProbeError(ValueError):
    """Raised when the endpoint or response fails the probe contract."""


def describe() -> dict[str, Any]:
    return {
        "component_id": "probe_qwen_mlx_tailscale",
        "default_operation": "GET /v1/models",
        "live_operation": "POST /v1/chat/completions only with --live",
        "vision_operation": "include one local image only with --live --image",
        "accepted_endpoints": ["http://127.0.0.1:11434/v1", "https://<machine>.<tailnet>.ts.net/v1"],
        "rejected_endpoints": ["public HTTP", "0.0.0.0", "direct public tunnel"],
        "writes": False,
    }


def normalized_base_url(raw: str) -> tuple[str, str]:
    value = raw.strip().rstrip("/")
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.username or parsed.password:
        raise ProbeError("endpoint must not contain embedded credentials")
    if parsed.scheme not in {"http", "https"} or not host:
        raise ProbeError("endpoint must be an http(s) URL")

    if host in {"127.0.0.1", "localhost", "::1"}:
        if parsed.scheme != "http" and parsed.scheme != "https":
            raise ProbeError("localhost endpoint must use http or https")
        tailnet_safe = "localhost"
    elif host.endswith(".ts.net") and parsed.scheme == "https":
        tailnet_safe = "tailscale_serve"
    else:
        raise ProbeError("only localhost or HTTPS *.ts.net Tailscale Serve endpoints are allowed")

    path = parsed.path.rstrip("/")
    if path not in {"", "/v1"}:
        raise ProbeError("endpoint path must be empty or /v1")
    path = "/v1"
    base = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    return base, tailnet_safe


def request_json(url: str, method: str, payload: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json", "User-Agent": "ai395-qwen-mlx-probe/1"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        # Ollama's OpenAI-compatible endpoint ignores this placeholder; it
        # keeps the adapter contract compatible with OpenAI-style clients.
        headers["Authorization"] = "Bearer ollama"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(4 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", errors="replace")
        raise ProbeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProbeError(f"request failed for {url}: {exc}") from exc
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeError(f"endpoint returned non-JSON data for {url}") from exc
    if not isinstance(result, dict):
        raise ProbeError(f"endpoint returned a non-object JSON response for {url}")
    return result


def listed_models(payload: dict[str, Any]) -> list[str]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise ProbeError("/models response does not contain a data array")
    models: list[str] = []
    for row in data:
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            models.append(row["id"])
    return sorted(set(models))


def build_live_payload(model: str, image: Path | None) -> dict[str, Any]:
    content: str | list[dict[str, Any]]
    if image is None:
        content = 'Return exactly this JSON object and no markdown: {"probe":"ok"}'
    else:
        mime = mimetypes.guess_type(image.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(image.read_bytes()).decode("ascii")
        content = [
            {"type": "text", "text": 'Return exactly this JSON object and no markdown: {"probe":"vision_ok"}'},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
        ]
    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0,
        "max_tokens": 32,
        "stream": False,
        "response_format": {"type": "json_object"},
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    raw_base = args.base_url or os.environ.get("QWEN_MLX_BASE_URL", "http://127.0.0.1:11434/v1")
    model = args.model or os.environ.get("QWEN_MLX_MODEL", "qwen3.8:27b-mlx")
    if not model.strip():
        raise ProbeError("model tag is empty")
    base_url, endpoint_class = normalized_base_url(raw_base)
    models_payload = request_json(f"{base_url}/models", "GET", None, args.timeout)
    models = listed_models(models_payload)
    if model not in models:
        raise ProbeError(f"model {model!r} is not listed by the endpoint; available={models}")

    result: dict[str, Any] = {
        "status": "pass",
        "endpoint": base_url,
        "endpoint_class": endpoint_class,
        "model": model,
        "listed_models": models,
        "live": bool(args.live),
        "vision": bool(args.image),
        "writes": False,
    }
    if args.image:
        if not args.live:
            raise ProbeError("--image requires --live")
        image = Path(args.image).resolve()
        if not image.is_file():
            raise ProbeError(f"image does not exist: {image}")
        if image.stat().st_size > 10 * 1024 * 1024:
            raise ProbeError("probe image is larger than 10 MiB")
    else:
        image = None
    if args.live:
        completion = request_json(
            f"{base_url}/chat/completions",
            "POST",
            build_live_payload(model, image),
            args.timeout,
        )
        choices = completion.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProbeError("completion response has no choices")
        result["response_model"] = completion.get("model")
        result["choice_count"] = len(choices)
        result["response_contract"] = "choices_present"
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="QWEN_MLX_BASE_URL; localhost or HTTPS *.ts.net /v1")
    parser.add_argument("--model", help="QWEN_MLX_MODEL; exact Ollama model tag")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--live", action="store_true", help="send one explicit generation request")
    parser.add_argument("--image", help="optional local image for the explicit live vision probe")
    parser.add_argument("--describe", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.describe:
        print(json.dumps(describe(), ensure_ascii=False, sort_keys=True))
        return 0
    try:
        print(json.dumps(run(args), ensure_ascii=False, sort_keys=True))
        return 0
    except (ProbeError, OSError) as exc:
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}", "writes": False}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
