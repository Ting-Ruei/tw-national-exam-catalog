# -*- coding: utf-8 -*-
"""Run the same questions through more than one local model and compare their verdicts.

The point is not to find which model is "better". It is to find which *jobs* each level of
model can be trusted with, so that the cheap one is used wherever it is sufficient and the
expensive one is only asked what the cheap one cannot answer.

Each model is asked the identical prompt, on the identical text, at temperature 0. The
report records the verdict, the defect code, the confidence, and what it cost - because a
model that is right but ten times slower is only worth using where it is also more right.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ask_local_model as asker


MODELS = {
    "35b": {"base_url": "http://127.0.0.1:18120", "name": "ornith-1.5-mtplx-35b",
            "api_key": "mtplx"},
    "9b": {"base_url": "http://127.0.0.1:18121", "name": "ornith-1.5-mtplx-9b",
           "api_key": "mtplx"},
}


def ask_with(model, messages, *, max_tokens, timeout):
    """Same as ask_local_model.ask but pointed at an arbitrary endpoint."""
    saved = (asker.BASE_URL, asker.MODEL, asker.API_KEY)
    asker.BASE_URL, asker.MODEL, asker.API_KEY = model["base_url"], model["name"], model["api_key"]
    try:
        return asker.ask(messages, max_tokens=max_tokens, timeout=timeout)
    finally:
        asker.BASE_URL, asker.MODEL, asker.API_KEY = saved


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare local models on the same questions.")
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--models", nargs="*", default=list(MODELS))
    parser.add_argument("--only", nargs="*", type=int, default=None)
    parser.add_argument("--subject", default="")
    parser.add_argument("--max-tokens", type=int, default=3000)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    with open(args.candidates, encoding="utf-8") as handle:
        questions = [json.loads(line) for line in handle if line.strip()]
    if args.only:
        wanted = set(args.only)
        questions = [q for q in questions if int(q["question_number"]) in wanted]
    questions.sort(key=lambda q: int(q["question_number"]))
    subject = args.subject or (questions[0].get("metadata") or {}).get(
        "normalized_subject_name") or ""

    records = []
    with open(args.out, "w", encoding="utf-8") as handle:
        for key in args.models:
            model = MODELS[key]
            print(f"\n=== {model['name']} ({model['base_url']}) ===", flush=True)
            totals = {"prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0,
                      "defect": 0, "ok": 0, "unparsed": 0}
            for question in questions:
                number = int(question["question_number"])
                messages = asker.build_messages(number=number, stem=question.get("stem"),
                                                options=question.get("options"), subject=subject)
                parsed, raw, usage, seconds = ask_with(model, messages,
                                                       max_tokens=args.max_tokens,
                                                       timeout=args.timeout)
                verdict = parsed or {"verdict": "UNPARSED", "code": None,
                                     "detail": raw[-160:]}
                bucket = {"DEFECT": "defect", "OK": "ok"}.get(verdict.get("verdict"), "unparsed")
                totals[bucket] += 1
                totals["prompt_tokens"] += usage.get("prompt_tokens") or 0
                totals["completion_tokens"] += usage.get("completion_tokens") or 0
                totals["seconds"] = round(totals["seconds"] + seconds, 1)
                record = {"model": key, "model_name": model["name"], "number": number,
                          "verdict": verdict, "usage": usage, "seconds": round(seconds, 1)}
                records.append(record)
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                print(f"  Q{number:03d} {str(verdict.get('code')):24} "
                      f"conf={verdict.get('confidence', '')} {seconds:5.1f}s "
                      f"{str(verdict.get('detail', ''))[:70]}", flush=True)
            print(f"  -- {model['name']}: ok={totals['ok']} defect={totals['defect']} "
                  f"unparsed={totals['unparsed']} "
                  f"tok={totals['prompt_tokens']}+{totals['completion_tokens']} "
                  f"{totals['seconds']}s", flush=True)
    print(f"\nwrote {args.out} ({len(records)} records)", flush=True)


if __name__ == "__main__":
    main()
