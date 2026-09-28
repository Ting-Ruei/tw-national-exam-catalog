#!/usr/bin/env python3
"""Draw the ground-truth figure regions, the question spans, and PP-DocLayout's
predictions on a page so a human can check the numbers by eye.

This is the second engine: the scorer is arithmetic over poppler's XML, this is the
page itself. The two must agree; a green number alone is not acceptance.

Usage:
  render_figure_overlay.py --paper P.pdf --page 16 --out /tmp/overlay16.png
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw  # noqa: E402

import run_pp_doclayout_onnx as layout  # noqa: E402
from measure_figure_attribution import (  # noqa: E402
    cluster, last_marker_number, parse_page, page_xml, question_spans, span_of,
)
from measure_figure_localization import ort_session, render_point_scale  # noqa: E402

GT = (0, 90, 255)      # blue   -- the PDF's own drawn picture objects
PRED = (220, 0, 0)     # red    -- PP-DocLayout-V3
SPAN = (0, 170, 0)     # green  -- question spans derived from the text layer
MARK = (255, 140, 0)   # orange -- figure clusters = union of touching GT objects


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="/Users/tim/models/glm-ocr/sdk-weights/pp-doclayoutv3-onnx/PP-DocLayoutV3.onnx")
    ap.add_argument("--paper", required=True)
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--threshold", type=float, default=0.3)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    images, texts = parse_page(page_xml(args.paper, args.page))
    spans = question_spans(texts, 595.22, 842.0,
                           carry_in=last_marker_number(args.paper, args.page - 1) if args.page > 1 else None)

    with tempfile.TemporaryDirectory() as tmp:
        png, (w_px, h_px) = render_point_scale(args.paper, args.page, args.dpi, tmp)
        tensor, _ = layout.preprocess(png)
        logits, boxes, _m, _o = ort_session(args.model).run(
            ["logits", "pred_boxes", "out_masks", "order_logits"], {"pixel_values": tensor}
        )
        rows = layout.postprocess(logits, boxes, (w_px, h_px), args.threshold)
        base = Image.open(png).convert("RGB")

    sx, sy = w_px / 595.22, h_px / 842.0
    d = ImageDraw.Draw(base)

    def rect(box, color, width=3):
        d.rectangle([box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy], outline=color, width=width)

    for s in spans:
        d.line([0, s["top"] * sy, w_px, s["top"] * sy], fill=SPAN, width=2)
        d.text((4, s["top"] * sy + 2), f"Q{s['number']}", fill=SPAN)

    for obj in images:
        rect(obj, GT, 2)
    for c in cluster(images):
        rect(c, MARK, 3)

    for r in rows:
        if r["label"] in layout.FIGURE_LIKE:
            b = [v / sx if i % 2 == 0 else v / sy for i, v in enumerate(r["box"])]
            rect(b, PRED, 4)
            q = (span_of(b, spans) or {}).get("number")
            d.text((b[0] * sx + 4, b[1] * sy + 4),
                   f"{r['label']} q{q}", fill=PRED)

    base.save(args.out)
    for s in spans:
        print(f"span Q{s['number']}: y {s['top']:.0f}-{s['bottom']:.0f}")
    print("wrote", args.out, base.size)
    print("legend: blue=PDF image object, orange=figure cluster (GT), red=PP-DocLayout, green=question span")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
