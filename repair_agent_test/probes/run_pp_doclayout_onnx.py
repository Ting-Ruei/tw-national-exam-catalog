#!/usr/bin/env python3
"""PP-DocLayout-V3 layout detection, torch-free (ONNX Runtime).

Emits layout boxes with class labels for one rendered page. This is the same
detector the GLM-OCR SDK uses to locate figures/tables on a page; the GLM-OCR
language model itself emits no boxes at all.

Usage:
  run_pp_doclayout_onnx.py --image page.png [--threshold 0.3] [--json out.json]

Requires: onnxruntime, numpy, opencv-python, Pillow  (see ~/models/venvs/omlxenv)
"""
from __future__ import annotations

import argparse
import json
import sys

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image

# inference.yml label_list, in id order.
LABELS = [
    "abstract", "algorithm", "aside_text", "chart", "content", "display_formula",
    "doc_title", "figure_title", "footer", "footer_image", "footnote",
    "formula_number", "header", "header_image", "image", "inline_formula",
    "number", "paragraph_title", "reference", "reference_content", "seal",
    "table", "text", "vertical_text", "vision_footnote",
]

# The classes that mean "there is a picture or a table here".
FIGURE_LIKE = {"image", "chart", "table", "header_image", "footer_image", "seal"}

# `figure_title` is the printed label under or beside a figure (measured on paper 1152 page 9:
# the three strips 圖（一）／圖（二）／圖（三） at y231-257, confidence 0.855-0.861). It is not
# a figure itself, but a review crop that must show "which panel is which" needs it. Scoring it
# separately keeps both numbers honest: a detector can find the figures and still miss the labels.
FIGURE_TITLE = {"figure_title"}
FIGURE_LIKE_WITH_TITLE = FIGURE_LIKE | FIGURE_TITLE


def preprocess(path: str, size: int = 800) -> tuple[np.ndarray, tuple[int, int]]:
    """800x800 RGB float32, /255, mean 0 std 1. Returns tensor and original (w, h)."""
    image = Image.open(path).convert("RGB")
    original = image.size  # (w, h)
    resized = image.resize((size, size), resample=Image.BILINEAR)
    array = np.asarray(resized, dtype=np.float32) / 255.0
    array = (array - 0.0) / 1.0
    tensor = np.transpose(array, (2, 0, 1))[None, ...]  # (1,3,H,W)
    return np.ascontiguousarray(tensor), original


def postprocess(logits, pred_boxes, target_size, threshold, size=800):
    """DETR decoding: sigmoid class scores, raw cxcywh -> xyxy.

    Mirrors transformers' PPDocLayoutV3ImageProcessor.post_process_object_detection
    for the box/label part. Masks and reading order are not needed to answer
    "where is the figure", so they are not computed.

    NOTE: the official postprocessor consumes `pred_boxes` **directly** (already
    normalised cxcywh); it does not softmax them. Softmaxing produced boxes that
    matched nothing on a page whose figure is known.
    """
    scores = 1.0 / (1.0 + np.exp(-logits[0]))                 # (300, 25)
    boxes = pred_boxes[0]                                      # (300, 4)
    cx, cy, w, h = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]

    # every (query, class) pair above the threshold is a candidate
    query_index, label_index = np.where(scores > threshold)
    if len(query_index) == 0:
        return []
    conf = scores[query_index, label_index]

    x1 = cx[query_index] - w[query_index] / 2
    y1 = cy[query_index] - h[query_index] / 2
    x2 = cx[query_index] + w[query_index] / 2
    y2 = cy[query_index] + h[query_index] / 2
    scale_w, scale_h = target_size
    boxes_out = np.stack(
        [x1 * scale_w, y1 * scale_h, x2 * scale_w, y2 * scale_h], axis=-1
    )

    rows = []
    for i in range(len(query_index)):
        label = LABELS[label_index[i]] if label_index[i] < len(LABELS) else str(label_index[i])
        rows.append({
            "label": label,
            "score": round(float(conf[i]), 4),
            "box": [round(float(v), 2) for v in boxes_out[i]],
        })
    return rows


def iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    return inter / (area_a + area_b - inter)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="/Users/tim/models/glm-ocr/sdk-weights/pp-doclayoutv3-onnx/PP-DocLayoutV3.onnx")
    ap.add_argument("--image", required=True)
    ap.add_argument("--threshold", type=float, default=0.3)
    ap.add_argument("--json")
    ap.add_argument("--expect-label", help="print IoU against --expect-box for this label")
    ap.add_argument("--expect-box", help="x1,y1,x2,y2 in PDF points")
    args = ap.parse_args()

    tensor, original = preprocess(args.image)
    session = ort.InferenceSession(args.model, providers=["CPUExecutionProvider"])
    logits, pred_boxes, _masks, _order = session.run(
        ["logits", "pred_boxes", "out_masks", "order_logits"], {"pixel_values": tensor}
    )
    rows = postprocess(logits, pred_boxes, original, args.threshold)
    rows.sort(key=lambda r: (-r["score"],))

    payload = {
        "image": args.image,
        "image_size": list(original),
        "threshold": args.threshold,
        "count": len(rows),
        "figure_like": [r for r in rows if r["label"] in FIGURE_LIKE],
        "all": rows,
    }

    if args.expect_box:
        want = [float(v) for v in args.expect_box.split(",")]
        cands = [r for r in rows if not args.expect_label or r["label"] == args.expect_label]
        payload["expect"] = {
            "box": want,
            "label": args.expect_label,
            "best_iou": max((iou(r["box"], want) for r in cands), default=0.0),
            "n_candidates": len(cands),
        }

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
