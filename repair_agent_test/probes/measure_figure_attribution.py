#!/usr/bin/env python3
"""Measure two things about figures on a page: which question a figure belongs to,
and whether a crop's range is right.

Ground truth (independent engine, poppler's `pdftohtml -xml -zoom 1.0`, PDF points):

  * `<image top left width height>`  -- where the PDF actually draws each picture object
  * `<text  top left width height>`  -- where the PDF actually draws each text run

From those two, without using our parser or the model under test:

  * question spans   -- a question marker (`30.` at the stem's left margin) opens a span
                        that runs to the next marker; markers are read from the text layer
  * figure clusters  -- drawn objects that touch are one figure
  * true attribution -- the question span a figure cluster sits in
  * true figure range -- the union of a question's figure clusters

Prediction: PP-DocLayout-V3 (ONNX), the layout detector the GLM-OCR SDK ships.

Reported per page:

  attribution accuracy   predicted figure -> question, vs true attribution
  range coverage         fraction of the question's true figure area a crop would include
  range purity           fraction of the crop that is the question's own figure
  bleed                  predicted figure area landing in a *neighbouring* question's span

Usage:
  measure_figure_attribution.py --paper P.pdf --pages 6,9,11,16 --json out.json
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

import run_pp_doclayout_onnx as layout  # noqa: E402
from measure_figure_localization import (  # noqa: E402
    iou, ort_session, render_point_scale,
)

# A question marker. Papers differ, and every form below was measured:
#   `30.病患因為...`  number glued to the stem        (1152 物理治療師)
#   `22.`             number and period alone          (1072 藥師)
#   `1 世界衛生組織`  number, space, stem               (護理師 1081 scan)
#   `二、`            CJK numbering for essay papers   (1051 諮商心理師)
#   `1`               bare number on its own line      (1051 諮商心理師)
# The period is a *prefix* here, not a prefix followed by text: requiring text after the
# period is what dropped every `22.` and broke a page that had already measured 1.000.
MARKER = re.compile(r"^\s*(\d{1,3})\s*[.．、]")
SPACED = re.compile(r"^\s*(\d{1,3})\s+\S")
BARE_NUMBER = re.compile(r"^\s*(\d{1,3})\s*$")
CJK_MARKER = re.compile(r"^\s*([一二三四五六七八九十]{1,3})\s*、")
# Option markers must never be read as question markers.
OPTION = re.compile(r"^\s*[（(]?[A-D][)）.．]")
# Left margin a question marker may sit at, in PDF points. Measured: question numbers at
# 28pt (1152/1072), 41pt (1051 bare numbers), option text at 39pt and option *numbers*
# like `9α`/`16α` at 51pt. 45pt admits every real marker and rejects the option numbers.
MARKER_MAX_X = 45.0
BARE_NUMBER = re.compile(r"^\s*(\d{1,3})\s*$")
CJK_MARKER = re.compile(r"^\s*([一二三四五六七八九十]{1,3})\s*、")
# Option markers must never be read as question markers.
OPTION = re.compile(r"^\s*[（(]?[A-D][)）.．]")
# Left margin a question marker may sit at, in PDF points. Measured: stems ~28pt, bare
# numbers ~41pt, option text ~39-51pt. 60pt admits the markers and stays well left of
# option bodies (which start at 51pt but are caught by OPTION first).
MARKER_MAX_X = 60.0


def page_xml(pdf: str, page: int) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["pdftohtml", "-xml", "-f", str(page), "-l", str(page), "-hidden",
             "-nodrm", "-zoom", "1.0", pdf, os.path.join(tmp, "p")],
            check=True, capture_output=True,
        )
        path = os.path.join(tmp, "p.xml")
        return open(path, encoding="utf-8", errors="replace").read() if os.path.exists(path) else ""


def parse_page(xml: str):
    images, texts = [], []
    for m in re.finditer(r'<image\s+top="(-?\d+)"\s+left="(-?\d+)"\s+width="(\d+)"\s+height="(\d+)"[^>]*>', xml):
        top, left, w, h = (float(v) for v in m.groups())
        images.append([left, top, left + w, top + h])
    for m in re.finditer(r'<text\s+top="(-?\d+)"\s+left="(-?\d+)"\s+width="(\d+)"\s+height="(\d+)"[^>]*>(.*?)</text>', xml):
        top, left, w, h = (float(v) for v in m.groups()[:4])
        body = re.sub(r"<[^>]+>", "", m.group(5))
        texts.append({"box": [left, top, left + w, top + h], "text": body})
    return images, texts


def question_spans(texts, page_width: float, page_height: float, carry_in: int | None = None):
    """Question markers -> (number, top) -> spans [top_i, top_{i+1}).

    A marker is a digit run followed by `.` at the left margin of the stem. Options
    (`A.`...) are rejected explicitly so `A.` is never read as question 1.

    `carry_in` is the question whose stem is on the previous page and whose options (and
    figure) continue at the top of this one. Measured on page 16 of paper 1152: the first
    marker is 79 at y=400, but a two-photo figure sits at y=29-286 and belongs to question
    78. Without `carry_in` that figure is attributed to nothing. It is passed in from the
    previous page's last marker rather than assumed to be `first - 1`.
    """
    markers = []
    for t in texts:
        raw = t["text"].strip()
        if not raw or OPTION.match(raw):
            continue
        left = t["box"][0]
        if left > MARKER_MAX_X:
            continue
        number = None
        m = CJK_MARKER.match(raw)
        if m:
            digits = {c: i + 1 for i, c in enumerate("一二三四五六七八九十")}
            token = m.group(1)
            if token == "十":
                number = 10
            elif token.startswith("十"):
                number = 10 + digits.get(token[1], 0)
            elif len(token) == 3 and token[1] == "十":
                number = digits.get(token[0], 0) * 10 + digits.get(token[2], 0)
            elif len(token) == 2 and token[1] == "十":
                number = digits.get(token[0], 0) * 10
            else:
                number = digits.get(token)
        elif BARE_NUMBER.match(raw):
            number = int(raw)
        elif MARKER.match(raw):
            number = int(MARKER.match(raw).group(1))
        elif SPACED.match(raw):
            number = int(SPACED.match(raw).group(1))
        else:
            number = None
        # Question stems start at the left margin; option text starts further right.
        if number is None or not (1 <= number <= 200):
            continue
        markers.append((number, t["box"][1], raw[:40]))

    # page-absolute spans, in reading order of the markers
    markers.sort(key=lambda x: x[1])
    spans = []
    if carry_in is not None:
        if not markers:
            # A continuation page: the question's stem and options are on the previous page
            # and this page holds only its figure. Measured on 醫事檢驗師 1121 page 13, which
            # prints three option labels and three figures and no question marker at all;
            # without this the page's figures are attributed to nothing and the *next*
            # page's carry-in chain also breaks.
            return [{"number": carry_in, "top": 0.0, "bottom": page_height,
                     "raw": "continuation page: no marker, belongs to the previous question"}]
        if markers[0][1] > 0:
            spans.append({"number": carry_in, "top": 0.0, "bottom": markers[0][1],
                          "raw": "carried from previous page"})
    for i, (number, top, raw) in enumerate(markers):
        bottom = markers[i + 1][1] if i + 1 < len(markers) else page_height
        spans.append({"number": number, "top": top, "bottom": bottom, "raw": raw})
    return spans


def last_marker_number(pdf: str, page: int, lookback: int = 3):
    """The last question marker at or before `page`, for the next page's carry-in.

    Walks back up to `lookback` pages, because a figure can sit on a page *two* pages after
    its question: measured on 醫事檢驗師 1121, where page 13 has no markers at all and page
    14's first figure belongs to the question whose stem is on page 12.
    """
    for candidate in range(page, max(0, page - lookback) - 1, -1):
        if candidate < 1:
            break
        texts = parse_page(page_xml(pdf, candidate))[1]
        spans = question_spans(texts, 595.22, 842.0)
        if spans:
            return spans[-1]["number"]
    return None


def cluster(boxes, gap=3.0):
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


def overlap_len(a, b):
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def span_of(box, spans):
    """The question span a box sits in: greatest vertical overlap, then topmost."""
    best = None
    for s in spans:
        ov = overlap_len((box[1], box[3]), (s["top"], s["bottom"]))
        if ov <= 0:
            continue
        if best is None or ov > best[0]:
            best = (ov, s)
    return best[1] if best else None


def ink_density(png: str, box, scale) -> float:
    """Fraction of dark pixels inside a box. A real figure fills its frame; glyph fragments
    (a subscript drawn as its own tiny image object) leave it mostly white.

    Measured on 職能治療師 1051 page 7, where `PCO2`'s subscript is ten 13pt objects that
    `cluster(gap=3)` glues into seven 24pt-tall blobs: ink density 0.02, versus 0.3-0.9 for
    the real circuit/photo figures measured on other medical papers. Without this the
    ground truth claims a figure, the detector correctly finds none, and the detector is
    scored wrong for being right.
    """
    from PIL import Image
    sx, sy = scale
    im = Image.open(png).convert("L")
    x0, y0 = max(0, int(box[0] * sx)), max(0, int(box[1] * sy))
    x1, y1 = min(im.width, int(box[2] * sx)), min(im.height, int(box[3] * sy))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    arr = np.asarray(im.crop((x0, y0, x1, y1)))
    return float((arr < 200).mean())


def area(box):
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def covered_by(region, preds):
    rw, rh = region[2] - region[0], region[3] - region[1]
    if rw <= 0 or rh <= 0:
        return 0.0
    sx, sy = max(0.5, rw / 400.0), max(0.5, rh / 400.0)
    xs = np.arange(region[0], region[2], sx) + sx / 2
    ys = np.arange(region[1], region[3], sy) + sy / 2
    gx, gy = np.meshgrid(xs, ys)
    hit = np.zeros(gx.shape, dtype=bool)
    for p in preds:
        hit |= (gx >= p[0]) & (gx <= p[2]) & (gy >= p[1]) & (gy <= p[3])
    return float(hit.mean()) * area(region)


def score_page(model, pdf, page, dpi=150, threshold=0.3, min_pt=24.0):
    xml = page_xml(pdf, page)
    images, texts = parse_page(xml)

    with tempfile.TemporaryDirectory() as tmp:
        png, (w_px, h_px) = render_point_scale(pdf, page, dpi, tmp)
        tensor, _ = layout.preprocess(png)
        session = ort_session(model)
        logits, pred_boxes, _m, _o = session.run(
            ["logits", "pred_boxes", "out_masks", "order_logits"], {"pixel_values": tensor}
        )
        rows = layout.postprocess(logits, pred_boxes, (w_px, h_px), threshold)
        sx, sy = w_px / 595.22, h_px / 842.0
        # Filter the *drawn objects* by size before clustering, not the clusters after.
        # A subscript such as `PCO2`'s 2 is drawn as its own ~22x13pt image object, and
        # `cluster(gap=3)` glues ten of them into a 24pt-tall blob that looks like a figure.
        # Real figures arrive as larger objects -- 462x34pt strips (1152 p9) or 107pt-tall
        # chemical structures (1072 p13). Ink density does NOT separate them: a circuit or a
        # structural formula is a line drawing and is mostly white, so filtering on density
        # threw away real figures and still kept the glyph blobs (measured, then changed).
        big = [b for b in images if min(b[2] - b[0], b[3] - b[1]) >= min_pt]
        figures = cluster(big)

    page_w, page_h = 595.22, 842.0

    spans = question_spans(texts, page_w, page_h,
                           carry_in=last_marker_number(pdf, page - 1) if page > 1 else None)

    # true attribution: question span each true figure sits in
    true_attr = {}
    for f in figures:
        s = span_of(f, spans)
        if s:
            true_attr.setdefault(s["number"], []).append(f)

    preds = [{"box": [r["box"][0] / sx, r["box"][1] / sy, r["box"][2] / sx, r["box"][3] / sy],
              "label": r["label"], "score": r["score"]}
             for r in rows if r["label"] in layout.FIGURE_LIKE]

    per_figure = []
    hits = 0
    for p in preds:
        s = span_of(p["box"], spans)
        guess = s["number"] if s else None
        # Attribution is a separate question from range. A prediction is attributed
        # correctly when the figure region it overlaps most has the same question as the
        # prediction claims. Deliberately NOT `IoU > 0.1`: on measurement, one correct
        # prediction on page 13 of paper 1072 scored IoU 0.093 only because the detector
        # had split one large figure into four strips -- an attribution scored by box IoU
        # would call a right answer wrong.
        best_ov, best_truth = 0.0, None
        for cluster_box in figures:
            ov = covered_by(cluster_box, [p["box"]])
            if ov > best_ov:
                best_ov, best_truth = ov, cluster_box
        truth_span = span_of(best_truth, spans) if best_truth else None
        ok = (guess is not None and truth_span is not None
              and truth_span["number"] == guess)
        hits += int(ok)
        bleed = 0.0
        if s:
            for other in spans:
                if other["number"] == guess:
                    continue
                ov = overlap_len((p["box"][1], p["box"][3]), (other["top"], other["bottom"]))
                bleed = max(bleed, ov * (p["box"][2] - p["box"][0]))
        per_figure.append({
            "predicted_question": guess,
            "correct": ok,
            "label": p["label"],
            "score": p["score"],
            "box": [round(v, 1) for v in p["box"]],
            "bleed_area_pt2": round(bleed, 1),
        })

    # range check, per question that truly has a figure
    per_question = []
    for number, true_boxes in sorted(true_attr.items()):
        true_region = [min(b[0] for b in true_boxes), min(b[1] for b in true_boxes),
                       max(b[2] for b in true_boxes), max(b[3] for b in true_boxes)]
        own = [p["box"] for p in preds
               if (span_of(p["box"], spans) or {}).get("number") == number]
        cov = covered_by(true_region, own) / area(true_region) if area(true_region) else None
        inside = sum(covered_by(p, [true_region]) for p in own)
        pur = inside / sum(area(p) for p in own) if own else None
        per_question.append({
            "question": number,
            "true_figure_count": len(true_boxes),
            "true_region_pt": [round(v, 1) for v in true_region],
            "predicted_boxes": len(own),
            "range_coverage": round(cov, 3) if cov is not None else None,
            "range_purity": round(pur, 3) if pur is not None else None,
        })

    return {
        "page": page,
        "markers_found": [s["number"] for s in spans],
        "true_figures": len(figures),
        "predicted_figures": len(preds),
        "attribution_correct": hits,
        "attribution_accuracy": round(hits / len(preds), 3) if preds else None,
        "per_figure": per_figure,
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
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    results = []
    for token in args.pages.split(","):
        try:
            results.append(score_page(args.model, args.paper, int(token), args.dpi, args.threshold))
        except Exception as exc:  # noqa: BLE001
            import traceback
            results.append({"page": int(token), "error": f"{type(exc).__name__}: {exc}",
                            "tb": traceback.format_exc()[-400:]})
        row = results[-1]
        print(json.dumps({k: v for k, v in row.items()
                          if k not in ("per_figure", "per_question", "markers_found")},
                         ensure_ascii=False))
        if args.verbose and "per_question" in row:
            for q in row["per_question"]:
                print("   ", json.dumps(q, ensure_ascii=False))
            for f in row["per_figure"]:
                print("   fig", json.dumps(f, ensure_ascii=False))

    scored = [r for r in results if "error" not in r and r["predicted_figures"]]
    if scored:
        n_pred = sum(r["predicted_figures"] for r in scored)
        qs = [q for r in scored for q in r["per_question"]]
        covs = [q["range_coverage"] for q in qs if q["range_coverage"] is not None]
        purs = [q["range_purity"] for q in qs if q["range_purity"] is not None]
        print(json.dumps({"SUMMARY": {
            "pages": len(scored),
            "predicted_figures": n_pred,
            "attribution_accuracy": round(sum(r["attribution_correct"] for r in scored) / n_pred, 3) if n_pred else None,
            "questions_with_figures": len(qs),
            "mean_range_coverage": round(sum(covs) / len(covs), 3) if covs else None,
            "mean_range_purity": round(sum(purs) / len(purs), 3) if purs else None,
        }}, ensure_ascii=False))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"paper": args.paper, "results": results}, fh, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
