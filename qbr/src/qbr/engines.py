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
    DGX Spark vLLM (`timsdgx:8888`) -> `reasoning_effort: "none"`
    occamy mlx (`18130`)         -> `reasoning_effort: "none"` (the top-level `enable_thinking`
                                      also works; `chat_template_kwargs` is accepted and ignored)
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
#: because sending both is how the ignored one hides.
#:
#: **Every address is a parameter, and the parameters are read at call time, not frozen at import.**
#: `endpoints()` rebuilds the table, so a long-running orchestrator can be pointed at another host
#: without a restart and two processes can use different engines in the same second. The old shape -
#: a literal dict built once from `os.environ` at import - is why "point this at the DGX" required
#: editing code: the module-level dict could not be changed after import, and every caller
#: (`reread`, the scripts, the tests) held a reference to the frozen copy.
#:
#: Overrides, in increasing precedence:
#:   1. `BUILTIN_ENDPOINTS` below
#:   2. `QBR_ENDPOINTS_FILE` - a JSON object `{"<name>": {"url": ..., "name": ..., "key": ...}}`
#:   3. `QBR_ENGINE_<NAME>_URL` / `_MODEL` / `_KEY` (name upper-cased, `-` -> `_`)
#:   4. the legacy per-engine vars (`QBR_SPLASH_BASE_URL`, `QBR_MODEL_BASE_URL`, ...), which still win
#:      so that every existing run command keeps working.
#:
#: The thinking switch is **not** overridable by environment: how to turn reasoning off is a measured
#: property of the engine, and a wrong spelling is accepted with HTTP 200 and silently ignored. An
#: operator may repoint the address; they may not repoint the spellings.
BUILTIN_ENDPOINTS = {
    # Splash: vLLM, `reasoning_effort: none` (the other spelling is accepted and ignored - 31.5s vs
    # 6.7s on the same question).
    "splash": {"url": "http://127.0.0.1:8088",
               "name": "incoai/Qwen3.8-27B-Splash", "key": "", "reasoning": "none"},
    # ornith MTPLX: `chat_template_kwargs.enable_thinking=False` (measured 2026-09-27: the top-level
    # `enable_thinking` also works here; `reasoning_effort` is accepted and ignored).
    "mtplx-35b": {"url": "http://127.0.0.1:18120",
                  "name": "ornith-1.5-mtplx-35b", "key": "mtplx",
                  "thinking": {"chat_template_kwargs": {"enable_thinking": False}}},
    # occamy 6bit (mlx, vision). Measured 2026-09-27: `reasoning_effort: none` -> 3.0s/rc=0; the
    # top-level `enable_thinking: false` also works; `chat_template_kwargs` is accepted and IGNORED
    # (6.2s/rc=1449, the same as no switch at all).
    "occamy-6bit": {"url": "http://127.0.0.1:18130",
                    "name": "occamy-1.0-6bit-xl-mlx", "key": "", "reasoning": "none"},
    # DGX Spark (Tailscale `timsdgx`). The designer authorised calling it when extra tokens are
    # needed; it is reached over Tailscale, not the LAN address, so it works from any host on the
    # tailnet. Not the default: an experiment must name it.
    "dgx-flash": {"url": "http://timsdgx:8888", "name": "qwen3.8-flash-next",
                  "key": "dgx-spark-local", "reasoning": "none"},
}

#: Legacy per-engine variables, kept so every existing run command keeps working unchanged.
#: `(url_var, model_var, key_var)` per engine; an empty string means "no legacy variable".
_LEGACY_VARS = {
    "splash": ("QBR_SPLASH_BASE_URL", "QBR_SPLASH_MODEL", "QBR_SPLASH_API_KEY"),
    "mtplx-35b": ("QBR_MODEL_BASE_URL", "QBR_MODEL_NAME", "QBR_MODEL_API_KEY"),
    "dgx-flash": ("QBR_DGX_BASE_URL", "QBR_DGX_MODEL", "QBR_DGX_API_KEY"),
}


def _engine_env_prefix(name: str) -> str:
    return "QBR_ENGINE_" + name.upper().replace("-", "_")


def _overrides() -> dict:
    """`{"<name>": {"url"|"name"|"key": value}}` from the overrides file and the environment.

    Only the three fields that describe *where* the engine is may be overridden. The thinking switch
    is a measured property and is deliberately not in the list, so a mistyped switch cannot be
    introduced by an environment variable.
    """
    out: dict = {}
    path = os.environ.get("QBR_ENDPOINTS_FILE", "").strip()
    if path:
        try:
            with open(path, encoding="utf-8") as handle:
                for engine_name, values in (json.load(handle) or {}).items():
                    if isinstance(values, dict):
                        out.setdefault(engine_name, {}).update(
                            {k: v for k, v in values.items() if k in {"url", "name", "key"}})
        except (OSError, ValueError):
            # A malformed overrides file must not take every run down. `endpoints()` still returns the
            # built-in table, and the next call re-reads the file - so fixing it does not need a
            # restart, which is the point of reading it here instead of at import.
            pass
    for engine_name in set(BUILTIN_ENDPOINTS) | set(out):
        prefix = _engine_env_prefix(engine_name)
        legacy = _LEGACY_VARS.get(engine_name, ("", "", ""))
        pairs = (("url", os.environ.get(prefix + "_URL") or os.environ.get(legacy[0], "")),
                 ("name", os.environ.get(prefix + "_MODEL") or os.environ.get(legacy[1], "")),
                 ("key", os.environ.get(prefix + "_KEY") if os.environ.get(prefix + "_KEY") is not None
                        else os.environ.get(legacy[2], "")))
        for field, value in pairs:
            if value:
                out.setdefault(engine_name, {})[field] = value
    return out


def endpoints() -> dict:
    """The engine table, resolved **now**.

    Returns a fresh copy every call, so repointing an engine is a property of the moment rather than
    of when the process started. `reload()` writes the same result back to the module-level
    `ENDPOINTS` for callers (and tests) that still index it directly.
    """
    table = {name: dict(values) for name, values in BUILTIN_ENDPOINTS.items()}
    for name, values in _overrides().items():
        if name in table:
            table[name].update(values)
        else:
            raise KeyError("QBR_ENDPOINTS_FILE names unknown engine %r; known: %s"
                           % (name, ", ".join(sorted(table))))
    return table


def reload() -> dict:
    """Re-resolve `ENDPOINTS` and return it. Call after changing the environment."""
    global ENDPOINTS
    ENDPOINTS = endpoints()
    return ENDPOINTS


#: The resolved table, for callers that index it by name. Rebuilt by `reload()`; prefer
#: `endpoints()` in new code so a long-running process is not pinned to import time.
ENDPOINTS = endpoints()


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
    """One engine by name, resolved now, with a message that lists the choices when the name is wrong.

    Reads through `endpoints()` rather than the module-level `ENDPOINTS` so a caller that just set
    `QBR_ENGINE_<NAME>_URL` (or wrote the overrides file) gets the new address without a restart.
    """
    table = endpoints()
    if name not in table:
        raise KeyError("unknown engine %r; known: %s" % (name, ", ".join(sorted(table))))
    return table[name]
