"""逐張驗證圖片裁切：裁切的框是不是紙本上那個圖的完整範圍。

使用者要求「每張圖都要用模型確認」。模型那一關做過了，而且量測結果是模型做不到
（見 `qbr/audit_crops.py` 與 `qbr/verify_crops.py` 的說明：80 張已知好壞各半，
模型全部回答 same；把結構只留最上面 8 pt 的細條，模型仍然 7/8 回答 same）。
所以這一關由紙張自己回答，而它答得出來，因為「同一張圖的其它切片」寫在檔案裡。
"""

import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import compare_skeleton  # noqa: E402

from qbr import extract
from qbr import repair
from qbr import reflow
from qbr import verify_crops
from qbr import vision


def paper_index(root):
    found = {}
    for base, _, files in os.walk(root):
        for name in files:
            if name.endswith(".pdf") and not name.endswith(("_ANS.pdf", "_MOD.pdf")):
                found.setdefault(name[:-4], os.path.join(base, name))
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, help="run directory holding review-ui/")
    parser.add_argument("--pdf-root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    candidates_path = os.path.join(args.work, "review-ui", "candidates.jsonl")
    rows = [json.loads(line) for line in open(candidates_path, encoding="utf-8") if line.strip()]
    papers = paper_index(args.pdf_root)

    # The band and the picture set are per question, so they are derived once per question and
    # reused for all of its crops.
    prepared = {}
    results = []
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
        if pdf not in prepared:
            try:
                cells = extract.extract_cells_a(pdf)
                kept, _ = repair.mask_chrome(cells)
                table, _, alphabet = reflow.cells_with_pages(kept)
                items = compare_skeleton.skeleton_items(reflow.skeleton(table), table, alphabet)
                images = extract.extract_images_a(pdf)
                entries = vision.figure_questions(items, kept, images, alphabet=alphabet)
                prepared[pdf] = {"kept": kept, "items": {i["number"]: i for i in items},
                                 "images": images,
                                 "next": {e["number"]: e.get("next_start") for e in entries},
                                 "bands": {}}
            except Exception as exc:
                prepared[pdf] = {"error": str(exc)}
        state = prepared[pdf]
        if state.get("error"):
            for ref in row.get("image_refs") or []:
                results.append({"paper": stem, "question": row.get("question_number"),
                                "label": ref.get("label"), "ok": False,
                                "error": "prepare-failed:" + state["error"]})
            continue
        number = row.get("question_number")
        item = state["items"].get(number)
        if item is not None and number not in state["bands"]:
            state["bands"][number] = verify_crops.option_bands(state["kept"], item)
        for ref in row.get("image_refs") or []:
            box = ref.get("box")
            page = ref.get("page")
            entry = {"paper": stem, "question": number, "label": ref.get("label"),
                     "role": ref.get("asset_role"), "path": ref.get("path")}
            if not box or not page:
                entry.update({"ok": False, "error": "no-box-or-page"})
                results.append(entry)
                continue
            band = (state["bands"].get(number) or {}).get(ref.get("option_key") or "")
            outcome = verify_crops.verify_one(
                pdf, page, box, images=state["images"], band=band,
                next_question=state["next"].get(number))
            entry.update({"ok": outcome["complete"], "reasons": outcome["reasons"],
                          "siblings": outcome["siblings"],
                          "box": outcome["box"], "page": page})
            results.append(entry)
            if args.limit and len(results) >= args.limit:
                break
        if args.limit and len(results) >= args.limit:
            break

    incomplete = [r for r in results if not r.get("ok")]
    reasons = Counter()
    for row in incomplete:
        for reason in row.get("reasons") or []:
            reasons[reason.split(":")[0]] += 1
    report = {"checked": len(results), "incomplete": len(incomplete),
              "skipped": len([r for r in results if r.get("error")]),
              "reason_counts": dict(reasons),
              "rows": results}
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
    print(f"檢查 {len(results)} 張，{len(incomplete)} 張不完整，"
          f"{report['skipped']} 張無法判定 -> {args.out}")
    for stem, count in Counter(r["paper"] for r in incomplete).most_common(15):
        print(f"   {count:3}  {stem}")
    if reasons:
        print("   原因：", dict(reasons))


if __name__ == "__main__":
    main()
