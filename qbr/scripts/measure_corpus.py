# -*- coding: utf-8 -*-
"""Does one skeleton rule hold for every year of a subject, and for every subject?

A rule that works on one year is not a rule about paper, it is a rule about that year's printing.
The only honest test of "this reads papers" is to run the same code, unchanged, over every subject
and every year and report where it fails - because the failures are the backlog and a success rate
per year is what says whether the rule is about the print form or about a printing.

What is measured is deliberately narrow, so that a failure names one thing:

    complete        the paper's own marks describe every question: the numbers run 1..N and each
                    question carries its four option markers
    matches_sheet   the number of questions the skeleton found equals the number the *answer sheet*
                    prints, which is the authority and is read independently of the question paper

A paper whose answer sheet is absent is counted separately and never as a failure: 101 and 102 had
no answer sheets published, so the skeleton cannot be checked against anything and saying nothing
is the correct report.

The output is one JSON line per paper plus a summary per subject per year, so a regression in one
year cannot hide inside a category average.
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

def categories():
    """Every subject directory, taken from `batch_package` so the corpus root is defined once."""
    import batch_package
    return sorted(name for name in os.listdir(batch_package.CORPUS)
                  if os.path.isdir(os.path.join(batch_package.CORPUS, name)))


def measure(paper):
    pdf = paper["path"]
    rows = extract.extract_cells_a(pdf)
    kept, _ = repair.mask_chrome(rows)
    table, _, alphabet = reflow.cells_with_pages(kept)
    paper_skeleton = reflow.skeleton(table)
    expected = reflow._sheet_count(pdf)
    reasons = collections.Counter()
    for missing in paper_skeleton["missing"]:
        if missing.endswith("-options"):
            reasons["no-option-marker"] += 1
        elif missing.endswith("-labels"):
            reasons["lacks-a-label"] += 1
        else:
            reasons["number-not-found"] += 1
    return {
        "category": paper["category"], "name": paper["name"], "subject": paper["subject"],
        "year": paper["year"], "ordinal": paper["ordinal"],
        "cells": len(table), "count": paper_skeleton["count"], "sheet": expected,
        "complete": paper_skeleton["complete"],
        "matches_sheet": bool(expected) and paper_skeleton["count"] == expected,
        "has_sheet": bool(expected),
        "missing": paper_skeleton["missing"][:8],
        "reasons": dict(reasons),
        "alphabet": [f"{code:04x}" for code in alphabet],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Skeleton over the whole corpus.")
    parser.add_argument("--categories", default="", help="comma separated; default all")
    parser.add_argument("--years", default="101-115")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0, help="papers per category, 0 = all")
    args = parser.parse_args()

    import batch_package
    wanted = [part for part in args.categories.split(",") if part.strip()] or categories()
    low, high = (int(part) for part in args.years.split("-"))

    totals = collections.Counter()
    per_category = collections.defaultdict(lambda: collections.Counter())
    per_year = collections.defaultdict(lambda: collections.Counter())
    failures = []
    with open(args.out, "w", encoding="utf-8") as handle:
        for category in wanted:
            papers = [paper for paper in batch_package.paper_pdfs(category)
                      if low <= int(paper["year"]) <= high]
            papers.sort(key=lambda paper: (-int(paper["year"]), -int(paper["ordinal"]),
                                           paper["name"]))
            if args.limit:
                papers = papers[:args.limit]
            seen = 0
            for paper in papers:
                try:
                    result = measure(paper)
                except Exception as exc:                        # noqa: BLE001
                    result = {"category": category, "name": paper["name"],
                              "subject": paper["subject"], "year": paper["year"],
                              "ordinal": paper["ordinal"], "error": str(exc),
                              "complete": False, "matches_sheet": False, "has_sheet": False}
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                handle.flush()
                seen += 1
                bucket = per_category[category]
                year_bucket = per_year[(category, result["year"])]
                bucket["papers"] += 1
                year_bucket["papers"] += 1
                if "error" in result:
                    bucket["errors"] += 1
                    continue
                if result["has_sheet"]:
                    bucket["checkable"] += 1
                    year_bucket["checkable"] += 1
                    if result["complete"]:
                        bucket["complete"] += 1
                        year_bucket["complete"] += 1
                    if result["matches_sheet"]:
                        bucket["matching"] += 1
                        year_bucket["matching"] += 1
                    if not result["complete"]:
                        failures.append(result)
                else:
                    bucket["no-sheet"] += 1
                totals["papers"] += 1
            rate = (per_category[category]["complete"] / max(per_category[category]["checkable"], 1))
            print(f"{category:16} {seen:>4} 卷  可判 {per_category[category]['checkable']:>4}  "
                  f"完整 {per_category[category]['complete']:>4} ({rate:5.1%})  "
                  f"題數相符 {per_category[category]['matching']:>4}  "
                  f"無答案卷 {per_category[category]['no-sheet']:>3}  "
                  f"失敗 {per_category[category]['errors']:>3}", flush=True)

    print("\n=== 全庫彙總")
    checked = sum(bucket["checkable"] for bucket in per_category.values())
    complete = sum(bucket["complete"] for bucket in per_category.values())
    matching = sum(bucket["matching"] for bucket in per_category.values())
    print(f"  卷 {totals['papers']}   可判 {checked}   骨架完整 {complete} ({complete/max(checked,1):.1%})"
          f"   題數相符 {matching} ({matching/max(checked,1):.1%})")

    print("\n=== 各年（全部科系合計）")
    by_year = collections.defaultdict(lambda: collections.Counter())
    for (category, year), bucket in per_year.items():
        for key, value in bucket.items():
            by_year[year][key] += value
    print("  年   可判  完整   相符")
    for year in sorted(by_year, reverse=True):
        bucket = by_year[year]
        print(f"  {year}  {bucket['checkable']:>5}  {bucket['complete']:>5} "
              f"({bucket['complete']/max(bucket['checkable'],1):5.1%})  {bucket['matching']:>5}")

    reasons = collections.Counter()
    for result in failures:
        for reason, count in (result.get("reasons") or {}).items():
            reasons[reason] += count
    print(f"\n=== 未完整的原因（{len(failures)} 卷）")
    for reason, count in reasons.most_common():
        print(f"  {reason:22} {count}")

    print("\n=== 骨架不完整的卷（依科系）")
    by_fail = collections.defaultdict(list)
    for result in failures:
        by_fail[result["category"]].append(result)
    for category in sorted(by_fail, key=lambda name: -len(by_fail[name])):
        names = by_fail[category]
        print(f"  {category:16} {len(names):>4}")
        for result in names[:4]:
            print(f"      {result['name'][:52]:54} "
                  f"count={result['count']}/{result['sheet']} {result['missing'][:3]}")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
