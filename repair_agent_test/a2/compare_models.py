#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Show each local model the shipped text and score its correction against the person's (A2).

The question this answers is **"which model did the thing a person did?"** - not "which model writes
a nicer sentence". The dataset (`build_dataset.py`) carries, for each question, the text the machine
shipped and the text the reviewer replaced it with. This script sends the shipped text to each model
and compares what comes back to the human text, field by field.

Scoring is deliberately two numbers, because they fail differently:

    field_accuracy   of the fields the person touched, how many did the model change the same way?
    over_edit        how many fields did the model change that the person left alone?

`field_accuracy` alone is gameable by rewriting everything; `over_edit` is the negative control on
it. A model that returns the input unchanged scores 0 on the first and 0 on the second, which is
the honest reading: it did nothing, and doing nothing is not "not over-editing".

A field is **exact** when the model's text equals the human text after whitespace normalisation, and
**near** when the only differences are inside markup (`<sub>`/`<sup>`/`<i>`) or are a known
normalisation (half/full-width, NBSP). Both are reported; neither is folded into the other.

Usage:
    python3 compare_models.py --models mtplx-35b occamy-6bit --limit 5
    python3 compare_models.py --models mtplx-35b --fields stem --out /tmp/a2/run.json

Writing: results go to `runs/<timestamp>-<models>.jsonl`, which is an **experiment record**. It is
not a review event and never enters `question_review_events.jsonl` (G4 is the person's alone).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(CATALOG, "qbr", "src"))

from qbr import engines  # noqa: E402

SYSTEM = (
    "你是國考題庫的校對員。使用者給你一題「機器從官方 PDF 抽出來、有人說它怪怪的」題目文字。\n"
    "\n"
    "你的工作：**照著紙本應該印的樣子**把它修好。\n"
    "\n"
    "規則：\n"
    "1. **只改有問題的地方**。沒有問題的欄位就原樣傳回，不要潤飾、不要改標點、不要重寫句子。\n"
    "2. 看到亂碼、掉字、簡體字、異體字、被壓平的上下標、被截斷的選項，就改成紙本該有的樣子。\n"
    "3. 上下標用標記寫：`C<sub>ss</sub>`、`10<sup>-3</sup>`。**不要**用 Unicode 上下標字元。\n"
    "4. 選項的 `key` 不可以改、不可以增減。\n"
    "5. 如果不確定紙本長怎樣，**保持原樣**，不要猜。\n"
    "\n"
    "只輸出這個 JSON，不要有其他文字：\n"
    '{"stem":"題幹","options":{"A":"...","B":"...","C":"...","D":"..."},"answer":"A"}'
)

MARKUP = re.compile(r"</?(?:sub|sup|i|b|em|strong)\s*/?>", re.I)


def normalize(text: str, *, strip_markup: bool = False) -> str:
    """Whitespace and width folded; optionally markup removed.

    Not a scoring rule by itself - it is the *precondition* for comparing two texts at all. A model
    that inserts a space after a Latin term and a model that does not are saying the same thing
    about the paper, and a score that separated them would be measuring the model's typing.
    """
    text = unicodedata.normalize("NFKC", str(text or ""))
    if strip_markup:
        text = MARKUP.sub("", text)
    return re.sub(r"\s+", "", text)


def field_map(question: dict) -> dict:
    """`{"stem": ..., "answer": ..., "option A": ...}` out of a dataset question."""
    out = {"stem": question.get("stem") or "", "answer": question.get("answer")}
    for option in question.get("options") or []:
        out["option %s" % option.get("key")] = option.get("text") or ""
    return out


def parse_answer(content: str):
    """The model's JSON out of its reply, or `None`.

    The models are told to emit JSON and nothing else, and most do; the ones that wrap it in a fence
    still said the thing. Extracting the outermost braces rather than failing on the fence is the
    difference between scoring a model's judgement and scoring its formatting.
    """
    if not content:
        return None
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(content[start:end + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def score(row: dict, reply: dict, fields) -> dict:
    """Per-field verdicts for one model on one row.

    Returns `{field: "same" | "exact" | "near" | "wrong" | "missed" | "over"}`. The two interesting
    failures are named apart: `missed` is "the person changed it, the model did not", and `over` is
    "the model changed it, the person did not". A model that confuses the two is not a slightly worse
    model; it is one whose edits cannot be trusted to be the paper's.
    """
    shipped = field_map(row["shipped"])
    human = field_map(row["human"])
    model = {"stem": reply.get("stem"), "answer": reply.get("answer")}
    for option in (reply.get("options") or {}).items() if isinstance(reply.get("options"), dict) \
            else ():
        model["option %s" % option[0]] = option[1]
    touched = set(row["fields_touched"])
    verdicts = {}
    for field in fields:
        if field not in shipped and field not in human:
            continue
        was, should, got = shipped.get(field), human.get(field), model.get(field)
        if got is None:
            verdicts[field] = "missed" if field in touched else "same"
            continue
        changed_by_model = normalize(got) != normalize(was)
        changed_by_person = normalize(should) != normalize(was)
        if not changed_by_person:
            verdicts[field] = "over" if changed_by_model else "same"
            continue
        if not changed_by_model:
            verdicts[field] = "missed"
        elif normalize(got) == normalize(should):
            verdicts[field] = "exact"
        elif normalize(got, strip_markup=True) == normalize(should, strip_markup=True):
            # The only difference is the markup itself. For this corpus that is a real distinction:
            # `C<sub>ax</sub>` and `Cax` are different things to a reviewer, so it is `near`, not
            # `exact` - but it is much closer than `wrong`, and saying so is the point of the column.
            verdicts[field] = "near"
        else:
            verdicts[field] = "wrong"
    return verdicts


def summarize(rows: list) -> dict:
    """The two headline numbers, plus the per-verdict counts they are made of."""
    counts = {}
    touched_total = touched_correct = over_total = 0
    for row in rows:
        for field, verdict in row["verdicts"].items():
            counts[verdict] = counts.get(verdict, 0) + 1
            if field in row["fields_touched"]:
                touched_total += 1
                touched_correct += verdict in ("exact", "near")
            elif verdict == "over":
                over_total += 1
    return {
        "rows": len(rows),
        "fields": sum(counts.values()),
        "field_accuracy": round(touched_correct / touched_total, 4) if touched_total else None,
        "touched_fields": touched_total,
        "over_edit_count": over_total,
        "verdicts": counts,
    }


def ask_model(endpoint, row: dict, *, max_tokens: int, timeout: int) -> tuple:
    shipped = row["shipped"]
    options = "\n".join("%s. %s" % (o["key"], o["text"]) for o in shipped["options"])
    user = ("科目：%s\n題號：%s\n\n題幹：\n%s\n\n選項：\n%s\n\n答案：%s\n"
            % (row.get("subject") or "", row.get("question_number") or "", shipped["stem"],
               options, shipped.get("answer")))
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    raw, seconds = engines.ask(messages, endpoint=endpoint, max_tokens=max_tokens, timeout=timeout)
    return engines.content_of(raw), seconds


def control_reply(row: dict) -> str:
    """The **negative control**: the shipped text handed back unchanged.

    A scorer that cannot say "this model did nothing" is a scorer that will call a do-nothing model
    safe. `--models control` runs this, and the run is only trustworthy when it reports
    `field_accuracy: 0.0` with `over_edit_count: 0` - zero, not "low". If the control ever scores
    above zero, the scoring is crediting the model for agreeing with text it was given, and every
    other model's number is inflated by the same amount.
    """
    shipped = row["shipped"]
    return json.dumps({"stem": shipped["stem"],
                       "options": {o["key"]: o["text"] for o in shipped["options"]},
                       "answer": shipped.get("answer")}, ensure_ascii=False)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", default=["mtplx-35b"],
                        help="engine names from qbr.engines (%s)" % ", ".join(sorted(engines.endpoints())))
    parser.add_argument("--dataset", default=os.path.join(HERE, "dataset.jsonl"))
    parser.add_argument("--limit", type=int, default=0, help="0 = every row")
    parser.add_argument("--fields", nargs="+", default=None,
                        help="restrict to some fields, e.g. --fields stem")
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    with open(args.dataset, encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        print("no rows in %s; run build_dataset.py first" % args.dataset, file=sys.stderr)
        return 2
    default_fields = ["stem", "answer"] + ["option %s" % key for key in "ABCDEF"]
    fields = args.fields or default_fields

    results = {}
    for name in args.models:
        endpoint = ({"url": "control", "name": "control"} if name == "control"
                    else engines.named(name))
        scored = []
        for index, row in enumerate(rows):
            if name == "control":
                content, seconds = control_reply(row), 0.0
            else:
                content, seconds = ask_model(endpoint, row,
                                             max_tokens=args.max_tokens, timeout=args.timeout)
            reply = parse_answer(content)
            verdicts = (score(row, reply, fields) if reply is not None
                        else {field: "missed" if field in row["fields_touched"] else "same"
                              for field in fields if field in field_map(row["shipped"])})
            scored.append({"candidate_key": row["candidate_key"],
                           "fields_touched": row["fields_touched"],
                           "verdicts": verdicts,
                           "parsed": reply is not None,
                           "seconds": round(seconds, 2),
                           "reply": (content or "")[:4000]})
            print("[%s %d/%d] %s %s parsed=%s %.1fs"
                  % (name, index + 1, len(rows), row["candidate_key"].split(":question:")[-1],
                     json.dumps(verdicts, ensure_ascii=False), reply is not None, seconds))
        results[name] = {"endpoint": endpoint["url"], "model": endpoint["name"],
                         "summary": summarize(scored), "rows": scored}

    stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    out = args.out or os.path.join(HERE, "runs", "%s-%s.jsonl" % (stamp, "-".join(args.models)))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "qbr_a2_model_run",
                                 "at": _dt.datetime.now().isoformat(timespec="seconds"),
                                 "dataset": os.path.abspath(args.dataset),
                                 "rows_scored": len(rows), "fields": fields},
                                ensure_ascii=False) + "\n")
        for name, payload in results.items():
            handle.write(json.dumps({"model": name, **payload}, ensure_ascii=False) + "\n")
    print("\n" + json.dumps({name: payload["summary"] for name, payload in results.items()},
                            ensure_ascii=False, indent=2))
    print("\nwrote " + out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
