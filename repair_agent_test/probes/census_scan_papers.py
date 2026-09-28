#!/usr/bin/env python3
"""Census the corpus for papers that are a raster of the page rather than a text layer.

Why not `pdffonts`/`pdftotext`
------------------------------
Both miss papers that carry a thin, useless text layer on top of a scanned raster, and
`pdftotext -f 2` returns empty for a one-page PDF, which is how an earlier pass produced a
false "no scans in the corpus" reading.

The quantity that separates them is measured, not inferred: how much of page 1's area the
largest drawn image object covers.

    digital paper   0.000   (measured: paper 1152, text drawn as glyphs)
    scanned paper   1.000   (measured: 1753x2480 px at 150 ppi on an A3 page -- the page
                             raster; a second CCITT stencil object carries the black)

Usage:
  census_scan_papers.py --sample 8000 --out scan-census.json
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor


def page_geometry(pdf: str):
    try:
        info = subprocess.run(["pdfinfo", pdf], capture_output=True, timeout=25).stdout.decode("utf-8", "replace")
    except Exception:
        return None
    pages = 0
    m = re.search(r"Pages:\s+(\d+)", info)
    if m:
        pages = int(m.group(1))
    pw, ph = 595.0, 842.0
    m = re.search(r"Page size:\s+([\d.]+)\s+x\s+([\d.]+)", info)
    if m:
        pw, ph = float(m.group(1)), float(m.group(2))
    creator = ""
    m = re.search(r"Creator:\s*(.*)", info)
    if m:
        creator = m.group(1).strip()
    return pages, pw, ph, creator


def raster_cover(pdf: str):
    """Largest drawn image object's area as a fraction of page 1, plus page count.

    The cover alone is not enough: a *digital* page can legitimately be mostly one big
    figure (measured: 中醫師 1002 中醫基礎醫學(二), cover 0.54, and it has embedded fonts and a
    real text layer). Requiring zero embedded fonts separates the two, and both are
    reported so the caller can see which reason fired.
    """
    geo = page_geometry(pdf)
    if geo is None:
        return None
    pages, pw, ph, creator = geo
    try:
        fonts = subprocess.run(["pdffonts", pdf], capture_output=True, timeout=25).stdout.decode("utf-8", "replace")
    except Exception:
        return None
    n_fonts = len([l for l in fonts.splitlines()[2:] if l.strip()])
    try:
        out = subprocess.run(["pdfimages", "-list", "-f", "1", "-l", "1", pdf],
                             capture_output=True, timeout=25).stdout.decode("utf-8", "replace")
    except Exception:
        return None
    best = 0.0
    for line in out.splitlines()[2:]:
        p = line.split()
        if len(p) < 14:
            continue
        try:
            w, h = int(p[3]), int(p[4])
            xppi, yppi = float(p[12]), float(p[13])
        except (ValueError, IndexError):
            continue
        if xppi <= 0 or yppi <= 0 or pw <= 0 or ph <= 0:
            continue
        best = max(best, (w / xppi * 72.0) * (h / yppi * 72.0) / (pw * ph))
    return {"cover": round(best, 4), "pages": pages, "creator": creator, "fonts": n_fonts}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--roots", nargs="+", default=[
        "國考題資料夾/10_official_pdf",
        "國考題資料夾_其他類型/10_official_pdf",
        "國考題資料夾_非醫學剩餘全集/10_official_pdf",
    ])
    ap.add_argument("--sample", type=int, default=8000)
    ap.add_argument("--seed", type=int, default=23)
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--out")
    args = ap.parse_args()

    files = []
    for root in args.roots:
        for dp, _dn, fn in os.walk(root):
            for f in fn:
                if f.endswith(".pdf") and not f.endswith("_ANS.pdf"):
                    files.append(os.path.join(dp, f))
    print(f"corpus pdfs: {len(files)}")

    random.seed(args.seed)
    sample = random.sample(files, min(args.sample, len(files)))
    raster_but_digital = []
    with ThreadPoolExecutor(args.workers) as ex:
        results = list(ex.map(raster_cover, sample))

    scans, errors = [], 0
    for path, r in zip(sample, results):
        if r is None:
            errors += 1
        elif r["cover"] >= args.threshold and r["fonts"] == 0:
            scans.append({"path": path, **r})
        elif r["cover"] >= args.threshold:
            r["path"] = path
            raster_but_digital.append(r)

    print(f"sampled={len(sample)} errors={errors} scans={len(scans)} "
          f"({100 * len(scans) / max(1, len(sample) - errors):.3f}%)")
    print(f"raster-page-but-fonts-present (a big figure on a digital page): {len(raster_but_digital)}")
    for s in sorted(raster_but_digital, key=lambda x: -x["cover"])[:5]:
        print(f"   cover={s['cover']:.2f} fonts={s['fonts']}  {s['path']}")
    print("by page count:", {p: sum(1 for s in scans if s["pages"] == p)
                             for p in sorted({s["pages"] for s in scans})})
    for s in sorted(scans, key=lambda x: -x["cover"])[:25]:
        print(f"  cover={s['cover']:.2f} pages={s['pages']:2d} creator={s['creator'][:26]!r}  {s['path']}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"sampled": len(sample), "errors": errors, "threshold": args.threshold,
                       "scans": scans}, fh, ensure_ascii=False, indent=2)
        print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
