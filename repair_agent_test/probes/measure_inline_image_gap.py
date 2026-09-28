#!/usr/bin/env python3
"""D4 第二個缺陷：**內嵌圖（inline image）看得到、卻切不出來。**

`measure_vector_figure_gap.py` 量的是「畫出來的圖」（向量，管線完全看不到）。
這支量另一件事，而且更隐蔽：

  `extract_images_a` 用 `page.get_image_info(xrefs=True)` 拿 bbox，這條路**看得到內嵌圖**
  （PDF 的 `BI/ID/EI`，沒有 XObject、沒有 xref，`xrefs=True` 回 `xref=0`）。
  但下一步要切圖時，`extract.py::image_bytes_of()` 第一行是：

      if not xref:
          return None, "no-xref"

  `xref=0` 是 falsy，所以**每一個內嵌圖都切不出來**。bbox 正確、圖卻拿不到。

實測（`1032_藥師(一)_藥理學與藥物化學` p10 三張化學結構式）：
  `extract_images_a` 回三個 shot，bbox 都對 `(47,27)-(196,126)` 等，
  但 `image_bytes_of` 對三個都回 `(None, 'no-xref')`。

這跟「向量圖」是兩回事：
  - 向量圖：`get_drawings` 看得到，`get_image_info` 看不到 → 要「渲染頁面再裁」。
  - 內嵌圖：`get_image_info` 看得到、xref 是 0 → 也要「渲染頁面再裁」。
兩者都需要**第三條抽圖路徑**：以 bbox 為準做頁面渲染裁切，不靠 xref。

用法：
  python measure_inline_image_gap.py --candidates <candidates.jsonl> --out <out.json>
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "qbr", "src"))
from qbr.extract import extract_images_a, image_bytes_of  # noqa: E402


#: 一個內嵌圖要算「真圖」而不是「PDF 產生器的雜點」的面積下限（pt²）。
#: 校準（負對照抓到一個 1000 倍的錯）：`藥師(一)/1072_藥師(一)_藥理學與藥物化學`
#: 一頁有 **15,136 個**內嵌圖，全部 < 50pt²（最大 35pt² ≈ 6×6pt）；
#: 同一頁的 **11 張真圖每張 > 3,000pt²**。差三個數量級，門檻不敏感。
#: 第一次量到「154,128 張內嵌圖切不出來」——那是把雜點也算進去了。加上這個門檻後真圖只有 151 張。
MIN_INLINE_FIGURE_PT2 = 400.0
#: 真圖的短邊下限（點）。
MIN_INLINE_FIGURE_SIDE_PT = 8.0


def pdfs_of(candidates: str):
    counts = collections.Counter()
    with open(candidates, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            rel = (row.get("metadata") or {}).get("question_pdf_relative")
            if rel:
                counts[rel] += 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    counts = pdfs_of(args.candidates)
    paths = sorted(counts)
    if args.limit:
        paths = paths[:args.limit]
    print("掃 %d 篇" % len(paths), file=sys.stderr)

    rows = []
    totals = collections.Counter()
    for i, path in enumerate(paths, 1):
        if not os.path.exists(path):
            continue
        try:
            shots = extract_images_a(path)
        except Exception as exc:
            rows.append({"pdf": path, "error": "%s: %s" % (type(exc).__name__, exc)})
            continue
        inline, object_ = 0, 0
        inline_real, inline_tiny = 0, 0
        for shot in shots:
            if shot.get("xref"):
                object_ += 1
                continue
            inline += 1
            w = abs(shot["x1"] - shot["x0"])
            h = abs(shot["y1"] - shot["y0"])
            if (w * h >= MIN_INLINE_FIGURE_PT2
                    and min(w, h) >= MIN_INLINE_FIGURE_SIDE_PT):
                inline_real += 1
            else:
                inline_tiny += 1
        # 內嵌圖的 bytes 一定拿不到；只在真的有一張真圖時才呼叫（雑點不值得逐張試）。
        broken = 0
        if inline_real:
            for shot in shots:
                if shot.get("xref"):
                    continue
                w = abs(shot["x1"] - shot["x0"])
                h = abs(shot["y1"] - shot["y0"])
                if not (w * h >= MIN_INLINE_FIGURE_PT2
                        and min(w, h) >= MIN_INLINE_FIGURE_SIDE_PT):
                    continue
                blob, _reason = image_bytes_of(path, shot["xref"])
                if not blob:
                    broken += 1
        totals["shots"] += len(shots)
        totals["object_backed"] += object_
        totals["inline"] += inline
        totals["inline_real"] += inline_real
        totals["inline_tiny"] += inline_tiny
        totals["inline_bytes_unavailable"] += broken
        rows.append({"pdf": path, "queue_questions": counts[path],
                     "shots": len(shots), "object_backed": object_,
                     "inline": inline, "inline_real": inline_real,
                     "inline_tiny": inline_tiny,
                     "inline_bytes_unavailable": broken})
        if i % 50 == 0:
            print("  ... %d/%d" % (i, len(paths)), file=sys.stderr)

    papers_with_real = [r for r in rows if r.get("inline_real")]
    papers_tiny_only = [r for r in rows if r.get("inline_tiny") and not r.get("inline_real")]
    questions_with_real = sum(r.get("queue_questions", 0) for r in papers_with_real)
    summary = {
        "candidates": args.candidates,
        "papers_scanned": len(rows),
        "errors": sum(1 for r in rows if r.get("error")),
        "figure_shots_total": totals["shots"],
        "figure_shots_object_backed": totals["object_backed"],
        "figure_shots_inline": totals["inline"],
        # 主數字：真圖（≥400pt²）且 bytes 拿不到。
        "figure_shots_inline_real": totals["inline_real"],
        "figure_shots_inline_tiny": totals["inline_tiny"],
        "figure_shots_inline_bytes_unavailable": totals["inline_bytes_unavailable"],
        "papers_with_inline_images": sum(1 for r in rows if r.get("inline")),
        "papers_with_real_inline_figure": len(papers_with_real),
        "papers_with_real_inline_figure_rate": (round(len(papers_with_real) / len(rows), 4)
                                                if rows else None),
        "papers_tiny_only": len(papers_tiny_only),
        "queue_questions_total": sum(counts.values()),
        "queue_questions_in_real_inline_papers": questions_with_real,
        # 上界：候選 JSONL 沒有頁碼，所以這是「整篇計」。
        "queue_questions_in_real_inline_papers_rate": (
            round(questions_with_real / sum(counts.values()), 4) if counts else None),
        "criteria": {"min_inline_figure_pt2": MIN_INLINE_FIGURE_PT2,
                     "min_inline_figure_side_pt": MIN_INLINE_FIGURE_SIDE_PT},
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump({"summary": summary, "papers": rows}, handle,
                  ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
