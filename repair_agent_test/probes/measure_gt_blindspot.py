#!/usr/bin/env python3
"""How much does the ground truth miss?

The figure ground truth comes from `pdftohtml -xml` (via `parse_page`), which reports
**raster image objects**. A figure drawn as vector art -- a chemical structure, a circuit,
a curve the author drew in Word -- is not a raster object and is therefore invisible to it.

That blind spot was found visually, not statistically: on 藥師(一) 1051 page 5 the ground
truth reported one figure (Aspirin) while the detector reported three, and the two extra
were Voriconazole and Naphthalene drawn as vectors. Both were real figures. Scoring the
detector against a ground truth that cannot see them charges it with two false positives
for being right.

This probe measures the size of the blind spot: take pages where `pdfimages` reports no
image large enough to be a figure, and ask PP-DocLayout whether it sees one. Any page
where it does is a page the ground truth cannot describe.

Usage:
  measure_gt_blindspot.py --papers 30 --pages-per-paper 3 --out ~/models/glm-ocr/runs/gt-blindspot.json
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from measure_figure_attribution import parse_page, page_xml  # noqa: E402
from measure_figure_localization import ort_session, render_point_scale  # noqa: E402
import run_pp_doclayout_onnx as layout  # noqa: E402

MODEL = "/Users/tim/models/glm-ocr/sdk-weights/pp-doclayoutv3-onnx/PP-DocLayoutV3.onnx"
DEFAULT_ROOT = "國考題資料夾/10_official_pdf/by_official_catalog"
# The same floor the attribution measurement uses to call a raster object a figure.
MIN_PT = 24.0


def raster_figures(pdf: str):
    """Per page, the count of raster images at least 80x80 px (a figure-sized raster)."""
    try:
        out = subprocess.run(["pdfimages", "-list", pdf], capture_output=True,
                             timeout=30).stdout.decode("utf-8", "replace")
        info = subprocess.run(["pdfinfo", pdf], capture_output=True,
                              timeout=30).stdout.decode("utf-8", "replace")
    except Exception:
        return None
    m = re.search(r"Pages:\s+(\d+)", info)
    pages = int(m.group(1)) if m else 0
    counts: dict[int, int] = {}
    for line in out.splitlines()[2:]:
        p = line.split()
        if len(p) < 5:
            continue
        try:
            pg, w, h = int(p[0]), int(p[3]), int(p[4])
        except ValueError:
            continue
        if w >= 80 and h >= 80:
            counts[pg] = counts.get(pg, 0) + 1
    return pages, counts


def detector_figures(pdf: str, page: int, threshold: float = 0.3):
    """Figure-like boxes the layout detector reports on a page, in PDF points."""
    with tempfile.TemporaryDirectory() as tmp:
        png, (w_px, h_px) = render_point_scale(pdf, page, 150, tmp)
        tensor, _ = layout.preprocess(png)
        session = ort_session(MODEL)
        logits, pred_boxes, _m, _o = session.run(
            ["logits", "pred_boxes", "out_masks", "order_logits"], {"pixel_values": tensor}
        )
        rows = layout.postprocess(logits, pred_boxes, (w_px, h_px), threshold)
    sx, sy = w_px / 595.22, h_px / 842.0
    return [{"box": [r["box"][0] / sx, r["box"][1] / sy, r["box"][2] / sx, r["box"][3] / sy],
             "label": r["label"], "score": r["score"]}
            for r in rows
            if r["label"] in layout.FIGURE_LIKE
            and min(r["box"][2] - r["box"][0], r["box"][3] - r["box"][1]) >= MIN_PT]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--papers", type=int, default=30)
    ap.add_argument("--pages-per-paper", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
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

    # Keep papers whose *first* page has no raster figure, then look at pages the raster
    # ground truth would call empty. Deliberately not filtering on "the paper has no
    # figures anywhere": that would find the same blind spot with far more renders.
    jobs = []
    scanned = 0
    for pdf in files:
        if len(jobs) >= args.papers * args.pages_per_paper:
            break
        r = raster_figures(pdf)
        scanned += 1
        if not r:
            continue
        pages, counts = r
        taken = 0
        for pg in range(1, pages + 1):
            if counts.get(pg):
                continue
            # A page with no text layer at all is a different probe's problem, not this one.
            if not parse_page(page_xml(pdf, pg))[1]:
                continue
            jobs.append((pdf, pg))
            taken += 1
            if taken >= args.pages_per_paper or len(jobs) >= args.papers * args.pages_per_paper:
                break

    print(f"pages with no raster figure, from {scanned} papers: {len(jobs)}")

    def run(job):
        pdf, pg = job
        try:
            return {"paper": pdf, "page": pg, "detector": detector_figures(pdf, pg)}
        except Exception as exc:  # noqa: BLE001
            return {"paper": pdf, "page": pg, "error": f"{type(exc).__name__}: {exc}"}

    with ThreadPoolExecutor(args.workers) as ex:
        results = list(ex.map(run, jobs))

    ok = [r for r in results if "error" not in r]
    blind = [r for r in ok if r["detector"]]
    summary = {
        "raster_empty_pages_checked": len(ok),
        "pages_detector_calls_a_figure": len(blind),
        "blindspot_rate": round(len(blind) / len(ok), 4) if ok else None,
        "errors": sum(1 for r in results if "error" in r),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for r in blind[:15]:
        boxes = ", ".join(f"{b['label']}@{b['score']:.2f}" for b in r["detector"])
        print(f"   {os.path.basename(r['paper'])[:52]} p{r['page']}: {boxes}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"summary": summary,
                       "jobs": [{"paper": p, "page": g} for p, g in jobs],
                       "results": results}, fh, ensure_ascii=False, indent=2)
        print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
