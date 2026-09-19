"""Measure an OpenAI-compatible endpoint's throughput, honestly.

Two numbers matter and they are not the same number:

* **prefill** - how fast the prompt is read. This decides whether a 30,000-character question paper is
  a two-second job or a two-minute one, and it is what a long-context reader is bounded by.
* **decode** - how fast the answer is written. This decides the wall time of a per-crop call, because
  a reading is short and the prompt is long.

They are measured separately, because a single "tokens/s" hides which of the two is the limit. A run
that reports one number cannot tell you that a model reads 1,500 tokens a second and writes 18, which
is exactly the shape of a model that is fine for reading a paper and wrong for writing an essay.

Concurrency is measured as well, because a per-paper pipeline is 80 calls and the difference between
1 and 16 in flight is the difference between an afternoon and ten minutes - but only if the endpoint
does not start failing, so the error count is reported next to the throughput rather than after it.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("QBR_MODEL_BASE_URL", "http://127.0.0.1:18120")
API_KEY = os.environ.get("QBR_MODEL_API_KEY", "mtplx")
MODEL = os.environ.get("QBR_MODEL_NAME", "ornith-1.5-mtplx-35b")

# The spelling that turns reasoning off is not the same on every engine. Measured: MTPLX obeys
# `chat_template_kwargs`, vLLM obeys that *and* a top-level `reasoning_effort`, and each engine
# ignores the other's spelling silently - the request succeeds and the reasoning is still there.
# So the control is tried in order and the first one that actually removes the reasoning tokens wins,
# which is measured rather than assumed.
THINK_OFF_FORMS = (
    ("chat_template_kwargs", {"chat_template_kwargs": {"enable_thinking": False}}),
    ("reasoning_effort", {"reasoning_effort": "none"}),
)


def post(body, *, timeout=900):
    request = urllib.request.Request(
        BASE_URL.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + API_KEY},
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as handle:
            payload = json.loads(handle.read().decode())
    except urllib.error.HTTPError as exc:
        return None, time.time() - started, f"HTTP {exc.code}: {exc.read().decode()[:200]}"
    except Exception as exc:
        return None, time.time() - started, f"{type(exc).__name__}: {exc}"
    return payload, time.time() - started, None


def usage_of(payload):
    usage = payload.get("usage") or {}
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    return usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0, reasoning


def text_of(payload):
    message = (payload.get("choices") or [{}])[0].get("message") or {}
    return message.get("content") or message.get("reasoning") or ""


def which_reasoning_off():
    """Which spelling actually removes the reasoning, decided by measurement.

    Delegated to `qbr.vision`, which probes both directions with a question that *does* provoke
    reasoning. The local check that used to live here looked reasonable and was wrong twice over:
    it probed with a question that provokes nothing, and one engine (mlx-vlm) does not report
    `reasoning_tokens` at all, so `if not reasoning` was true for every spelling and the first one
    was reported as working. It is not enough to ask whether reasoning came back - a spelling can
    only be shown to work by removing reasoning that was demonstrably there.
    """
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
        from qbr import vision
        vision.BASE_URL, vision.MODEL, vision.API_KEY = BASE_URL, MODEL, API_KEY
        vision._THINKING_FORMS.clear()
        forms = vision._thinking_forms()
        return json.dumps(forms["off"], ensure_ascii=False), None, {
            "on": forms["on"], "on_reasoning": forms["reasoning_on"],
            "off": forms["off"], "off_reasoning": forms["reasoning_off"]}
    except Exception as exc:
        return None, f"probe failed: {exc}", None


def _unique_filler(chars, seed=0):
    """Fresh filler of roughly `chars` characters, with no repeated run long enough to be cached.

    Built from a vocabulary of Chinese words rather than one repeated sentence, because the whole
    point of the measurement is that no two prompts share a prefix.
    """
    import random
    rng = random.Random(seed)
    words = [
        "臨床", "檢驗", "品質", "控制", "醫學", "實驗", "數據", "分析", "標準", "程序",
        "樣本", "處理", "儀器", "校正", "誤差", "範圍", "參考", "數值", "結果", "報告",
        "病人", "檢體", "血液", "生化", "免疫", "微生物", "鏡檢", "培養", "染色", "試劑",
        "濃度", "稀釋", "反應", "時間", "溫度", "離心", "過濾", "分裝", "保存", "運送",
        "醫院", "部門", "人員", "訓練", "認證", "稽核", "紀錄", "追蹤", "改善", "風險",
    ]
    out = []
    total = 0
    while total < chars:
        word = rng.choice(words)
        out.append(word)
        total += len(word)
    return "".join(out)


def measure_prefill(sizes, reasoning_off, repeats=2):
    """Prefill throughput at several prompt lengths.

    Reported per length rather than averaged, because prefill is not linear in practice: the first
    pass pays for the weights and the later ones do not, and an average over both would describe
    neither.
    """
    rows = []
    for size in sizes:
        # The filler must not repeat. Measured with a repeating filler: 1,428 then 2,104 then 4,901
        # then 10,418 tokens a second, which is not a real curve - the later prompts shared a prefix
        # with the earlier ones and the engine serves a cached prefix without recomputing it. A
        # "throughput" that grows with length is measuring the cache, not the model. Each size gets
        # fresh text so the number is a prefill and not a lookup.
        prompt = "以下是一段文字，請只回答「收到」。\n" + _unique_filler(size)
        best = None
        for _ in range(repeats):
            body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 8, "temperature": 0}
            if reasoning_off:
                body.update((reasoning_off if isinstance(reasoning_off, dict) else {}))
            payload, elapsed, failure = post(body)
            if failure:
                rows.append({"chars": len(prompt), "error": failure})
                best = None
                break
            prompt_tokens, _, _ = usage_of(payload)
            if best is None or elapsed < best["elapsed_s"]:
                best = {"chars": len(prompt), "prompt_tokens": prompt_tokens,
                        "elapsed_s": round(elapsed, 2),
                        "prefill_tokens_per_s": round(prompt_tokens / elapsed, 1) if elapsed else None}
        if best:
            rows.append(best)
    return rows


def measure_decode(reasoning_off, target=512, repeats=3):
    body_base = {"model": MODEL, "temperature": 0, "max_tokens": target}
    if reasoning_off:
        body_base.update((reasoning_off if isinstance(reasoning_off, dict) else {}))
    rows = []
    for _ in range(repeats):
        prompt = "請用繁體中文寫一篇關於臨床檢驗品質控制的短文，約四百字。"
        payload, elapsed, failure = post({**body_base,
                                          "messages": [{"role": "user", "content": prompt}]})
        if failure:
            rows.append({"error": failure})
            continue
        _, tokens, reasoning = usage_of(payload)
        rows.append({"completion_tokens": tokens, "reasoning_tokens": reasoning,
                     "elapsed_s": round(elapsed, 2),
                     "decode_tokens_per_s": round(tokens / elapsed, 1) if elapsed else None})
    return rows


def measure_concurrency(levels, reasoning_off, tokens=64):
    rows = []
    for level in levels:
        body_base = {"model": MODEL, "temperature": 0, "max_tokens": tokens}
        if reasoning_off:
            body_base.update((reasoning_off if isinstance(reasoning_off, dict) else {}))

        def one(index):
            payload, elapsed, failure = post({**body_base, "messages": [
                {"role": "user", "content": f"用繁體中文簡短說明第 {index} 項的定義。"}]},
                timeout=300)
            if failure:
                return elapsed, 0, failure
            _, count, _ = usage_of(payload)
            return elapsed, count, None

        started = time.time()
        with futures.ThreadPoolExecutor(level) as pool:
            results = list(pool.map(one, range(level)))
        wall = time.time() - started
        good = [r for r in results if not r[2]]
        errors = [r[2] for r in results if r[2]]
        total = sum(r[1] for r in good)
        rows.append({"concurrency": level, "wall_s": round(wall, 2),
                     "ok": len(good), "errors": len(errors),
                     "throughput_tokens_per_s": round(total / wall, 1) if wall else None,
                     "per_request_s": round(sum(r[0] for r in good) / len(good), 2) if good else None,
                     "first_error": errors[0][:120] if errors else None})
    return rows


def main():
    parser = argparse.ArgumentParser(description="Measure an OpenAI-compatible endpoint's speed.")
    parser.add_argument("--out", required=True)
    parser.add_argument("--sizes", default="2000,8000,16000,32000",
                        help="prompt sizes in characters")
    parser.add_argument("--decode-tokens", type=int, default=512)
    parser.add_argument("--concurrency", default="1,4,8,16")
    parser.add_argument("--skip-prefill", action="store_true")
    parser.add_argument("--skip-decode", action="store_true")
    parser.add_argument("--skip-concurrency", action="store_true")
    args = parser.parse_args()

    report = {"base_url": BASE_URL, "model": MODEL, "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    print(f"endpoint {BASE_URL}  model {MODEL}", flush=True)

    # A cheap identity check first: if the model name is wrong every later number is about an error
    # message, and those look like very fast answers.
    probe, elapsed, failure = post({"model": MODEL, "max_tokens": 8, "temperature": 0,
                                    "messages": [{"role": "user", "content": "回答：好"}]})
    if failure:
        report["fatal"] = failure
        print("  FATAL:", failure)
        json.dump(report, open(args.out, "w"), ensure_ascii=False, indent=2)
        return
    report["identity"] = {"echoed_model": probe.get("model"), "latency_s": round(elapsed, 2)}

    form, complaint, evidence = which_reasoning_off()
    report["reasoning_off"] = {"form": form, "complaint": complaint, "evidence": evidence}
    print(f"  reasoning control: on={json.dumps(evidence.get('on') if evidence else None, ensure_ascii=False)} "
          f"off={form or 'NONE'} ({complaint or 'measured'})", flush=True)

    if not args.skip_prefill:
        report["prefill"] = measure_prefill([int(s) for s in args.sizes.split(",")], form)
        print("  prefill:", json.dumps(report["prefill"], ensure_ascii=False))
    if not args.skip_decode:
        report["decode"] = measure_decode(form, args.decode_tokens)
        print("  decode:", json.dumps(report["decode"], ensure_ascii=False))
    if not args.skip_concurrency:
        report["concurrency"] = measure_concurrency(
            [int(c) for c in args.concurrency.split(",")], form)
        print("  concurrency:", json.dumps(report["concurrency"], ensure_ascii=False))

    report["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    json.dump(report, open(args.out, "w"), ensure_ascii=False, indent=2)
    print("->", args.out)


if __name__ == "__main__":
    main()
