"""Run the SAME battery against several engines and put the answers side by side.

The value of this script is not that it tests a model - it is that it tests *the same thing* on
every model. Every task is fixed text with a fixed expected answer, so a difference between two
columns is a difference in the engine and not in the question. Anything that varies per run
(temperature, max_tokens, the prompt) is held constant here and printed with the result, because a
comparison whose controls are not visible cannot be checked later.

Four kinds of task, chosen because they fail differently:

* **arithmetic trap** - `9.11` vs `9.9`. A reasoning switch is only *proven* to work by turning an
  answer from wrong to right, so this task is also the reasoning-control probe.
* **defect detection** - the five text defects, with two negative controls that must come back CLEAN.
  A detector that flags everything scores the same as one that flags nothing once the negatives are
  dropped, so the negatives are the measurement.
* **vision** - real crops from this corpus, asking for the figure count and content.
* **reading** - take a real stem and options, answer the question. This is the task the pipeline
  actually needs, and it is where a model that reasons well and a model that reasons quickly diverge.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import time
import urllib.error
import urllib.request

# These are the engines as they were measured, and the record matters: `docs/ENGINE_BOUNDARY_REPORT.md`
# reports numbers produced against exactly these. So they stay written down - but they are also
# overridable, because the charter's rule is that a port is a parameter and a machine address is not
# part of the logic. The hard-coded home directory that used to be here was a third thing: a model
# path that only resolves on one machine, which is neither a record nor a default.
FLASH_NEXT_URL = os.environ.get("QBR_ENGINE_FLASH_NEXT_URL", "http://192.168.10.90:8888")
QWEN38_URL = os.environ.get("QBR_ENGINE_QWEN38_URL", "http://127.0.0.1:8082")
QWEN38_MODEL = os.environ.get("QBR_ENGINE_QWEN38_MODEL", "Youssofal--Qwen3.8-27B-MTPLX-Optimized-Speed")

ENGINES = {
    "flash-next": {"url": FLASH_NEXT_URL, "name": "qwen3.8-flash-next",
                   "key": "mtplx", "vision": True},
    "qwen38-27b": {"url": QWEN38_URL, "name": QWEN38_MODEL,
                   "key": None, "vision": True},
}

THINKING_ON = ({"enable_thinking": True}, {"chat_template_kwargs": {"enable_thinking": True}})
THINKING_OFF = ({"enable_thinking": False}, {"chat_template_kwargs": {"enable_thinking": False}},
                {"reasoning_effort": "none"})


def post(engine, body, *, timeout=900):
    headers = {"Content-Type": "application/json"}
    if engine.get("key"):
        headers["Authorization"] = "Bearer " + engine["key"]
    request = urllib.request.Request(engine["url"].rstrip("/") + "/v1/chat/completions",
                                     data=json.dumps(body).encode(), headers=headers)
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as handle:
            return json.loads(handle.read().decode()), time.time() - started, None
    except urllib.error.HTTPError as exc:
        return None, time.time() - started, f"HTTP {exc.code}: {exc.read().decode()[:160]}"
    except Exception as exc:
        return None, time.time() - started, f"{type(exc).__name__}: {exc}"


def reasoning_of(payload):
    message = ((payload or {}).get("choices") or [{}])[0].get("message") or {}
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return len(value)
    details = ((payload or {}).get("usage") or {}).get("completion_tokens_details") or {}
    return int(details.get("reasoning_tokens") or 0)


def text_of(payload):
    message = ((payload or {}).get("choices") or [{}])[0].get("message") or {}
    return (message.get("content") or "").strip()


def discover_control(engine, want_reasoning):
    """First spelling that produces (or removes) reasoning, decided by measurement.

    A spelling the engine ignores is accepted with HTTP 200 and changes nothing, so it has to be
    tested rather than assumed - see the note in `qbr/vision.py`.
    """
    forms = THINKING_ON if want_reasoning else THINKING_OFF
    fallback, results = dict(forms[0]), []
    for form in forms:
        payload, _, failure = post(engine, {
            "model": engine["name"], "temperature": 0, "max_tokens": 900,
            "messages": [{"role": "user", "content": "9.11 和 9.9 哪個大？請說明理由。"}],
            **form})
        if failure:
            continue
        length = reasoning_of(payload)
        results.append({"form": form, "reasoning": length})
        if (want_reasoning and length) or (not want_reasoning and not length):
            return form, results
    return fallback, results


def ask(engine, prompt, *, control, max_tokens=1200, images=(), system=None, timeout=900):
    content = [{"type": "text", "text": prompt}]
    for path in images:
        encoded = base64.b64encode(open(path, "rb").read()).decode()
        content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}})
    messages = ([{"role": "system", "content": system}] if system else [])
    messages.append({"role": "user", "content": content if images else prompt})
    payload, elapsed, failure = post(engine, {
        "model": engine["name"], "temperature": 0, "max_tokens": max_tokens,
        "messages": messages, **control}, timeout=timeout)
    if failure:
        return {"error": failure, "seconds": round(elapsed, 2)}
    return {"answer": text_of(payload), "seconds": round(elapsed, 2),
            "reasoning": reasoning_of(payload),
            "completion_tokens": ((payload.get("usage") or {}).get("completion_tokens") or 0)}


def first_letter(text):
    for char in (text or ""):
        if char.isalpha():
            return char.upper()
    return ""


DEFECT_SYSTEM = (
    "你是考題審查員。使用者會給你一道選擇題的題幹與選項。\n"
    "你的工作只有一件事：判斷這道題目在**文字上**是否有缺陷。\n"
    "缺陷的定義，只有下列五種，其他一律不算：\n"
    "1. 選項數量不是四個\n2. 題幹要求的東西與選項不符\n"
    "3. 有一個以上選項文字完全相同\n4. 題幹出現明顯缺字（如『下列何者為』後面沒有受詞）\n"
    "5. 選項與題幹的類別不一致（例如問疾病、選項卻是藥物）\n"
    "如果沒有這五種缺陷，答案必須是 CLEAN。\n"
    "只輸出一個 JSON：{\"verdict\":\"CLEAN\"或\"DEFECT\",\"kind\":\"\",\"why\":\"簡短理由\"}")

DEFECT_CASES = [
    ("clean-organ", "題幹：下列何者為人體最大的器官？\nA.肝臟 B.皮膚 C.肺臟 D.腎臟", "CLEAN"),
    ("clean-resp", "題幹：正常成人安靜時的呼吸速率約為每分鐘幾次？\nA.4-8 B.12-20 C.30-40 D.50-60", "CLEAN"),
    ("clean-enzyme", "題幹：下列何種酵素缺乏會造成苯酮尿症？\nA.苯丙胺酸羥化酶 B.酪胺酸酶 C.乳酸去氫酶 D.丙酮酸激酶", "CLEAN"),
    ("defect-three-options", "題幹：下列何者為人體最大的器官？\nA.肝臟 B.皮膚 C.肺臟", "DEFECT"),
    ("defect-duplicate", "題幹：下列何者為人體最大的器官？\nA.肝臟 B.皮膚 C.皮膚 D.腎臟", "DEFECT"),
    ("defect-missing-word", "題幹：下列何者為人體最大的？\nA.肝臟 B.皮膚 C.肺臟 D.腎臟", "DEFECT"),
    ("defect-category", "題幹：下列何者為高血壓的第一線用藥？\nA.肝臟 B.皮膚 C.肺臟 D.腎臟", "DEFECT"),
]


def run_defects(engine, control, out):
    rows = []
    for name, text, expected in DEFECT_CASES:
        result = ask(engine, text, control=control, max_tokens=1500, system=DEFECT_SYSTEM)
        verdict = ""
        if not result.get("error"):
            body = result["answer"]
            try:
                verdict = json.loads(body[body.find("{"):body.rfind("}") + 1]).get("verdict", "")
            except Exception:
                verdict = "UNPARSED"
        raw = result.get("answer") or result.get("error") or ""
        rows.append({"case": name, "expected": expected, "verdict": verdict,
                     "ok": verdict == expected, **{k: v for k, v in result.items() if k != "answer"},
                     "raw": raw[:600]})
        shown = verdict if verdict not in ("UNPARSED", "") else repr(raw[:70])
        print(f"    {'OK ' if rows[-1]['ok'] else 'BAD'} {name:22} → {shown}", flush=True)
    out["defects"] = rows
    good = sum(1 for row in rows if row["ok"])
    negatives = [row for row in rows if row["expected"] == "CLEAN"]
    out["defects_score"] = {
        "correct": good, "total": len(rows),
        "false_alarms": sum(1 for row in negatives if row["verdict"] == "DEFECT"),
        "negatives": len(negatives)}
    return out["defects_score"]


def run_traps(engine, control, out):
    cases = [("9.11 vs 9.9", "9.11 和 9.9 哪個大？只輸出較大的一個。", "9.9"),
             ("bat and ball", "棒球棒和球共 1.10 美元，棒球棒比球貴 1.00 美元。球多少錢？只輸出數字。", "0.05")]
    rows = []
    for name, prompt, expected in cases:
        result = ask(engine, prompt, control=control, max_tokens=2000)
        got = (result.get("answer") or "").replace(" ", "")
        ok = expected in got
        rows.append({"case": name, "expected": expected, "ok": ok, **result})
        print(f"    {'OK ' if ok else 'BAD'} {name:16} {result.get('seconds')}s → {got[:40]!r}", flush=True)
    out["traps"] = rows
    out["traps_score"] = {"correct": sum(1 for row in rows if row["ok"]), "total": len(rows)}
    return out["traps_score"]


def run_vision(engine, control, crops, out):
    rows = []
    for path in crops:
        prompt = ("這張圖裡有幾張圖片？每一張各是什麼？請用繁體中文簡短回答，"
                  "並且只描述你真正看到的內容。")
        result = ask(engine, prompt, control=control, max_tokens=1200, images=[path])
        rows.append({"crop": os.path.basename(path), "paper": os.path.basename(os.path.dirname(path)),
                     **result})
        print(f"    {result.get('seconds')}s {os.path.basename(path)[:34]:36} "
              f"→ {repr((result.get('answer') or result.get('error') or '')[:60])}", flush=True)
    out["vision"] = rows
    out["vision_score"] = {"read": sum(1 for row in rows if not row.get("error")), "total": len(rows)}
    return out["vision_score"]


def main():
    parser = argparse.ArgumentParser(description="Same battery, several engines.")
    parser.add_argument("--engines", nargs="*", default=list(ENGINES))
    parser.add_argument("--out", required=True)
    parser.add_argument("--crops", nargs="*", default=None)
    parser.add_argument("--skip", nargs="*", default=[],
                        choices=["traps", "defects", "vision"])
    args = parser.parse_args()

    crops = args.crops or []
    if not crops:
        import glob
        # One crop from each of the two kinds: a two-panel microbiology figure and a chemistry
        # option strip, so the vision score is not a score on one kind of picture.
        for pattern in ("*1152_醫事檢驗師_微生物*/q068_embedded-image.png",
                        "*1042_藥師_藥理學與藥物化學/q068_option_*.png"):
            crops.extend(sorted(glob.glob("/tmp/qbr-live7/review-ui/crops/" + pattern))[:2])

    report = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "controls": {"temperature": 0, "prompts": "fixed"},
              "engines": {}}
    for tag in args.engines:
        engine = ENGINES[tag]
        print(f"\n=== {tag}  {engine['url']}", flush=True)
        entry = {"url": engine["url"], "model": engine["name"]}
        on_form, on_probe = discover_control(engine, True)
        off_form, off_probe = discover_control(engine, False)
        entry["thinking"] = {"on": on_form, "off": off_form,
                             "probe_on": on_probe, "probe_off": off_probe}
        print(f"  thinking on  via {json.dumps(on_form, ensure_ascii=False)} "
              f"(reasoning {on_probe[-1]['reasoning'] if on_probe else '?'})", flush=True)
        print(f"  thinking off via {json.dumps(off_form, ensure_ascii=False)} "
              f"(reasoning {off_probe[-1]['reasoning'] if off_probe else '?'})", flush=True)

        for think, control in (("off", off_form), ("on", on_form)):
            print(f"  -- thinking {think}", flush=True)
            block = {}
            # Each runner writes its own rows into `block` and returns the score. The score goes to
            # a *different* key: assigning it back over the rows key made the rows vanish, and the
            # report then held a score with nothing behind it - which is worse than no score, since
            # it cannot be checked.
            if "traps" not in args.skip:
                block["traps_score"] = run_traps(engine, control, block)
            if "defects" not in args.skip:
                block["defects_score"] = run_defects(engine, control, block)
            if "vision" not in args.skip and crops:
                block["vision_score"] = run_vision(engine, control, crops, block)
            entry[think] = block
        report["engines"][tag] = entry

    report["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    json.dump(report, open(args.out, "w"), ensure_ascii=False, indent=2)

    print("\n=== 總結")
    for tag, entry in report["engines"].items():
        for think in ("off", "on"):
            block = entry.get(think) or {}
            pieces = []
            for key in ("traps_score", "defects_score", "vision_score"):
                score = block.get(key)
                if score:
                    extra = (f" 誤報 {score['false_alarms']}/{score['negatives']}"
                             if "false_alarms" in score else "")
                    pieces.append(f"{key.split('_')[0]} {score['correct' if 'correct' in score else 'read']}"
                                  f"/{score['total']}{extra}")
            seconds = sum(row.get("seconds") or 0
                          for rows in (block, ) for k, v in rows.items()
                          if isinstance(v, list) for row in v if isinstance(row, dict))
            print(f"  {tag:12} think={think:3} " + " | ".join(pieces) + f" | {seconds:.0f}s")
    print("->", args.out)


if __name__ == "__main__":
    main()
