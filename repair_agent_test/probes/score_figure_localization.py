#!/usr/bin/env python3
"""Score figure localisation by area coverage, not by one-box-per-figure IoU.

Why area metrics
----------------
The ground truth (`pdftohtml -xml -zoom 1.0`) reports the image objects the PDF actually
draws, in PDF points. That is an independent engine and is not circular, but its *shape*
depends on how the PDF was authored, and two shapes were measured on paper 1152:

  * page 9  one figure drawn as five 462x34pt strips (touching) -> one true region,
            and the detector legitimately returned three adjacent regions.
  * page 16 two photos printed side by side, drawn as seven *full width* strips, so the
            GT cannot separate the two photos even though the detector correctly did
            (IoU 0.92 / 0.87 against the two visual regions).

Per-figure IoU scores the detector for the authoring style. Area coverage and purity do
not:

  coverage = (GT area covered by any prediction) / (total GT area)     -- did it find it
  purity   = (prediction area inside GT)         / (total prediction area) -- is it tight

Both tolerate a split. Purity punishes a box that swallows the page. Per-cluster best IoU
is still reported, as a secondary number.

Usage:
  score_figure_localization.py --paper P.pdf --pages 6,9,11,16 --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402

import run_pp_doclayout_onnx as layout  # noqa: E402
from measure_figure_localization import (  # noqa: E402
    iou, ort_session, render_point_scale, true_image_boxes,
)


def cluster(boxes, gap=3.0):
    """Merge drawn objects that touch or nearly touch (one figure, one box)."""
    remaining = [list(b) for b in boxes]
    merged = True
    while merged:
        merged = False
        out = []
        while remaining:
            a = remaining.pop()
            for i, b in enumerate(remaining):
                if not (a[2] + gap < b[0] or b[2] + gap < a[0]
                        or a[3] + gap < b[1] or b[3] + gap < a[1]):
                    remaining[i] = [min(a[0], b[0]), min(a[1], b[1]),
                                    max(a[2], b[2]), max(a[3], b[3])]
                    merged = True
                    break
            else:
                out.append(a)
        remaining = out
    return remaining


def area(box):
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def intersection(a, b):
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * \
           max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def covered_by(region, preds):
    """Area of `region` covered by the union of `preds`, computed on a raster grid.

    A grid is used rather than clipped rectangles because the union of several
    overlapping boxes has no single rectangle, and double-counting overlaps is
    exactly the error that would flatter a detector.
    """
    rw, rh = region[2] - region[0], region[3] - region[1]
    if rw <= 0 or rh <= 0:
        return 0.0
    step_x = max(0.5, rw / 400.0)
    step_y = max(0.5, rh / 400.0)
    xs = np.arange(region[0], region[2], step_x) + step_x / 2
    ys = np.arange(region[1], region[3], step_y) + step_y / 2
    gx, gy = np.meshgrid(xs, ys)
    hit = np.zeros(gx.shape, dtype=bool)
    for p in preds:
        hit |= (gx >= p[0]) & (gx <= p[2]) & (gy >= p[1]) & (gy <= p[3])
    return float(hit.mean()) * area(region)


def score_page(model, pdf, page, min_pt, dpi, threshold):
    with tempfile.TemporaryDirectory() as tmp:
        png, (w_px, h_px) = render_point_scale(pdf, page, dpi, tmp)
        raw_gt = true_image_boxes(pdf, page)
        tensor, _ = layout.preprocess(png)
        session = ort_session(model)
        logits, pred_boxes, _m, _o = session.run(
            ["logits", "pred_boxes", "out_masks", "order_logits"], {"pixel_values": tensor}
        )
        rows = layout.postprocess(logits, pred_boxes, (w_px, h_px), threshold)

    sx, sy = w_px / 595.22, h_px / 842.0
    clusters = cluster(raw_gt)
    small = [c for c in clusters if min(c[2] - c[0], c[3] - c[1]) < min_pt]
    gt = [c for c in clusters if min(c[2] - c[0], c[3] - c[1]) >= min_pt]

    preds = [[r["box"][0] / sx, r["box"][1] / sy, r["box"][2] / sx, r["box"][3] / sy]
             for r in rows if r["label"] in layout.FIGURE_LIKE]
    labels = [r["label"] for r in rows if r["label"] in layout.FIGURE_LIKE]

    gt_area = sum(area(g) for g in gt)
    pred_area = sum(area(p) for p in preds)
    gt_cov = sum(covered_by(g, preds) for g in gt)
    pred_in = sum(covered_by(p, gt) for p in preds) if gt else 0.0

    per_gt = []
    for g in gt:
        best = max((iou(p, g) for p in preds), default=0.0)
        per_gt.append({"gt_box": [round(v, 1) for v in g], "best_iou": round(best, 3)})

    return {
        "page": page,
        "gt_figures": len(gt),
        "gt_dropped_small": len(small),
        "predicted_figures": len(preds),
        "predicted_labels": labels,
        "coverage": round(gt_cov / gt_area, 3) if gt_area else None,
        "purity": round(pred_in / pred_area, 3) if pred_area else None,
        "best_iou_per_gt": per_gt,
        "median_best_iou": round(sorted(p["best_iou"] for p in per_gt)[len(per_gt) // 2], 3) if per_gt else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="/Users/tim/models/glm-ocr/sdk-weights/pp-doclayoutv3-onnx/PP-DocLayoutV3.onnx")
    ap.add_argument("--paper", required=True)
    ap.add_argument("--pages", required=True)
    ap.add_argument("--min-pt", type=float, default=24.0)
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--threshold", type=float, default=0.3)
    ap.add_argument("--json")
    args = ap.parse_args()

    results = []
    for token in args.pages.split(","):
        try:
            results.append(score_page(args.model, args.paper, int(token),
                                      args.min_pt, args.dpi, args.threshold))
        except Exception as exc:  # noqa: BLE001
            results.append({"page": int(token), "error": f"{type(exc).__name__}: {exc}"})
        row = results[-1]
        print(json.dumps({k: v for k, v in row.items() if k != "best_iou_per_gt"},
                         ensure_ascii=False))

    scored = [r for r in results if "error" not in r and r["gt_figures"]]
    if scored:
        print(json.dumps({"SUMMARY": {
            "pages_scored": len(scored),
            "gt_figures_total": sum(r["gt_figures"] for r in scored),
            "pages_with_zero_prediction": [r["page"] for r in scored if r["predicted_figures"] == 0],
            "mean_coverage": round(sum(r["coverage"] for r in scored) / len(scored), 3),
            "mean_purity": round(sum(r["purity"] for r in scored if r["purity"] is not None)
                                 / len([r for r in scored if r["purity"] is not None]), 3),
        }}, ensure_ascii=False))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"paper": args.paper, "min_pt": args.min_pt,
                       "results": results}, fh, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
