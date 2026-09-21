#!/usr/bin/env python3
"""The acceptance standard, executable: every shipped field must be found by the second engine.

Re-segmenting a paper with engine B in the lead cannot be the test. Engine B puts an option's marker,
and every wrapped line, on a row of its own, so a B-led reading rotates each option by one - a
property of the instrument, not a disagreement about the paper. Measured: 9.06% of fields came out
"unexplained" that way, and almost all of it was that single instrument property.

The unit that works is the option's own text span, measured inside each engine's own reading.
`qbr.continuation` holds the rule and its control; this script only runs it over papers and reports.

Run it on both engines. A loss seen by ONE engine may be that engine's line splitting.
A loss seen by BOTH is not - two independent readings agree the paper continues and our field stops.

Measured over 160 sampled papers (12,840 questions): A alone 631, B alone 9,157,
BOTH 463 fields on 301 questions (2.34%), across 75 of the 160 papers.

Usage:
    .venv/bin/python scripts/verify_option_continuation.py [papers] [seed]
"""
import argparse
import collections
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qbr import continuation, extract, paths, repair  # noqa: E402

#: The official PDFs live under more than one corpus root, and which one holds a given category is
#: a fact about the corpus rather than about this check. Searched in order.
CORPUS_ROOTS = ("國考題資料夾", "國考題資料夾_非醫學剩餘全集", "國考題資料夾_其他類型")


def question_pdf(root, paper):
    """The question sheet for one paper in `queue_index.json`, or None if the corpus lacks it."""
    relative = os.path.join("10_official_pdf", "by_official_catalog", paper["category"],
                            str(paper["year"]), "第%s次" % paper["ordinal"])
    for corpus in CORPUS_ROOTS:
        directory = os.path.join(root, corpus, relative)
        if not os.path.isdir(directory):
            continue
        names = [name for name in os.listdir(directory)
                 if name.startswith(paper["run"]) and name.endswith(".pdf")
                 and "_ANS" not in name and "_MOD" not in name]
        if names:
            return os.path.join(directory, sorted(names)[0])
    return None


def shipped_fields(items):
    """The packaged fields, in the shape `continuation` compares against."""
    out = {}
    for item in items:
        number = item.get("number")
        if number is None:
            continue
        options = item.get("options") or {}
        if isinstance(options, list):
            options = {option.get("key"): (option.get("text") or "")
                       for option in options if isinstance(option, dict)}
        out[number] = {key: (value if isinstance(value, str) else (value or {}).get("text") or "")
                       for key, value in options.items()}
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("papers", nargs="?", type=int, default=160)
    parser.add_argument("seed", nargs="?", type=int, default=7)
    parser.add_argument("--queue", default=os.path.join("data", "review-queues", "live", "review-ui"))
    args = parser.parse_args(argv)

    root = paths.repo_root()
    index = json.load(open(os.path.join(root, "qbr", args.queue, "queue_index.json"),
                           encoding="utf-8"))
    papers = index["per_paper"]
    random.seed(args.seed)
    sample = random.sample(papers, min(args.papers, len(papers)))

    results = []
    questions = 0
    examples = []
    papers_with_loss = set()
    for paper in sample:
        pdf = question_pdf(root, paper)
        if not pdf:
            continue
        rows_a = repair.mask_chrome(extract.extract_lines_a(pdf))[0]
        rows_b = repair.mask_chrome(extract.extract_lines_b(pdf))[0]
        # The shipped fields are read from the same reading the pipeline segments, so the check and
        # the defect share no assumption beyond the paper itself.
        repaired_a = repair.text_from_rows(rows_a)
        records, _residual, _diagnostics = repair.segment_best(repaired_a)
        fields = shipped_fields(records)
        questions += len(fields)
        result = continuation.verify_paper(
            repair.text_from_rows(rows_a).split("\n"),
            repair.text_from_rows(rows_b).split("\n"),
            fields)
        results.append(result)
        if result["both"]:
            papers_with_loss.add(paper["run"])
        for item in result["both"]:
            if len(examples) < 20:
                examples.append(dict(item, paper=paper["run"][:38]))

    totals = continuation.summarise(results)
    print("=== option text dropped at a line wrap, measured inside each engine's own reading ===")
    print("  papers                %d" % totals["papers"])
    print("  questions read        %d" % questions)
    print("  loss seen by A        %d" % totals["loss_a"])
    print("  loss seen by B        %d" % totals["loss_b"])
    print("  seen by BOTH (hard)   %d" % totals["loss_both"])
    print("  distinct questions    %d" % totals["questions_with_loss"])
    print("  distinct papers       %d" % len(papers_with_loss))
    print("\n=== seen by both engines ===")
    for item in examples:
        print("  %s q%s %s: shipped ...%r  DROPPED %r"
              % (item["paper"], item["question_number"], item["option"],
                 item["shipped_tail"], item["dropped"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
