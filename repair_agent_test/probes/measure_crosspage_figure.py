"""量「跨頁且有圖的題目，**實際送給審題者的裁切**是不是幾乎都錯」——以及錯在哪。

設計者（2026-09-27）：「如果題目有跨頁並且有圖片，則幾乎都會錯誤」。

## 這支探針第一次量錯了，這一版是更正

第一版量的是 `vision.figure_questions` 回傳的 `entry["box"]`（一個題目一個框）。
**那是管線的中間產物，不是審題者看到的裁切。** 實測：
`moex:115020:305:0401:1:question:q052` 在 `candidates.jsonl` 裡的 `image_refs` 是
**四個按選項的框**（`選項 A`..`選項 D`，`source: page-region`），
而 `figure_questions` 給的是**一個** `embedded-image` 框 `[39.2, 0.0, 228.2, 457.9]`。
用後者判定「框內含別題文字」＝量錯對象，會產生假的缺陷。

所以本版**只量 `image_refs` 裡真正 ship 出去的 box**。

## 量什麼（全部是紙張性質，不是「看起來像」）

對每一題的每一個 shipped `image_ref`：

1. `spans_pages`：這一題的 cell 跨不跨頁（來自 PDF 的 row page）。
2. `on_continuation_page`：這一張裁切的頁 ≠ 這一題的第一頁。
3. `starts_at_page_top`：裁切框的上緣 == 0.0（`vision.py` 對續頁刻意如此，
   為了抓住「上一頁最後一個選項的圖」）。
4. `contains_other_question`：**框內有別的題目的 row**。
   這是「截錯」最客觀的定義——框裡出現了不屬於這一題的文字。

## 負對照（必須證明這支探針會說謊）

- **不跨頁**的題：`on_continuation_page` 必須為 False、
  `starts_at_page_top` 必須為 False。若有不跨頁卻從頁頂起，判準壞了。
- 框內若只有**自己的** row，不得算成 `contains_other_question`。

用法：
    qbr/.venv/bin/python repair_agent_test/probes/measure_crosspage_figure.py \
        --candidates qbr/data/review-queues/live/review-ui/candidates.jsonl \
        --root . --limit 40 --out ~/models/glm-ocr/runs/crosspage-figure.json
"""

import argparse
import collections
import json
import os
import sys


def _pipeline():
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.abspath(os.path.join(here, "..", ".."))
    sys.path.insert(0, os.path.join(repo, "qbr", "src"))
    sys.path.insert(0, os.path.join(repo, "qbr", "scripts"))
    from qbr import extract, repair, reflow, vision  # noqa: E402
    import compare_skeleton  # noqa: E402

    return extract, repair, reflow, vision, compare_skeleton


def read_candidates(candidates_path):
    """{paper_relative: [candidate_row, ...]}，只留有 shipped image_refs 的題目。"""
    wanted = collections.OrderedDict()
    for line in open(candidates_path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if not (row.get("image_refs") or []):
            continue
        paper = (row.get("metadata") or {}).get("question_pdf_relative")
        if paper:
            wanted.setdefault(paper, []).append(row)
    return wanted


def rows_by_question(pdf, extract, repair, reflow, vision, compare_skeleton):
    """{question_number: [(page, y0, y1, x0, x1), ...]}，這一題自己的 row。"""
    cells = extract.extract_cells_a(pdf)
    kept, _ = repair.mask_chrome(cells)
    table, _, alphabet = reflow.cells_with_pages(kept)
    items = compare_skeleton.skeleton_items(reflow.skeleton(table), table, alphabet)
    by_id = dict(enumerate(kept, start=1))
    out = {}
    for item in items:
        own = []
        for number in vision.cell_ids_of(item):
            row = by_id.get(number)
            if row is None:
                continue
            own.append((int(row["page"]), float(row["y0"]), float(row["y1"]),
                        float(row["x0"]), float(row["x1"])))
        out.setdefault(item["number"], []).extend(own)
    return out


def _page_figures(pdf, page, cache):
    """這一頁的圖物件 bbox（`get_image_info(xrefs=True)`）——紙張的性質。"""
    key = (pdf, page)
    if key in cache:
        return cache[key]
    import pymupdf  # noqa: PLC0415

    out = []
    try:
        doc = pymupdf.open(pdf)
        if 1 <= page <= doc.page_count:
            for info in doc[page - 1].get_image_info(xrefs=True):
                out.append(tuple(float(v) for v in info["bbox"]))
        doc.close()
    except Exception:  # noqa: BLE001
        out = []
    cache[key] = out
    return out


def _overlap(a, b):
    return (min(a[2], b[2]) - max(a[0], b[0]) > 0.5
            and min(a[3], b[3]) - max(a[1], b[1]) > 0.5)


def _contains(outer, inner, tol=1.0):
    return (outer[0] - tol <= inner[0] and outer[1] - tol <= inner[1]
            and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)


def measure_paper(pdf, candidates, extract, repair, reflow, vision, compare_skeleton):
    own_rows = rows_by_question(pdf, extract, repair, reflow, vision, compare_skeleton)
    figure_cache = {}
    facts = []
    for row in candidates:
        number = row.get("question_number")
        mine = own_rows.get(number) or []
        pages = sorted({p for p, *_ in mine})
        first_page = pages[0] if pages else None

        for ref in row.get("image_refs") or []:
            box = ref.get("box")
            page = ref.get("page")
            if not box or page is None:
                continue
            page = int(page)
            top, bottom = float(box[1]), float(box[3])
            my_rows_on_page = [r for r in mine if r[0] == page]
            my_first_y0 = min((r[1] for r in my_rows_on_page), default=None)
            my_last_y1 = max((r[2] for r in my_rows_on_page), default=None)
            # 這個框伸出這一題「自己的 row」多遠？管線的 ownership pass 用的就是這把尺：
            # 「a crop reaching outside their own question's rows」是審題者回報的缺陷。
            reach_above = None if my_first_y0 is None else max(0.0, my_first_y0 - top)
            reach_below = None if my_last_y1 is None else max(0.0, bottom - my_last_y1)

            # 那一塊「伸出去」的區域裡，有別題的 row 嗎？（＝真的截到別題的東西）
            others = []
            for other_number, rows in own_rows.items():
                if other_number == number:
                    continue
                for r in rows:
                    if r[0] != page:
                        continue
                    if top - 0.5 <= r[1] and r[2] <= bottom + 0.5:
                        others.append((other_number, round(r[1], 1)))
            other_numbers = sorted({n for n, _ in others})

            # 這一張裁切對到紙本上哪些圖物件？有重疊但**沒有被完整包住**的，就是被切掉的圖。
            page_figs = _page_figures(pdf, page, figure_cache)
            overlapping = [f for f in page_figs if _overlap((top, top, bottom, bottom)
                                                            if False else (float(box[0]), top, float(box[2]), bottom), f)]
            cut = [f for f in overlapping
                   if not _contains((float(box[0]), top, float(box[2]), bottom), f)]

            facts.append({
                "paper": os.path.basename(pdf)[:-4],
                "key": row.get("candidate_key"),
                "number": number,
                "label": ref.get("label"),
                "source": ref.get("source"),
                "page": page,
                "first_page": first_page,
                "box": [round(float(v), 1) for v in box],
                "spans_pages": len(pages) > 1,
                "on_continuation_page": first_page is not None and page != first_page,
                "starts_at_page_top": top == 0.0,
                "own_first_y0_on_page": None if my_first_y0 is None else round(my_first_y0, 1),
                "own_last_y1_on_page": None if my_last_y1 is None else round(my_last_y1, 1),
                "reach_above_own_rows": None if reach_above is None else round(reach_above, 1),
                "reach_below_own_rows": None if reach_below is None else round(reach_below, 1),
                "reaches_outside_own_rows": bool(
                    (reach_above or 0) > 2.0 or (reach_below or 0) > 2.0),
                "page_figure_objects": len(page_figs),
                "figures_overlapping_box": len(overlapping),
                "figures_cut_by_box": len(cut),
                "cuts_a_figure": bool(cut),
                "has_no_figure": bool(page_figs) and not overlapping,
                "other_questions_inside_box": other_numbers,
                "contains_other_question": bool(other_numbers),
            })
    return facts


def summarise(rows):
    def frac(sub):
        return len(sub), len(sub) / max(1, len(rows))

    cont = [r for r in rows if r["on_continuation_page"]]
    span = [r for r in rows if r["spans_pages"]]
    nonspan = [r for r in rows if not r["spans_pages"]]
    bad = [r for r in rows if r["contains_other_question"]]
    outside = [r for r in rows if r["reaches_outside_own_rows"]]
    cut = [r for r in rows if r["cuts_a_figure"]]
    nofig = [r for r in rows if r["has_no_figure"]]

    def rate(sub, sel):
        hit = [r for r in sub if sel(r)]
        return {"n": len(sub), "hit": len(hit), "rate": len(hit) / max(1, len(sub))}

    return {
        "crops": len(rows),
        "spans_pages": len(span),
        "on_continuation_page": len(cont),
        "starts_at_page_top": len([r for r in rows if r["starts_at_page_top"]]),
        "reaches_outside_own_rows": len(outside),
        "contains_other_question": len(bad),
        "cuts_a_figure": len(cut),
        "has_no_figure": len(nofig),
        "non_span_on_continuation": len([r for r in nonspan if r["on_continuation_page"]]),
        "non_span_starts_at_page_top": len([r for r in nonspan if r["starts_at_page_top"]]),
        "frac_span_of_crops": frac(span),
        "frac_cont_of_crops": frac(cont),
        "frac_outside_of_crops": frac(outside),
        "frac_bad_of_crops": frac(bad),
        # 設計者的假設：跨頁 ⇒ 幾乎都會錯。這裡用同一把尺分別量兩群。
        "outside_rate_spans_pages": rate(span, lambda r: r["reaches_outside_own_rows"]),
        "outside_rate_no_span": rate(nonspan, lambda r: r["reaches_outside_own_rows"]),
        "bad_rate_spans_pages": rate(span, lambda r: r["contains_other_question"]),
        "bad_rate_no_span": rate(nonspan, lambda r: r["contains_other_question"]),
        # 最強的形式：這一張裁切把紙本上的圖物件**切了一半**（有重疊、沒被完整包住）。
        "cut_rate_spans_pages": rate(span, lambda r: r["cuts_a_figure"]),
        "cut_rate_no_span": rate(nonspan, lambda r: r["cuts_a_figure"]),
        "cut_rate_cont": rate(cont, lambda r: r["cuts_a_figure"]),
        "cut_rate_not_cont": rate([r for r in rows if not r["on_continuation_page"]],
                                  lambda r: r["cuts_a_figure"]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--root", default=".")
    parser.add_argument("--limit", type=int, default=0, help="只做前 N 篇（0＝全部）")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    extract, repair, reflow, vision, compare_skeleton = _pipeline()
    wanted = read_candidates(args.candidates)

    index = {}
    for base, _, files in os.walk(os.path.join(args.root, "國考題資料夾")):
        for name in files:
            if name.endswith(".pdf") and not name.endswith(("_ANS.pdf", "_MOD.pdf")):
                index.setdefault(name[:-4], os.path.join(base, name))

    rows, missing, errors = [], [], []
    papers = list(wanted.items())
    if args.limit:
        papers = papers[:args.limit]
    for n, (paper, candidates) in enumerate(papers, 1):
        stem = os.path.basename(paper)[:-4]
        pdf = index.get(stem)
        if not pdf:
            missing.append(stem)
            continue
        try:
            rows.extend(measure_paper(pdf, candidates, extract, repair, reflow, vision,
                                      compare_skeleton))
        except Exception as exc:  # noqa: BLE001
            errors.append({"paper": stem, "error": f"{type(exc).__name__}: {exc}"})
        if n % 20 == 0:
            print(f"  … {n}/{len(papers)} 篇，累計 {len(rows)} 張裁切", flush=True)

    report = {
        "candidates": args.candidates,
        "papers_scanned": len(papers) - len(missing) - len(errors),
        "papers_missing": missing,
        "errors": errors,
        "summary": summarise(rows),
        "rows": rows,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)

    s = report["summary"]
    print()
    print(f"實際 ship 的裁切         {s['crops']}")
    print(f"  這一題 cell 跨頁       {s['spans_pages']} ({s['frac_span_of_crops'][1]:.1%})")
    print(f"  裁切在續頁             {s['on_continuation_page']} ({s['frac_cont_of_crops'][1]:.1%})")
    print(f"  裁切從頁頂起           {s['starts_at_page_top']}")
    print(f"  框伸出自己的 row       {s['reaches_outside_own_rows']}"
          f" ({s['frac_outside_of_crops'][1]:.1%})")
    print(f"  框內含別題的文字       {s['contains_other_question']}"
          f" ({s['frac_bad_of_crops'][1]:.1%})")
    print(f"  把紙本的圖切掉一半     {s['cuts_a_figure']}")
    print(f"  標成圖、裡面沒有圖     {s['has_no_figure']}")
    print()
    print("設計者的假設（跨頁 ⇒ 幾乎都會錯），用同一把尺分別量：")
    for name in ("cut_rate_spans_pages", "cut_rate_no_span", "cut_rate_cont",
                 "cut_rate_not_cont"):
        v = s[name]
        print(f"  裁切把圖切掉   {name:<22} {v['rate']:.1%} ({v['hit']}/{v['n']})")
    print("負對照（必須為 0）:")
    print(f"  不跨頁卻在續頁         {s['non_span_on_continuation']}")
    print(f"  不跨頁卻從頁頂起       {s['non_span_starts_at_page_top']}")
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
