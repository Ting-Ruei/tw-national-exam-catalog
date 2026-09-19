# -*- coding: utf-8 -*-
"""Where the local model's balance point is, measured per task rather than assumed.

Two questions are being settled here, and neither can be settled by argument because the engine
behaves in ways that are not documented:

    does turning thinking off keep the answer?   A faster wrong answer is the most expensive thing
                                                 in this pipeline - it costs a re-run of the whole
                                                 paper - so the comparison is on the answer.
    which triggers actually find a figure?       The word `圖` is in 775 questions in range and most
                                                 need no crop; 88 questions have no option text at
                                                 all and every one needs a crop. Counting both is
                                                 the only way to know which to use.

The tasks are run through the same prompts the pipeline uses, so what is measured is what runs.
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


def _usage_of(result):
    usage = result.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    thinking = details.get("reasoning_tokens")
    if thinking is None:
        thinking = usage.get("reasoning_tokens")
    return {"out": usage.get("completion_tokens"), "think_tokens": thinking,
            "prompt": usage.get("prompt_tokens")}


def figure_inventory(papers, *, limit_papers=0):
    """How many figures each trigger finds, before any model is called.

    This is the measurement that decides the trigger. It costs nothing, so it can be run over the
    whole corpus rather than over a sample.
    """
    totals = collections.Counter()
    rows_out = []
    for index, paper in enumerate(papers, start=1):
        if limit_papers and index > limit_papers:
            break
        try:
            rows = extract.extract_cells_a(paper["path"])
            kept, _ = repair.mask_chrome(rows)
            table, _, alphabet = reflow.cells_with_pages(kept)
            items = _items_of(paper["path"], kept, table, alphabet)
            images = extract.extract_images_a(paper["path"])
            found = vision.figure_questions(items, kept, images, alphabet=alphabet)
        except Exception as exc:                                     # noqa: BLE001
            totals["errors"] += 1
            rows_out.append({"name": paper["name"], "error": str(exc)})
            continue
        reasons = collections.Counter(reason for entry in found for reason in entry["reasons"])
        word = sum(1 for item in items if "圖" in (item.get("stem") or ""))
        totals["papers"] += 1
        totals["items"] += len(items)
        totals["images"] += len(images)
        totals["questions"] += len(found)
        totals["stem-says-圖"] += word
        totals.update({f"reason:{k}": v for k, v in reasons.items()})
        rows_out.append({"name": paper["name"], "year": paper["year"], "items": len(items),
                         "images": len(images), "figures": len(found),
                         "stem-says-圖": word, "reasons": dict(reasons)})
        print(f"[{index:>3}] {paper['name'][:44]:46} items={len(items):>4} 圖={word:>3} "
              f"img={len(images):>3} figures={len(found):>3} {dict(reasons)}", flush=True)
    return totals, rows_out


def _items_of(pdf, kept, table, alphabet):
    """The deterministic reading as items, so the figure triggers can be measured without a model.

    The skeleton is used because it is the paper's own statement of its structure, and it is the
    only reading that records *cell* ids - the geometric reading records text lines, which are a
    different and coarser unit, so a trigger computed from it would compare the wrong things. A
    partial skeleton is used as-is: the questions it did not find are ones the paper's own marks
    did not describe, and they are already reported as needing a person, so dropping them here
    understates the figure count rather than overstating it.
    """
    import compare_skeleton
    skeleton = reflow.skeleton(table)
    return compare_skeleton.skeleton_items(skeleton, table, alphabet)


def thinking_balance(papers, *, kinds=("text", "vision"), limit_papers=1, numbers=None):
    """Run the same task with thinking on and off, and compare the answers.

    The comparison is not a score: for the text side it is the guardrail's own verdict plus the
    agreement with the paper's skeleton, and for the figure side it is the verdict itself. What is
    being asked is whether thinking off returns a *usable* answer, which is a different question
    from whether it returns the same one.
    """
    out = []
    for paper in papers[:limit_papers]:
        if "text" in kinds:
            out.append(_compare_text(paper, think=True))
            out.append(_compare_text(paper, think=False))
        if "vision" in kinds:
            out.extend(_compare_vision(paper, numbers=numbers))
    return out


def _compare_text(paper, *, think, numbers=None):
    started = time.time()
    result = reflow.read_paper(paper["path"], subject=paper.get("subject", ""),
                               year=str(paper.get("year", "")), think=think)
    report = result.get("report") or {}
    return {
        "task": "text", "think": think, "paper": paper["name"],
        "seconds": round(time.time() - started, 1),
        "admissible": result.get("admissible"),
        "questions": len(result.get("items") or []),
        "lines": result.get("lines"),
        "invented": len(report.get("invented_characters") or {}),
        "dropped": len(report.get("dropped_characters") or {}),
        "duplicated": report.get("duplicated_lines"),
        "unassigned": report.get("unassigned_lines"),
        "assigned": report.get("assigned"),
        "perm_lines": report.get("perm_lines"),
        **_usage_of(result),
    }


def _compare_vision(paper, *, numbers=None):
    """The figure records, twice: once thinking, once not, on the same crops."""
    rows = extract.extract_cells_a(paper["path"])
    kept, _ = repair.mask_chrome(rows)
    table, _, alphabet = reflow.cells_with_pages(kept)
    items = _items_of(paper["path"], kept, table, alphabet)
    images = extract.extract_images_a(paper["path"])
    found = vision.figure_questions(items, kept, images, alphabet=alphabet)
    if numbers:
        found = [entry for entry in found if entry["number"] in set(numbers)]
    if not found:
        return []
    out = []
    for entry in found[:4]:
        png, page = vision.crop_for(entry, paper["path"])
        if png is None:
            continue
        for think in (True, False):
            result = vision.describe_crop(png, subject=paper.get("subject", ""),
                                          question=entry["stem"], think=think)
            verdict = result.get("verdict") or {}
            out.append({
                "task": "vision", "think": think, "paper": paper["name"],
                "number": entry["number"], "reasons": entry["reasons"], "page": page,
                "seconds": result.get("seconds"), "contains": verdict.get("contains"),
                "confidence": verdict.get("confidence"),
                "describes": (verdict.get("describes") or "")[:80],
                "readable": len(verdict.get("readable_values") or []),
                "uncertain": len(verdict.get("uncertain") or []),
                "usable": bool(verdict),
                **_usage_of(result),
            })
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure the local model's balance point.")
    parser.add_argument("--mode", choices=("inventory", "think", "both"), default="both")
    parser.add_argument("--category", default="醫事檢驗師")
    parser.add_argument("--years", default="101-115")
    parser.add_argument("--papers", type=int, default=1)
    parser.add_argument("--numbers", default="")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    import batch_package
    low, high = (int(part) for part in args.years.split("-"))
    papers = [paper for paper in batch_package.paper_pdfs(args.category)
              if low <= int(paper["year"]) <= high]
    papers.sort(key=lambda paper: (-int(paper["year"]), -int(paper["ordinal"]), paper["name"]))
    numbers = [int(part) for part in args.numbers.split(",") if part.strip()]
    out = {}

    if args.mode in ("inventory", "both"):
        print("=== 圖片觸發條件盤點（不呼叫模型）")
        totals, rows_out = figure_inventory(papers, limit_papers=args.papers if args.papers > 1 else 0)
        out["inventory"] = {"totals": dict(totals), "papers": rows_out}
        print(f"\n  卷 {totals['papers']}  題 {totals['items']}  內嵌圖 {totals['images']}")
        print(f"  觸發到的圖片題 {totals['questions']}  其中題幹提到「圖」 {totals['stem-says-圖']}")
        for key, value in sorted(totals.items()):
            if key.startswith("reason:"):
                print(f"    {key:26} {value}")

    if args.mode in ("think", "both"):
        print("\n=== 思考開/關對照")
        rows_out = thinking_balance(papers, limit_papers=args.papers, numbers=numbers or None)
        out["think"] = rows_out
        for row in rows_out:
            if row["task"] == "text":
                print(f"  text   think={str(row['think']):5} {row['seconds']:7.1f}s "
                      f"out={str(row['out']):>6} think={str(row['think_tokens']):>6} "
                      f"admissible={row['admissible']} q={row['questions']}/{row['lines']} "
                      f"dup={row['duplicated']} unassigned={row['unassigned']}")
            else:
                print(f"  vision think={str(row['think']):5} Q{row['number']:<4} "
                      f"{str(row['seconds']):>6}s out={str(row['out']):>5} "
                      f"think={str(row['think_tokens']):>5} contains={row['contains']} "
                      f"conf={row['confidence']} {row['describes'][:40]}")

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(out, handle, ensure_ascii=False, indent=2)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
