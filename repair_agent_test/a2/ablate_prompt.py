#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Which line of the prompt is actually paying? (A2 prompt ablation)

The prompt has five numbered rules. `compare_models_vision.py` showed the *picture* moves the score
(20.0% -> 47.8%), but it could not say what each **line** contributes, because "no image" changes
two things at once. This runs the same 47 corrections under variants that each remove one thing.

Two of the variants exist because of a defect found while reading the production prompt
(`qa-log.md` Q21): the shipped prompt does not send an image, it sends a **sentence describing that
an image exists** ("這一題的圖片：1 張"). That sentence makes the model assert "this is a picture
option, not a defect" with the confidence of something it has seen. So the variants include:

    image            the page image (the A2.1 condition, the reference)
    image_text       no image, but the "this question has N images" sentence  <- the shipped shape
    none             neither

`none` vs `image_text` measures exactly what that sentence is worth, which is the number that
decides whether it should survive into the production prompt.

Every variant is scored by `compare_models.score`, on the same 47 corrections, so the numbers are
comparable to every earlier run. The control is `--variant control` (hand the input back), which
must be 0.0 / 0.

Usage:
    python3 ablate_prompt.py --models occamy-6bit                 # every variant
    python3 ablate_prompt.py --models occamy-6bit --variants none image
    python3 ablate_prompt.py --models occamy-6bit --variant image --limit 3
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(CATALOG, "qbr", "src"))
sys.path.insert(0, os.path.join(CATALOG, "qbr", "scripts"))
sys.path.insert(0, HERE)

from qbr import engines  # noqa: E402
import compare_models as compare  # noqa: E402

#: The five rules, each on its own so a variant can drop exactly one. Written as the (key, line)
#: pairs that `build_system` assembles, so the ablation and the prompt cannot drift apart.
RULES = [
    ("no_over_edit",
     "1. **只改有問題的地方**。沒有問題的欄位就原樣傳回，不要潤飾、不要改標點、不要重寫句子。"),
    ("defect_kinds",
     "2. 看到亂碼、掉字、簡體字、異體字、被壓平的上下標、被截斷的選項，就改成紙本該有的樣子。"),
    ("markup_form",
     "3. 上下標用標記寫：`C<sub>ss</sub>`、`10<sup>-3</sup>`。**不要**用 Unicode 上下標字元。"),
    ("keys_frozen",
     "4. 選項的 `key` 不可以改、不可以增減。"),
    ("keep_if_unsure",
     "5. 如果不確定紙本長怎樣，**保持原樣**，不要猜。"),
]

#: A variant names what to change relative to the full prompt. `drop` removes rules by key;
#: `vision` adds the picture paragraph (and cancels rule 5, which would otherwise forbid the repair
#: the picture makes possible); `describe_images` adds the shipped "N images" sentence.
VARIANTS = {
    "full": {"drop": (), "vision": False, "describe_images": False, "page_cue": False},
    "none": {"drop": ("defect_kinds",), "vision": False, "describe_images": False,
             "page_cue": False},
    "image": {"drop": (), "vision": True, "describe_images": False, "page_cue": False},
    "image_cue": {"drop": (), "vision": True, "describe_images": False, "page_cue": True},
    "image_text": {"drop": (), "vision": False, "describe_images": True, "page_cue": False},
    "image_text_cue": {"drop": (), "vision": False, "describe_images": True,
                       "page_cue": True},
}

VISION_PARAGRAPH = (
    "\n\n**這次你會拿到題目所在那一頁的圖。** 以圖為準：圖上印什麼字就是什麼字。\n"
    "如果文字和圖不一致，**以圖為準**。\n"
    "**看得到圖之後**，前面那條「不確定就保持原樣」不再適用於你能從圖上讀出來的字——\n"
    "能讀出來就照圖改，讀不出來才保持原樣。"
)


def variant_spec(name: str) -> dict:
    """The changes a named variant makes, or a ValueError listing the names that work.

    Every variant is either a named preset or an ablation of one rule, so a typo cannot silently run
    the full prompt while claiming to be an ablation - the exact failure that would make the whole
    table meaningless.
    """
    if name in VARIANTS:
        return VARIANTS[name]
    if name.startswith("drop:"):
        key = name[len("drop:"):]
        if key not in {rule[0] for rule in RULES}:
            raise ValueError("unknown rule %r; known: %s"
                             % (key, ", ".join(rule[0] for rule in RULES)))
        return {"drop": (key,), "vision": False, "describe_images": False, "page_cue": False}
    raise ValueError("unknown variant %r; known: %s, drop:<rule>"
                     % (name, ", ".join(sorted(VARIANTS))))


def build_system(spec: dict) -> str:
    kept = [line for key, line in RULES if key not in spec["drop"]]
    system = ("你是國考題庫的校對員。使用者給你一題「機器從官方 PDF 抽出來、有人說它怪怪的」題目文字。\n"
              "\n你的工作：**照著紙本應該印的樣子**把它修好。\n\n規則：\n"
              + "\n".join(kept)
              + "\n\n只輸出這個 JSON，不要有其他文字：\n"
                '{"stem":"題幹","options":{"A":"...","B":"...","C":"...","D":"..."},"answer":"A"}')
    if spec["vision"]:
        system += VISION_PARAGRAPH
    return system


def user_message(row: dict, *, describe_images: bool, image_count: int = 0,
                 page_cue: bool = False, image_paths: list | None = None) -> str:
    shipped = row["shipped"]
    options = "\n".join("%s. %s" % (o["key"], o["text"]) for o in shipped["options"])
    text = ("科目：%s\n題號：%s\n\n題幹：\n%s\n\n選項：\n%s\n\n答案：%s\n"
            % (row.get("subject") or "", row.get("question_number") or "",
               shipped["stem"], options, shipped.get("answer")))
    if describe_images:
        # This is the shipped sentence, verbatim in shape. It is the thing being measured.
        text += ("\n這一題的圖片：%d 張（figure-crop）。\n"
                 "若某個選項的文字是空的，但上面說它有對應的圖片，那就是**圖片選項**，"
                 "不是選項遺失——請不要把它當成缺陷。\n" % image_count)
    if page_cue:
        # The trailing sentence of the proven `compare_models_vision` prompt. It was found by
        # accident: an earlier run of this ablation omitted it and scored ~2-9pp lower on the same
        # image, and that gap was first misread as nondeterminism. It is a cue that an image is
        # attached, so it is measured as its own variable rather than left implicit.
        text += ("\n（附圖：這題在官方 PDF 的第 %s 頁。）"
                 % "、".join(str(path) for path in (image_paths or [])))
    return text


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", default=["occamy-6bit"])
    parser.add_argument("--variants", nargs="+", default=None)
    parser.add_argument("--variant", default="", help="run a single variant")
    parser.add_argument("--dataset", default=os.path.join(HERE, "dataset.jsonl"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--image-dir", default=os.path.join(HERE, "page-cache"))
    parser.add_argument("--max-tokens", type=int, default=1600)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--out", default="")
    parser.add_argument("--repeat", type=int, default=1,
                        help=("run each variant this many times and report mean/stdev. Needed because "
                              "the engines are not deterministic: the same byte-identical prompt "
                              "flipped 9.4%% of per-field verdicts between two runs, so a single run "
                              "cannot separate a variant effect from noise."))
    args = parser.parse_args(argv)

    names = [args.variant] if args.variant else (args.variants or
             ["full", "none", "drop:no_over_edit", "drop:defect_kinds", "drop:markup_form",
              "drop:keys_frozen", "drop:keep_if_unsure", "image", "image_text"])
    try:
        specs = {name: variant_spec(name) for name in names}
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    with open(args.dataset, encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    if args.limit:
        rows = rows[:args.limit]

    pdfs = {}
    queue = os.path.join(CATALOG, "qbr", "data", "review-queues", "live", "review-ui",
                         "candidates.jsonl")
    wanted = {row["candidate_key"] for row in rows}
    image_counts = {row["candidate_key"]: 0 for row in rows}
    with open(queue, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            candidate = json.loads(line)
            key = candidate.get("candidate_key")
            if key in wanted:
                pdfs[key] = (candidate.get("metadata") or {}).get("question_pdf_relative")
                image_counts[key] = len(candidate.get("image_refs") or [])

    import compare_models_vision as vision  # page rendering, reused rather than reimplemented

    fields = ["stem", "answer"] + ["option %s" % key for key in "ABCDEF"]
    page_cache = {}
    results = {}
    summaries = {}
    for name in names:
        spec = specs[name]
        system = build_system(spec)
        for model in args.models:
            endpoint = engines.named(model)
            tag = "%s|%s" % (model, name)
            attempts = []
            for attempt in range(max(1, args.repeat)):
                scored = []
                for index, row in enumerate(rows):
                    content, seconds = "", 0.0
                    images = []
                    if spec["vision"]:
                        relative = pdfs.get(row["candidate_key"])
                        if relative:
                            try:
                                page = vision.question_page(os.path.join(CATALOG, relative),
                                                            int(row.get("question_number") or 0),
                                                            page_cache)
                                if page:
                                    images = [vision.page_png(os.path.join(CATALOG, relative), page,
                                                              args.image_dir, dpi=args.dpi)]
                            except Exception as exc:                    # noqa: BLE001 - recorded
                                print("skip image %s: %r" % (row["candidate_key"], exc))
                    message = user_message(row, describe_images=spec["describe_images"],
                                           image_count=image_counts.get(row["candidate_key"], 0),
                                           page_cue=spec.get("page_cue", False),
                                           image_paths=images)
                    if images:
                        payload = [{"type": "text", "text": message}]
                        for path in images:
                            import base64
                            with open(path, "rb") as handle:
                                encoded = base64.b64encode(handle.read()).decode()
                            payload.append({"type": "image_url",
                                            "image_url": {"url": "data:image/png;base64," + encoded}})
                        messages = [{"role": "system", "content": system},
                                    {"role": "user", "content": payload}]
                    else:
                        messages = [{"role": "system", "content": system},
                                    {"role": "user", "content": message}]
                    raw, seconds = engines.ask(messages, endpoint=endpoint,
                                               max_tokens=args.max_tokens, timeout=args.timeout)
                    content = engines.content_of(raw)
                    reply = compare.parse_answer(content)
                    verdicts = (compare.score(row, reply, fields) if reply is not None
                                else {field: "missed" if field in row["fields_touched"] else "same"
                                      for field in fields if field in compare.field_map(row["shipped"])})
                    scored.append({"candidate_key": row["candidate_key"],
                                   "fields_touched": row["fields_touched"], "verdicts": verdicts,
                                   "parsed": reply is not None, "seconds": round(seconds, 2),
                                   "reply": (content or "")[:4000]})
                attempts.append({"attempt": attempt + 1, "summary": compare.summarize(scored),
                                 "rows": scored})
            accs = [a["summary"]["field_accuracy"] for a in attempts]
            results[tag] = {"model": model, "variant": name, "spec": spec,
                            "system_prompt": system, "attempts": attempts,
                            "summary": attempts[-1]["summary"], "rows": attempts[-1]["rows"]}
            summaries[tag] = {
                "variant": name, "repeats": len(attempts),
                "accuracy_mean": round(statistics.fmean(accs), 4),
                "accuracy_stdev": round(statistics.pstdev(accs), 4) if len(accs) > 1 else None,
                "accuracy_values": accs,
                "over_edit_mean": round(statistics.fmean(
                    [a["summary"]["over_edit_count"] for a in attempts]), 2),
            }
            print("[%s] %s" % (tag, json.dumps(summaries[tag], ensure_ascii=False)))

    stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    out = args.out or os.path.join(HERE, "runs", "%s-ablation.jsonl" % stamp)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "qbr_a2_ablation",
                                 "at": _dt.datetime.now().isoformat(timespec="seconds"),
                                 "dataset": os.path.abspath(args.dataset),
                                 "rows_scored": len(rows), "fields": fields},
                                ensure_ascii=False) + "\n")
        for tag, payload in results.items():
            handle.write(json.dumps({"model": tag, **payload}, ensure_ascii=False) + "\n")
    print("\n" + json.dumps(summaries, ensure_ascii=False, indent=2))
    print("\nwrote " + out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
