#!/usr/bin/env python3
"""Run the figure-attribution measurement over many medical papers and aggregate.

Why a batch driver: the single-paper runs answered "does the machine work" on 2 papers and
11 pages. A rate needs a sample, and the medical corpus is where the demand is, so this
walks medical papers that actually carry figures, picks their figure pages, and calls
`measure_figure_attribution.score_page` on each.

It also reports which pages have a caption (圖N) and whether the caption's question matches
the question the figure was attributed to -- the same two-independent-ground-truths check
used for scanned pages, applied to digital ones so the two are comparable.

Usage:
  measure_figure_attribution_batch.py --sample 15 --out medical-attribution.json
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import random
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from measure_figure_attribution import score_page  # noqa: E402

DEFAULT_ROOT = "國考題資料夾/10_official_pdf/by_official_catalog"


def figure_pages(pdf: str):
    """Pages that carry a drawn image at least 80x80 px, and the total page count."""
    try:
        info = subprocess.run(["pdfinfo", pdf], capture_output=True, timeout=30).stdout.decode("utf-8", "replace")
        m = re.search(r"Pages:\s+(\d+)", info)
        pages = int(m.group(1)) if m else 0
        out = subprocess.run(["pdfimages", "-list", pdf], capture_output=True, timeout=30).stdout.decode("utf-8", "replace")
    except Exception:
        return None
    counter = collections.Counter()
    for line in out.splitlines()[2:]:
        p = line.split()
        if len(p) < 5:
            continue
        try:
            pg, w, h = int(p[0]), int(p[3]), int(p[4])
        except ValueError:
            continue
        if w >= 80 and h >= 80:
            counter[pg] += 1
    return {"pages": pages, "figure_pages": sorted(counter)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--sample", type=int, default=15, help="number of papers to measure")
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--max-pages-per-paper", type=int, default=4)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out")
    args = ap.parse_args()

    files = []
    for dp, _dn, fn in os.walk(args.root):
        for f in fn:
            if f.endswith(".pdf") and not f.endswith(("_ANS.pdf", "_MOD.pdf")):
                files.append(os.path.join(dp, f))
    random.seed(args.seed)
    random.shuffle(files)

    # find enough papers that carry figures
    picked = []
    with ThreadPoolExecutor(24) as ex:
        for pdf, info in zip(files, ex.map(figure_pages, files)):
            if info and info["figure_pages"]:
                picked.append((pdf, info))
            if len(picked) >= args.sample:
                break
    print(f"papers with figures: {len(picked)} (scanned from {len(files)} medical pdfs)")

    jobs = []
    for pdf, info in picked:
        for pg in info["figure_pages"][:args.max_pages_per_paper]:
            jobs.append((pdf, pg))
    print(f"figure pages to measure: {len(jobs)}")

    def run(job):
        pdf, pg = job
        try:
            return score_page("/Users/tim/models/glm-ocr/sdk-weights/pp-doclayoutv3-onnx/PP-DocLayoutV3.onnx",
                              pdf, pg)
        except Exception as exc:  # noqa: BLE001
            return {"page": pg, "paper": pdf, "error": f"{type(exc).__name__}: {exc}"}

    with ThreadPoolExecutor(args.workers) as ex:
        results = list(ex.map(run, jobs))

    ok = [r for r in results if "error" not in r]
    withpred = [r for r in ok if r["predicted_figures"]]
    n_pred = sum(r["predicted_figures"] for r in withpred)
    n_correct = sum(r["attribution_correct"] for r in withpred)
    qs = [q for r in withpred for q in r["per_question"]]
    covs = [q["range_coverage"] for q in qs if q["range_coverage"] is not None]
    purs = [q["range_purity"] for q in qs if q["range_purity"] is not None]

    summary = {
        "papers": len(picked),
        "pages_measured": len(ok),
        "pages_with_a_predicted_figure": len(withpred),
        "predicted_figures": n_pred,
        "attribution_correct": n_correct,
        "attribution_accuracy": round(n_correct / n_pred, 4) if n_pred else None,
        "questions_with_figures": len(qs),
        "mean_range_coverage": round(sum(covs) / len(covs), 4) if covs else None,
        "mean_range_purity": round(sum(purs) / len(purs), 4) if purs else None,
        "errors": sum(1 for r in results if "error" in r),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # pages the detector found nothing on, although the PDF draws a picture there
    misses = [{"paper": os.path.basename(j[0]), "page": j[1]} for j, r in zip(jobs, results)
              if "error" not in r and r["predicted_figures"] == 0 and r["true_figures"] > 0]
    if misses:
        print(f"pages where the PDF draws a figure but the detector returned none: {len(misses)}")
        for m in misses[:12]:
            print("   ", m["paper"], "p", m["page"])

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"summary": summary, "jobs": [{"paper": p, "page": g} for p, g in jobs],
                       "misses": misses, "results": results}, fh, ensure_ascii=False, indent=2)
        print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
