#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A2.1 — the same 47 corrections, but **with the page image in front of the model** (D13's real test).

`compare_models.py` measured a text-only pass and found the ceiling was 41%: 58.9% of the reviewer's
edits are to characters the shipped text does not contain. This script asks the question that follows
directly from it:

    **if the model can see the page, does that ceiling move?**

The mechanism is deliberately the cheap one. The paper is segmented by `three_way.analyse_items`
(0.2 s per paper, ~16 pages), and for each question the **whole page the question is on** is rendered
to PNG and handed to the model beside the shipped text. That is the *largest* possible crop - it
contains the answer wherever on the page it is - so if the score does not move with the whole page,
it will not move with a tighter crop either. If it *does* move, a tighter crop is a refinement, not a
prerequisite.

Why the page and not the option crop: the defect classes A2.1 is testing (`罕 疾病`→`罕見疾病`,
`3ࠕ間Әཊ使`→`3年間不行使`) are about **characters inside the stem**, not about a figure's extent.
The question a reviewer answers is "what does the paper say here", and the paper says it on the page.

Usage:
    python3 compare_models_vision.py --models mtplx-35b occamy-6bit --limit 4
    python3 compare_models_vision.py --models mtplx-35b --limit 4

Writes to `runs/<stamp>-vision-<models>.jsonl`, the same schema as `compare_models.py` so
`split_by_ceiling.py` and `build_compare_page.py` read it unchanged.
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(CATALOG, "qbr", "src"))
sys.path.insert(0, os.path.join(CATALOG, "qbr", "scripts"))
sys.path.insert(0, HERE)

from qbr import engines  # noqa: E402
import compare_models as compare  # noqa: E402

VISION_SYSTEM = compare.SYSTEM + (
    "\n\n**這次你會拿到題目所在那一頁的圖。** 以圖為準：圖上印什麼字就是什麼字。\n"
    "如果文字和圖不一致，**以圖為準**。\n"
    "**看得到圖之後**，前面那條「不確定就保持原樣」不再適用於你能從圖上讀出來的字——\n"
    "能讀出來就照圖改，讀不出來才保持原樣。"
)


def page_png(pdf_path: str, page: int, out_dir: str, dpi: int = 150) -> str:
    """Render one page to PNG, cached on disk by `(pdf mtime, page, dpi)`.

    Cached because a run over 27 papers would otherwise re-render the same page for every question
    on it, and because the PNG is the evidence: keeping it lets a later reader check what the model
    was actually shown rather than trusting that `page=3` meant the same thing then as now.
    """
    import fitz  # PyMuPDF, imported late so `--help` works without it
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    stamp = int(os.path.getmtime(pdf_path))
    out = os.path.join(out_dir, "%s-p%03d-d%03d-%d.png" % (stem, page, dpi, stamp))
    if os.path.exists(out):
        return out
    with fitz.open(pdf_path) as document:
        if page < 1 or page > document.page_count:
            raise ValueError("page %d out of range (1..%d) for %s"
                             % (page, document.page_count, pdf_path))
        pixmap = document[page - 1].get_pixmap(dpi=dpi)
        pixmap.save(out)
    return out


def question_page(pdf_path: str, number: int, cache: dict) -> int:
    """The page a question's number is printed on, via the real segmentation.

    Reuses the production reader rather than a regex here, because "which page is Q38 on" is exactly
    the question `three_way` already answers and a second answer could disagree with the pipeline.
    """
    if pdf_path not in cache:
        import three_way
        result = three_way.analyse_items(pdf_path)
        cache[pdf_path] = {int(item.get("number") or 0): int(item.get("page") or 0)
                           for item in result["items"]}
    return cache[pdf_path].get(number, 0)


def ask_model_vision(endpoint, row: dict, image_paths: list, *, max_tokens: int, timeout: int):
    shipped = row["shipped"]
    options = "\n".join("%s. %s" % (o["key"], o["text"]) for o in shipped["options"])
    user = ("科目：%s\n題號：%s\n\n題幹：\n%s\n\n選項：\n%s\n\n答案：%s\n\n"
            "（附圖：這題在官方 PDF 的第 %s 頁。）"
            % (row.get("subject") or "", row.get("question_number") or "", shipped["stem"],
               options, shipped.get("answer"),
               "、".join(str(path) for path in image_paths)))
    content = [{"type": "text", "text": user}]
    for path in image_paths:
        with open(path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode()
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/png;base64," + encoded}})
    messages = [{"role": "system", "content": VISION_SYSTEM},
                {"role": "user", "content": content}]
    raw, seconds = engines.ask(messages, endpoint=endpoint, max_tokens=max_tokens, timeout=timeout)
    return engines.content_of(raw), seconds


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", default=["mtplx-35b"])
    parser.add_argument("--dataset", default=os.path.join(HERE, "dataset.jsonl"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--image-dir", default=os.path.join(HERE, "page-cache"))
    parser.add_argument("--max-tokens", type=int, default=1600)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--out", default="")
    parser.add_argument("--no-image", action="store_true",
                        help=("the negative control: send the vision prompt but attach no image. If "
                              "accuracy rises without the image, the gain was the prompt, not the "
                              "picture, and the vision claim is false."))
    args = parser.parse_args(argv)

    with open(args.dataset, encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    if args.limit:
        rows = rows[:args.limit]

    # `question_pdf_relative` is not in the dataset (it is a property of the queue, not of the
    # correction); read it from the live queue once, keyed by candidate.
    pdfs = {}
    queue = os.path.join(CATALOG, "qbr", "data", "review-queues", "live", "review-ui",
                         "candidates.jsonl")
    wanted = {row["candidate_key"] for row in rows}
    with open(queue, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            candidate = json.loads(line)
            key = candidate.get("candidate_key")
            if key in wanted:
                metadata = candidate.get("metadata") or {}
                pdfs[key] = metadata.get("question_pdf_relative")

    fields = ["stem", "answer"] + ["option %s" % key for key in "ABCDEF"]
    page_cache = {}
    results = {}
    for name in args.models:
        endpoint = engines.named(name)
        scored = []
        for index, row in enumerate(rows):
            relative = pdfs.get(row["candidate_key"])
            if not relative:
                print("skip %s: no question_pdf_relative" % row["candidate_key"])
                continue
            pdf_path = os.path.join(CATALOG, relative)
            number = int(row.get("question_number") or 0)
            try:
                page = question_page(pdf_path, number, page_cache)
                image = (None if args.no_image else
                         (page_png(pdf_path, page, args.image_dir, dpi=args.dpi) if page else None))
            except Exception as exc:                              # noqa: BLE001 - recorded, not hidden
                print("skip %s: %r" % (row["candidate_key"], exc))
                continue
            content, seconds = ask_model_vision(endpoint, row, [image] if image else [],
                                                max_tokens=args.max_tokens, timeout=args.timeout)
            reply = compare.parse_answer(content)
            verdicts = (compare.score(row, reply, fields) if reply is not None
                        else {field: "missed" if field in row["fields_touched"] else "same"
                              for field in fields if field in compare.field_map(row["shipped"])})
            scored.append({"candidate_key": row["candidate_key"],
                           "fields_touched": row["fields_touched"],
                           "verdicts": verdicts, "parsed": reply is not None,
                           "page": page, "image": (os.path.basename(image) if image else None),
                           "seconds": round(seconds, 2), "reply": (content or "")[:4000]})
            print("[%s %d/%d] %s p%s %s parsed=%s %.1fs"
                  % (name, index + 1, len(rows), row["candidate_key"].split(":question:")[-1],
                     page, json.dumps(verdicts, ensure_ascii=False), reply is not None, seconds))
        results[name] = {"endpoint": endpoint["url"], "model": endpoint["name"],
                         "summary": compare.summarize(scored), "rows": scored,
                         "modality": ("text+vision-prompt-no-image" if args.no_image
                                      else "text+page-image"),
                         "dpi": args.dpi}

    stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    tag = "vision-prompt-no-image" if args.no_image else "vision"
    out = args.out or os.path.join(HERE, "runs", "%s-%s-%s.jsonl" % (stamp, tag, "-".join(args.models)))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "qbr_a2_model_run",
                                 "at": _dt.datetime.now().isoformat(timespec="seconds"),
                                 "dataset": os.path.abspath(args.dataset),
                                 "modality": ("text+vision-prompt-no-image" if args.no_image
                                              else "text+page-image"),
                                 "rows_scored": sum(len(payload["rows"]) for payload in results.values()),
                                 "fields": fields}, ensure_ascii=False) + "\n")
        for name, payload in results.items():
            handle.write(json.dumps({"model": name, **payload}, ensure_ascii=False) + "\n")
    print("\n" + json.dumps({name: payload["summary"] for name, payload in results.items()},
                            ensure_ascii=False, indent=2))
    print("\nwrote " + out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
