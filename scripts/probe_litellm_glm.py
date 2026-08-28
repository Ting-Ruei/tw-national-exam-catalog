#!/usr/bin/env python3
"""Probe the LiteLLM test gateway for the GLM-5.3-Flash contract.

The default operation is a read-only authenticated GET /v1/models.  A live
text completion and a live vision completion require explicit ``--live``;
the latter additionally requires ``--image``.  The API key is read from
``LITELLM_API_KEY`` (or ``--api-key-env``) and is never printed or persisted.
This probe does not modify the repository, database, LiteLLM gateway, or
question assets.
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
from typing import Any

from ai395_llm_adapter import LLMAdapterError, normalized_openai_base_url


class ProbeError(ValueError):
    """Raised when the LiteLLM endpoint or response fails the probe contract."""


MAX_PROBE_IMAGE_BYTES = 10 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def describe() -> dict[str, Any]:
    return {
        "component_id": "probe_litellm_glm",
        "provider": "litellm_glm",
        "default_operation": "authenticated GET /v1/models",
        "live_operation": "POST /v1/chat/completions only with --live",
        "vision_operation": "include one local raster image only with --live --image",
        "model_default": "glm-5.3-flash",
        "accepted_endpoints": ["https://<internal-litellm-host>/v1", "http://localhost:<port>/v1"],
        "writes": False,
        "secret_output": False,
    }


def request_json(
    url: str,
    method: str,
    payload: dict[str, Any] | None,
    timeout: float,
    api_key: str,
) -> dict[str, Any]:
    body = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "ai395-glm-litellm-probe/1",
        "Authorization": f"Bearer {api_key}",
    }
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = exc.read(4_000).decode("utf-8", errors="replace")
        raise ProbeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProbeError(f"request failed for {url}: {exc}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProbeError("probe response exceeded the size limit")
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
    if image is None:
        content: str | list[dict[str, Any]] = (
            'Return exactly this JSON object and no markdown: {"probe":"ok"}'
        )
    else:
        mime = mimetypes.guess_type(image.name)[0] or "application/octet-stream"
        if not mime.startswith("image/"):
            raise ProbeError(f"probe image is not a raster image: {image.name}")
        encoded = base64.b64encode(image.read_bytes()).decode("ascii")
        content = [
            {
                "type": "text",
                "text": 'Return exactly this JSON object and no markdown: {"probe":"vision_ok"}',
            },
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
        ]
    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0,
        "max_tokens": 32,
        "stream": False,
        "reasoning_effort": "low",
        "thinking": {"type": "enabled", "clear_thinking": False},
        "response_format": {"type": "json_object"},
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    endpoint_env = str(args.base_url_env or "LITELLM_BASE_URL")
    key_env = str(args.api_key_env or "LITELLM_API_KEY")
    raw_base = args.base_url or os.environ.get(endpoint_env, "")
    api_key = os.environ.get(key_env, "").strip()
    model = (args.model or os.environ.get("LITELLM_MODEL", "glm-5.3-flash")).strip()
    if not raw_base:
        raise ProbeError(f"{endpoint_env} is not set")
    if not api_key:
        raise ProbeError(f"{key_env} is not set; do not paste the secret into the command line")
    if not model:
        raise ProbeError("model is empty")
    try:
        base_url, endpoint_class = normalized_openai_base_url(
            raw_base,
            allow_insecure_http=bool(args.allow_insecure_http),
        )
    except LLMAdapterError as exc:
        raise ProbeError(f"invalid LiteLLM endpoint: {exc}") from exc

    models_payload = request_json(f"{base_url}/models", "GET", None, args.timeout, api_key)
    models = listed_models(models_payload)
    model_listed = model in models
    if not model_listed and not args.allow_unlisted:
        raise ProbeError(f"model {model!r} is not listed by the endpoint; available={models}")

    image: Path | None = None
    if args.image:
        if not args.live:
            raise ProbeError("--image requires --live")
        image = Path(args.image).resolve()
        if not image.is_file():
            raise ProbeError(f"image does not exist: {image}")
        if image.stat().st_size > MAX_PROBE_IMAGE_BYTES:
            raise ProbeError("probe image is larger than 10 MiB")

    result: dict[str, Any] = {
        "status": "pass",
        "endpoint": base_url,
        "endpoint_class": endpoint_class,
        "provider": "litellm_glm",
        "model": model,
        "model_listed": model_listed,
        "listed_models": models,
        "api_key_configured": True,
        "live": bool(args.live),
        "vision": bool(image),
        "writes": False,
    }
    if args.live:
        completion = request_json(
            f"{base_url}/chat/completions",
            "POST",
            build_live_payload(model, image),
            args.timeout,
            api_key,
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
    parser.add_argument("--base-url", help="LiteLLM base URL; normally LITELLM_BASE_URL and ending in /v1")
    parser.add_argument("--base-url-env", default="LITELLM_BASE_URL")
    parser.add_argument("--api-key-env", default="LITELLM_API_KEY")
    parser.add_argument("--model", help="model alias; defaults to LITELLM_MODEL or glm-5.3-flash")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--live", action="store_true", help="send one explicit generation request")
    parser.add_argument("--image", help="optional local image for the explicit live vision probe")
    parser.add_argument("--allow-unlisted", action="store_true", help="allow a gateway whose /models omits the configured alias")
    parser.add_argument("--allow-insecure-http", action="store_true", help="explicitly allow non-loopback HTTP for a private test gateway")
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
        print(
            json.dumps(
                {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "writes": False},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
