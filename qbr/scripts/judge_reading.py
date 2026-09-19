# -*- coding: utf-8 -*-
"""Judge a model reading against the paper's own skeleton, question by question.

`verify` asks whether a reading is a repartition of the cells. This asks a different question:
is it *this paper's* repartition. The skeleton is the judge because it is read from the two marks
the paper prints - the question numbers and the option markers - so it does not depend on the
model being judged, on the prompt, or on any threshold.

Three outcomes, and they are not the same kind of thing:

    a question the skeleton has and the reading lacks    a silently dropped question, the one
                                                         failure the whole gate exists to prevent
    a question both have, assembled differently          a real disagreement; either side can be
                                                         wrong, and the cells are named so it can
                                                         be looked at
    a question the reading has and the skeleton lacks    expected when the skeleton is incomplete,
                                                         which is why the skeleton's own
                                                         completeness is reported alongside

The comparison is on normalized text, so a difference in spacing is not reported as a difference
in content. That is deliberate: whether a space is present is a typesetting detail, while whether
a character is present is the thing being checked.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qbr import extract, repair, reflow  # noqa: E402

_SPACE = re.compile(r"[\s\u3000\u00a0]+")


def normalized(text):
    """Text with spacing removed, so only the characters themselves are compared."""
    return _SPACE.sub("", (text or "").strip())


def judge(reading_items, paper_items, *, complete=True):
    """Compare a reading against the skeleton. Returns a report, not a verdict."""
    mine = {item["number"]: item for item in reading_items}
    theirs = {item["number"]: item for item in paper_items}
    report = {"reading": len(mine), "skeleton": len(theirs), "skeleton_complete": complete,
              "missing": [], "extra": [], "stem_differs": [], "options_differ": [],
              "same": 0, "kinds": {}}
    for number in sorted(set(mine) | set(theirs)):
        left, right = mine.get(number), theirs.get(number)
        if right is not None and left is None:
            report["missing"].append(number)
            continue
        if left is not None and right is None:
            # Only meaningful when the skeleton is complete. An incomplete skeleton never found
            # the question, so its absence says nothing about the reading.
            if complete:
                report["extra"].append(number)
            continue
        if normalized(left.get("stem")) != normalized(right.get("stem")):
            report["stem_differs"].append({
                "number": number,
                "reading": normalized(left.get("stem"))[:70],
                "skeleton": normalized(right.get("stem"))[:70]})
        left_options = _options(left)
        right_options = _options(right)
        if left_options != right_options:
            report["options_differ"].append({
                "number": number,
                "labels": [label for label in sorted(set(left_options) | set(right_options))
                           if left_options.get(label) != right_options.get(label)],
                "reading": {k: v[:40] for k, v in left_options.items()},
                "skeleton": {k: v[:40] for k, v in right_options.items()}})
            continue
        if normalized(left.get("stem")) == normalized(right.get("stem")):
            report["same"] += 1
    report["agree"] = report["same"]
    report["disagree"] = len(set(report["stem_differs"] and [row["number"] for row in report["stem_differs"]] or [])
                              | {row["number"] for row in report["options_differ"]})
    report["comparable"] = len(set(mine) | set(theirs))
    return report


def _options(item):
    options = item.get("options") or {}
    if isinstance(options, dict):
        return {label: normalized(body) for label, body in options.items()}
    return {str((entry or {}).get("label") or ""): normalized((entry or {}).get("text"))
            for entry in options if isinstance(entry, dict)}


def skeleton_of(pdf, rows=None, alphabet=None):
    rows = rows if rows is not None else extract.extract_cells_a(pdf)
    kept, _ = repair.mask_chrome(rows)
    table, _, measured = reflow.cells_with_pages(kept, alphabet=alphabet or ())
    import compare_skeleton
    paper = reflow.skeleton(table)
    return paper, compare_skeleton.skeleton_items(paper, table, measured), table, kept


def main() -> None:
    parser = argparse.ArgumentParser(description="Judge a reading against the skeleton.")
    parser.add_argument("--category", default="醫事檢驗師")
    parser.add_argument("--papers", default="")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--think", choices=("on", "off", "both"), default="both")
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-tokens", type=int, default=0)
    args = parser.parse_args()

    import batch_package
    wanted = [part for part in args.papers.split(",") if part.strip()]
    papers = batch_package.paper_pdfs(args.category)
    if wanted:
        papers = [paper for paper in papers
                  if any(paper["name"].startswith(prefix) for prefix in wanted)]
    papers.sort(key=lambda paper: (-int(paper["year"]), -int(paper["ordinal"]), paper["name"]))
    papers = papers[:args.limit]

    out = []
    for paper in papers:
        paper_skeleton, paper_items, table, kept = skeleton_of(paper["path"])
        print(f"\n=== {paper['name']}")
        print(f"  骨架： {paper_skeleton['count']} 題  完整={paper_skeleton['complete']}  "
              f"缺={paper_skeleton['missing'][:4]}  格數={len(table)}")
        for think in ((True,) if args.think == "on" else
                      (False,) if args.think == "off" else (True, False)):
            result = reflow.read_paper(paper["path"], subject=paper.get("subject", ""),
                                       year=str(paper.get("year", "")), think=think,
                                       rows=kept)
            items = result.get("items") or []
            report = judge(items, paper_items, complete=paper_skeleton["complete"])
            usage = result.get("usage") or {}
            record = {"paper": paper["name"], "think": think, "seconds": result.get("seconds"),
                      "admissible": result.get("admissible"), "items": len(items),
                      "usage": usage, "reasoning_tokens": result.get("reasoning_tokens"),
                      "skeleton_complete": paper_skeleton["complete"],
                      "report": report, "notes": result.get("notes")}
            out.append(record)
            print(f"  思考{'開' if think else '關'}： {result.get('seconds')}s  "
                  f"輸出 {usage.get('completion_tokens')}  "
                  f"思考 {result.get('reasoning_tokens')}  讀出 {len(items)} 題")
            print(f"    admissible={result.get('admissible')}  "
                  f"與骨架同題 {report['same']}/{report['comparable']}  "
                  f"漏題 {len(report['missing'])}  多題 {len(report['extra'])}  "
                  f"題幹異 {len(report['stem_differs'])}  選項異 {len(report['options_differ'])}")
            for row in report["stem_differs"][:3]:
                print(f"      Q{row['number']} 幹  讀:{row['reading'][:44]!r}")
                print(f"                紙:{row['skeleton'][:44]!r}")
            for row in report["options_differ"][:3]:
                print(f"      Q{row['number']} 項 {row['labels']}")
                for label in row["labels"][:2]:
                    print(f"           {label} 讀:{row['reading'].get(label,'')[:40]!r}"
                          f"  紙:{row['skeleton'].get(label,'')[:40]!r}")
            if result.get("notes"):
                print(f"    notes: {result['notes'][:200]}")

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(out, handle, ensure_ascii=False, indent=2)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
