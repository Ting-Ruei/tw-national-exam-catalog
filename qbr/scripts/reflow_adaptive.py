# -*- coding: utf-8 -*-
"""Read a paper cheaply, then pay for a second reading only where the first one disagreed.

The measurements say thinking off is about 33 times faster (45 s against 1,500 s) and is usually
right, and that it fails in a way that is *visible*: the reading drifts, so its questions stop
matching the paper's own skeleton. The drift is not detectable by the guardrail - every cell is
still assigned, just to the wrong question - but it is detectable by comparison with the skeleton,
which costs nothing to read.

So the procedure is:

    1. read the paper with thinking off                       ~45 s
    2. compare the reading against the skeleton               free
    3. if every question agrees, keep it                      done
    4. if some disagree, read the whole paper with thinking    ~25 min, only for these papers

Only step 4 is expensive and step 3 decides whether to take it, on evidence rather than on a
prediction. Step 4 re-reads the whole paper rather than the disputed questions because the failure
being repaired is a shift, and a shift means the disputed question's neighbours are suspect too.

The result records both readings, the disagreement that triggered the second one, and the time and
tokens each took, so the policy can be judged on its own numbers after the fact.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from qbr import extract, repair, reflow  # noqa: E402

import judge_reading  # noqa: E402


def read_adapted(pdf_path, *, subject="", year="", rows=None, cheap_first=True, verbose=False):
    """Read one paper, cheaply when the cheap reading turns out to be right."""
    rows = rows if rows is not None else extract.extract_cells_a(pdf_path)
    kept, _ = repair.mask_chrome(rows)
    paper, paper_items, table, _ = judge_reading.skeleton_of(pdf_path, rows=kept)
    record = {"pdf": pdf_path, "subject": subject, "year": year,
              "skeleton_complete": paper["complete"], "skeleton_count": paper["count"],
              "cells": len(table), "skeleton_missing": paper["missing"][:6]}

    first = reflow.read_paper(pdf_path, subject=subject, year=year, rows=kept, think=not cheap_first)
    cheap_disagreement = None
    if cheap_first:
        cheap_disagreement = reflow.disagreements(first.get("items") or [], paper_items)
    record["first"] = _summary(first, len(cheap_disagreement) if cheap_disagreement is not None
                               else None, cheap_disagreement)
    used = first

    # The second reading is taken when the skeleton is complete and the cheap reading disagrees
    # with it. An incomplete skeleton cannot judge anything, so a cheap reading on such a paper is
    # taken as it stands - with the incompleteness recorded, because that is the finding.
    if cheap_first and paper["complete"] and cheap_disagreement:
        if verbose:
            print(f"    disagreed on {len(cheap_disagreement)} questions: "
                  f"{cheap_disagreement[:12]} → re-reading with thinking", flush=True)
        second = reflow.read_paper(pdf_path, subject=subject, year=year, rows=kept, think=True)
        second_disagreement = reflow.disagreements(second.get("items") or [], paper_items)
        record["second"] = _summary(second, len(second_disagreement), second_disagreement)
        # The second reading is preferred when it agrees with the paper more often, which is the
        # only comparison available that is not the model judging itself.
        if len(second_disagreement) <= len(cheap_disagreement):
            used = second
            record["chosen"] = "thinking"
        else:
            record["chosen"] = "cheap"
        if verbose:
            print(f"    after thinking: disagreed on {len(second_disagreement)} → "
                  f"chose {record['chosen']}", flush=True)
    else:
        record["chosen"] = "cheap" if cheap_first else "thinking"
    record["items"] = used.get("items") or []
    record["admissible"] = used.get("admissible")
    record["chosen_report"] = used.get("report")
    record["notes"] = used.get("notes")
    return record


def _summary(result, disagree_count, disagree_numbers):
    usage = result.get("usage") or {}
    return {"seconds": result.get("seconds"), "admissible": result.get("admissible"),
            "questions": len(result.get("items") or []),
            "out_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": result.get("reasoning_tokens"),
            "disagreements": disagree_count,
            "disagreement_numbers": (disagree_numbers or [])[:20]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Cheap reading, escalation only on disagreement.")
    parser.add_argument("--category", default="醫事檢驗師")
    parser.add_argument("--years", default="115-115")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--papers", default="")
    parser.add_argument("--out", required=True)
    parser.add_argument("--force", choices=("cheap", "thinking", "adaptive"), default="adaptive")
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
        try:
            record = read_adapted(paper["path"], subject=paper.get("subject", ""),
                                  year=str(paper["year"]),
                                  cheap_first=args.force != "thinking",
                                  verbose=True)
        except Exception as exc:                                    # noqa: BLE001
            totals["errors"] += 1
            print(f"[{index:>3}/{len(papers)}] {paper['name'][:44]:46} ERROR {exc}", flush=True)
            continue
        record["name"] = paper["name"]
        record["wall"] = round(time.time() - started, 1)
        out.append(record)
        first = record["first"]
        totals["papers"] += 1
        totals["wall"] += record["wall"]
        totals["out_tokens"] += first["out_tokens"] or 0
        totals["reasoning_tokens"] += first["reasoning_tokens"] or 0
        if record["chosen"] == "thinking" and "second" in record:
            totals["escalated"] += 1
            totals["wall"] += 0
            totals["out_tokens"] += record["second"]["out_tokens"] or 0
            totals["reasoning_tokens"] += record["second"]["reasoning_tokens"] or 0
            totals["out_tokens"] += (record["second"]["out_tokens"] or 0)
        totals[f"chose:{record['chosen']}"] += 1
        if record["admissible"]:
            totals["admissible"] += 1
        print(f"[{index:>3}/{len(papers)}] {paper['name'][:44]:46} "
              f"skel={record['skeleton_count']:>3} "
              f"cheap Δ{first['disagreements'] if first['disagreements'] is not None else '-'} "
              f"{first['seconds']}s → {record['chosen']:8} "
              f"final Δ{record.get('second', {}).get('disagreements', first['disagreements'])} "
              f"wall={record['wall']}s", flush=True)

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(out, handle, ensure_ascii=False, indent=2)
    print(f"\n=== 自適應讀卷")
    print(f"  卷 {totals['papers']}   升級重讀 {totals['escalated']}   採用思考 {totals['chose:thinking']}"
          f"   採用便宜 {totals['chose:cheap']}   admissible {totals['admissible']}")
    print(f"  總 wall {totals['wall']}s   輸出 {totals['out_tokens']} tokens   "
          f"思考 {totals['reasoning_tokens']} tokens   錯誤 {totals['errors']}")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
