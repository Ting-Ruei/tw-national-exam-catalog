#!/usr/bin/env python3
"""Measure figure-localization accuracy: PP-DocLayout-V3 vs the PDF's own image placement.

Ground truth is `pdftohtml -xml -zoom 1.0`, an independent engine that reports where the
PDF actually draws each image object, in PDF points. It does not use our parser, our
vision stage, or the model under test, so it is not circular.

Two candidates are scored per page:
  ppdoc    PP-DocLayout-V3 (ONNX), the detector the GLM-OCR SDK uses for layout
  stored   whatever box the qbr candidate file already recorded for that question

Usage:
  measure_figure_localization.py --paper PAPER.pdf --pages 6,9,11 --out result.json
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


def true_image_boxes(pdf: str, page: int) -> list[list[float]]:
    """Every image object the PDF draws on this page, as [x1,y1,x2,y2] PDF points.

    `pdftohtml -xml -zoom 1.0` emits one `<image top left width height>` per drawn
    object, already in points. A figure built from several tiled objects therefore
    arrives as several boxes; the union is the region a person would point at.
    """
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["pdftohtml", "-xml", "-f", str(page), "-l", str(page), "-hidden",
             "-nodrm", "-zoom", "1.0", pdf, os.path.join(tmp, "p")],
            check=True, capture_output=True,
        )
        xml_path = os.path.join(tmp, "p.xml")
        if not os.path.exists(xml_path):
            return []
        xml = open(xml_path, encoding="utf-8", errors="replace").read()
    boxes = []
    for m in re.finditer(
        r'<image\s+top="(-?\d+)"\s+left="(-?\d+)"\s+width="(\d+)"\s+height="(\d+)"', xml
    ):
        top, left, width, height = (float(v) for v in m.groups())
        boxes.append([left, top, left + width, top + height])
    return boxes


def union(boxes):
    if not boxes:
        return None
    return [
        min(b[0] for b in boxes), min(b[1] for b in boxes),
        max(b[2] for b in boxes), max(b[3] for b in boxes),
    ]


def iou(a, b):
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    ua = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    ub = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    denom = ua + ub - inter
    return inter / denom if denom > 0 else 0.0


def render_point_scale(pdf: str, page: int, dpi: int, tmp: str):
    subprocess.run(
        ["pdftoppm", "-png", "-r", str(dpi), "-f", str(page), "-l", str(page),
         pdf, os.path.join(tmp, "page")],
        check=True, capture_output=True,
    )
    for name in sorted(os.listdir(tmp)):
        if name.startswith("page") and name.endswith(".png"):
            path = os.path.join(tmp, name)
            im = Image.open(path)
            return path, im.size
    raise RuntimeError("render produced no file")


def score_page(model_path, pdf, page, stored_box=None, dpi=150, threshold=0.3):
    with tempfile.TemporaryDirectory() as tmp:
        png, (w_px, h_px) = render_point_scale(pdf, page, dpi, tmp)
        gt_boxes = true_image_boxes(pdf, page)
        gt = union(gt_boxes)

        tensor, original = layout.preprocess(png)
        session = ort_session(model_path)
        logits, pred_boxes, _m, _o = session.run(
            ["logits", "pred_boxes", "out_masks", "order_logits"], {"pixel_values": tensor}
        )
        rows = layout.postprocess(logits, pred_boxes, original, threshold)

    page_w, page_h = original[0] / (w_px / 595.22), original[1] / (h_px / 842.0)
    # recover true page size in points from the render scale
    sx, sy = w_px / 595.22, h_px / 842.0

    def px_to_pt(box):
        return [box[0] / sx, box[1] / sy, box[2] / sx, box[3] / sy]

    figs = [r for r in rows if r["label"] in layout.FIGURE_LIKE]
    best = None
    if gt:
        for r in figs:
            cand = px_to_pt(r["box"])
            score = iou(cand, gt)
            if best is None or score > best["iou"]:
                best = {"iou": round(score, 4), "label": r["label"],
                        "score": r["score"], "box_pt": [round(v, 1) for v in cand]}

    out = {
        "page": page,
        "page_points": [round(595.22, 1), round(842.0, 1)],
        "gt_image_objects": len(gt_boxes),
        "gt_union_pt": [round(v, 1) for v in gt] if gt else None,
        "ppdoc_figure_count": len(figs),
        "ppdoc_best": best,
    }
    if stored_box:
        out["stored_box_pt"] = stored_box
        out["stored_iou"] = round(iou(stored_box, gt), 4) if gt else None
    return out


_session = None


def ort_session(model_path):
    global _session
    if _session is None:
        import onnxruntime as ort
        _session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
    return _session


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="/Users/tim/models/glm-ocr/sdk-weights/pp-doclayoutv3-onnx/PP-DocLayoutV3.onnx")
    ap.add_argument("--paper", required=True)
    ap.add_argument("--pages", required=True, help="comma-separated 1-based page numbers")
    ap.add_argument("--out")
    args = ap.parse_args()

    results = []
    for token in args.pages.split(","):
        page = int(token)
        try:
            results.append(score_page(args.model, args.paper, page))
        except Exception as exc:  # noqa: BLE001
            results.append({"page": page, "error": f"{type(exc).__name__}: {exc}"})
        print(json.dumps(results[-1], ensure_ascii=False))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"paper": args.paper, "results": results}, fh,
                      ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
