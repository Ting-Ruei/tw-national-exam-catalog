# -*- coding: utf-8 -*-
"""Describe the figure-bearing questions of a paper, and write the crops a reviewer needs.

The figure path is the one place the local model is measurably better than every deterministic
reading, and the measurements are unambiguous: 101 of 101 judgments correct across 18 papers, on
both positive controls (an embedded image, or option markers with no text) and negative controls
(the same prompt on a region that holds only text). Thinking off is used, because the score is
identical and the time is half.

What this writes is evidence, never a decision. Each question gets its crop on disk beside a JSON
record holding the measured reason it was cropped and what the model saw. A person reads the two
together; nothing here changes a package, which is the same position the text side takes.

    a crop that cannot be honest      a question whose option markers split across a page break has
                                      no single rectangle that shows it, so it is reported with
                                      `split: true` and not cropped from a page showing a quarter
    a crop with nothing in it         an embedded image object may be a rule or a logo; the area
                                      floor drops those before anything is asked
    a description that cannot be read the model is required to quote what it saw and to say what it
                                      is unsure of, so a verdict can be checked by whoever reads it
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qbr import extract, repair, reflow, vision  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Read the figures of one paper or a category.")
    parser.add_argument("--category", default="醫事檢驗師")
    parser.add_argument("--years", default="115-115")
    parser.add_argument("--papers", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", required=True)
    parser.add_argument("--crops", default="")
    parser.add_argument("--think", action="store_true",
                        help="use the reasoning channel; measured identical, twice as slow")
    parser.add_argument("--min-height", type=float, default=vision.MIN_FIGURE_HEIGHT)
    args = parser.parse_args()

    import batch_package
    low, high = (int(part) for part in args.years.split("-"))
    papers = [paper for paper in batch_package.paper_pdfs(args.category)
              if low <= int(paper["year"]) <= high]
    if args.papers:
        wanted = [part for part in args.papers.split(",") if part.strip()]
        papers = [paper for paper in papers
                  if any(paper["name"].startswith(prefix) for prefix in wanted)]
    papers.sort(key=lambda paper: (-int(paper["year"]), -int(paper["ordinal"]), paper["name"]))
    if args.limit:
        papers = papers[:args.limit]

    totals = collections.Counter()
    out = []
    for index, paper in enumerate(papers, start=1):
        started = time.time()
        rows = extract.extract_cells_a(paper["path"])
        kept, _ = repair.mask_chrome(rows)
        table, _, alphabet = reflow.cells_with_pages(kept)
        import compare_skeleton
        items = compare_skeleton.skeleton_items(reflow.skeleton(table), table, alphabet)
        images = extract.extract_images_a(paper["path"])
        found = vision.figure_questions(items, kept, images, alphabet=alphabet,
                                        min_image_height=args.min_height)
        if not found:
            continue
        crop_dir = os.path.join(args.crops, paper["name"].replace(".pdf", "")) if args.crops else None
        record = {"name": paper["name"], "year": paper["year"], "subject": paper.get("subject", ""),
                  "cells": len(table), "embedded_images": len(images), "figures": len(found),
                  "questions": []}
        for entry in found:
            one = vision.read_figures(paper["path"], items, kept, subject=paper.get("subject", ""),
                                      think=args.think, out_dir=crop_dir, entries=[entry])
            for row in one["records"]:
                row.update({"split": entry["split"], "option_pages": entry["option_pages"]})
                record["questions"].append(row)
            totals[f"reason:{'+'.join(entry['reasons'])}"] += 1
            if entry["split"]:
                totals["split-across-pages"] += 1
        totals["papers"] += 1
        totals["figures"] += len(record["questions"])
        for row in record["questions"]:
            totals[row.get("contains") or "unread"] += 1
            if row.get("seconds"):
                totals["seconds"] = round(totals["seconds"] + row["seconds"], 1)
            usage = row.get("usage") or {}
            totals["out_tokens"] += usage.get("completion_tokens") or 0
            details = usage.get("completion_tokens_details") or {}
            totals["reasoning_tokens"] += details.get("reasoning_tokens") or 0
        out.append(record)
        kinds = collections.Counter(row.get("contains") or "unread" for row in record["questions"])
        print(f"[{index:>3}/{len(papers)}] {paper['name'][:44]:46} "
              f"圖 {len(record['questions']):>2}  {dict(kinds)}  {round(time.time()-started,1)}s",
              flush=True)

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(out, handle, ensure_ascii=False, indent=2)
    print(f"\n=== 圖片判讀")
    print(f"  卷 {totals['papers']}   題 {totals['figures']}   "
          f"跨頁無法誠實裁切 {totals['split-across-pages']}")
    print(f"  判讀結果 { {k: totals[k] for k in ('figure', 'table', 'text', 'empty', 'unread') if totals[k]} }")
    print(f"  判讀秒數 {totals['seconds']}   輸出 {totals['out_tokens']} tokens   "
          f"思考 {totals['reasoning_tokens']} tokens")
    for key in sorted(k for k in totals if k.startswith("reason:")):
        print(f"    {key:34} {totals[key]}")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
