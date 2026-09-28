#!/usr/bin/env python3
"""對照「手工裁圖參考庫」量 figure_questions 的召回，並逐題歸因每一個 miss。

參考庫＝設計者/前人已經人手裁好的資產（556 檔／462 題），是本流程唯一的圖片歸屬 ground truth。
本 probe 只讀，不寫任何資料。歸因方式：把圖物件、band、ownership 三個量分開印出來，
讓「這一題為什麼沒被看到」有可反駁的證據，而不是一句推測。

用法：
  qbr/.venv/bin/python repair_agent_test/probes/measure_ref_recall_causes.py \
      --map /tmp/d14/ref_pdf_map.json --missing /tmp/d14/ref_recall.json
"""
import sys, os, json, argparse, collections
sys.path.insert(0, 'qbr/src'); sys.path.insert(0, 'qbr/scripts')
from qbr import extract, vision, repair, reflow
import compare_skeleton

parser = argparse.ArgumentParser()
parser.add_argument('--map', default='/tmp/d14/ref_pdf_map.json')
parser.add_argument('--missing', default='/tmp/d14/ref_recall.json')
parser.add_argument('--out', default='/tmp/d14/ref_causes.json')
args = parser.parse_args()

pdf_map = json.load(open(args.map))
missing = json.load(open(args.missing))['missing']

cache = {}
def build(pdf):
    if pdf in cache:
        return cache[pdf]
    rows = extract.extract_cells_a(pdf); kept, _ = repair.mask_chrome(rows)
    table, _, alphabet = reflow.cells_with_pages(kept)
    items = compare_skeleton.skeleton_items(reflow.skeleton(table), table, alphabet)
    images = extract.extract_images_a(pdf)
    found = {e['number']: e for e in vision.figure_questions(items, kept, images)}
    cache[pdf] = (kept, items, images, found)
    return cache[pdf]

report = []
for key in missing:
    pdf = pdf_map.get(key)
    if not pdf or not os.path.exists(pdf):
        report.append({"key": key, "cause": "no_pdf"}); continue
    num = int(key.split(':q')[-1])
    kept, items, images, found = build(pdf)
    it = next((x for x in items if x['number'] == num), None)
    if it is None:
        report.append({"key": key, "pdf": os.path.basename(pdf), "num": num,
                       "cause": "no_item"}); continue
    ids = set(vision.cell_ids_of(it))
    cells = [r for i, r in enumerate(kept, 1) if i in ids]
    pages = sorted({int(r['page']) for r in cells})
    y0 = min(float(r['y0']) for r in cells); y1 = max(float(r['y1']) for r in cells)
    # 所有圖物件（不受 24pt 門檻限制），看這一題附近有沒有物件
    boxes = [(int(pg), bx) for pg, bx in vision.picture_boxes(images, min_image_height=0.0)]
    near = [bx for pg, bx in boxes if pg in pages and y1 + 60 >= bx[1] and bx[3] >= y0 - 60]
    big = [bx for bx in near if float(bx[3]) - float(bx[1]) >= vision.MIN_FIGURE_HEIGHT]
    # 誰拿走了最近的那張大圖
    taken_by = None
    if big:
        b = min(big, key=lambda bx: abs(((float(bx[1]) + float(bx[3])) / 2) - ((y0 + y1) / 2)))
        for n, e in found.items():
            if all(abs(a - c) < 1 for a, c in zip(e['box'], b)):
                taken_by = n
    opts = vision.option_texts(it)
    report.append({
        "key": key, "pdf": os.path.basename(pdf), "num": num,
        "pages": pages, "y": [round(y0, 1), round(y1, 1)],
        "option_count": len(opts),
        "option_kind": "EMPTY" if not opts else ("MARKER-ONLY" if all(not o.strip() for o in opts) else "text"),
        "objects_near": len(near), "big_near": len(big),
        "taken_by": taken_by,
        "cause": ("no_picture_object" if not big else
                  ("neighbor_stole" if taken_by is not None else "picture_unowned")),
    })

stats = collections.Counter(r['cause'] for r in report)
print(json.dumps(stats, ensure_ascii=False, indent=1))
json.dump(report, open(args.out, 'w'), ensure_ascii=False, indent=1)
for r in report:
    if r.get('cause') not in ('no_pdf',):
        print("  q%-3s p%-8s y%-14s opts=%-11s near=%-2s big=%-2s taken_by=%-4s %s" % (
            r.get('num'), r.get('pages'), r.get('y'), r.get('option_kind'),
            r.get('objects_near'), r.get('big_near'), r.get('taken_by'), os.path.basename(r.get('pdf',''))[:40]))
