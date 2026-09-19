"""用本地模型逐張審查圖片裁切，與紙本原區域並排比對。

這不是「問模型這張圖完不完整」——那個問題模型答不出來（已量測，見 `qbr/audit_crops.py`）。
問的是「左邊這張裁切，是不是右邊這塊紙本區域裡那個圖的完整內容」，這只需要比對，不需要記憶。
"""

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from qbr import audit_crops, vision  # noqa: E402


def papers_by_stem(root):
    found = {}
    for base, _, files in os.walk(root):
        for name in files:
            if name.endswith(".pdf") and not name.endswith(("_ANS.pdf", "_MOD.pdf")):
                found[name[:-4]] = os.path.join(base, name)
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, help="run directory holding review-ui/")
    parser.add_argument("--pdf-root", required=True, help="official PDF root to read pages from")
    parser.add_argument("--out", required=True, help="JSON report path")
    parser.add_argument("--limit", type=int, default=0, help="audit at most N crops")
    parser.add_argument("--think", action="store_true", help="reasoning ON (slow, for disputes)")
    parser.add_argument("--only", default="", help="audit only crops whose path contains this")
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()

    candidates_path = os.path.join(args.work, "review-ui", "candidates.jsonl")
    rows = [json.loads(line) for line in open(candidates_path, encoding="utf-8") if line.strip()]
    papers = papers_by_stem(args.pdf_root)

    jobs = []
    for row in rows:
        path = None
        for ref in row.get("image_refs") or []:
            if ref.get("path"):
                path = ref["path"]
                break
        if not path:
            continue
        stem = os.path.basename(os.path.dirname(path))
        pdf = papers.get(stem)
        if not pdf:
            continue
        for ref in row.get("image_refs") or []:
            if args.only and args.only not in (ref.get("path") or ""):
                continue
            jobs.append((row, ref, pdf))
    if args.limit:
        jobs = jobs[:args.limit]
    print(f"要審查 {len(jobs)} 張裁切（{len(rows)} 題）", flush=True)

    results = []
    started = time.time()
    for index, (row, ref, pdf) in enumerate(jobs, start=1):
        where = ref.get("page") or row.get("metadata", {}).get("question_page")
        box = ref.get("box")
        entry = {"question": row.get("question_number"),
                 "subject": row.get("subject"), "paper": os.path.basename(pdf)[:-4],
                 "label": ref.get("label"), "role": ref.get("asset_role"),
                 "path": ref.get("path")}
        if not where or not box:
            entry.update({"ok": False, "error": "no-page-or-box"})
            results.append(entry)
            continue
        outcome = audit_crops.audit_one(
            pdf, where, box, subject=row.get("subject") or "",
            stem=row.get("stem") or "", think=args.think)
        entry.update(outcome)
        results.append(entry)
        flag = "" if outcome.get("same") else "  <-- 需重看"
        print(f"  [{index}/{len(jobs)}] {entry['paper'][:22]} q{entry['question']} "
              f"{entry['label']} same={outcome.get('same')} "
              f"missing={outcome.get('missing')} {outcome.get('seconds')}s{flag}", flush=True)

    elapsed = time.time() - started
    doubtful = [r for r in results if r.get("same") is not True]
    report = {"audited": len(results), "seconds": round(elapsed, 1),
              "think": bool(args.think),
              "model": vision.MODEL,
              "doubtful": len(doubtful),
              "results": results}
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
    print(f"\n完成 {len(results)} 張，{len(doubtful)} 張需重看，{elapsed:.0f} 秒 -> {args.out}")


if __name__ == "__main__":
    main()
