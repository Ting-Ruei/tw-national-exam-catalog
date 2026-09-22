# -*- coding: utf-8 -*-
"""Which local engine answers, and how to ask it to stop thinking.

This module exists because of a **silent** failure, not a crash.

How to turn reasoning off is a property of the *engine*, and every engine ignores the spelling that
belongs to another one with HTTP 200. Measured on the local servers, all serving the same model
family:

    MTPLX (`18120`)              -> `chat_template_kwargs.enable_thinking=False`
                                      (`reasoning_effort` is accepted and ignored)
    vLLM / Splash (`8088`)       -> `reasoning_effort: "none"`
                                      (`chat_template_kwargs` is accepted and **ignored**)
    DGX Spark vLLM (`8888`)      -> `reasoning_effort: "none"`
    mlx-vlm (`8082`)             -> top-level `enable_thinking` only

The Splash line was measured directly, three times on the same question:

    default                            31.8s   reasoning_chars=612
    reasoning_effort: none              6.7s   reasoning_chars=0
    chat_template_kwargs (enable_thinking: False)  31.5s   reasoning_chars=612

So a caller that sends the MTPLX spelling to Splash gets a successful response, a plausible answer,
and 4.7x the latency - and nothing in the response says so. That is how a "thinking off" run keeps
thinking, and it is why the spelling is stored **with the engine** here rather than written at each
call site. There was a copy of the wrong spelling in `reread.py`; the only way a second copy cannot
appear is for there to be no second copy.

`ask()` is the one request builder. It is in the library rather than in a script because both a
script (`ask_about_blocks.py`, `confirm_dispute.py`) and a library module (`reread.py`) need it, and
a library importing a script is a dependency that runs the script's argument parsing on import.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

#: The engines, by name. `splash` is the default: it is the one measured to find defects the smaller
#: engine calls NONE - on `108030:305 q076` it reported `FIGURE_MISSING` where ornith said NONE, and
#: on `q049` it named the Kangxi radicals the deterministic scan had decided were harmless. One
#: engine answering everything is also the point: two engines answering the same question is how
#: this project ends up with two records that disagree and no rule for which one is right.
#:
#: `reasoning`/`thinking` is the engine's own off-switch; exactly one of them is set per engine,
#: because sending both is how the ignored one hides. A port is a parameter, so every URL here is
#: overridable from the environment.
ENDPOINTS = {
    "splash": {"url": os.environ.get("QBR_SPLASH_BASE_URL", "http://127.0.0.1:8088"),
               "name": os.environ.get("QBR_SPLASH_MODEL", "incoai/Qwen3.8-27B-Splash"),
               "key": os.environ.get("QBR_SPLASH_API_KEY", ""),
               "reasoning": "none"},
    "mtplx-35b": {"url": os.environ.get("QBR_MODEL_BASE_URL", "http://127.0.0.1:18120"),
                  "name": os.environ.get("QBR_MODEL_NAME", "ornith-1.5-mtplx-35b"),
                  "key": os.environ.get("QBR_MODEL_API_KEY", "mtplx"),
                  "thinking": {"chat_template_kwargs": {"enable_thinking": False}}},
    "qwen3.8-flash-next": {"url": "http://192.168.10.90:8888", "name": "qwen3.8-flash-next",
                           "key": "dgx-spark-local", "reasoning": "none"},
}


def endpoint_url(base: str) -> str:
    base = base.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base + "/chat/completions"


def body_for(endpoint, messages, *, max_tokens, temperature=0):
    """The request body, with the engine's own thinking switch applied.

    Raises if the endpoint declares neither switch: an engine that was added without deciding how to
    stop thinking would otherwise run with reasoning on and nobody would notice, which is the exact
    failure this module was written to prevent.
    """
    body = {"model": endpoint["name"], "messages": messages, "max_tokens": max_tokens,
            "temperature": temperature}
    if endpoint.get("reasoning"):
        body["reasoning_effort"] = endpoint["reasoning"]
    elif endpoint.get("thinking"):
        body.update(endpoint["thinking"])
    else:
        raise ValueError("endpoint %r has no thinking control" % endpoint.get("name"))
    return body


def ask(messages, *, endpoint, max_tokens, timeout, temperature=0):
    """One call. Returns `(raw_response_or_None, seconds)`; never raises on transport error.

    The raw response is returned unparsed because the callers disagree about what to do with it: the
    finding pass parses it into a verdict, the transcription pass reads `content` as text. Parsing
    here would make one of those two the owner of the schema.
    """
    request = urllib.request.Request(
        endpoint_url(endpoint["url"]),
        data=json.dumps(body_for(endpoint, messages, max_tokens=max_tokens,
                                 temperature=temperature)).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + endpoint.get("key", "")})
    started = time.time()
    try:
        raw = json.loads(urllib.request.urlopen(request, timeout=timeout).read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        return None, time.time() - started
    return raw, time.time() - started


def content_of(raw) -> str:
    """The assistant's text out of a raw response, or `""`.

    Empty-string rather than raise: a response with no content is a thing that happens (a truncated
    completion, a refusal), and every caller's next step handles text of any length. Raising here
    would turn "the model said nothing" into "the run died", which loses the record of the attempt.
    """
    if not raw:
        return ""
    message = (raw.get("choices") or [{}])[0].get("message") or {}
    return message.get("content") or ""


def usage_of(raw) -> dict:
    return (raw or {}).get("usage") or {}


def named(name: str):
    """One engine by name, with a message that lists the choices when the name is wrong."""
    if name not in ENDPOINTS:
        raise KeyError("unknown engine %r; known: %s" % (name, ", ".join(sorted(ENDPOINTS))))
    return ENDPOINTS[name]
