#!/usr/bin/env python3
"""D4：向量的圖片缺口有多大？——**在真正的題庫語料上量，不是在代理指標上量。**

設計者 2026-09-27 的話：

> 「我以為 PDF 就是扁平化圖片，以為 OCR 就能鎖定圖片位置，所以不懂內部還有向量，
>   這部分缺口你要測試看看。」

## 為什麼需要另一支探針

`measure_gt_blindspot.py` 量的是**代理指標**：拿「`pdfimages` 說沒有夠大的點陣圖」的頁，
問 PP-DocLayout 有沒有看到圖。它回答的是「GT 看不見的比例」。

設計者問的是**更下游、也更要緊的那個問題**：

> **在站上正在給人看的 79,090 題裡，有多少題的圖是向量圖，因而 `image_refs` 是空的？**

這兩者不同。前者是「GT 有多瞎」，後者是「**有多少題現在是空的**」。這支探針量後者。

## 量法（兩條互相獨立的證據，必須一致才算有圖）

1. **向量繪圖**：PyMuPDF `page.get_drawings()`——回傳這一頁的**向量路徑**（填色、畫線、
   貝茲曲線）。化學結構式、電路圖、作者用 Word 畫的曲線都在這裡，而 `extract_images_a`
   （`get_image_info(xrefs=True)`）與 `extract_images_b`（poppler xml `<image>`）都看不到。
2. **點陣圖**：`page.get_images()`——管線現在唯一看得見的那一種。

一頁「有向量但沒有點陣圖」＝ 這頁的圖**只**以向量存在 ＝ 管線必然漏掉它。

**判準（保守）**：向量路徑要有多條（預設 ≥ 8）才算圖，且路徑的總面積要夠大
（預設 ≥ 2% 頁面積）。單一條分隔線、表格框、頁框都不是圖——沒有這個下限，
每一頁都會被算成有向量圖。

## 這裡不量「切得對不對」

範圍與歸屬已經在 `measure_figure_attribution.py`／`score_figure_range_by_ink.py` 量過
（歸屬 0.992、墨跡 recall 0.987）。這支探針只回答一個是／否：**這題有沒有圖，
而管線有沒有看到它**。

用法：
  measure_vector_figure_gap.py --papers 40 --out ~/models/glm-ocr/runs/vector-gap.json
  measure_vector_figure_gap.py --all-medical --out ...      # 醫學類科全掃
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

import fitz  # PyMuPDF

#: 一個向量物件至少要這麼大的**短邊**（點）才算「有形的東西」。
#: 校準證據：假圖案（`1001_藥師_藥劑學` p1 的 9 條分隔線）短邊全 < 4；
#: 真圖案（反應卡／構音圖／解剖圖）短邊 ≥ 8。
MIN_OBJ_SIDE_PT = 8.0
#: 一個向量物件的面積下限（點²）。分隔線的面積 ≤ 63。
MIN_OBJ_AREA_PT = 100.0
#: 一個向量物件的面積上限，佔頁面積的比例。超過的是內容外框／底圖（實測 0.844）。
MAX_OBJ_AREA_RATIO = 0.5
#: 一頁要有這麼多個「有形物件」才判定為向量圖。
MIN_PATHS = 3
#: 點陣圖要有這麼大（點）才算「圖」。
#: 內嵌圖（inline image）可以很小：實測 `1061_醫事放射師_醫學物理學與輻射安全` p2
#: 的三張小圖只有 22×18.5pt。原本設 24pt 會把它們誤判成「沒有圖」。
MIN_RASTER_PT = 8.0


def raster_area(page) -> float:
    """這一頁最大的點陣圖面積（點²）。

    **必須同時走兩條路，不然會把「內嵌圖」誤判成「沒有圖」。**

    負對照抓到的錯：`1032_藥師(一)_藥理學與藥物化學` p10 的三張化學結構式，
    `get_images()` 回 **0 個**、`get_image_rects()` 當然也空——它們是 **inline image**
    （PDF 的 `BI/ID/EI` 運算子，沒有 XObject 也沒有 xref，`xrefs=True` 回 `xref=0`）。
    只有 `get_image_info()` 看得見。若只信 `get_images()`，這頁會被錯判成 `vector_only`。
    管線的 `extract_images_a` 用的正是 `get_image_info(xrefs=True)`，所以它**看得到**這些圖。
    """
    best = 0.0
    boxes = []
    # 路一：真正的 XObject 點陣圖。
    for info in page.get_images(full=True):
        try:
            boxes.extend(page.get_image_rects(info[0]))
        except Exception:
            continue
    # 路二：內嵌圖（inline image），只有 get_image_info 看得到。
    try:
        for entry in page.get_image_info():
            if entry.get("bbox"):
                boxes.append(fitz.Rect(entry["bbox"]))
    except Exception:
        pass
    for rect in boxes:
        try:
            w, h = abs(rect.width), abs(rect.height)
        except Exception:
            continue
        if min(w, h) >= MIN_RASTER_PT:
            best = max(best, w * h)
    return best


def vector_stats(page):
    """回傳 (有形物件數, 聯集面積, 頁面積, 表格狀物件數)。

    **逐物件篩選，不用整頁的聯集面積。** 第一版用「所有路徑的聯集 bbox 佔頁 ≥2%」時，
    `1001_藥師_藥劑學` p1 的 9 條散落分隔線會通過——它們聯合起來橫跨整頁，
    但每一條都只有 0.4 點高。這是**負對照抓到的假圖**。

    所以一個物件要算「有形」必須同時滿足：短邊 ≥ `MIN_OBJ_SIDE_PT`、
    面積 ≥ `MIN_OBJ_AREA_PT`、面積佔頁 ≤ `MAX_OBJ_AREA_RATIO`（排除內容外框／底圖）。

    另外回報 `re`-only 的表格狀物件數——表格是另一件事（文字可重建），不算「圖」，
    但要單獨看得見。
    """
    page_area = page.rect.width * page.rect.height
    try:
        drawings = page.get_drawings()
    except Exception:
        return 0, 0.0, page_area, 0
    if not drawings or page_area <= 0:
        return 0, 0.0, page_area, 0

    shaped, table_objs, hit = [], 0, set()
    gx = gy = 8
    for d in drawings:
        rect = d.get("rect")
        if rect is None:
            continue
        try:
            r = fitz.Rect(rect)
        except Exception:
            continue
        if not r.is_valid or r.is_empty:
            continue
        w, h, area = abs(r.width), abs(r.height), abs(r.width * r.height)
        ratio = area / page_area
        items = [it[0] for it in (d.get("items") or [])]
        # 裸矩形（只有一個 re 的填色塊）是**框架或遮罩**，不是「畫出來的圖」。
        # 負對照抓到的錯：`1032_藥師(一)_藥理學與藥物化學` p10 的三張化學結構式是
        # inline image，上面各蓋一個白色 re 遮罩；若不排除，這頁會被誤判成向量圖。
        # 對照真圖：反應卡是 re 30 ＋ l 47 ＋ c 4 的**混合** items。
        bare_rect = items == ["re"]
        if bare_rect and ratio < MAX_OBJ_AREA_RATIO and area >= 1.0:
            table_objs += 1
        if (not bare_rect and min(w, h) >= MIN_OBJ_SIDE_PT
                and area >= MIN_OBJ_AREA_PT and ratio <= MAX_OBJ_AREA_RATIO):
            shaped.append(r)
            x0 = max(0, min(gx - 1, int(r.x0 / page.rect.width * gx)))
            x1 = max(0, min(gx - 1, int(r.x1 / page.rect.width * gx)))
            y0 = max(0, min(gy - 1, int(r.y0 / page.rect.height * gy)))
            y1 = max(0, min(gy - 1, int(r.y1 / page.rect.height * gy)))
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    hit.add((x, y))
    area = len(hit) / float(gx * gy) * page_area if hit else 0.0
    return len(shaped), area, page_area, table_objs


def classify_page(page) -> dict:
    """一頁的五種狀態之一。

    - `both`         有畫出來的圖（向量）也有點陣圖——**向量那部分管線看不見**
    - `vector_only`  只有向量圖、**完全沒有點陣圖** → 最貴的一種，管線交出空的
    - `raster`       只有點陣圖
    - `table_like`   沒有圖，但有表格框且文字層重建不出來
    - `none`         都沒有

    判 `kind` 的**優先序有實際後果**：第一版把 `table_like` 擺在 `raster` 前面，
    結果「有點陣圖（所以管線看得見）且剛好有 ≥8 個框線」的頁被貼成 `table_like`。
    現在 `raster` 優先於 `table_like`：「有圖」比「有框」重要。

    設計者真正要的數字是 `has_drawn_figure`：**這頁有没有畫出來、而管線看不見的圖**。
    它跟這頁有没有點陣圖無關——兩者可以並存（`both`）。
    """
    ra = raster_area(page)
    n_paths, va, pa, table_objs = vector_stats(page)
    vec_ok = n_paths >= MIN_PATHS and pa > 0
    ras_ok = ra > 0
    # 表格狀但沒有「圖」形的頁：要看文字層是否重建得出來。
    # 實測 `1102_構音與語暢障礙學` p2：表格畫成 120 個 re，文字層完全沒有表格内容
    # （`Months`／`15 months`／`Stoel` 全部 False）→ 這張表是**真的掉了**，不是可重建。
    table_like = (not vec_ok) and (not ras_ok) and table_objs >= 8
    text_chars = None
    if table_like:
        try:
            text_chars = len((page.get_text() or "").strip())
        except Exception:
            text_chars = None
    return {
        "shaped_objs": n_paths,
        "table_objs": table_objs,
        "vector_area_ratio": round(va / pa, 4) if pa else 0.0,
        "raster_area_pt": round(ra, 1),
        "vector_figure": bool(vec_ok),
        "raster_figure": bool(ras_ok),
        # 設計者要的那個數字：有沒有「畫出來、管線看不到」的圖。
        "has_drawn_figure": bool(vec_ok),
        "table_like": bool(table_like),
        "text_chars": text_chars,
        "kind": ("both" if (vec_ok and ras_ok) else
                 "vector_only" if vec_ok else
                 "raster" if ras_ok else
                 "table_like" if table_like else "none"),
    }


def scan_paper(path: str) -> dict:
    """一篇 PDF 的每一頁，以及它的結論。"""
    out = {"pdf": os.path.relpath(path), "pages": [], "error": None}
    try:
        doc = fitz.open(path)
    except Exception as exc:
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
        return out
    try:
        for index, page in enumerate(doc, start=1):
            row = classify_page(page)
            row["page"] = index
            out["pages"].append(row)
    finally:
        doc.close()
    kinds = collections.Counter(p["kind"] for p in out["pages"])
    out["counts"] = dict(kinds)
    # 表格頁：分「文字層還在」與「文字層也没了」兩類（後者才是真的掉了）。
    tables = [p for p in out["pages"] if p["kind"] == "table_like"]
    out["table_like_pages"] = [p["page"] for p in tables]
    out["table_text_absent_pages"] = [p["page"] for p in tables
                                      if (p.get("text_chars") or 0) < 200]
    # 這篇有沒有「只有向量」的頁——那是最貴的一種，因為管線交出的是空的。
    out["has_vector_only"] = kinds.get("vector_only", 0) > 0
    out["has_any_figure"] = any(k in kinds for k in ("both", "vector_only", "raster"))
    out["vector_only_pages"] = [p["page"] for p in out["pages"] if p["kind"] == "vector_only"]
    return out


def iter_pdfs(root: str, limit: int | None, medical_only: bool):
    """走過語料。`medical_only` 用路徑關鍵字篩醫學類科（設計者的首要目標）。"""
    medical = ("醫師", "藥師", "護理", "醫事", "營養師", "物理治療", "職能治療",
               "醫檢", "放射", "牙醫", "語言治療", "呼吸治療", "助產", "驗光",
               "聽力", "心理師", "中醫師", "獸醫")
    found = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            if not name.lower().endswith(".pdf"):
                continue
            if name.endswith("_ANS.pdf") or name.endswith("_MOD.pdf"):
                continue
            if medical_only and not any(m in name for m in medical):
                continue
            yield os.path.join(dirpath, name)
            found += 1
            if limit and found >= limit:
                return


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default="國考題資料夾/10_official_pdf/by_official_catalog")
    ap.add_argument("--papers", type=int, default=40, help="0 = 全部")
    ap.add_argument("--medical-only", action="store_true", help="只掃醫學類科（設計者的首要目標）")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    papers = list(iter_pdfs(args.root, args.papers or None, args.medical_only))
    print("掃 %d 篇（medical_only=%s）" % (len(papers), args.medical_only), file=sys.stderr)

    results = []
    for i, path in enumerate(papers, 1):
        results.append(scan_paper(path))
        if i % 25 == 0:
            print("  ... %d/%d" % (i, len(papers)), file=sys.stderr)

    total_pages = sum(len(r["pages"]) for r in results)
    kinds = collections.Counter()
    for r in results:
        for p in r["pages"]:
            kinds[p["kind"]] += 1
    papers_vector_only = sum(1 for r in results if r["has_vector_only"])
    papers_any_figure = sum(1 for r in results if r["has_any_figure"])

    summary = {
        "root": args.root,
        "papers_scanned": len(results),
        "errors": sum(1 for r in results if r["error"]),
        "pages_total": total_pages,
        "page_kinds": dict(kinds),
        "vector_only_pages": kinds.get("vector_only", 0),
        # 這是最要緊的一個數字：管線在這些頁上交出空的 image_refs。
        "vector_only_page_rate": round(kinds.get("vector_only", 0) / total_pages, 4) if total_pages else None,
        "papers_with_vector_only": papers_vector_only,
        "papers_with_any_figure": papers_any_figure,
        "criteria": {"min_paths": MIN_PATHS, "min_obj_side_pt": MIN_OBJ_SIDE_PT,
                     "min_obj_area_pt": MIN_OBJ_AREA_PT,
                     "max_obj_area_ratio": MAX_OBJ_AREA_RATIO,
                     "min_raster_pt": MIN_RASTER_PT},
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump({"summary": summary, "papers": results}, handle, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
