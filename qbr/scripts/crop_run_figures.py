# -*- coding: utf-8 -*-
"""Cut the figures out of a packaged run, so a reviewer can see what was read.

The figure stage produces evidence and nothing else, which means the evidence has to be *visible*
or the claim that it works cannot be checked. This writes, for one packaged run, a crop per
figure-bearing question into the run's own `review-ui/crops/` directory and records the file name
on the candidate row's `image_refs`, which is the field the Review UI already knows how to show.

Two things are deliberately not done here:

    it does not re-read the paper's structure. The run already holds the questions, and the cells
    each question owns are recoverable from the package's own lineage, so a crop is cut for a
    question that was *measured* during packaging - not for one this script guesses at.

    it does not apply anything. A crop and its description are recorded beside the question, and
    the reviewer decides, which is the same position the text side takes (`GOV-05`).

The cost is bounded by the measurement: 319 questions in the whole medical-technologist range,
about 7 seconds a paper, with no reasoning tokens.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qbr import extract, repair, reflow, review_queue, vision  # noqa: E402

CORPUS = None


def paper_path_of(category, run_name):
    """The question paper a run was built from, found by its own name.

    The name is `1152_醫事檢驗師_生物化學與臨床生化學`, which is the paper's identity in the
    corpus and is what the run directory is called, so the search is exact rather than a guess.
    """
    head = run_name.split("_", 1)[0]
    if len(head) < 4 or not head[:3].isdigit():
        return None
    year, ordinal = head[:3], head[3]
    import batch_package
    for paper in batch_package.paper_pdfs(category):
        if paper["name"][:-4] == run_name:
            return paper["path"]
        if (str(paper["year"]) == year and str(paper["ordinal"]) == ordinal
                and os.path.basename(paper["path"])[:-4] == run_name):
            return paper["path"]
    return None


def crops_for_run(run_dir, paper_path, *, subject="", out_dir, think=False, limit=0,
                  describe=True):
    """Cut a crop for every figure-bearing question of one run. Returns `{candidate_key: [file]}`."""
    rows = extract.extract_cells_a(paper_path)
    kept, _ = repair.mask_chrome(rows)
    table, _, alphabet = reflow.cells_with_pages(kept)
    import compare_skeleton
    items = compare_skeleton.skeleton_items(reflow.skeleton(table), table, alphabet)
    images = extract.extract_images_a(paper_path)
    found = vision.figure_questions(items, kept, images, alphabet=alphabet)
    if limit:
        found = found[:limit]
    if not found:
        return {}, []

    candidates_path = os.path.join(run_dir, "review-ui", "candidates.jsonl")
    with open(candidates_path, encoding="utf-8") as handle:
        candidates = [json.loads(line) for line in handle if line.strip()]
    by_number = {row.get("question_number"): row for row in candidates}
    by_item = {item["number"]: item for item in items}

    os.makedirs(out_dir, exist_ok=True)
    refs, records = {}, []
    for entry in found:
        number = entry["number"]
        candidate = by_number.get(number)
        if candidate is None:
            records.append({"number": number, "error": "no-candidate-row"})
            continue
        # A question whose options are pictures gets one crop per option, because the answer slot
        # has to show the option the key names. This is the reference bank's model: it files those
        # as `__option_image_00N` with an `option_key`, and 98 of its 556 assets are of that kind.
        option_crops = []
        item = by_item.get(number)
        if item is not None:
            # The last option has no following marker, so its band is bounded by where the next
            # question begins - which `figure_questions` already measured for this entry.
            option_crops = vision.option_figures(item, kept, images,
                                                 limit=entry.get("next_start"))
        # The file name carries the question number and the reason, so a reviewer looking at a
        # directory of crops can tell what each one is for without opening the JSON.
        reason = "+".join(entry["reasons"])
        made = []
        if option_crops:
            # Each option is taken as its own picture *object* when the page has one (995 of 1,067
            # option slots measured over the corpus), because that is the picture's own boundary
            # and the only crop that cannot carry a neighbouring line of text. A slot whose object
            # cannot be read falls back to a rendered region of the page.
            for option in option_crops:
                key = option["key"]
                # An option crop is rendered from the page, not served as the picture's own bytes.
                #
                # Serving the object bytes is tempting - it is the picture's exact boundary and
                # cannot carry a neighbouring line of text - but it leaves the crop **without the
                # option's marker and without the option's own text**. Measured over the corpus:
                # 866 crops were raw object bytes and 183 were rendered regions, so a reviewer
                # looking down one question saw some options labelled and some not, and on
                # `1152_藥師(一)_藥學(一)` Q53 the four drug names appeared in none of them because
                # they are printed beside the structure rather than inside it. One standard is
                # worth the few pixels of margin: `box` is already the picture joined with the
                # rows the skeleton gave this option, so a render of it holds both.
                blob = vision.crop_region(paper_path, option["page"], option["box"],
                                          dpi=vision.DEFAULT_DPI, margin=0.0)
                suffix, source = ".png", "page-region"
                if not blob:
                    continue
                name = f"q{number:03d}_option_{key}{suffix}"
                with open(os.path.join(out_dir, name), "wb") as handle:
                    handle.write(blob)
                made.append({"path": os.path.join(out_dir, name), "raw_ref": name,
                             "asset_role": "option-image", "option_key": key,
                             "source": source, "page": option["page"],
                             "box": [round(float(value), 1) for value in option["box"]],
                             "strips": option.get("strips"),
                             "bytes": len(blob)})
        # An object can be placed on the page and never drawn: measured on
        # `1022_醫事檢驗師_臨床血液學與血庫學` page 2, where `xref` 24 is placed at y=393.9-462.9 and
        # the page renders pure white there while the object's own pixels hold an option list. Seven
        # questions came back as figure questions on boxes that show nothing. So the render is
        # checked before the crop is kept - which is the only way to tell "there is a picture here"
        # from "there is an object here".
        region = vision.figure_region(
            entry, exclude=[o["box"] for o in option_crops])
        ink = (vision.rendered_ink(paper_path, entry["page"], region)
               if region else None)
        if ink is not None and ink < vision.MIN_INK:
            # The row is written as holding *no* pictures. Leaving it alone would keep whatever the
            # previous run put there, and the queue would then serve a reference to a file this run
            # deliberately did not write - which is how 48 missing files appeared.
            refs[candidate["candidate_key"]] = []
            records.append({"number": number, "reasons": entry["reasons"], "page": entry["page"],
                            "box": [round(value, 1) for value in entry["box"]],
                            "file": None, "bytes": 0, "ink": round(ink, 5),
                            "blank_render": True,
                            "option_images": []})
            continue
        # When the option pictures together ARE the figure, one crop of the question is the option
        # crops stacked, and showing both makes the reviewer compare two renderings of the same
        # objects. The option crops stay; the stacked copy is dropped.
        #
        # `no-figure` is the same finding reached from the other side: the entry's pictures are all
        # option pictures, so `exclude` removed every one of them and there is nothing left to cut.
        # It is not an error - the option crops carry the whole figure - and recording it as one
        # would turn 98 questions that are correctly served into a failure report.
        covered = vision.options_cover_the_figure(entry, option_crops)
        png, page = ((None, None) if covered
                     else vision.crop_figure(entry, paper_path,
                                             exclude=[o["box"] for o in option_crops]))
        if png is None and not covered and page != "no-figure":
            records.append({"number": number, "error": page, "reasons": entry["reasons"]})
            continue
        path = name = None
        if png is not None:
            name = f"q{number:03d}_{reason}.png"
            path = os.path.join(out_dir, name)
            with open(path, "wb") as handle:
                handle.write(png)
        # Cutting the crop is a measurement of the page; asking a model what it contains is a
        # reading of it. They are separable, and separating them matters: a re-cut after a fix to
        # the geometry has no reason to re-ask the model about pictures that were already
        # described, and paying for 1,100 readings to move a box is how a rebuild becomes
        # something nobody runs. `--no-describe` re-cuts and leaves the reading to a later pass.
        #
        # And when the option pictures ARE the figure (`covered`), there is no crop to ask about:
        # `png` is None, and passing it to the model is a crash, not a reading. The pictures the
        # reviewer sees are the option crops, and their labels already say so.
        verdict = (vision.describe_crop(png, subject=subject, question=entry["stem"], think=think)
                   if (describe and png is not None) else {})
        parsed = verdict.get("verdict") or {}
        # The shape the Review UI reads: a list of dicts, each with an absolute `path` (the run is
        # a registered asset root, so it is servable) and the measured reason as its label. A bare
        # string is silently dropped by `candidate_visual_profile`, which would leave the crop on
        # disk and invisible - the exact failure this script exists to prevent.
        payload = []
        if path:
            payload.append({
                "path": path,
                "raw_ref": name,
                "asset_role": "figure-crop",
                "source": "qbr_vision_crop",
                "page": entry.get("page"),
                "box": [round(float(value), 1) for value in entry["box"]],
                "label": reason,
                "description": (f"第 {number} 題：{'、'.join(entry['reasons'])}"
                                + ("（選項跨頁）" if entry.get("split") else "")),
                "placement": "question",
            })
        for option in made:
            payload.append({
                "path": option["path"], "raw_ref": option["raw_ref"],
                "asset_role": "option-image", "option_key": option["option_key"],
                "source": option["source"], "page": option["page"],
                "box": option.get("box"), "strips": option.get("strips"),
                "label": f"選項 {option['option_key']}",
                "description": f"第 {number} 題選項 {option['option_key']}",
                "placement": "option",
            })
        refs[candidate["candidate_key"]] = payload
        records.append({
            "number": number, "reasons": entry["reasons"], "page": page,
            "option_images": [{"key": o["option_key"], "source": o["source"],
                               "page": o["page"], "file": os.path.basename(o["path"])}
                              for o in made],
            "box": [round(value, 1) for value in entry["box"]],
            "split": entry["split"], "option_pages": entry["option_pages"],
            "file": name, "bytes": len(png) if png else 0,
            "covered_by_options": covered,
            "contains": parsed.get("contains"), "confidence": parsed.get("confidence"),
            "describes": parsed.get("describes"),
            "readable_values": parsed.get("readable_values"),
            "uncertain": parsed.get("uncertain"),
            "axis_labels": parsed.get("axis_labels"),
            "error": verdict.get("error"),
            "raw_chars": len(verdict.get("raw") or ""),
            "seconds": verdict.get("seconds"), "usage": verdict.get("usage"),
        })
    return refs, records


def main() -> None:
    parser = argparse.ArgumentParser(description="Cut figure crops into packaged runs.")
    parser.add_argument("--work", nargs="+", required=True, help="batch_package work roots")
    parser.add_argument("--think", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-describe", action="store_true",
                        help="cut the crops without asking the model what they contain")
    parser.add_argument("--only", nargs="*", default=None, help="run directory names")
    parser.add_argument("--reannotate-only", action="store_true",
                        help="recompute disputes on the existing rows without re-cutting any crop; "
                             "for when a detector changed and the pictures did not")
    args = parser.parse_args()

    import batch_package
    totals = collections.Counter()
    for root in args.work:
        for name in sorted(os.listdir(root)):
            run_dir = os.path.join(root, name)
            if args.only and name not in set(args.only):
                continue
            candidates_path = os.path.join(run_dir, "review-ui", "candidates.jsonl")
            if not os.path.isfile(candidates_path):
                continue

            # `--reannotate-only`: a detector changed, the pictures did not.
            #
            # `image_refs` already records which options got a picture, and disputes are computed
            # **from the rows plus that field** (`review_queue.disputes_for_paper` reads `image_refs`
            # and needs no crop). So a detector change can be propagated by rewriting the rows
            # alone - re-cutting 3,500 crops to refresh a JSON field would be minutes of work for a
            # field that is already on disk. This path exists so that "the new rule reaches the
            # reviewer" does not require pretending the pictures changed too.
            if args.reannotate_only:
                with open(candidates_path, encoding="utf-8") as handle:
                    rows = [json.loads(line) for line in handle if line.strip()]
                before = collections.Counter(d.get("kind") for row in rows
                                             for d in (row.get("disputes") or []))
                # How many *rows* changed, before the rewrite computes the new disputes.
                #
                # Reported, not just counted internally: the first version of this path printed the
                # summary line's `更新候選列 0` while rewriting every row in 989 files, because the
                # counter is only incremented by the crop path. "0 rows updated" next to a detector
                # that had just started firing is the kind of summary that makes somebody re-run the
                # step to check whether it worked - so the number has to come from this path too.
                before_rows = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows]
                review_queue.disputes_for_paper(rows)
                after = collections.Counter(d.get("kind") for row in rows
                                            for d in (row.get("disputes") or []))
                changed = sum(1 for row, previous in zip(rows, before_rows)
                              if json.dumps(row, ensure_ascii=False, sort_keys=True) != previous)
                temporary = candidates_path + ".partial"
                with open(temporary, "w", encoding="utf-8") as handle:
                    for row in rows:
                        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                os.replace(temporary, candidates_path)
                delta = {kind: after.get(kind, 0) - before.get(kind, 0)
                         for kind in set(before) | set(after)
                         if after.get(kind, 0) != before.get(kind, 0)}
                totals["runs"] += 1
                totals["changed-rows"] += changed
                # The per-run line is printed only when something moved, so a 989-run pass reads as
                # the handful of papers the new detector actually touched.
                if changed or delta:
                    print(f"  {name[:52]:54} 列 {len(rows):>3}  改 {changed:>3}  差 {delta or '無'}",
                          flush=True)
                continue

            category = name.split("_")[1] if len(name.split("_")) > 1 else ""
            paper = paper_path_of(category, name)
            if paper is None:
                totals["no-paper"] += 1
                print(f"  {name[:52]:54} 找不到對應 PDF", flush=True)
                continue
            subject = reflow_subject(name)
            out_dir = os.path.join(run_dir, "review-ui", "crops")
            refs, records = crops_for_run(run_dir, paper, subject=subject, out_dir=out_dir,
                                          think=args.think, limit=args.limit,
                                          describe=not args.no_describe)
            if not records:
                continue
            # `image_refs` is written back into the candidate rows, which is what makes the crops
            # visible in the UI. The rows are rewritten in place because the run is the unit the
            # UI serves, and a second file beside it would be a second thing to keep in step.
            with open(candidates_path, encoding="utf-8") as handle:
                rows = [json.loads(line) for line in handle if line.strip()]
            changed = 0
            for row in rows:
                # Every row is rewritten from this run's own result, including the rows that end up
                # with no pictures. Setting only the ones that found something leaves an earlier
                # run's reference in place, and once that file is no longer written the queue serves
                # a path to nothing - measured at 48 such references across 18 papers, all of them
                # left behind by a run that had found a crop the current run correctly rejects.
                want = refs.get(row["candidate_key"], [])
                if row.get("image_refs") != want:
                    row["image_refs"] = want
                    changed += 1
            # Disputes are computed **here** and not at packaging time, because one of the checks
            # needs the option pictures: an option with no text is usually correct - the option IS
            # the picture - and only the crop run knows which options got one. Measured: doing this
            # earlier raised 274 empty-option disputes to find the 23 that are real. `crop_run_figures`
            # is the last stage that touches `image_refs`, so it is the first moment the question
            # "is this option empty *and* imageless" can be answered.
            review_queue.disputes_for_paper(rows)
            temporary = candidates_path + ".partial"
            with open(temporary, "w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            os.replace(temporary, candidates_path)
            with open(os.path.join(run_dir, "review-ui", "figures.json"), "w",
                      encoding="utf-8") as handle:
                json.dump({"paper": name, "subject": subject, "pdf": paper,
                           "figures": records}, handle, ensure_ascii=False, indent=2)
            kinds = collections.Counter(r.get("contains") or r.get("error") or "?"
                                        for r in records)
            totals["runs"] += 1
            totals["figures"] += len(records)
            totals["changed-rows"] += changed
            print(f"  {name[:52]:54} 圖 {len(records):>2}  {dict(kinds)}", flush=True)

    print(f"\n=== 圖片裁切")
    print(f"  卷 {totals['runs']}   圖 {totals['figures']}   更新候選列 {totals['changed-rows']}"
          f"   找不到 PDF {totals['no-paper']}")
    if args.reannotate_only:
        print("  （--reannotate-only：只重算偵測結果，沒有重切任何圖）")
        print("   （列上的 disputes 由列自己算出來，圖片沒有參與，所以不需要重切）")


def reflow_subject(run_name):
    parts = run_name.split("_")
    return "_".join(parts[2:]) if len(parts) > 2 else ""


if __name__ == "__main__":
    main()
