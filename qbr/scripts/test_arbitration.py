#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure which model arbitrates a dispute best, against ground truth.

The point of arbitration is narrow and worth stating, because a vague "ask the model" experiment
produces a vague answer:

    A dispute is a question the pipeline could not settle. Arbitrating it means the model reads the
    **page**, not the extracted text, and says what the reading should have been. The reviewer
    compares that against the paper.

So the measurement needs three things, and this script exists to provide all three:

1. **A dispute with a known answer.** The `dangling-answer` class is used: the paper prints its four
   options as the marks `\ue18c\ue18d\ue18e\ue18f`, the segmenter left them in the stem, and the
   question therefore read as having no options while the answer sheet names a letter. The right
   arbitration is "the marks `\ue18c`..`\ue18f` are the four option markers", which is checkable.
2. **A negative control.** A question whose options *were* read correctly, shown to the same model
   with the same prompt. A model that reports four marks on everything - including the control -
   is not reading, and its recall on the disputed set means nothing. This is the failure the crop
   audit already made (`audit_crops.py`: 80/80 `same=true`, and the positive control still said
   `same=true` when shown only the top 8 pt), so the control is not optional here.
3. **A fixed question**, so the comparison is between models and not between prompts.

Usage:

    # every engine, the default 10 disputed + 10 control
    .venv/bin/python scripts/test_arbitration.py --out /tmp/arb.json

    # one engine
    QBR_MODEL_BASE_URL=http://127.0.0.1:8082 QBR_MODEL_NAME=... \
      .venv/bin/python scripts/test_arbitration.py --out /tmp/arb-27b.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

from qbr import extract, paths, repair, reflow, vision  # noqa: E402

# An environment override stays first (an operator pointing at another corpus is the authority), but
# the default is now **found** instead of written down. A hard-coded home directory is correct in
# exactly one checkout, which is the one it was written in.
CORPUS = os.environ.get(
    "QBR_CORPUS",
    os.path.join(paths.asset_root(), "10_official_pdf", "by_official_catalog"))

#: The four private-use marks this paper family prints as its option labels. A question using them
#: has four options whatever the text layer did with them.
OPTION_MARKS = ("\ue18c", "\ue18d", "\ue18e", "\ue18f")

#: The one question, asked identically of every model. It names the answer shape so a reply can be
#: scored, and it explicitly allows "I cannot tell" - a model that never abstains is not measuring
#: its own confidence, and on this task abstaining is sometimes correct (the control's options are
#: printed as ordinary letters, not as marks).
ARBITRATION_SYSTEM = """你在協助校對一份台灣國家考試題目卷。

系統會給你一張局部截圖。這張截圖裡可能是：
(A) 一個題目的題幹與它的選項；
(B) 只有題幹，選項是圖（四張圖分別是 A、B、C、D）；
(C) 只有題幹，選項的文字被系統吃掉了。

請只回答你在圖上**實際看到**的東西，不要推測、不要補充。輸出 JSON：

{
  "question_number": 12,                   // 圖上第一個題號，只寫數字；看不到就 null
  "option_labels": ["A", "B", "C", "D"],   // 這題的選項標記，依出現順序
  "labels_are_pictures": true/false,       // 選項是不是四張圖
  "evidence": "你根據什麼判斷（一句話，指到圖上的東西）"
}

規則：
- `question_number` 只看**最上面那個題號**，不要看下面的。
- 只列出你**真的看到**的標記。看不到任何選項標記就回空陣列 []。
- `A` `B` `C` `D` 這種普通字母與 `\ue18c` `\ue18d` 這種罕見符號都算標記，只要你在圖上看到。
- 不確定就不要列，寧可少列。"""


def option_label_set(marks):
    """The mark characters a string contains, as a set."""
    return {mark for mark in OPTION_MARKS if mark in (marks or "")}


def paper_path(relative):
    root = CORPUS
    for base, _dirs, files in os.walk(root):
        for name in files:
            if name == relative:
                return os.path.join(base, name)
    return None


def build_cases(queue_path, limit=10):
    """Three classes, and the third is what makes the other two mean anything.

    A **disputed** case is a `dangling-answer`: the answer sheet names a letter and no option was
    read, with the four marks still sitting in the stem. The right arbitration is "all four marks are
    present", so the ground truth is four labels.

    A **control** case is a question whose four options were read normally. Its ground truth is also
    four labels, and on its own it proves very little - "four" is the most common answer in the
    corpus, so a model that always says four scores 100% on both of these classes.

    A **stem-only** case is the control that can fail: the crop holds the question's stem and stops,
    so the correct answer is **no labels at all**. This is the case the crop audit learned to
    demand - the positive control there (only the top 8 pt of a structure) still drew `same=true`,
    and it was that case, not the 80 known-broken ones, that showed the model was not looking.

    A **neighbour** case is the one that discriminates, because the first three do not: measured on
    2026-09, all three engines in this shop scored 54/54 on them, so the classes were saturated and
    "which model is best" had no answer. A neighbour crop is the question's own band **plus the next
    question's stem and options**, and the ground truth is still this question's four labels. It
    tests the thing a model that pattern-matches would get wrong: reporting what is on the page
    rather than what belongs to the question being asked about.
    """
    disputed, control, stem_only, neighbour = [], [], [], []
    rows = [json.loads(line) for line in open(queue_path, encoding="utf-8") if line.strip()]
    by_paper = {}
    for row in rows:
        rel = (row.get("metadata") or {}).get("question_pdf_relative")
        if rel:
            by_paper.setdefault(rel, []).append(row)

    for rel, paper_rows in by_paper.items():
        for row in paper_rows:
            found = row.get("disputes") or []
            kinds = {d.get("kind") for d in found}
            if "dangling-answer" in kinds:
                marks = option_label_set(row.get("stem"))
                if len(marks) >= 3 and len(disputed) < limit:
                    disputed.append({"kind": "disputed", "row": row, "relative": rel,
                                     "expected": sorted(OPTION_MARKS[:len(marks)]),
                                     "stem_only": False})
            elif (len(row.get("options") or []) == 4 and not found
                  and not row.get("image_refs")):
                if len(control) < limit:
                    control.append({"kind": "control", "row": row, "relative": rel,
                                    "expected": list(OPTION_MARKS), "stem_only": False})
                else:
                    if len(stem_only) < limit:
                        # Same question, cropped to the stem alone. The crop is a measurement, so the
                        # negative control is the identical question with a different rectangle -
                        # which removes the question's content as an explanation for any difference.
                        stem_only.append({"kind": "stem-only", "row": row, "relative": rel,
                                          "expected": [], "stem_only": True})
                    elif len(neighbour) < limit:
                        neighbour.append({"kind": "neighbour", "row": row, "relative": rel,
                                          "expected": list(OPTION_MARKS), "stem_only": False,
                                          "with_next": True})
        if (len(disputed) >= limit and len(control) >= limit and len(stem_only) >= limit
                and len(neighbour) >= limit):
            break
    return disputed + control + stem_only + neighbour


def _stitch(crops):
    """Several page regions as one image, stacked with a separator.

    Needed because a question is not bounded by the page: measured in this corpus, a stem can sit at
    the foot of one page and its options at the head of the next, and a crop that keeps only the
    first page does not merely lose information - it **manufactures a false negative control**. On
    `1152_藥師(一)_藥學(一)` Q5 the model answered `[]` and was marked wrong, but the crop genuinely
    held only the stem; the model was right and the harness was wrong. Stacking keeps the whole
    question, and the separator makes the page break visible so the model does not read the join as
    a continuation of a line.
    """
    if len(crops) == 1:
        return crops[0]
    import io
    from PIL import Image
    images = [Image.open(io.BytesIO(blob)).convert("RGB") for blob in crops]
    width = max(image.width for image in images)
    gap = 14
    height = sum(image.height for image in images) + gap * (len(images) - 1)
    sheet = Image.new("RGB", (width, height), "white")
    top = 0
    for index, image in enumerate(images):
        sheet.paste(image, (0, top))
        top += image.height
        if index + 1 < len(images):
            for y in range(top + 2, top + gap - 2):
                for x in range(0, width, 8):
                    sheet.putpixel((x, y), (150, 150, 150))
            top += gap
    buffer = io.BytesIO()
    sheet.save(buffer, format="PNG")
    return buffer.getvalue()


def crop_of(case):
    """The page band for one case: the question's own space on the page, full width.

    The band runs from the top of the question to the top of the next one, across the whole text
    column - not just the cells the skeleton assigned to this question. That is deliberate and it is
    the third correction this harness needed, each time because the model was right and the crop was
    wrong:

    * `1152_藥師(一)_藥學(一)` Q5: stem at the foot of a page, options overleaf. Fixed by stacking
      the pages (see `_stitch`).
    * `1001_醫事檢驗師_臨床血液學與血庫學` Q6: a stem with four picture-options, printed two per
      row. The skeleton assigned only the *left* column's labels to the question, so a crop built
      from its cells held (A) and (B) and cut (C) and (D) - the model answered "A and B" and was
      scored wrong for reading the image accurately.

    So the crop is a property of the **page**, not of the parse: the same rectangle a person would
    draw around the question. The parse is what is being tested, and letting it decide what the test
    can see makes the test agree with the parse by construction.

    `stem_only` still cuts to the stem rows alone, because that is the negative control: a crop with
    no option labels on it, where the right answer is none.
    """
    path = paper_path(os.path.basename(case["relative"]))
    if not path:
        return None, "paper-not-found"
    row = case["row"]
    try:
        kept, _ = repair.mask_chrome(extract.extract_cells_a(path))
        text, _pages, _alpha = reflow.cells_with_pages(kept)
        skeleton = reflow.skeleton(text)
        number = int(row["question_number"])
        questions = skeleton.get("questions") or {}
        cells = questions.get(number) or {}
        ids = list(cells.get("stem") or [])
        by_id = dict(enumerate(kept, start=1))
        stem_rows = [by_id[i] for i in ids if i in by_id]
        if not stem_rows:
            return None, "no-rows"
        if case.get("stem_only"):
            chosen = stem_rows
        else:
            # The band is vertical and runs to the **next question's stem**, not to the end of this
            # question's parsed cells. That is what makes it independent of the parse: a question
            # whose options were never parsed still gets its whole space on the page, which is
            # exactly the case being tested. It spans the page break too, because a stem at the foot
            # of a page has its options overleaf - and it **stops** at the next stem, so the band
            # never swallows the following questions.
            following = {}
            for other, body in questions.items():
                if int(other) <= number:
                    continue
                first = next((by_id[i] for i in (body.get("stem") or []) if i in by_id), None)
                if first is not None:
                    following[int(other)] = first
            stem_page = stem_rows[0]["page"]
            top = min(r["y0"] for r in stem_rows if r["page"] == stem_page)
            end_page, end_bottom = None, None
            # `with_next` pushes the end past the following question's own band, so its labels are
            # inside the picture and a model that reports everything it sees is caught.
            ordered = sorted(following)
            for index, other in enumerate(ordered):
                first = following[other]
                if first["page"] < stem_page:
                    continue
                if case.get("with_next"):
                    nxt = ordered[index + 1] if index + 1 < len(ordered) else None
                    if nxt is None:
                        end_page, end_bottom = first["page"], None
                    else:
                        after = following[nxt]
                        end_page = after["page"]
                        end_bottom = float(after["y0"]) if after["page"] == first["page"] else None
                        if after["page"] != first["page"]:
                            end_page = after["page"]
                            end_bottom = float(after["y0"])
                else:
                    end_page, end_bottom = first["page"], float(first["y0"])
                break
            chosen = []
            for entry in kept:
                page = entry["page"]
                y0 = float(entry["y0"])
                if page < stem_page:
                    continue
                if end_page is not None and page > end_page:
                    continue
                if page == stem_page and y0 < top - 2:
                    continue
                if end_page is not None and page == end_page and y0 >= end_bottom - 1:
                    continue
                chosen.append(entry)
        by_page = {}
        for entry in chosen:
            by_page.setdefault(entry["page"], []).append(entry)
        # The width comes from the text cells **and from the picture objects that share the band's
        # vertical span**. Without the pictures the band is only as wide as the text column, and on
        # `1001_醫事檢驗師_臨床血液學與血庫學` Q6 the four picture-options are laid out two per row
        # out to x=556 while the stem ends at x=446 - so a text-only band cut off (C) and (D), and
        # the model answered "A and B" and was scored wrong for reading the image accurately. That
        # is the fourth time in this one harness that the model was right and the crop was wrong,
        # which is the reason the harness carries all three classes: a measurement rig is as likely
        # to be the defect as the thing it measures.
        pictures = extract.extract_images_a(path)
        if case.get("stem_only"):
            pictures = []
        crops = []
        for page in sorted(by_page):
            same = by_page[page]
            top = min(r["y0"] for r in same) - 6
            bottom = max(r["y1"] for r in same) + 6
            x0 = min(r["x0"] for r in same) - 6
            x1 = max(r["x1"] for r in same) + 6
            for image in pictures:
                if int(image.get("page") or 0) != int(page):
                    continue
                left, image_top, right, image_bottom = (float(image["x0"]), float(image["y0"]),
                                                        float(image["x1"]), float(image["y1"]))
                if min(bottom, image_bottom) - max(top, image_top) <= 0:
                    continue
                x0 = min(x0, left - 6)
                x1 = max(x1, right + 6)
            crops.append(vision.crop_region(path, int(page), (x0, top, x1, bottom),
                                            dpi=vision.DEFAULT_DPI, margin=0.0))
    except Exception as exc:                                   # noqa: BLE001
        return None, "measure-failed:%s" % exc
    return _stitch(crops), None


def parse_labels(verdict):
    """The labels a verdict claims, normalised to `A/B/C/D` letters.

    A model shown `\ue18c` may reasonably write either the mark or the letter, and both are the same
    claim about the paper, so both are accepted as the same answer.
    """
    if not isinstance(verdict, dict):
        return None
    raw = verdict.get("option_labels")
    if not isinstance(raw, list):
        return None
    out = []
    for value in raw:
        text = str(value)
        for index, mark in enumerate(OPTION_MARKS):
            if mark and mark in text:
                out.append(OPTION_MARKS[index])
                break
        else:
            match = re.search(r"[ABCD]", text.upper())
            if match:
                out.append(OPTION_MARKS["ABCD".index(match.group(0))])
    return sorted(set(out))


def run_case(case, *, think, budget):
    png, error = crop_of(case)
    if png is None:
        return {"error": error}
    started = time.time()
    result = vision._ask([
        {"role": "system", "content": ARBITRATION_SYSTEM},
        vision._image_message(png, "請看這張截圖並輸出 JSON。"),
    ], think=think, max_tokens=budget, expect=("option_labels",))
    parsed, raw, complaint, usage, _seconds = result
    labels = parse_labels(parsed)
    # The question number is scored as well, and on the **neighbour** class it is the only field that
    # can discriminate: option labels repeat across questions, so a crop holding two questions still
    # yields the set {A,B,C,D} and a labels-only check cannot tell "read the right question" from
    # "read the next one" - measured: all three engines scored a perfect 54/54 on the labels alone,
    # which is a saturated test and answers nothing. The number is the field that breaks the tie.
    seen = None
    if isinstance(parsed, dict):
        value = parsed.get("question_number")
        if isinstance(value, bool):
            value = None
        if isinstance(value, (int, float)):
            seen = int(value)
        elif isinstance(value, str):
            match = re.search(r"\d+", value)
            seen = int(match.group(0)) if match else None
    return {"verdict": parsed, "raw": (raw or "")[:400], "complaint": complaint,
            "usage": usage, "seconds": round(time.time() - started, 1),
            "bytes": len(png), "labels": labels, "seen_number": seen}


def main():
    parser = argparse.ArgumentParser(description="Which model arbitrates best.")
    parser.add_argument("--queue", default="/tmp/qbr-live-sf9/review-ui/candidates.jsonl")
    parser.add_argument("--out", default="/tmp/arbitration.json")
    parser.add_argument("--limit", type=int, default=10, help="per class")
    parser.add_argument("--budget", type=int, default=8000)
    parser.add_argument("--think", action="store_true")
    parser.add_argument("--reps", type=int, default=1, help="a single pass is not a pass")
    args = parser.parse_args()

    cases = build_cases(args.queue, limit=args.limit)
    disputed = [c for c in cases if c["kind"] == "disputed"]
    control = [c for c in cases if c["kind"] == "control"]
    stem_only = [c for c in cases if c["kind"] == "stem-only"]
    neighbour = [c for c in cases if c["kind"] == "neighbour"]
    print("模型：%s   base=%s" % (vision.MODEL, vision.BASE_URL))
    print("案例：爭議 %d　對照 %d　只有題幹 %d　含下一題 %d　thinking=%s  budget=%d  reps=%d"
          % (len(disputed), len(control), len(stem_only), len(neighbour), args.think,
             args.budget, args.reps), flush=True)

    records = []
    for case in cases:
        expected = case["expected"]
        target = int(case["row"]["question_number"])
        for rep in range(args.reps):
            got = run_case(case, think=args.think, budget=args.budget)
            # Two things must be right, not one. The labels say what the model found; the number
            # says it found it on the right question. On `stem-only` the number is still checkable -
            # the stem carries it - so every class is scored the same way and no class is let off.
            correct = (got.get("labels") == expected and got.get("seen_number") == target)
            records.append({"kind": case["kind"], "question_number": target,
                            "paper": os.path.basename(case["relative"]),
                            "expected": expected, "rep": rep, "correct": correct,
                            "number_ok": got.get("seen_number") == target, **got})
            mark = "ok " if correct else "!! "
            print("  %s%-9s Q%-3s 期望 %d 得到 %s  題號 %s (%ss)"
                  % (mark, case["kind"], target, len(expected), got.get("labels"),
                     got.get("seen_number"), got.get("seconds")), flush=True)

    def score(rows):
        if not rows:
            return {"n": 0}
        good = sum(1 for r in rows if r["correct"])
        abstained = sum(1 for r in rows if r.get("labels") == [])
        empty = sum(1 for r in rows if not r.get("labels"))
        return {"n": len(rows), "correct": good, "accuracy": round(good / len(rows), 3),
                "abstained": abstained, "no_answer": empty,
                "number_ok": sum(1 for r in rows if r.get("number_ok")),
                "median_seconds": sorted(r.get("seconds") or 0 for r in rows)[len(rows) // 2]}

    summary = {"model": vision.MODEL, "base_url": vision.BASE_URL, "think": args.think,
               "budget": args.budget, "reps": args.reps,
               "disputed": score([r for r in records if r["kind"] == "disputed"]),
               "control": score([r for r in records if r["kind"] == "control"]),
               "stem_only": score([r for r in records if r["kind"] == "stem-only"]),
               "neighbour": score([r for r in records if r["kind"] == "neighbour"])}
    # The one number that says whether the model is reading: on `stem-only` the correct answer is
    # no labels, so `abstained / n` should be 1.0. Anything less means it is inventing marks, and
    # the accuracy figures above it are then measuring the shape of the question rather than the
    # page. It is reported separately and never folded into an average, because averaging it in
    # would let strong performance on the easy classes hide a model that never looks.
    summary["reads_the_page"] = (summary["stem_only"].get("n", 0) > 0
                                and summary["stem_only"].get("abstained", 0)
                                == summary["stem_only"].get("n"))
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump({"summary": summary, "records": records}, handle, ensure_ascii=False, indent=1)
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=1))
    print("-> %s" % args.out)


if __name__ == "__main__":
    main()
