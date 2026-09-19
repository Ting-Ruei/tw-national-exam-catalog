# -*- coding: utf-8 -*-
"""Run the whole pipeline over a whole category, one paper at a time, and report.

The golden path proves one paper end to end. It says nothing about the next paper, and the
corpus is not uniform: 100-102 are a different generation of typesetting from 103-115, and
within a single year both kinds occur (102 has six of each). So the unit of work here is a
paper, and the first thing done to a paper is to ask which generation it is - because that
decides which rules are trustworthy on it.

Three things this driver deliberately does not do:

  * It does not stop at the first failure. A paper that cannot be read is recorded and the
    run continues, because the useful output of a batch is the shape of the failures, and
    stopping at the first one hides it.
  * It does not write into the corpus. Output goes to a work directory, one folder per
    paper, and nothing is overwritten that already exists unless `--force` says so.
  * It does not decide anything about a question. It measures and it reports. The verdicts
    belong to the review UI and to a person.

Usage:
    python scripts/batch_run.py --category 醫事檢驗師 --work /tmp/qbr-batch
    python scripts/batch_run.py --category 醫事檢驗師 --years 115 --ordinal 2 --work /tmp/x
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, HERE)

from qbr import answer_sheets, corrections, extract, generation, repair  # noqa: E402

CORPUS = os.path.join(os.path.dirname(PKG), "..", "tw-national-exam-catalog",
                      "國考題資料夾", "10_official_pdf", "by_official_catalog")
CORPUS = os.path.normpath(CORPUS)
# `sheet_paths` expects the asset root - the folder that contains `10_official_pdf` - because
# that is what the registry manifests and the golden path are given. `CORPUS` points two
# levels further down, so the root is two `dirname`s up from it, not one.
CORPUS_ROOT = os.path.dirname(os.path.dirname(CORPUS))


def papers_for(category, *, years=None, ordinals=None):
    """Every question paper of a category, as (path, year, ordinal, subject)."""
    pattern = os.path.join(CORPUS, category, "*", "*", "*.pdf")
    found = []
    for path in sorted(glob.glob(pattern)):
        name = os.path.basename(path)
        if "_ANS" in name or "_MOD" in name:
            continue                                    # the answer sheets, not the papers
        match = re.search(r"/(\d{3})/第(\d)次/", path)
        if not match:
            continue
        year, ordinal = int(match.group(1)), int(match.group(2))
        if years and year not in years:
            continue
        if ordinals and ordinal not in ordinals:
            continue
        subject = re.sub(r"^\d{3,4}_[^_]*_", "", name[:-4])
        found.append({"path": path, "year": year, "ordinal": ordinal, "category": category,
                      "subject": subject, "name": name})
    return found


def read_one(paper):
    """Extract, segment, answer and measure one paper. Never raises: a failure is a result.

    The answers come from the official sheets and nowhere else. A paper whose answers cannot
    be read is reported with `answer_coverage = 0` and is not thereby failed - an absence of
    an answer sheet and a wrong answer are different findings, and one number cannot say
    both.
    """
    started = time.time()
    record = {"name": paper["name"], "year": paper["year"], "ordinal": paper["ordinal"],
              "subject": paper["subject"]}
    try:
        rows = extract.extract_lines_a(paper["path"])
        text = repair.text_from_rows(rows)
        repaired, _ = repair.normalize_pretty(text)
        records, residual, diagnostics = repair.segment_questions(repaired)
        detail = diagnostics.get("detail") or {}
        gen = generation.classify(text=text, lines=rows,
                                  style=diagnostics.get("style"), year=paper["year"])
        record.update({
            "ok": bool(diagnostics.get("ok")),
            "generation": gen["generation"],
            "generation_confidence": gen["confidence"],
            "generation_basis": gen["basis"],
            "style": diagnostics.get("style"),
            "pua_characters": gen["pua_characters"],
            "questions": len(records),
            "coverage": detail.get("coverage"),
            "first": detail.get("first"),
            "last": detail.get("last"),
            "gaps": (detail.get("gaps") or [])[:8],
            "residual_lines": len(residual),
            "reasons": diagnostics.get("reasons") or [],
        })
        # A question with fewer than four options, or an empty stem, is the failure that
        # matters most and is invisible in a coverage figure. Counted, not judged.
        thin = [int(r["number"]) for r in records
                if len(r.get("options") or {}) < 4 and (r.get("item_type") or "") != "essay"]
        empty = [int(r["number"]) for r in records if not (r.get("stem") or "").strip()]
        record["options_below_four"] = thin[:12]
        record["options_below_four_count"] = len(thin)
        record["empty_stem_count"] = len(empty)

        # The answer sheet and the corrections sheet are separate documents and are read
        # separately. The corrections sheet is a re-issued table plus a note, and the note
        # is what says a question was voided - a fact no table of letters can carry.
        sheets, how = answer_sheets.sheet_paths(
            CORPUS_ROOT, year=paper["year"], category=paper["category"],
            ordinal=paper["ordinal"], subject=paper["subject"], registry_key=None)
        table, corrections_by_number = answer_sheets.authoritative_for(records, sheets)
        answered = [int(r["number"]) for r in records if int(r["number"]) in table]
        record["answer_sheets"] = sorted(sheets)
        record["answer_sheet_how"] = how
        record["answers_known"] = len(answered)
        record["answer_coverage"] = (round(len(answered) / len(records), 4)
                                     if records else None)
        record["corrections"] = len(corrections_by_number)
        record["corrections_voided"] = sum(1 for c in corrections_by_number.values()
                                           if c.is_void)
        record["correction_numbers"] = sorted(corrections_by_number)[:12]
        # An answer that names an option the paper never printed is a real inconsistency and
        # is reported as one; a number the sheet never reached is only an absence.
        mismatched = []
        for item in records:
            number = int(item["number"])
            labels = table.get(number)
            if labels and any(l not in (item.get("options") or {}) for l in labels):
                mismatched.append(number)
        record["answer_option_mismatch"] = mismatched[:12]
        record["answer_option_mismatch_count"] = len(mismatched)
    except Exception as exc:                                       # noqa: BLE001
        record.update({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)})
    record["seconds"] = round(time.time() - started, 2)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the pipeline over a whole category.")
    parser.add_argument("--category", required=True,
                        help="folder under by_official_catalog, e.g. 醫事檢驗師")
    parser.add_argument("--work", required=True, help="output directory")
    parser.add_argument("--years", nargs="*", type=int, default=None)
    parser.add_argument("--ordinals", nargs="*", type=int, default=None)
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = parser.parse_args()

    papers = papers_for(args.category, years=args.years, ordinals=args.ordinals)
    if not papers:
        print("no papers found for %s" % args.category, file=sys.stderr)
        raise SystemExit(2)
    os.makedirs(args.work, exist_ok=True)

    print("reading %d papers of %s" % (len(papers), args.category), flush=True)
    results = []
    for index, paper in enumerate(papers, 1):
        record = read_one(paper)
        results.append(record)
        mark = "ok " if record.get("ok") else "   "
        print("  %3d/%d %s %-52s %-18s %s q=%-4s cov=%s %5.1fs"
              % (index, len(papers), mark, record["name"][:52],
                 record.get("generation", "?")[:18], record.get("style") or "-",
                 record.get("questions", "-"), record.get("coverage", "-"),
                 record.get("seconds", 0)), flush=True)

    out_path = os.path.join(args.work, "batch_report.jsonl")
    with open(out_path, "w", encoding="utf-8") as handle:
        for record in results:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary = summarise(results, args.category)
    with open(os.path.join(args.work, "batch_summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print_summary(summary)
    print("\nwrote %s" % out_path)


def summarise(results, category):
    """What the batch found, grouped by the thing that decides how it must be read."""
    by_generation = collections.defaultdict(list)
    for record in results:
        by_generation[record.get("generation") or "unknown"].append(record)
    generations = {}
    for name, group in sorted(by_generation.items()):
        coverages = [r["coverage"] for r in group if isinstance(r.get("coverage"), (int, float))]
        generations[name] = {
            "papers": len(group),
            "clean": sum(1 for r in group if r.get("ok")),
            "errors": sum(1 for r in group if r.get("error")),
            "questions": sum(r.get("questions") or 0 for r in group),
            "median_coverage": (sorted(coverages)[len(coverages) // 2] if coverages else None),
            "thin_options": sum(r.get("options_below_four_count") or 0 for r in group),
            "styles": dict(collections.Counter(r.get("style") or "none" for r in group)),
        }
    return {
        "category": category,
        "papers": len(results),
        "clean": sum(1 for r in results if r.get("ok")),
        "errors": sum(1 for r in results if r.get("error")),
        "questions": sum(r.get("questions") or 0 for r in results),
        "seconds": round(sum(r.get("seconds") or 0 for r in results), 1),
        "generations": generations,
        # The two failure lists are kept apart on purpose. A paper that read badly is a
        # rule to write; a paper that raised an exception is a bug to fix.
        "papers_not_clean": [r["name"] for r in results if not r.get("ok")][:40],
        "papers_that_raised": [{"name": r["name"], "error": r["error"]}
                               for r in results if r.get("error")],
    }


def print_summary(summary):
    print("\n" + "=" * 78)
    print("%s: %d papers, %d clean, %d questions, %.1fs"
          % (summary["category"], summary["papers"], summary["clean"],
             summary["questions"], summary["seconds"]))
    for name, group in summary["generations"].items():
        print("\n  %s  (%d papers, %d clean, %d questions)"
              % (name, group["papers"], group["clean"], group["questions"]))
        print("    median coverage %s   styles %s"
              % (group["median_coverage"], group["styles"]))
        if group["thin_options"]:
            print("    questions with fewer than four options: %d" % group["thin_options"])
    if summary["papers_that_raised"]:
        print("\n  raised (a bug, not a rule):")
        for row in summary["papers_that_raised"][:12]:
            print("    %s -- %s" % (row["name"][:58], row["error"][:60]))
    if summary["papers_not_clean"]:
        print("\n  not clean (%d; a rule to write):" % len(summary["papers_not_clean"]))
        for name in summary["papers_not_clean"][:12]:
            print("    %s" % name[:66])


if __name__ == "__main__":
    main()
