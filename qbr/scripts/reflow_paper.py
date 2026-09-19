# -*- coding: utf-8 -*-
"""Read one paper with the local model and report what it did, next to what geometry did.

The point of running this on a paper the deterministic pipeline already handles is that the two
readings are then comparable: the same paper, the same cells, two readings of them. A model
reading is only interesting where it can be shown to differ, and the comparison is what shows it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qbr import reflow  # noqa: E402


def _deterministic(pdf):
    """The geometric reading of the same paper, for comparison. Never raises."""
    try:
        import three_way
        parsed = three_way.analyse_items(pdf)
        return {int(item["number"]): item for item in parsed["items"]}
    except Exception as exc:                                     # noqa: BLE001
        return {"__error__": str(exc)}


def compare(model_items, geometric):
    """Where the two readings disagree, question by question."""
    model = {item["number"]: item for item in model_items}
    rows = []
    for number in sorted(set(model) | set(item for item in geometric if isinstance(item, int))):
        mine, theirs = model.get(number), geometric.get(number)
        if mine is None:
            rows.append((number, "model-missing", "", ""))
            continue
        if theirs is None:
            rows.append((number, "geometry-missing", "", ""))
            continue
        option_labels = sorted(set(mine.get("options") or {}) | set((theirs.get("options") or {})))
        differing = [label for label in option_labels
                     if (mine.get("options") or {}).get(label) != (theirs.get("options") or {}).get(label)]
        if not differing and (mine.get("stem") or "") == (theirs.get("stem") or ""):
            continue
        rows.append((number, "stem" if (mine.get("stem") or "") != (theirs.get("stem") or "") else "options",
                     (mine.get("stem") or "")[:70], (theirs.get("stem") or "")[:70]))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Read one paper by model reflow.")
    parser.add_argument("pdf")
    parser.add_argument("--subject", default="")
    parser.add_argument("--year", default="")
    parser.add_argument("--count", type=int, default=None)
    parser.add_argument("--out", help="write the whole result here as JSON")
    parser.add_argument("--raw", action="store_true", help="keep the model's raw answer in the result")
    parser.add_argument("--show", type=int, default=0, help="print the first N items")
    parser.add_argument("--compare", action="store_true", help="compare against the geometric reading")
    args = parser.parse_args()

    result = reflow.read_paper(args.pdf, subject=args.subject, year=args.year,
                               count=args.count, emit_raw=args.raw)
    print(f"cells      {result['lines']}")
    print(f"alphabet   {result['alphabet']}")
    print(f"count      {result['count']} (答案卷)")
    print(f"parsed     {result['parsed']}")
    print(f"admissible {result['admissible']}")
    print(f"items      {len(result['items'])}")
    print(f"seconds    {result['seconds']}")
    print(f"usage      {result['usage']}")
    print(f"report     {json.dumps(result['report'], ensure_ascii=False)}")
    if result.get("notes"):
        print(f"notes      {result['notes']}")

    for item in result["items"][:args.show]:
        print(f"\nQ{item['number']}" + (f"  [{', '.join(item['flags'])}]" if item["flags"] else ""))
        print(f"  {item['stem']}")
        for label in item["option_order"]:
            print(f"    {label}. {item['options'][label]}")

    if args.compare:
        geometric = _deterministic(args.pdf)
        if "__error__" in geometric:
            print(f"\ngeometric reading failed: {geometric['__error__']}")
        else:
            rows = compare(result["items"], geometric)
            print(f"\n=== 與幾何讀法之差異：{len(rows)} 題有出入 / {len(geometric)} 題")
            for number, kind, mine, theirs in rows[:25]:
                print(f"  Q{number:<4} {kind:16} 模型={(mine or '')!r}")
                print(f"  {'':4} {'':16} 幾何={(theirs or '')!r}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=1)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
