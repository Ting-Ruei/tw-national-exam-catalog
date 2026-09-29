#!/usr/bin/env python3
"""Attribute figures on a *scanned* page, where there is no text layer.

Two independent ground truths, because one is not enough
--------------------------------------------------------
A scanned page has no text objects, so `pdftohtml` (the ground truth used for digital
papers) has nothing to say. Two other signals are used, and they are independent of each
other and of the model under test:

  1. Vision OCR (Apple's transcriber) reads the question markers and their boxes.
     From the markers, question spans are built, and a figure is attributed to the
     question whose span contains it. *Positional* attribution.
  2. Essay papers say which figure they use: the stem writes 如圖一 and the caption under
     the drawing writes 圖一. The caption therefore belongs to the question that mentions
     it. *Referential* attribution -- derived from words that the paper prints, not from
     where the ink sits.

The two must agree. When they do not, that is a defect in the measurement or in the paper,
not a number to average away.

Prediction: PP-DocLayout-V3 (ONNX), on the rendered scan.

Usage:
  measure_scanned_attribution.py --paper P.pdf --out out.json [--pages 1]
  measure_scanned_attribution.py --image page.png        # a pre-rendered scan
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import run_pp_doclayout_onnx as layout  # noqa: E402
from measure_figure_localization import ort_session, render_point_scale  # noqa: E402

VISION = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "macOS_vision_ocr")

CJK = "一二三四五六七八九十"
# 一、二、三 ... or 1. 2. 3. at the left margin. Sub-parts use （一）（二）/ 一 （no 、）.
CN_MARKER = re.compile(r"^\s*([" + CJK + r"]{1,3})\s*、\s*\S")
AR_MARKER = re.compile(r"^\s*(\d{1,3})\s*[.．、]?\s+\S")
# A question number printed on its own line -- measured on the scanned 護理師 1081 paper,
# page 3, where Vision reports `20`, `21`, `22` as standalone lines at x~95 while the stem
# text is a separate observation at x~150. Requiring trailing text after the number (as an
# earlier version of AR_MARKER did) reads those pages as having no questions at all.
BARE_NUMBER = re.compile(r"^\s*(\d{1,3})\s*$")
# The left margin a question number sits at. Measured: markers ~92-99 px, option text
# ~145-150 px, page meta ~995-1005 px (all at 150 dpi on A4).
MARKER_MAX_X = 135
OPTION = re.compile(r"^\s*[（(]?[A-Da-d][)）.．]")
# "如圖一" / "圖一" / "圖 1"
REF = re.compile(r"圖\s*([0-9０-９" + CJK + r"]{1,3})")
CAPTION = re.compile(r"^\s*圖\s*([0-9０-９" + CJK + r"]{1,3})\s*$")


def cjk_to_int(token: str):
    token = token.strip()
    if token.isdigit():
        return int(token)
    digits = {c: i + 1 for i, c in enumerate(CJK)}
    if token == "十":
        return 10
    if token.startswith("十"):
        return 10 + digits.get(token[1], 0)
    if len(token) == 2 and token[0] == "一" and token[1] == "十":
        return 10
    if len(token) == 3 and token[1] == "十":
        return digits.get(token[0], 0) * 10 + digits.get(token[2], 0)
    return digits.get(token, None)


def vision_ocr(image: str):
    if not os.path.exists(VISION):
        raise SystemExit(f"{VISION} not built -- run probes/build_macos_vision_ocr.sh")
    out = subprocess.run([VISION, image], capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        raise RuntimeError(f"vision ocr failed: {out.stderr[:200]}")
    lines = []
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        x0, y0, x1, y1 = (float(v) for v in parts[:4])
        lines.append({"box": [x0, y0, x1, y1], "text": "\t".join(parts[4:])})
    return lines


def question_spans(lines, page_h):
    """Markers -> spans. The marker decides the numbering; `1,2,3` and `一,二,三` both work."""
    markers = []
    for line in lines:
        raw = line["text"].strip()
        if not raw or OPTION.match(raw):
            continue
        left = line["box"][0]
        if left > MARKER_MAX_X:
            continue
        m = CN_MARKER.match(raw)
        if m:
            number = cjk_to_int(m.group(1))
        elif BARE_NUMBER.match(raw):
            number = int(raw)
        else:
            m = AR_MARKER.match(raw)
            number = int(m.group(1)) if m else None
        if number is None or not (1 <= number <= 200):
            continue
        markers.append({"number": number, "top": line["box"][1], "raw": raw[:40]})
    markers.sort(key=lambda m: m["top"])
    spans = []
    for i, m in enumerate(markers):
        bottom = markers[i + 1]["top"] if i + 1 < len(markers) else page_h
        spans.append({**m, "bottom": bottom})
    return spans, markers


def span_of(box, spans):
    best = None
    for s in spans:
        ov = max(0.0, min(box[3], s["bottom"]) - max(box[1], s["top"]))
        if ov <= 0:
            continue
        if best is None or ov > best[0]:
            best = (ov, s)
    return best[1] if best else None


def analyse(image: str, model: str, threshold: float, dpi: int):
    img = Image.open(image)
    w_px, h_px = img.size
    tensor, _ = layout.preprocess(image)
    session = ort_session(model)
    logits, boxes, _m, _o = session.run(
        ["logits", "pred_boxes", "out_masks", "order_logits"], {"pixel_values": tensor}
    )
    rows = layout.postprocess(logits, boxes, (w_px, h_px), threshold)
    figures = [{"box": [r["box"][0], r["box"][1], r["box"][2], r["box"][3]],
                "label": r["label"], "score": r["score"]}
               for r in rows if r["label"] in layout.FIGURE_LIKE_WITH_TITLE]

    lines = vision_ocr(image)
    spans, markers = question_spans(lines, h_px)

    # --- captions (圖一 ...) and their owning question, by reference ---
    captions = []
    for line in lines:
        m = CAPTION.match(line["text"].strip())
        if m:
            captions.append({"figure": cjk_to_int(m.group(1)), "box": line["box"],
                             "text": line["text"].strip()})
    # question stems that mention 如圖N / 圖N
    mentions = []
    for line in lines:
        if CAPTION.match(line["text"].strip()):
            continue
        for m in REF.finditer(line["text"]):
            n = cjk_to_int(m.group(1))
            if n is None:
                continue
            owner = span_of(line["box"], spans)
            if owner:
                mentions.append({"figure": n, "question": owner["number"],
                                 "box": line["box"], "text": line["text"].strip()[:50]})
                break

    referential = {}
    for c in captions:
        owners = [m["question"] for m in mentions if m["figure"] == c["figure"]]
        if owners:
            referential[c["figure"]] = owners[0]

    # --- attribute each prediction ---
    results = []
    for f in figures:
        owner = span_of(f["box"], spans)
        results.append({
            "box_px": [round(v, 1) for v in f["box"]],
            "label": f["label"],
            "score": round(f["score"], 4),
            "positional_question": owner["number"] if owner else None,
        })

    # --- cross-check: the two independent ground truths must agree ---------------
    # For each caption (圖N printed under a drawing), the prediction nearest that caption
    # must be attributed to the question whose stem says 如圖N. Position and reference are
    # independent: one uses ink geometry, the other uses the words the paper prints.
    cross = []
    for c in captions:
        if not results:
            break
        def vdist(p, cbox=c["box"]):
            if p["box_px"][3] < cbox[1]:
                return cbox[1] - p["box_px"][3]
            if cbox[3] < p["box_px"][1]:
                return p["box_px"][1] - cbox[3]
            return 0.0
        nearest = min(results, key=vdist)
        expected = referential.get(c["figure"])
        cross.append({
            "figure": c["figure"],
            "caption": c["text"],
            "caption_question": expected,
            "nearest_prediction_question": nearest["positional_question"],
            "agree": expected is not None and expected == nearest["positional_question"],
        })

    return {
        "image": image,
        "size_px": [w_px, h_px],
        "markers": [{"number": m["number"], "top": round(m["top"], 1), "raw": m["raw"]} for m in markers],
        "spans": [{"number": s["number"], "top": round(s["top"], 1), "bottom": round(s["bottom"], 1)}
                  for s in spans],
        "captions": captions,
        "mentions": mentions,
        "referential_attribution": referential,
        "predicted_figures": len(figures),
        "predictions": results,
        "cross_check": cross,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="/Users/tim/models/glm-ocr/sdk-weights/pp-doclayoutv3-onnx/PP-DocLayoutV3.onnx")
    ap.add_argument("--paper")
    ap.add_argument("--image")
    ap.add_argument("--pages", default="1")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--threshold", type=float, default=0.3)
    ap.add_argument("--json")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    images = []
    if args.image:
        images = [args.image]
    else:
        for token in args.pages.split(","):
            tmp = tempfile.mkdtemp()   # one directory per page: `render_point_scale`
                                       # returns the first `page*.png` it finds, so a
                                       # shared directory would hand back page 1 for
                                       # every page (measured, then fixed).
            png, _ = render_point_scale(args.paper, int(token), args.dpi, tmp)
            images.append(png)

    out = {"paper": args.paper, "images": []}
    for image in images:
        r = analyse(image, args.model, args.threshold, args.dpi)
        out["images"].append(r)
        print(json.dumps({"image": os.path.basename(image), "markers": len(r["markers"]),
                          "predicted_figures": r["predicted_figures"],
                          "captions": len(r["captions"]),
                          "attributed": sum(1 for p in r["predictions"] if p["positional_question"]),
                          "cross_checked": len(r["cross_check"]),
                          "cross_agree": sum(1 for c in r["cross_check"] if c["agree"])},
                         ensure_ascii=False))
        if args.verbose:
            print("  spans:", [(s["number"], int(s["top"]), int(s["bottom"])) for s in r["spans"]])
            print("  captions:", [(c["figure"], c["text"]) for c in r["captions"]])
            print("  mentions:", [(m["figure"], m["question"]) for m in r["mentions"]])
            print("  referential:", r["referential_attribution"])
            for c in r["cross_check"]:
                print("   cross", json.dumps(c, ensure_ascii=False))
            for p in r["predictions"]:
                print("   fig", json.dumps(p, ensure_ascii=False))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
        print("wrote", args.json)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
