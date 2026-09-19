# -*- coding: utf-8 -*-
"""Does the vision model actually tell a figure from text?

The trigger says where the figures are; it does not say whether the model can read them. Those are
two different claims and only the second one needs a model, so this script tests the model against
answers that were established *without* it:

    a positive control   a region that was measured to hold an embedded image (a blood smear, an
                         ECG trace, a spectrum). The model has to say `figure`.
    a negative control   a region that was measured to hold only text - a question's own cells,
                         with no image object overlapping them. The model has to NOT say `figure`.

The negative control is the half that matters. A model that answers `figure` to everything would
pass every positive case and be worthless; a model that answers `text` to everything would pass
every negative case and be worthless. Running both, on the same paper, is what makes the result
mean something.

Each case is run with thinking on and with thinking off, because the balance point cannot be found
from one side: what is being measured is whether the cheaper answer is still the right answer.
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


def cases_of(paper, *, max_positives=3, max_negatives=3):
    """Positive and negative cases for one paper, both established by measurement."""
    rows = extract.extract_cells_a(paper["path"])
    kept, _ = repair.mask_chrome(rows)
    table, _, alphabet = reflow.cells_with_pages(kept)
    import compare_skeleton
    items = compare_skeleton.skeleton_items(reflow.skeleton(table), table, alphabet)
    images = extract.extract_images_a(paper["path"])

    positives = vision.figure_questions(items, kept, images, alphabet=alphabet)
    # A negative is a question the trigger did *not* fire on, and whose cells are all on one page.
    # One page matters: a crop is one rectangle in one page's coordinates, so a question spanning
    # two pages has no single honest crop and cannot serve as a control.
    fired = {entry["number"] for entry in positives}
    by_number = {}
    for index, row in enumerate(kept, start=1):
        by_number[index] = row
    negatives = []
    for item in items:
        if item["number"] in fired:
            continue
        cells = vision.cell_ids_of(item)
        page_rows = [by_number[cell] for cell in cells if cell in by_number]
        if len(page_rows) < 3:
            continue
        if len({int(row["page"]) for row in page_rows}) != 1:
            continue
        negatives.append({"number": item["number"], "page": int(page_rows[0]["page"]),
                          "box": vision._box_of(page_rows), "stem": (item.get("stem") or "")[:200],
                          "reasons": ["text-only"]})
    return (positives[:max_positives], negatives[:max_negatives])


def run_case(pdf, entry, *, subject, think, expected=None):
    png, page = vision.crop_for(entry, pdf)
    if png is None:
        return None
    result = vision.describe_crop(png, subject=subject, question=entry["stem"], think=think)
    verdict = result.get("verdict") or {}
    usage = result.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    thinking = details.get("reasoning_tokens")
    if thinking is None:
        thinking = usage.get("reasoning_tokens")
    # The expectation is passed in rather than derived from the reason, because a positive can be
    # positive for either of two measured reasons and one of them - `options-without-text` - is a
    # property of the question rather than of an image object. Deriving it from the reason was a
    # real bug in this harness: question 16 of `1152_醫事檢驗師_臨床血液學與血庫學` is four
    # blood-smear photographs, the model read it correctly as `figure`, and the harness scored it
    # wrong because the trigger that found it was not the image one.
    if expected is None:
        expected = "figure" if "embedded-image" in entry["reasons"] else "text"
    return {
        "number": entry["number"], "page": page, "expected": expected,
        "reasons": entry["reasons"], "think": think,
        "contains": verdict.get("contains"), "confidence": verdict.get("confidence"),
        "describes": verdict.get("describes"), "axis_labels": verdict.get("axis_labels"),
        "readable_values": len(verdict.get("readable_values") or []),
        "uncertain": len(verdict.get("uncertain") or []),
        "seconds": result.get("seconds"), "out": usage.get("completion_tokens"),
        "think_tokens": thinking, "png_bytes": len(png),
        # A positive is any answer that says "there is something here that is not running text",
        # which is what the crop is for. The prompt distinguishes `figure` from `table` because a
        # reviewer wants to know which, but both mean the question carries content the text layer
        # did not have - and scoring only `figure` as correct was a real defect in this harness:
        # question 36 of `1102_醫事檢驗師_醫學分子檢驗學與臨床鏡檢學` is a urinalysis table, the
        # model called it `table` at 0.98 confidence, and the harness counted it wrong twice.
        "correct": (verdict.get("contains") in ("figure", "table")) if expected == "figure"
                   else (bool(verdict.get("contains")) and verdict.get("contains") != "figure"),
        "usable": bool(verdict),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Test the vision model against measured controls.")
    parser.add_argument("--category", default="醫事檢驗師")
    parser.add_argument("--years", default="101-115")
    parser.add_argument("--papers", type=int, default=3)
    parser.add_argument("--positives", type=int, default=2)
    parser.add_argument("--negatives", type=int, default=2)
    parser.add_argument("--out", required=True)
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--only-figures", action="store_true",
                        help="papers measured to hold an embedded image, most first")
    args = parser.parse_args()

    import batch_package
    low, high = (int(part) for part in args.years.split("-"))
    papers = [paper for paper in batch_package.paper_pdfs(args.category)
              if low <= int(paper["year"]) <= high]
    if args.only_figures:
        # Ordered by how many embedded images the paper was measured to hold, so the run meets the
        # positive controls early instead of spending its budget on papers that have none.
        def image_count(paper):
            return len([image for image in extract.extract_images_a(paper["path"])
                        if (image["y1"] - image["y0"]) >= vision.MIN_FIGURE_HEIGHT])

        papers = [paper for paper in papers if image_count(paper) > 0]
        papers.sort(key=image_count, reverse=True)
    else:
        # Oldest first here, deliberately: the newest papers are the ones the pipeline already
        # reads well, so a test run on them would be a test of the easy cases.
        papers.sort(key=lambda paper: (int(paper["year"]), int(paper["ordinal"]), paper["name"]))

    rows_out = []
    totals = collections.Counter()
    started = time.time()
    for paper in papers:
        if totals["papers"] >= args.papers:
            break
        try:
            positives, negatives = cases_of(paper, max_positives=args.positives,
                                            max_negatives=args.negatives)
        except Exception as exc:                                    # noqa: BLE001
            print(f"  {paper['name'][:44]:46} ERROR {exc}", flush=True)
            continue
        if not positives and not negatives:
            continue
        totals["papers"] += 1
        print(f"\n=== {paper['name']}", flush=True)
        for entry in positives + negatives:
            expected = "figure" if entry in positives else "text"
            for think in (True, False):
                try:
                    record = run_case(paper["path"], entry, subject=paper.get("subject", ""),
                                      think=think, expected=expected)
                except Exception as exc:                            # noqa: BLE001
                    print(f"    Q{entry['number']:<4} ERROR {exc}", flush=True)
                    continue
                if record is None:
                    continue
                record["paper"] = paper["name"]
                rows_out.append(record)
                totals[f"{record['expected']}:{'ok' if record['correct'] else 'wrong'}"] += 1
                totals[f"think={record['think']}"] += 1
                if record["out"]:
                    totals["out_tokens"] += record["out"]
                if record["think_tokens"]:
                    totals["think_tokens"] += record["think_tokens"]
                totals["seconds"] = round(totals["seconds"] + (record["seconds"] or 0), 1)
                if args.out_dir and not think:
                    os.makedirs(args.out_dir, exist_ok=True)
                    png, _ = vision.crop_for(entry, paper["path"])
                    suffix = "fig" if record["expected"] == "figure" else "txt"
                    path = os.path.join(args.out_dir,
                                        f"{paper['name'][:8]}_q{record['number']:03d}_{suffix}.png")
                    with open(path, "wb") as handle:
                        handle.write(png)
                print(f"    Q{record['number']:<4} {record['expected']:>6} "
                      f"think={str(think):5} {str(record['seconds']):>6}s "
                      f"out={str(record['out']):>5} think={str(record['think_tokens']):>5} "
                      f"→ {str(record['contains']):>7} conf={record['confidence']} "
                      f"{'✓' if record['correct'] else '✗'}", flush=True)

    print(f"\n=== 視覺判別（{args.papers} 卷上限，正/負對照）")
    print(f"  總秒數 {totals['seconds']}  完成 {totals['papers']} 卷  {len(rows_out)} 次判讀")
    for key in ("figure:ok", "figure:wrong", "text:ok", "text:wrong"):
        print(f"  {key:14} {totals[key]}")
    for think in (True, False):
        cases = [row for row in rows_out if row["think"] is think]
        if not cases:
            continue
        good = sum(1 for row in cases if row["correct"])
        secs = sum(row["seconds"] or 0 for row in cases)
        out = sum(row["out"] or 0 for row in cases)
        thinking = sum(row["think_tokens"] or 0 for row in cases)
        print(f"\n  思考{'開' if think else '關'}： {good}/{len(cases)} 正確   "
              f"平均 {secs / len(cases):.1f}s   輸出 {out} tokens   思考 {thinking} tokens")
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump({"totals": dict(totals), "rows": rows_out}, handle,
                  ensure_ascii=False, indent=2)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
