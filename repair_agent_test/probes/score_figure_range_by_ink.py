#!/usr/bin/env python3
"""Score figure range by ink, not by the PDF's drawn object extents.

Why this exists
---------------
`measure_figure_attribution.py` scores `range_coverage` against the *extents of the PDF's
drawn picture objects*. That ground truth carries the whitespace padding each object was
authored with, so a detector that returns a tight box around the figure content scores
below 1.0 for being right. Measured on paper 1072 page 13: one question's figure is four
PDF objects with 19pt / 52pt / 28pt of internal whitespace, and PP-DocLayout's four tight
boxes cover the ink but not the padding -- coverage 0.673 while every structure is inside
a box.

Ink is the independent quantity: the rendered pixels that are not white. It does not care
how the PDF was authored.

  ink_recall  = dark pixels of the question's figure region inside a prediction
                / dark pixels of the question's figure region    -- did the crop keep the figure
  ink_added   = dark pixels inside a prediction that are outside the question's figure region
                / total dark pixels inside the prediction       -- did the crop drag other content in

`ink_added` is the number that matters for a review crop: a crop that includes a
neighbouring question's ink is the failure the reviewer would actually see.

Usage:
  score_figure_range_by_ink.py --paper P.pdf --pages 6,9,13 --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import run_pp_doclayout_onnx as layout  # noqa: E402
from measure_figure_attribution import (  # noqa: E402
    cluster, last_marker_number, page_xml, parse_page, question_spans, span_of,
)
from measure_figure_localization import ort_session, render_point_scale  # noqa: E402

DARK = 200  # luminance below this is ink; the papers print black on white


def ink_mask(img: Image.Image, box_px) -> np.ndarray:
    x0, y0, x1, y1 = (int(round(v)) for v in box_px)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(img.width, x1), min(img.height, y1)
    if x1 <= x0 or y1 <= y0:
        return np.zeros((0, 0), dtype=bool)
    crop = np.asarray(img.convert("L").crop((x0, y0, x1, y1)))
    return crop < DARK


def score_page(model, pdf, page, dpi=150, threshold=0.3, min_pt=24.0):
    images, texts = parse_page(page_xml(pdf, page))
    spans = question_spans(texts, 595.22, 842.0,
                           carry_in=last_marker_number(pdf, page - 1) if page > 1 else None)

    with tempfile.TemporaryDirectory() as tmp:
        png, (w_px, h_px) = render_point_scale(pdf, page, dpi, tmp)
        tensor, _ = layout.preprocess(png)
        logits, boxes, _m, _o = ort_session(model).run(
            ["logits", "pred_boxes", "out_masks", "order_logits"], {"pixel_values": tensor}
        )
        rows = layout.postprocess(logits, boxes, (w_px, h_px), threshold)
        img = Image.open(png).convert("L")

    sx, sy = w_px / 595.22, h_px / 842.0
    figures = [c for c in cluster(images) if min(c[2] - c[0], c[3] - c[1]) >= min_pt]
    preds = [{"box": [r["box"][0] / sx, r["box"][1] / sy, r["box"][2] / sx, r["box"][3] / sy],
              "label": r["label"]}
             for r in rows if r["label"] in layout.FIGURE_LIKE]
    preds_with_title = [{"box": [r["box"][0] / sx, r["box"][1] / sy, r["box"][2] / sx, r["box"][3] / sy],
                         "label": r["label"]}
                        for r in rows if r["label"] in layout.FIGURE_LIKE_WITH_TITLE]

    # true figure region per question, and the ink of the page
    per_question = []
    by_q = {}
    for f in figures:
        s = span_of(f, spans)
        if s:
            by_q.setdefault(s["number"], []).append(f)

    page_ink = int(ink_mask(img, (0, 0, w_px, h_px)).sum())
    dark = np.asarray(img) < DARK

    def measure(own_boxes, region, region_mask):
        union = np.zeros((h_px, w_px), dtype=bool)
        for p in own_boxes:
            union[int(p[1] * sy):int(p[3] * sy), int(p[0] * sx):int(p[2] * sx)] = True
        kept = int((dark & region_mask & union).sum())
        pred_ink = int((dark & union).sum())
        return kept, pred_ink - kept

    for number, boxes_ in sorted(by_q.items()):
        region = [min(b[0] for b in boxes_), min(b[1] for b in boxes_),
                  max(b[2] for b in boxes_), max(b[3] for b in boxes_)]

        region_mask = np.zeros((h_px, w_px), dtype=bool)
        rx0, ry0 = int(region[0] * sx), int(region[1] * sy)
        rx1, ry1 = int(region[2] * sx), int(region[3] * sy)
        region_mask[ry0:ry1, rx0:rx1] = True
        region_ink = int((dark & region_mask).sum())

        own = [p["box"] for p in preds
               if (span_of(p["box"], spans) or {}).get("number") == number]
        own_t = [p["box"] for p in preds_with_title
                 if (span_of(p["box"], spans) or {}).get("number") == number]
        kept, added = measure(own, region, region_mask)
        kept_t, added_t = measure(own_t, region, region_mask)

        per_question.append({
            "question": number,
            "true_figure_count": len(boxes_),
            "predicted_boxes": len(own),
            "region_pt": [round(v, 1) for v in region],
            "region_whitespace_fraction": round(1 - region_ink / region_mask.sum(), 3) if region_mask.sum() else None,
            "region_ink_px": region_ink,
            "ink_recall": round(kept / region_ink, 3) if region_ink else None,
            "ink_added_px": added,
            "ink_added_fraction": round(added / (kept + added), 4) if (kept + added) else None,
            "ink_recall_with_titles": round(kept_t / region_ink, 3) if region_ink else None,
            "ink_added_px_with_titles": added_t,
        })

    return {
        "page": page,
        "page_ink_px": page_ink,
        "true_figures": len(figures),
        "predicted_figures": len(preds),
        "per_question": per_question,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="/Users/tim/models/glm-ocr/sdk-weights/pp-doclayoutv3-onnx/PP-DocLayoutV3.onnx")
    ap.add_argument("--paper", required=True)
    ap.add_argument("--pages", required=True)
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--threshold", type=float, default=0.3)
    ap.add_argument("--json")
    args = ap.parse_args()

    results = []
    for token in args.pages.split(","):
        try:
            results.append(score_page(args.model, args.paper, int(token), args.dpi, args.threshold))
        except Exception as exc:  # noqa: BLE001
            import traceback
            results.append({"page": int(token), "error": f"{type(exc).__name__}: {exc}",
                            "tb": traceback.format_exc()[-300:]})
        r = results[-1]
        print(json.dumps({k: v for k, v in r.items() if k != "per_question"}, ensure_ascii=False))
        for q in r.get("per_question", []):
            print("   ", json.dumps(q, ensure_ascii=False))

    qs = [q for r in results for q in r.get("per_question", []) if q["region_ink_px"]]
    if qs:
        print(json.dumps({"SUMMARY": {
            "questions": len(qs),
            "mean_ink_recall": round(sum(q["ink_recall"] for q in qs) / len(qs), 3),
            "mean_ink_recall_with_titles": round(sum(q["ink_recall_with_titles"] for q in qs) / len(qs), 3),
            "total_ink_added_px": sum(q["ink_added_px"] for q in qs),
            "total_ink_added_px_with_titles": sum(q["ink_added_px_with_titles"] for q in qs),
            "mean_region_whitespace": round(sum(q["region_whitespace_fraction"] for q in qs) / len(qs), 3),
        }}, ensure_ascii=False))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"paper": args.paper, "results": results}, fh, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
