# -*- coding: utf-8 -*-
"""How often does the paper itself state its own structure completely?

`reflow.skeleton` reads only what the paper prints: the question numbers and the option markers.
Where it is complete, the structure needs no inference at all, and the model is then reserved for
what the skeleton cannot do - superscripts, figures, and the papers the marks do not describe.
Where it is not complete, the reason is recorded, because the reasons are the backlog.

This is the measurement that decides the division of labour, so it is run over a whole category
and reported per year rather than on a sample.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qbr import extract, repair, reflow  # noqa: E402


def sheets_count(pdf):
    return reflow._sheet_count(pdf)


def measure(paper):
    pdf = paper["path"]
    rows = extract.extract_cells_a(pdf)
    kept, _ = repair.mask_chrome(rows)
    table, _, alphabet = reflow.cells_with_pages(kept)
    sk = reflow.skeleton(table)
    # The answer sheet is the authority for how many questions there are, so a skeleton that
    # claims a different number is disagreeing with the sheet, which is a separate finding.
    expected = sheets_count(pdf)
    reasons = collections.Counter()
    for missing in sk["missing"]:
        if missing.endswith("-options"):
            reasons["question-has-no-option-marker"] += 1
        elif missing.endswith("-labels"):
            reasons["question-lacks-a-label"] += 1
        else:
            reasons["question-number-not-found"] += 1
    return {
        "name": paper["name"],
        "year": paper["year"],
        "ordinal": paper["ordinal"],
        "cells": len(table),
        "count": sk["count"],
        "expected": expected,
        "complete": sk["complete"],
        "matches_sheet": bool(expected) and sk["count"] == expected,
        "missing": sk["missing"][:10],
        "missing_total": len(sk["missing"]),
        "reasons": dict(reasons),
        "alphabet": [f"{code:04x}" for code in alphabet],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure the paper-stated skeleton over a category.")
    parser.add_argument("--category", default="醫事檢驗師")
    parser.add_argument("--years", default="101-115", help="e.g. 101-115")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    import batch_package
    low, high = (int(part) for part in args.years.split("-"))
    papers = [paper for paper in batch_package.paper_pdfs(args.category)
              if low <= int(paper["year"]) <= high]
    papers.sort(key=lambda paper: (-int(paper["year"]), -int(paper["ordinal"]), paper["name"]))
    if args.limit:
        papers = papers[:args.limit]

    results = []
    with open(args.out, "w", encoding="utf-8") as handle:
        for index, paper in enumerate(papers, start=1):
            try:
                result = measure(paper)
            except Exception as exc:                             # noqa: BLE001
                result = {"name": paper["name"], "year": paper["year"],
                          "ordinal": paper["ordinal"], "error": str(exc),
                          "complete": False, "matches_sheet": False}
            results.append(result)
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
            handle.flush()
            mark = "OK " if result.get("complete") else "   "
            print(f"[{index:>3}/{len(papers)}] {mark} {result['name'][:52]:54} "
                  f"count={result.get('count')}/{result.get('expected')}", flush=True)

    done = [r for r in results if "error" not in r]
    complete = [r for r in done if r["complete"]]
    agreeing = [r for r in done if r["matches_sheet"]]
    print(f"\n=== {args.category} {args.years}")
    print(f"卷數            {len(results)}  (讀取失敗 {len(results) - len(done)})")
    print(f"骨架完整        {len(complete)}/{len(done)}  ({len(complete) / max(len(done), 1):.1%})")
    print(f"骨架題數=答案卷  {len(agreeing)}/{len(done)}  ({len(agreeing) / max(len(done), 1):.1%})")
    by_year = collections.defaultdict(lambda: [0, 0, 0])
    for result in done:
        row = by_year[result["year"]]
        row[0] += 1
        row[1] += 1 if result["complete"] else 0
        row[2] += 1 if result["matches_sheet"] else 0
    print("\n年  完整/卷  題數相符/卷")
    for year in sorted(by_year, reverse=True):
        total, ok, agree = by_year[year]
        print(f"{year}  {ok:>2}/{total:<3}    {agree:>2}/{total:<3}")
    reasons = collections.Counter()
    for result in done:
        if not result["complete"]:
            for reason, count in (result.get("reasons") or {}).items():
                reasons[reason] += count
    print(f"\n未完整的原因：{dict(reasons)}")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
