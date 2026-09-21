# -*- coding: utf-8 -*-
"""Ask the local model what is wrong with each question a person blocked, and record the answer.

This is the loop's step 3, made concrete. `repair_loop.py` finds the blocks no detector explains
and prints them; this script asks the model about exactly those questions, one at a time, and
writes down for each one: **where it thinks the error is**, **how it would fix it**, and **whether
it thinks this is a class or a one-off**.

Why the record is the point
---------------------------
Some of these questions are 國考題的意外 - real defects whose shape is strange enough that nobody can
classify them on sight. For those, a model's opinion is not a verdict, it is a note: something to
read later when a person sits down to discuss that single case. That only works if the note is
written down at the time, next to the exact text the model saw, naming the model that said it. A
suggestion remembered but not recorded is a suggestion nobody can check.

The one distinction that matters to the caller
----------------------------------------------
`rule_worthy` separates a class from a one-off, because the two need opposite treatment. A class
gets a rule in `disputes.py` and a rescan. A one-off gets looked at by a person and stays a
one-off - writing a rule for a single accident is how a detector starts flagging innocent
questions, which is the treadmill the charter warns about.

Usage
-----
    # what does the model say about the blocks no detector explains?
    .venv/bin/python scripts/ask_about_blocks.py --queue data/review-queues/live

    # only some of them, or re-ask without losing the earlier answer
    .venv/bin/python scripts/ask_about_blocks.py --queue ... --only q012 q059
    .venv/bin/python scripts/ask_about_blocks.py --queue ... --limit 5

    # read back what was recorded, classes and one-offs apart
    .venv/bin/python scripts/ask_about_blocks.py --queue ... --report
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, HERE)

from qbr import ai_findings  # noqa: E402
from qbr import vision  # noqa: E402
import repair_loop  # noqa: E402

# The endpoint is a local engine, so it is a parameter. Two are configured because they answer
# differently and the point of a note is that a person can compare them later; the default is the
# one that was measured to read this project's papers.
ENDPOINTS = {
    "mtplx-35b": {"url": os.environ.get("QBR_MODEL_BASE_URL", "http://127.0.0.1:18120"),
                  "name": os.environ.get("QBR_MODEL_NAME", "ornith-1.5-mtplx-35b"),
                  "key": os.environ.get("QBR_MODEL_API_KEY", "mtplx")},
    "qwen3.8-flash-next": {"url": "http://192.168.10.90:8888", "name": "qwen3.8-flash-next",
                           "key": "dgx-spark-local"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queue", required=True,
                        help="queue root; decisions live in its review-ui/ subdirectory")
    parser.add_argument("--model", default="mtplx-35b", choices=sorted(ENDPOINTS))
    parser.add_argument("--only", nargs="*", default=None,
                        help="question numbers (q012 or 12); default is every unexplained block")
    parser.add_argument("--limit", type=int, default=0, help="ask at most this many (0 = all)")
    parser.add_argument("--out", help="findings path; default <queue>/review-ui/"
                                        + ai_findings.STREAM)
    parser.add_argument("--report", action="store_true",
                        help="read back what was recorded instead of asking")
    parser.add_argument("--max-tokens", type=int, default=3000,
                        help="the reasoning is charged here too, so this is not the answer's length")
    parser.add_argument("--timeout", type=int, default=1800)
    return parser.parse_args()


def _endpoint(base: str) -> str:
    base = base.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base + "/chat/completions"


def ask(messages, *, endpoint, max_tokens, timeout):
    """One call. Returns (parsed_or_None, raw, complaint, usage, seconds).

    Thinking is turned **off** with the same control `vision.py` measured for this engine, and that
    is not a tuning choice - it is the difference between an answer and no answer. Measured here on
    `108030:305 q049`: with thinking on, the engine spent 8000/8000 tokens on reasoning and returned
    an *empty* string (and 2927/3000 on q014, cutting the JSON off mid-field). With thinking off the
    same question answered in 2 seconds. A note that is never written is worse than a terse one.
    """
    body = {"model": endpoint["name"], "messages": messages, "max_tokens": max_tokens,
            "temperature": 0}
    body.update(vision.THINKING_CONTROLS[0])
    request = urllib.request.Request(
        _endpoint(endpoint["url"]), data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + endpoint["key"]})
    started = time.time()
    try:
        raw = json.loads(urllib.request.urlopen(request, timeout=timeout).read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        return None, "", "request failed: %s" % exc, {}, time.time() - started
    seconds = time.time() - started
    message = (raw.get("choices") or [{}])[0].get("message") or {}
    content = message.get("content") or ""
    return (ai_findings.parse_finding(content), content, None, raw.get("usage") or {}, seconds)


def targets(queue_dir: str, only, limit):
    """The questions a person blocked and no detector explains - the loop's own step 3 output.

    Reusing `repair_loop.explain` rather than recomputing is the point: two definitions of "the
    blocks that nothing explains" is two places for it to drift, and the whole workflow is that the
    model is asked about the *same* set the loop printed.
    """
    blocked, rows = repair_loop.collect_blocks(queue_dir)
    _explained, unexplained = repair_loop.explain(blocked, rows)
    entries = [{**entry, "question": rows.get(entry["candidate_key"])} for entry in unexplained]
    if only:
        wanted = {str(item).lower().lstrip("q").lstrip("0") or "0" for item in only}
        entries = [entry for entry in entries
                   if str(entry["question"].get("question_number")).lstrip("0") in wanted]
    if limit:
        entries = entries[:limit]
    return entries


def report(path: str) -> int:
    records = ai_findings.load(path)
    if not records:
        print("還沒有任何 AI 記錄：%s" % path)
        return 0
    summary = ai_findings.summarize(records)
    print("AI 記錄：%s" % path)
    print("  共 %d 筆（模型判定不是抽取缺陷的 %d 筆、無法解析的 %d 筆）"
          % (len(records), len(summary["not_extraction"]), len(summary["failed"])))
    print()
    print("== 模型認為是「一類」問題（值得寫規則）==")
    if not summary["classes"]:
        print("  （無）")
    for what, keys in sorted(summary["classes"].items()):
        print("  %-26s %d 題" % (what, len(keys)))
        for key in keys:
            print("      %s" % key.replace("moex:", ""))
    print()
    print("== 模型認為是個案（要單獨探討，不要寫規則）==")
    if not summary["one_offs"]:
        print("  （無）")
    for item in summary["one_offs"]:
        print("  %s  [%s]" % (item["candidate_key"].replace("moex:", ""), item["what"]))
        print("      哪裡錯：%s" % (item["where"] or "（沒說）"))
        print("      怎麼修：%s" % (item["fix"] or "（沒說）"))
    print()
    print("== 模型認為不是抽取缺陷（＝可能是紙本或題目本身的問題）==")
    for key in summary["not_extraction"] or ["（無）"]:
        print("  %s" % str(key).replace("moex:", ""))
    if summary["unclassified"]:
        print()
        print("== 有備註、但那個代碼／判定不認識（筆記照樣可讀）==")
        for item in summary["unclassified"]:
            label = item.get("what_reported") or item.get("verdict_reported") or "?"
            print("  %s  [%s]" % (item["candidate_key"].replace("moex:", ""), label))
            print("      哪裡錯：%s" % (item["where"] or "（沒說）"))
            print("      怎麼修：%s" % (item["fix"] or "（沒說）"))
    return 0


def main() -> int:
    args = parse_args()
    queue_dir = repair_loop.review_ui_dir(args.queue)
    out = args.out or ai_findings.store_path(args.queue)

    if args.report:
        return report(out)

    if not os.path.exists(os.path.join(queue_dir, "candidates.jsonl")):
        print("找不到 candidates.jsonl：%s" % queue_dir, file=sys.stderr)
        return 2

    entries = targets(queue_dir, args.only, args.limit)
    if not entries:
        print("沒有要問的題目（沒有未解釋的 block，或 --only 沒對上）。")
        return 0
    endpoint = ENDPOINTS[args.model]
    print("問 %s（%s）關於 %d 題未解釋的 block" % (endpoint["name"], endpoint["url"], len(entries)))
    print("記錄寫到：%s" % out)
    print()

    asked = 0
    for entry in entries:
        question = entry["question"]
        number = question.get("question_number")
        stem = (question.get("stem") or "")[:38].replace("\n", " ")
        print("Q%-4s %-30s …%s" % (number, entry["candidate_key"].replace("moex:", ""), stem),
              flush=True)
        system, user = ai_findings.build_prompt(question)
        parsed, raw, complaint, usage, seconds = ask(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            endpoint=endpoint, max_tokens=args.max_tokens, timeout=args.timeout)
        error = None if parsed is not None else (complaint or "unparsed")
        record = ai_findings.make_record(
            question=question, finding=parsed, model=endpoint["name"], endpoint=endpoint["url"],
            prompt_system=system, prompt_user=user, raw="" if parsed else raw, usage=usage,
            seconds=round(seconds, 1), error=error)
        ai_findings.append(out, record)
        asked += 1
        if parsed is None:
            print("      → 無法解析（%s）%s" % (error, (raw or "")[-90:].replace("\n", " ")))
        else:
            print("      → %s／%s  規則價值=%s  %.0fs"
                  % (parsed.get("verdict"), parsed.get("what"), parsed.get("rule_worthy"),
                     seconds))
            print("        哪裡錯：%s" % (parsed.get("where") or "（沒說）"))
            print("        怎麼修：%s" % (parsed.get("fix") or "（沒說）"))

    print()
    print("已記錄 %d 筆。讀回來：--report" % asked)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
