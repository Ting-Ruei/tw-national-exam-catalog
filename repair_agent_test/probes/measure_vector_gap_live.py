#!/usr/bin/env python3
"""D4 批次：掃過**佇列裡真正在審的那 989 篇 PDF**，數向量圖缺口。

`measure_vector_figure_gap.py` 是單篇／抽樣的探針；這支是把它套到**站上正在服務的
那份佇列**（`live/review-ui/candidates.jsonl` 裡出現過的每一篇 PDF）。

為什麼要對「佇列裡的 PDF」而不是全庫：
  - 全庫 8,349 篇裡大部分不在審。設計者問的是「**現在給人看的這些題，有多少圖是空的**」。
  - 佇列裡的 989 篇是實際會被人打開的範圍。

產出每篇的頁面分類；最後加總。也順便記下**每一頁**，因為要能回頭目視驗證。

用法：
  python measure_vector_gap_live.py --candidates <candidates.jsonl> --out <out.json>
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measure_vector_figure_gap import classify_page, iter_pdfs  # noqa: E402

import fitz  # noqa: E402


def pdfs_of(candidates: str):
    """佇列裡出現過的每一篇 PDF，附它在佇列裡的題數（才知道缺口的重量）。"""
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


def scan(path: str) -> dict:
    out = {"pdf": path, "pages": [], "error": None}
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
    out["vector_only_pages"] = [p["page"] for p in out["pages"] if p["kind"] == "vector_only"]
    # 設計者要的數字：這一頁有「畫出來、管線看不到」的圖（不管有没有點陣圖）。
    out["drawn_figure_pages"] = [p["page"] for p in out["pages"] if p["has_drawn_figure"]]
    out["table_like_pages"] = [p["page"] for p in out["pages"] if p["kind"] == "table_like"]
    out["table_text_absent_pages"] = [p["page"] for p in out["pages"]
                                      if p["kind"] == "table_like"
                                      and (p.get("text_chars") or 0) < 200]
    out["has_vector_only"] = bool(out["vector_only_pages"])
    out["has_drawn_figure"] = bool(out["drawn_figure_pages"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    counts = pdfs_of(args.candidates)
    print("佇列裡的 PDF: %d 篇，共 %d 題" % (len(counts), sum(counts.values())),
          file=sys.stderr)
    paths = sorted(counts)
    if args.limit:
        paths = paths[:args.limit]

    papers = []
    for i, path in enumerate(paths, 1):
        if not os.path.exists(path):
            papers.append({"pdf": path, "error": "missing", "pages": [], "counts": {}})
            continue
        row = scan(path)
        row["queue_questions"] = counts[path]
        papers.append(row)
        if i % 50 == 0:
            print("  ... %d/%d" % (i, len(paths)), file=sys.stderr)

    kinds = collections.Counter()
    for r in papers:
        for p in r["pages"]:
            kinds[p["kind"]] += 1
    pages_total = sum(len(r["pages"]) for r in papers)
    vo = kinds.get("vector_only", 0)
    papers_vo = [r["pdf"] for r in papers if r.get("has_vector_only")]
    # 「有畫出來的圖」＝ vector_only ＋ both（前者管線完全空，後者管線只拿到點陣那部分）。
    drawn_pages = sum(len(r.get("drawn_figure_pages") or []) for r in papers)
    vo_only_pages = vo
    both_pages = kinds.get("both", 0)
    table_pages = kinds.get("table_like", 0)
    table_absent = sum(len(r.get("table_text_absent_pages") or []) for r in papers)

    # 缺口的重量：有這些頁的那些 PDF，各自在佇列上有幾題。
    questions_in_drawn_papers = sum(r.get("queue_questions", 0) for r in papers
                                    if r.get("has_drawn_figure"))
    questions_in_vo_papers = sum(r.get("queue_questions", 0) for r in papers
                                 if r.get("has_vector_only"))
    summary = {
        "candidates": args.candidates,
        "papers_scanned": len(papers),
        "errors": sum(1 for r in papers if r.get("error")),
        "pages_total": pages_total,
        "page_kinds": dict(kinds),
        # 主數字：有「畫出來、管線看不到」的圖的頁。
        "drawn_figure_pages": drawn_pages,
        "drawn_figure_page_rate": round(drawn_pages / pages_total, 4) if pages_total else None,
        "vector_only_pages": vo_only_pages,
        "both_pages": both_pages,
        "table_like_pages": table_pages,
        "table_text_absent_pages": table_absent,
        "papers_with_drawn_figure": sum(1 for r in papers if r.get("has_drawn_figure")),
        "papers_with_vector_only": len(papers_vo),
        "papers_with_vector_only_rate": round(len(papers_vo) / len(papers), 4) if papers else None,
        "queue_questions_total": sum(counts.values()),
        "queue_questions_in_drawn_figure_papers": questions_in_drawn_papers,
        "queue_questions_in_drawn_figure_papers_rate": (
            round(questions_in_drawn_papers / sum(counts.values()), 4) if counts else None),
        "queue_questions_in_vector_only_papers": questions_in_vo_papers,
        "queue_questions_in_vector_only_papers_rate": (
            round(questions_in_vo_papers / sum(counts.values()), 4) if counts else None),
        "papers_vector_only_sample": papers_vo[:40],
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump({"summary": summary, "papers": papers}, handle,
                  ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
