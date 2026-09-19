# -*- coding: utf-8 -*-
"""Compare the paper's own skeleton against the geometric reading, question by question.

The skeleton is read from what the paper prints - its question numbers and its option markers -
and the geometric reading is what the pipeline has been packaging. Neither is the authority by
assumption: they are two readings of the same cells, and where they disagree one of them is wrong.
That makes the disagreement itself the finding, and it can be looked for without a model, on every
paper, for nothing.

What this replaces: a rule written for each anomaly that was met, each measured against the corpus
and each narrow enough to have a next case. Here every paper is asked the same question - do the
two readings agree - and the answer is per question, with the cells named.

The comparison is only run on a paper whose skeleton is complete, because an incomplete skeleton
says nothing about the questions it never found. What it covers, it covers exactly: the two stems
are compared as text and the two option sets are compared as label-to-text maps, so a question
assembled differently, an option carrying the wrong label, and a character that only one reading
has are all reported and all distinguishable.
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


def _strip_marker(text, label, alphabet_labels):
    """Remove the printed option marker from the head of an option cell.

    The marker is a position, not content, and the geometric reading removes it. Two forms exist
    and both are printed: an ASCII `A.`, which `repair.option_body` knows, and a private-use
    codepoint, which `repair` does not know because the codepoint is a property of that paper's
    font rather than of the format. Passing the paper's own alphabet in is what keeps the two
    readings comparable on the papers that use one - measured: without it, every option of every
    paper carrying private-use markers was reported as a disagreement, 80 per paper.
    """
    if not text:
        return text
    if text[0] in alphabet_labels:
        return text[1:].lstrip(" \t\u3000\u00a0")
    return repair.option_body(text)


def skeleton_items(paper, table, alphabet=()):
    """The paper's own skeleton as items, with its cells composed the way the paper joins them.

    The question number and the option markers are removed from the text, because they are
    positions rather than content. The geometric reading removes them too, so leaving them in one
    side and not the other would report every question of every paper as a disagreement.
    """
    text_of = {cell_id: text for cell_id, text, _ in table}
    alphabet_labels = {chr(code) for code in alphabet}
    items = []
    for number, question in sorted(paper["questions"].items()):
        stem = ""
        for index, cell in enumerate(question["stem"]):
            text = (text_of.get(cell) or "").strip()
            # The number is dropped only from the first cell, and only where it is this question's
            # own number: a number further into the stem is content, and so is one in the first
            # cell that is not this question's number (measured: `53.33歲油漆工`).
            if index == 0:
                match = reflow._QUESTION_LEAD.match(text)
                if match and int(match.group(1)) == number:
                    text = text[match.end():].strip()
                elif text == str(number):
                    continue
            stem = repair.join_lines(stem, text)
        options = {}
        for label, cells in sorted(question["options"].items()):
            body = ""
            for index, cell in enumerate(cells):
                text = (text_of.get(cell) or "").strip()
                if index == 0:
                    text = _strip_marker(text, label, alphabet_labels)
                body = repair.join_lines(body, text)
            options[label] = body.strip()
        items.append({"number": number, "stem": stem.strip(), "options": options,
                      "cells": {"stem": list(question["stem"]),
                                "options": {k: list(v) for k, v in question["options"].items()}}})
    return items


def compare(paper_items, geometric_items):
    """Where the two readings disagree. Returns rows of (number, kind, mine, theirs)."""
    mine = {item["number"]: item for item in paper_items}
    theirs = {item["number"]: item for item in geometric_items}
    rows = []
    for number in sorted(set(mine) | set(theirs)):
        left, right = mine.get(number), theirs.get(number)
        if left is None:
            rows.append((number, "absent-from-skeleton", "", ""))
            continue
        if right is None:
            rows.append((number, "absent-from-geometry", left["stem"][:60], ""))
            continue
        # The option labels first, because a label attached to the wrong text is worse than a
        # character that differs: the answer sheet names labels, so a shift attaches the official
        # answer to a different option.
        left_labels = {label: body for label, body in left["options"].items() if body}
        right_labels = {label: (body or "").strip() for label, body in right["options"].items()}
        if left_labels != right_labels:
            differing = sorted(set(left_labels) | set(right_labels))
            kind = "labels" if set(left_labels) != set(right_labels) else "options-text"
            rows.append((number, kind,
                         "; ".join(f"{l}={left_labels.get(l, '')[:34]}" for l in differing),
                         "; ".join(f"{l}={right_labels.get(l, '')[:34]}" for l in differing)))
        if (left["stem"] or "").strip() != (right["stem"] or "").strip():
            rows.append((number, "stem", left["stem"][:70], (right["stem"] or "")[:70]))
    return rows


def examine(paper, totals):
    pdf = paper["path"]
    rows = extract.extract_cells_a(pdf)
    kept, _ = repair.mask_chrome(rows)
    table, _, alphabet = reflow.cells_with_pages(kept)
    skeleton = reflow.skeleton(table)
    geometric = _geometric(pdf)
    totals["papers"] += 1
    result = {"name": paper["name"], "year": paper["year"], "ordinal": paper["ordinal"],
              "cells": len(table), "questions": len(geometric),
              "skeleton_complete": skeleton["complete"],
              "skeleton_missing": skeleton["missing"][:6],
              "alphabet": [f"{code:04x}" for code in alphabet]}
    if not skeleton["complete"]:
        totals["incomplete"] += 1
        result["agree"] = None
        return result
    totals["complete"] += 1
    rows_out = compare(skeleton_items(skeleton, table, alphabet), geometric)
    kinds = collections.Counter(kind for _, kind, _, _ in rows_out)
    totals.update(kinds)
    totals["questions"] += len(geometric)
    totals["agree"] += len(geometric) - len({row[0] for row in rows_out})
    result.update({"agree": len(geometric) - len({row[0] for row in rows_out}),
                   "disagreements": [{"number": n, "kind": k, "paper": a, "geometry": b}
                                     for n, k, a, b in rows_out[:20]],
                   "kinds": dict(kinds)})
    return result


def _geometric(pdf):
    """The pipeline's reading, as items."""
    import three_way
    return three_way.analyse_items(pdf)["items"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Skeleton versus geometry, per paper.")
    parser.add_argument("--category", default="醫事檢驗師")
    parser.add_argument("--years", default="101-115")
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

    totals = collections.Counter()
    with open(args.out, "w", encoding="utf-8") as handle:
        for index, paper in enumerate(papers, start=1):
            try:
                result = examine(paper, totals)
            except Exception as exc:                             # noqa: BLE001
                totals["errors"] += 1
                result = {"name": paper["name"], "year": paper["year"], "error": str(exc)}
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
            handle.flush()
            print(f"[{index:>3}/{len(papers)}] {result['name'][:48]:50} "
                  f"agree={result.get('agree')} {result.get('kinds') or ''}", flush=True)

    print(f"\n=== {args.category} {args.years}")
    print(f"  卷數            {totals['papers']}  (讀取失敗 {totals['errors']})")
    print(f"  骨架完整        {totals['complete']}  (不完整 {totals['incomplete']})")
    print(f"  可比對題數      {totals['questions']}")
    print(f"  完全一致        {totals['agree']}")
    if totals["questions"]:
        print(f"  一致率          {totals['agree'] / totals['questions']:.1%}")
    for kind in ("absent-from-skeleton", "absent-from-geometry", "labels", "options-text", "stem"):
        print(f"  {kind:22} {totals[kind]}")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
