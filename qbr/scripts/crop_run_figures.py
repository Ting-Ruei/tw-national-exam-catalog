# -*- coding: utf-8 -*-
"""Cut the figures out of a packaged run, and the tables a queue's readings asked for.

The figure stage produces evidence and nothing else, which means the evidence has to be *visible*
or the claim that it works cannot be checked. This writes, for one packaged run, a crop per
figure-bearing question into the run's own `review-ui/crops/` directory and records the file name
on the candidate row's `image_refs`, which is the field the Review UI already knows how to show.

`--queue` is the second half, and it exists because a table cannot be cut at packaging time. A
figure is a measurement of the page and can be found before anyone has read the question; a table is
*meaning* - a run of printed lines is a table only if the reading says so - and the reading of a
question (`confirm_dispute.py`'s transcription, stored in the queue's
`review-ui/question_ai_findings.jsonl` as `finding["transcription"]`) exists only in a queue, after
a reviewer's block has had a question read. So the queue mode cuts the crop the reading asked for:
the quoted lines are located among the page's measured lines (`vision.quoted_lines_region`) and the
band they cover is rendered through the same `vision.crop_figure` the figures use, with the option
boxes excluded. A question whose reading reported no table is left exactly as it was.

Two things are deliberately not done here:

    it does not re-read the paper's structure. The run already holds the questions, and the cells
    each question owns are recoverable from the package's own lineage, so a crop is cut for a
    question that was *measured* during packaging - not for one this script guesses at. The queue
    mode is the same rule from the other side: the cells are measured (`re-read` from the paper,
    because a queue has no package lineage) and the *table* is the reading's.

    it does not apply anything. A crop and its description are recorded beside the question, and
    the reviewer decides, which is the same position the text side takes (`GOV-05`).

The cost is bounded by the measurement: 319 questions in the whole medical-technologist range,
about 7 seconds a paper, with no reasoning tokens. The queue mode pays per paper whose reading asked
for a table, and it makes no model call at all.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import io
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qbr import ai_findings, extract, repair, reflow, reread, review_queue, vision  # noqa: E402
# 讀審題者的話的那一支（`ask_about_blocks`，走 `qbr.engines` 的同一個 lane）：`--human-flagged`
# 的圖版那一趟用它把「這一題沒有圖／多截圖／範圍錯了」轉成一個標籤。放在同一個門，因為
# 「怎麼讀他的話」只能有一份（兩份就是兩個答案）。
import ask_about_blocks  # noqa: E402

CORPUS = None

#: The label a table crop carries, and the key the Review UI reads to decide that a row's flattened
#: table text is replaced by the paper's own table. The crop's `asset_role` stays `figure-crop`,
#: because that is the same kind of thing - a crop of the paper attached to the question - and a
#: second role would be a second thing for every reader of `image_refs` to learn. What distinguishes
#: it is *why* it exists, which is what `label` already means on a crop (`embedded-image`,
#: `options-without-text`), and `table_lines` carries the reading's own lines with it so the UI can
#: say which part of the stem it is standing in for.
TABLE_REASON = "paper-table"


def band_extents(rows, number):
    """這一題自己的列在紙本上的垂直範圍：`{page: (y0, y1)}`。

    The same measurement the table pass searches in (`reread.band_rows`): the rows the skeleton gave
    this question, on the pages it sits on. It is what "this question's own part of the page" means,
    and it is the only thing that tells a picture belonging to this question from one belonging to the
    question above or below it. Measured on the live queue 2026-09-24: of 3,263 figure crops,
    **844** were not inside their own question's rows and **416** could not be checked at all, because
    no rows were measured for that question - and a crop of a neighbour's picture is exactly what a
    reviewer sees as 「這一題沒有圖，卻掛了上一題的圖」.
    """
    out = {}
    for row in reread.band_rows(rows, int(number or 0)) or ():
        page = int(row.get("page") or 0)
        lo, hi = float(row.get("y0") or 0.0), float(row.get("y1") or 0.0)
        if page in out:
            out[page] = (min(out[page][0], lo), max(out[page][1], hi))
        else:
            out[page] = (lo, hi)
    return out


def clip_to_band(page, box, band):
    """把量到的一個框裁到這一題自己的列。回 `(box, above, below)`，或 `None`。

    `None` means the box does not reach this question's rows at all: it is the picture of the question
    above or below, and attaching it here is the mis-filing this exists to stop. `above`／`below` are
    how many points were cut off and they are *reported*, not hidden - a picture that reaches into the
    next question is a fact about the paper a reviewer may want to know, and hiding it is how a crop
    that was taken from the neighbour's picture looks complete.
    """
    extent = (band or {}).get(int(page or 0))
    if not extent:
        return None
    lo, hi = float(box[1]), float(box[3])
    blo, bhi = extent
    top, bottom = max(lo, blo), min(hi, bhi)
    if bottom - top <= 0.0:
        return None
    return ([float(box[0]), round(top, 1), float(box[2]), round(bottom, 1)],
            round(max(0.0, blo - lo), 1), round(max(0.0, hi - bhi), 1))


def clip_entry_to_band(entry, band):
    """`(entry, notes)`：這個 entry 的圖框裁成這一題自己的那一段；整張圖都不是這一題的 ⇒ `None`。

    `notes` 是一條一條記下來的：哪個框被裁掉多少、哪個框整張都在這一題之外而被丟掉。記錄是給
    紀錄用的（`records` 與裁切的說明文字），所以審題者看到的是「這張圖紙本上還蓋到隔壁題」，
    不是一張看起來很完整的鄰題圖片。
    """
    notes = []
    kept = []
    for box in [tuple(b) for b in (entry.get("figure_boxes") or ())]:
        got = clip_to_band(entry.get("page"), box, band)
        if got is None:
            notes.append({"box": [round(float(v), 1) for v in box],
                          "dropped": "outside-this-question"})
            continue
        clipped, above, below = got
        if above or below:
            notes.append({"box": [round(float(v), 1) for v in box], "clipped": clipped,
                          "above": above, "below": below})
        kept.append(clipped)
    out = dict(entry)
    if kept:
        out["figure_boxes"] = kept
        out["box"] = [min(b[0] for b in kept), min(b[1] for b in kept),
                      max(b[2] for b in kept), max(b[3] for b in kept)]
    elif entry.get("figure_boxes"):
        return None, notes
    elif entry.get("box"):
        got = clip_to_band(entry.get("page"), entry["box"], band)
        if got is None:
            return None, notes
        out["box"] = got[0]
    return out, notes


def picture_of_box(pictures, box):
    """`box` 落在哪一張量到的圖物件上（垂直重疊最多的那一張），沒有就 `None`。"""
    best, best_overlap = None, 0.0
    if len(box or []) != 4:
        return None
    for picture in pictures or ():
        if not isinstance(picture, (tuple, list)) or len(picture) != 4:
            continue
        overlap = (min(float(box[3]), float(picture[3])) - max(float(box[1]), float(picture[1])))
        if overlap > best_overlap:
            best, best_overlap = [round(float(value), 1) for value in picture], overlap
    return best


def picture_crossing(picture, extent):
    """那張圖越過這一題的列界多少：`(above, below)`（0 就是沒有越過）。

    說明的數字要用**圖**越過多少，不是框越過多少：框是上一次裁過的版本，它的越界量在補回完整之後
    是 0，寫出來就是「跨過列界：上 0pt、下 0pt」——一句正確但沒有資訊的句子（站上實測
    `moex:106100:311:11:1:question:q078` 就是這樣印的）。
    """
    if not picture or not extent:
        return (0.0, 0.0)
    return (round(max(0.0, float(extent[0]) - float(picture[1])), 1),
            round(max(0.0, float(picture[3]) - float(extent[1])), 1))


def human_notes(events_path):
    """每一題**人**留下的最後一句話：`{candidate_key: (created_at, notes)}`。

    這一支不判斷那句話在說什麼（那是提示詞的工——charter：腳本只保留紙張的性質）。它只做一件事：
    **不要把人已經說過的話當成沒說過**。業主 2026-09-25：「我其實都會打住記，但是結果不盡理想」
    ——他打了記，而管線沒有讀，所以同一張截圖下一趟照樣被判定「本來就在自己列裡」而跳過。
    所以在這裡：有人說過話的題目，這一趟**不放過**（重切那一題自己的圖），而且把那句話原文
    放進 `figure_ownership.json` 的紀錄裡，讓下一個讀的人看到同一件事。
    """
    out = {}
    if not events_path or not os.path.exists(events_path):
        return out
    with open(events_path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if str(row.get("reviewer") or "").startswith("repair_"):
                continue
            note = str(row.get("notes") or "").strip()
            key = str(row.get("candidate_key") or "")
            if not note or not key:
                continue
            stamp = str(row.get("created_at") or "")
            if key not in out or stamp >= out[key][0]:
                out[key] = (stamp, note.replace("\n", " ")[:160])
    return out


def question_region(band):
    """這一題在紙上佔的區域，**含跨頁的接縫**：`{page: (y0, y1)}`。

    `band_extents` 說的是這一題的**文字列**在哪一頁的哪一段，而紙本的圖常常不在那一段裡：跨頁的題目
    把圖放在**下一頁的頁首**（或上一頁的頁尾），所以只拿列的邊界去框，框到的是題目的文字、甚至隔壁
    題的答案列。業主 2026-09-25 回報的「跨頁截圖一定會錯」量到的就是這個形狀——`q019` 的列在第 2、3
    頁，紙本第 3 頁那張圖在 y 28.1–327.4，而截圖是 y 331.6–386.0（＝這一題在第 3 頁的文字），
    `q055`／`q040`／`q060`／`q036` 同一類。

    所以：第一頁從這一題的第一列往下（`inf` 由下面的重疊檢查限住）、最後一頁從頁首到這一題的最後
    一列、中間整頁。單頁的題目就是它自己的列（行為與以前一樣）。
    """
    pages = sorted(band)
    if not pages:
        return {}
    if len(pages) == 1:
        return {pages[0]: (float(band[pages[0]][0]), float(band[pages[0]][1]))}
    out = {}
    for index, page in enumerate(pages):
        lo, hi = float(band[page][0]), float(band[page][1])
        if index == 0:
            out[page] = (lo, float("inf"))
        elif index == len(pages) - 1:
            out[page] = (-float("inf"), hi)
        else:
            out[page] = (-float("inf"), float("inf"))
    return out


def pictures_in_region(region, page, boxes):
    """這一題自己的區域在 `page` 這一頁上的圖：`[(page, [x0, y0, x1, y1])]`。

    `boxes` 是 `vision.picture_boxes` 量到的圖物件（那一頁上的），不是印象。
    """
    extent = region.get(int(page or 0))
    hits = []
    if not extent:
        return hits
    for box in boxes:
        if len(box) != 4:
            continue
        if float(box[3]) > extent[0] - 0.5 and float(box[1]) < extent[1] + 0.5:
            hits.append((int(page), [float(value) for value in box]))
    return hits


def crop_pictures(pdf, page, hits, *, dpi=None):
    """把這一題區域裡的圖由上而下縫成一張（跨頁就縫；與讀法那一張同一個做法）。"""
    from PIL import Image
    import io

    dpi = dpi or vision.DEFAULT_DPI
    pieces = []
    for found_page, box in sorted(hits):
        blob = vision.crop_region(pdf, found_page, box, dpi=dpi, margin=0)
        if blob:
            pieces.append(Image.open(io.BytesIO(blob)).convert("RGB"))
    if not pieces:
        return b""
    if len(pieces) == 1:
        out = io.BytesIO()
        pieces[0].save(out, format="PNG")
        return out.getvalue()
    gap = 8
    width = max(piece.width for piece in pieces)
    height = sum(piece.height for piece in pieces) + gap * (len(pieces) - 1)
    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    top = 0
    for piece in pieces:
        canvas.paste(piece, (0, top))
        top += piece.height + gap
    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()


def clip_keeping_pictures(page, box, band, pictures):
    """裁到這一題自己的列，**但不切過一張圖**：切到圖就把那張圖補回完整。

    回 `(clipped, above, below, extended)`；`above`／`below` 仍然是「紙本這張圖還蓋到隔壁題多少」
    那個事實（它沒有變），`extended` 是被補回來的那張圖的框（沒有補就是 `None`）。

    為什麼不是單純裁到列：`band` 是**文字列**的邊界，不是圖的邊界。一張圖從這一題的列裡開始、
    往下畫進下一題的列（紙本上很常見），裁在列界上就是把那張圖切一半——業主 2026-09-25 回報的
    「某些截圖不完整」正是這一類（當天的量：3,297 張圖版裁切裡 79 張被裁掉 6.5–249.8pt、55 張
    只剩一小片而被整張丟掉）。

    規則用紙本自己的物件，不用印象：`vision.picture_boxes` 量到的圖物件（同一頁相鄰的切片已經合成
    一張）只要**有一端落在這一題的列裡**，就把那一端補回完整；兩端都不在這一題的列裡的圖不補
    ——那是整張屬於隔壁題的圖，而掛上一張鄰題的圖正是 2026-09-24 修掉的那個缺陷（當時 3,263 張裡
    844 張伸到自己的列外面）。補完的框仍然只含圖物件，所以不會把隔壁題的**文字**帶進來。
    """
    got = clip_to_band(page, box, band)
    if got is None:
        return None
    clipped, above, below = got
    extent = (band or {}).get(int(page or 0))
    if not extent or not pictures:
        return clipped, above, below, None
    lo, hi = float(extent[0]), float(extent[1])
    grown = [float(value) for value in clipped]
    extended = None
    # 固定點：相鄰的圖物件會互相接續（一張圖的下緣接下一張的上緣），所以補完之後要再看一輪，
    # 直到沒有東西可補。上限只是為了不讓壞資料把這一圈變成無窮迴圈。
    for _ in range(8):
        before = list(grown)
        for picture in pictures:
            if not isinstance(picture, (tuple, list)) or len(picture) != 4:
                continue
            py0, py1 = float(picture[1]), float(picture[3])
            if py1 <= grown[1] or py0 >= grown[3]:
                continue
            if not (lo <= py0 <= hi or lo <= py1 <= hi):
                continue
            grown[1], grown[3] = min(grown[1], py0), max(grown[3], py1)
            extended = [round(float(picture[0]), 1), round(py0, 1),
                        round(float(picture[2]), 1), round(py1, 1)]
        if grown == before:
            break
    if grown == [float(value) for value in clipped]:
        return clipped, above, below, None
    return ([float(box[0]), round(grown[1], 1), float(box[2]), round(grown[3], 1)],
            above, below, extended)


#: 這一支自己寫進 `description` 的兩句歸屬說明。它們描述的是**上一次量到的框**，所以框一改，舊句
#: 就是錯的說法；留著兩句會讓審題者讀到互相矛盾的兩句（「只切這一題的列」＋「整張留著不切」）。
_OWNERSHIP_NOTE = re.compile(r"（紙本這張圖(?:還蓋到隔壁題|跨過這一題的列界)：[^）]*）")


def ownership_note(above, below, *, whole: bool) -> str:
    """這一張圖的框相對於這一題的列，一句話（`whole`＝整張留著不切）。"""
    if whole:
        return ("（紙本這張圖跨過這一題的列界：上 %gpt、下 %gpt，整張留著不切，"
                "因為紙本的圖物件就這麼大）" % (above, below))
    return "（紙本這張圖還蓋到隔壁題：上 %gpt、下 %gpt，只切這一題的列）" % (above, below)


def set_ownership_note(ref, above, below, *, whole: bool) -> None:
    """把 ref 的歸屬說明**換成**現在量到的那一句（先拿掉舊的，不是再接一句上去）。

    舊句是上一次那一個框的事實：一列被裁過又補回來時，兩句都會在，而審題者讀到的是兩句互相
    矛盾的話。沒有東西要說（上下都是 0、框完整落在列裡）時就只拿掉舊句、不加新句。
    """
    base = _OWNERSHIP_NOTE.sub("", ref.get("description") or "")
    if above is None or below is None or (not above and not below and not whole):
        ref["description"] = base
        return
    ref["description"] = base + ownership_note(above, below, whole=whole)


#: A crop that survives less than this many points of its own band is not a crop. Measured on question
#: 42 of `1152_藥師(一)_藥學(一)`: the rows that question owns on page 9 are the three option markers
#: `B.`/`C.`/`D.` (x 39.2-50.3) while the option pictures beside them reach x=158.4, so the clip left
#: an **11 pt** sliver of text - 32,331 bytes of it were shown to the model, it reported the options as
#: blank, and the question was answered `▢`. It is the same 24 pt the figure stage uses
#: (`vision.MIN_FIGURE_HEIGHT`), because that is the height below which nothing in this corpus is a
#: figure rather than a glyph or a rule (7,190 objects measure under it, 663 measure over).
SLIVER_MIN_HEIGHT = 24.0


#: A surviving slice that holds at least this much of a detected **picture** is this question's own
#: figure, not a strip of its neighbours.
#:
#: Measured on the live queue 2026-09-25 (`--fix-figure-ownership`, 3,297 figure crops): of the 95
#: crops the half-the-box test wanted to drop, **44** have a picture inside the surviving slice
#: (survivors 42-138 pt) and 50 are 10-17 pt text strips with nothing but type in them. The 44 are
#: questions whose crop had been inflated by the old overlap rule - the box was the question's rows
#: unioned with a picture that belongs to the question below, so it measured 376 pt while the
#: question's own rows (and its own figure) are the 138 pt that survives. Dropping those removed the
#: figure from 44 questions; clipping them is what the owner asked for.
PICTURE_MIN_OVERLAP = 8.0


def survivor_has_picture(clipped, pictures, min_overlap=PICTURE_MIN_OVERLAP):
    """Does the surviving slice cover a picture? `pictures` are that page's boxes (`(page, box)`)."""
    y0, y1 = float(clipped[1]), float(clipped[3])
    for entry in pictures or ():
        box = entry[1] if isinstance(entry, (tuple, list)) and len(entry) == 2 else entry
        if not isinstance(box, (tuple, list)) or len(box) != 4:
            continue
        overlap = min(y1, float(box[3])) - max(y0, float(box[1]))
        if overlap >= min_overlap:
            return True
    return False


def sliver_record(box, clipped, pictures=()):
    """The record for a crop too thin to show, or `None` when it is worth keeping.

    Two ways a clip comes out too thin, and both were the same fact about the paper until the
    ownership pass measured otherwise: the surviving height is under `SLIVER_MIN_HEIGHT`, or it is
    under **half** of the box it was cut from - a 200 pt picture of which 30 pt is inside this
    question's rows reads as badly as an 11 pt one, whatever the floor says.

    The second test needs one more fact, because the box it measures against is not always honest:
    under the old overlap rule a question's crop was its rows unioned with the pictures it claimed,
    including pictures belonging to the question below. So "under half the box" can mean "the box was
    twice as tall as it should have been" - and the surviving half is the question's own rows *with
    its own figure inside them*. `pictures` decides it: a survivor that covers a picture is kept and
    clipped, one that is all type is still dropped. See `PICTURE_MIN_OVERLAP` for the measurement.

    `box` is the box as it was measured and `clipped` the part of it that survived; both go in the
    record, because the owner has to be able to see how many crops were dropped and why rather than
    find them quietly missing.
    """
    survived = round(float(clipped[3]) - float(clipped[1]), 1)
    measured = round(float(box[3]) - float(box[1]), 1)
    if survived < SLIVER_MIN_HEIGHT:
        why = "surviving-height-under-floor"
    elif survived < measured / 2.0:
        if survivor_has_picture(clipped, pictures):
            return None
        why = "surviving-height-under-half-the-box"
    else:
        return None
    return {"dropped": "sliver", "why": why, "box": [round(float(v), 1) for v in clipped],
            "was": [round(float(v), 1) for v in box],
            "surviving": survived, "was_height": measured}


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
        # **這一題自己的列。** Every crop this entry produces is clipped to the rows the skeleton
        # gave this question, because a picture object on a page belongs to the page, not to a
        # question: measured on the live queue, 844 of 3,263 figure crops reached past their own
        # question's rows into the one above or below, and those are the crops a reviewer cannot
        # review. When no rows were measured for the question there is nothing to clip to, and the
        # honest answer is no crop plus the reason - not a picture that might belong to a neighbour.
        reasons = list(entry.get("reasons") or [])
        band = band_extents(rows, number)
        if not band:
            refs[candidate["candidate_key"]] = []
            records.append({"number": number, "reasons": reasons, "error": "no-band",
                            "file": None, "bytes": 0})
            continue
        entry, clip_notes = clip_entry_to_band(entry, band)
        if entry is None:
            refs[candidate["candidate_key"]] = []
            records.append({"number": number, "reasons": reasons,
                            "error": "picture-outside-this-question", "notes": clip_notes,
                            "file": None, "bytes": 0})
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
            # An option's picture is a picture object on a page like any other, so it is clipped to
            # this question's rows for the same reason the figure is: an object that runs into the
            # next question would otherwise be filed as this question's option.
            clipped_options = []
            for option in option_crops:
                got = clip_to_band(option["page"], option["box"], band)
                if got is None:
                    clip_notes.append({"option": option.get("key"),
                                       "box": [round(float(v), 1) for v in option["box"]],
                                       "dropped": "outside-this-question"})
                    continue
                clipped, above, below = got
                if above or below:
                    clip_notes.append({"option": option.get("key"), "clipped": clipped,
                                       "above": above, "below": below})
                entry_option = dict(option)
                entry_option["box"] = clipped
                clipped_options.append(entry_option)
            option_crops = clipped_options
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
            # 這張圖紙本上還蓋到隔壁題時，說明文字要說出來：審題者看到的是「只切這一題的列」，
            # 而不是一張看起來很完整、其實屬於鄰題的圖。
            above = max([n.get("above") or 0 for n in clip_notes] or [0])
            below = max([n.get("below") or 0 for n in clip_notes] or [0])
            note = ""
            if above or below:
                note = "（紙本這張圖還蓋到隔壁題：上 %gpt、下 %gpt，只切這一題的列）" % (above, below)
            payload.append({
                "path": path,
                "raw_ref": name,
                "asset_role": "figure-crop",
                "source": "qbr_vision_crop",
                "page": entry.get("page"),
                "box": [round(float(value), 1) for value in entry["box"]],
                "label": reason,
                "clipped": clip_notes or None,
                "description": (f"第 {number} 題：{'、'.join(entry['reasons'])}"
                                + ("（選項跨頁）" if entry.get("split") else "") + note),
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
            "clip_notes": clip_notes or None,
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


# --- the queue mode: the crop a *reading* asked for -------------------------------------------
# The run mode above cuts what the page *measures*. This one cuts what a reading *reported*, which is
# the only way a table can be cropped at all: `vision.figure_questions` triggers on an embedded image
# object or on option markers printed with nothing after them, and a table printed as text has
# neither, so no crop was ever cut for one (`moex:115020:305:0403:1:question:q065`).
#
# The reading is `confirm_dispute.py`'s transcription of the question - the pass that has the model
# read the question's own crop - stored in the queue's `question_ai_findings.jsonl` under
# `finding["transcription"]`. It reports the table by quoting the table's own lines
# (`ai_findings.TABLE_LINES_RULE`), and this mode's whole job is: find those printed lines among the
# page's measured lines, and cut the band they cover. Nothing here decides what a table is.

def review_ui_dir(queue: str) -> str:
    """The `review-ui/` directory of a queue, tolerating either spelling of `--queue`."""
    import repair_loop
    return repair_loop.review_ui_dir(queue)


def queue_root_of(queue: str) -> str:
    """The queue root itself, which is the directory a stored crop path is spelled against.

    `image_refs` stores `review-ui/crops/<paper>/<name>.png` (`crop_output_path` and `_adopt_crops`
    both write that spelling, and the Review UI resolves it against the queue root it was pointed
    at), so the relative path has to be taken from the root and not from `review-ui/` - a crop
    referenced as `crops/...` is a 404 on the reviewer's screen.
    """
    directory = review_ui_dir(queue)
    return os.path.dirname(directory) if os.path.basename(directory) == "review-ui" else directory


def is_table_ref(ref) -> bool:
    """Whether a crop ref is one of this mode's own, so it can be replaced rather than duplicated."""
    return (isinstance(ref, dict) and ref.get("asset_role") == "figure-crop"
            and ref.get("label") == TABLE_REASON)


def table_readings(queue: str, *, keys=None) -> dict:
    """`{candidate_key: {"lines": [...], "reading": {...}}}` for the readings that reported a table.

    The **latest reading** wins, not the latest record: the audit pass writes records for the same
    key too (`population` `blocked` or a corpus sweep) and those hold no transcription, so a record
    that answers a different question must not be read as this one. A question whose latest reading
    reports no table is simply absent from the result - the reading is the authority on whether the
    body is a table, so a later reading that says nothing retracts an earlier one that quoted lines.
    """
    path = ai_findings.store_path(queue)
    latest = {}
    for record in ai_findings.load(path):
        key = record.get("candidate_key")
        if not key or (keys is not None and key not in keys):
            continue
        finding = record.get("finding")
        transcription = finding.get("transcription") if isinstance(finding, dict) else None
        if isinstance(transcription, dict):
            latest[key] = record            # file order: the last reading is the current one
    readings = {}
    for key, record in latest.items():
        lines = ai_findings.table_lines_of(record)
        if lines:
            readings[key] = {"lines": lines, "created_at": record.get("created_at"),
                             "model": record.get("model")}
    return readings


def matches_only(row, only) -> bool:
    """Whether a row is named by `--only`: a candidate key, `q65`, or the question number."""
    if not only:
        return True
    names = {str(name).strip() for name in only}
    key = str(row.get("candidate_key") or "")
    number = row.get("question_number")
    if key in names:
        return True
    if number is None:
        return False
    return str(number) in names or "q%03d" % int(number) in names


def table_crops_for_rows(rows, readings, *, queue_root, crops_root, paper_of, limit=0,
                         dpi=vision.DEFAULT_DPI):
    """Cut the paper's table for every row whose reading quoted one. Returns `(refs, records)`.

    The reading is the whole authority. A row with no reading, or one whose reading reported no
    table, keeps its `image_refs` untouched; a table whose quoted lines cannot be found on the page
    is *reported and not cut*, because a crop of the wrong region still looks like a picture of a
    table and is therefore worse than no crop at all.

    Re-runnable by construction: a row that already carries the same quoted lines in a table crop is
    left alone, and one carrying an older reading's lines has those refs replaced rather than added
    to. The lines are the identity because they are the reading's own words - two readings that quote
    the same lines are asking for the same crop.
    """
    from PIL import Image

    refs, records = {}, []
    wanted = [row for row in rows if row.get("candidate_key") in readings]
    if limit:
        wanted = wanted[:limit]
    by_paper = {}
    for row in wanted:
        by_paper.setdefault(paper_of(row), []).append(row)
    for pdf_path, paper_rows in sorted(by_paper.items(), key=lambda item: str(item[0])):
        base = {"paper": pdf_path}
        if not pdf_path or not os.path.isfile(pdf_path):
            # No paper, no page, no crop: the row's own reference to its paper did not resolve, and
            # cutting from some other file would be a crop of the wrong question.
            records.extend({**base, "key": row.get("candidate_key"),
                            "number": row.get("question_number"), "error": "no-paper"}
                           for row in paper_rows)
            continue
        cells, _ = repair.mask_chrome(extract.extract_cells_a(pdf_path))
        lines, _ = repair.mask_chrome(extract.extract_lines_a(pdf_path))
        for row in paper_rows:
            key = row.get("candidate_key")
            number = row.get("question_number")
            reading = readings[key]["lines"]
            band = reread.band_rows(cells, int(number)) if number is not None else []
            span_pages = sorted({int(item.get("page") or 0) for item in band})
            existing = [ref for ref in (row.get("image_refs") or []) if is_table_ref(ref)]
            # 同一份讀法的表**不重切**，但「題目跨頁就要整題縫成一張」是後來才有的規則：一份**單頁**
            # 的舊截圖（沒有 `pages`）在跨頁的題目上正是「跨頁截圖只有一半」的那一種，要被取代。
            # 只比對讀法會讓它永遠留著（2026-09-25 量到的 q065／q049／q070 都是這個形狀）。
            legacy_single = len(span_pages) > 1 and not any(ref.get("pages") for ref in existing)
            if existing and not legacy_single and all(ref.get("table_lines") == reading
                                                      for ref in existing):
                records.append({**base, "key": key, "number": number, "unchanged": True,
                                "files": [ref.get("raw_ref") for ref in existing]})
                continue
            record = {**base, "key": key, "number": number, "page": None, "box": None,
                      "file": None, "bytes": 0, "pixels": None, "lines": reading}
            if not band:
                record["error"] = "no-band"
                records.append(record)
                continue
            bands, complaint = vision.quoted_lines_region(vision.within_band(lines, band), reading)
            if not bands:
                record["error"] = complaint
                records.append(record)
                continue
            paper_name = os.path.basename(pdf_path)[:-4]
            cut = []
            if len(span_pages) > 1:
                # **題目自己跨頁 ⇒ 整題縫成一張**（owner 2026-09-25：「跨頁截圖一定會錯」）。量到的
                # 三例（`q065` 表在第 13 頁、選項在第 14 頁；`q049` 題幹第一行在第 7 頁；`q070` 選項在
                # 第 12 頁）都是「截圖只有一頁」——只切表格那幾行的話，審題者看到的永遠是半張。
                # 縫法就是讀法那一張（`reread.crop_rows`，一頁一塊、由上而下），所以兩邊看到的
                # 是同一件事。
                png = reread.crop_rows(pdf_path, band, dpi=dpi)
                if not png:
                    record["error"] = "no-crop:stitch"
                    records.append(record)
                    continue
                name = "q%03d_%s.png" % (int(number), TABLE_REASON)
                path = os.path.join(crops_root, paper_name, name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as handle:
                    handle.write(png)
                with Image.open(path) as image:
                    pixels = [image.width, image.height]
                extents = reread.page_extents(band)
                cut.append({"path": path, "raw_ref": name, "page": span_pages[0],
                            "box": [round(float(v), 1) for v in extents.get(span_pages[0], ())],
                            "bytes": len(png), "pixels": pixels, "pages": span_pages,
                            "page_boxes": {str(page): [round(float(v), 1) for v in box]
                                           for page, box in sorted(extents.items())},
                            "whole_question": True})
                payload = [{
                    "path": os.path.relpath(cut[0]["path"], queue_root),
                    "raw_ref": cut[0]["raw_ref"],
                    "asset_role": "figure-crop", "source": "qbr_vision_crop",
                    "page": cut[0]["page"], "pages": cut[0]["pages"],
                    "page_boxes": cut[0]["page_boxes"], "box": cut[0]["box"],
                    "label": TABLE_REASON, "table_lines": list(reading),
                    "description": ("第 %s 題：紙本表格（讀法指出 %d 行；這一題跨第 %s 頁，"
                                    "整題縫成一張）"
                                    % (number, len(reading),
                                       "、".join(str(p) for p in span_pages))),
                    "placement": "question",
                }]
                row["image_refs"] = [ref for ref in (row.get("image_refs") or [])
                                     if not is_table_ref(ref)] + payload
                refs[key] = payload
                record.update({"page": cut[0]["page"], "box": cut[0]["box"],
                               "file": cut[0]["raw_ref"], "bytes": cut[0]["bytes"],
                               "pixels": cut[0]["pixels"], "files": [cut[0]["raw_ref"]],
                               "pages": span_pages, "whole_question": True})
                records.append(record)
                continue
            pieces = []
            for page in sorted(bands):
                box = [round(float(value), 1) for value in bands[page]]
                entry = {"page": page, "figure_boxes": [list(bands[page])]}
                png, got = vision.crop_figure(entry, pdf_path, dpi=dpi)
                if png is None:
                    pieces = []
                    record["error"] = "no-crop:%s" % got
                    break
                pieces.append((page, box, png))
            if pieces:
                # **跨頁的表要縫成一張**（owner 2026-09-25：「跨頁截圖一定會錯」）。以前每一頁各自
                # 一個檔案（`-p13.png`／`-p14.png`），於是審題者看到的永遠是半張表：量到 q65 的截圖
                # 只有第 13 頁的框、選項在第 14 頁沒被任何一張蓋到。縫法與讀法那一張（`reread.
                # crop_rows`）一致：一頁一塊、由上而下、中間留一道白，並且在說明裡寫出跨了哪幾頁。
                if len(pieces) == 1:
                    page, box, png = pieces[0]
                    name = "q%03d_%s.png" % (int(number), TABLE_REASON)
                    path = os.path.join(crops_root, paper_name, name)
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as handle:
                        handle.write(png)
                    with Image.open(path) as image:
                        pixels = [image.width, image.height]
                    cut.append({"path": path, "raw_ref": name, "page": page, "box": box,
                                "bytes": len(png), "pixels": pixels, "pages": [page],
                                "page_boxes": {str(page): box}})
                else:
                    images = [Image.open(io.BytesIO(item[2])).convert("RGB") for item in pieces]
                    width = max(image.width for image in images)
                    gap = 8
                    canvas = Image.new("RGB", (width, sum(i.height for i in images)
                                               + gap * (len(images) - 1)), (255, 255, 255))
                    top = 0
                    for image in images:
                        canvas.paste(image, (0, top))
                        top += image.height + gap
                    name = "q%03d_%s.png" % (int(number), TABLE_REASON)
                    path = os.path.join(crops_root, paper_name, name)
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    canvas.save(path, format="PNG")
                    with open(path, "rb") as handle:
                        blob = handle.read()
                    cut.append({"path": path, "raw_ref": name, "page": pieces[0][0],
                                "box": pieces[0][1], "bytes": len(blob),
                                "pixels": [canvas.width, canvas.height],
                                "pages": [item[0] for item in pieces],
                                "page_boxes": {str(item[0]): item[1] for item in pieces}})
            if not cut:
                records.append(record)
                continue
            payload = [{
                "path": os.path.relpath(item["path"], queue_root),
                "raw_ref": item["raw_ref"],
                # A crop of the paper's own table is the same kind of asset as a figure crop, with
                # its own `label` saying which reading asked for it and `table_lines` carrying the
                # reading's own lines, so a reader can check the crop against the row it replaces.
                "asset_role": "figure-crop",
                "source": "qbr_vision_crop",
                "page": item["page"],
                "pages": item["pages"],
                "page_boxes": item["page_boxes"],
                "box": item["box"],
                "label": TABLE_REASON,
                "table_lines": list(reading),
                "description": "第 %s 題：紙本表格（讀法指出 %d 行；文字層只剩壓平的一串字%s）"
                               % (number, len(reading),
                                  "；跨第 %s 頁，縫成一張"
                                  % "、".join(str(p) for p in item["pages"])
                                  if len(item["pages"]) > 1 else ""),
                "placement": "question",
            } for item in cut]
            # The option crops are *kept*: a question can have a picture per option and a table in
            # its stem, and the table must be cut even then.
            row["image_refs"] = [ref for ref in (row.get("image_refs") or [])
                                 if not is_table_ref(ref)] + payload
            refs[key] = payload
            first = cut[0]
            record.update({"page": first["page"], "box": first["box"], "file": first["raw_ref"],
                           "bytes": sum(item["bytes"] for item in cut),
                           "pixels": first["pixels"],
                           "files": [item["raw_ref"] for item in cut]})
            records.append(record)
    return refs, records


def cut_queue_tables(args) -> None:
    """`--queue`: cut the table crops a queue's own readings asked for, and write the rows back.

    The rows are re-read at the start of this process rather than passed down from an earlier step of
    the same one: this runs as its own stage, immediately after the stage that rewrites
    `candidates.jsonl` (`apply_text_corrections.py`), and a snapshot taken before that stage would
    write back the text it had just corrected away.
    """
    import confirm_dispute

    queue_root = queue_root_of(args.queue)
    directory = review_ui_dir(args.queue)
    candidates_path = os.path.join(directory, "candidates.jsonl")
    with open(candidates_path, encoding="utf-8") as handle:
        originals = [json.loads(line) for line in handle if line.strip()]
    rows = [dict(row) for row in originals]
    # `dict(row)` 是淺拷貝：`image_refs` 那份 list 與 `originals` 共用，原地改一個 ref 兩邊都會變，
    # 所以「更新了幾列」要拿**改動前的序列化字串**比，不是比物件身分（比身分會永遠說 0）。
    before_text = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows]
    wanted = {row.get("candidate_key") for row in rows if matches_only(row, args.only)}
    readings = table_readings(args.queue, keys=wanted)
    refs, records = table_crops_for_rows(
        rows, readings, queue_root=queue_root, crops_root=os.path.join(directory, "crops"),
        paper_of=confirm_dispute.paper_pdf_of, limit=args.limit, dpi=vision.DEFAULT_DPI)
    changed = sum(1 for row, text in zip(rows, before_text)
                  if json.dumps(row, ensure_ascii=False, sort_keys=True) != text)
    temporary = candidates_path + ".partial"
    with open(temporary, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temporary, candidates_path)
    with open(os.path.join(directory, "table_crops.json"), "w", encoding="utf-8") as handle:
        json.dump({"queue": queue_root, "candidates": candidates_path,
                   "readings": {key: value["lines"] for key, value in readings.items()},
                   "crops": records}, handle, ensure_ascii=False, indent=2)
    print("=== 紙本表格裁切（只有讀法指出表格的題目才切）")
    print("  卷 %s" % queue_root)
    print("  讀法指出表格 %d 題   裁切 %d 題   更新候選列 %d" % (len(readings), len(refs), changed))
    for record in records:
        if record.get("unchanged"):
            print("    q%-4s 已有同一份讀法的裁切，略過" % record.get("number"))
        elif record.get("error"):
            print("    q%-4s 未裁切：%s" % (record.get("number"), record["error"]))
        else:
            print("    q%-4s %s  %s  %d bytes  %dx%d" % (
                record.get("number"), record["file"], record["box"], record["bytes"],
                record["pixels"][0], record["pixels"][1]))


def fix_queue_figure_ownership(args) -> None:
    """`--fix-figure-ownership`: 把佇列裡每一張圖重新裁到**這一題自己的列**。

    站上實測（2026-09-24）：3,263 張 `figure-crop` 裡有 **844** 張的框不在它自己那一題的列裡，
    **416** 張連列都沒量到、無法確認。原因不是裁切壞了，是**歸屬**：圖片物件屬於紙本的那一頁，
    不是屬於某一題，一張跨到隔壁題的圖被整張掛上來，審題者看到的就是「這一題沒有圖，卻掛了上一題
    的圖」。

    這裡不做任何猜測，也不改左右邊界：框是先前量到的，這一題自己的列是 `reread.band_rows` 量到的
    （與表格裁切同一把尺），把框**垂直**裁到那一段，再用紙本自己的像素重畫同一張檔名。整張框都落在
    這一題的列之外 ⇒ 那不是這一題的圖，**移除那個 ref** 並記錄。列沒量到 ⇒ 不動它，但在 ref 上記
    `ownership: unverified`，讓審題者知道這張圖的歸屬沒被確認過，而不是讓他以為已經確認過了。

    裁完只剩**一小片**的框也不能留：存活高度 < `SLIVER_MIN_HEIGHT`（24pt）或不到原框一半 ⇒ 移除並記
    `sliver`（`stats` 與 `records` 都看得到，摘要也印出來）。站上實測（`1152_藥師(一)_藥學(一)` 第 42
    題）：存活 11pt 的文字條被當成這一題的圖給模型看，模型說選項是空白，這一題就答了 `▢` —— 一張看不
    清的圖比沒有圖更糟，因為它看起來像紙本的答案。
    """
    import confirm_dispute

    queue_root = queue_root_of(args.queue)
    directory = review_ui_dir(args.queue)
    candidates_path = os.path.join(directory, "candidates.jsonl")
    with open(candidates_path, encoding="utf-8") as handle:
        originals = [json.loads(line) for line in handle if line.strip()]
    rows = [dict(row) for row in originals]
    # `dict(row)` is a *shallow* copy: the `image_refs` list is shared with `originals`, so mutating a
    # ref in place changes both and a "changed rows" count taken from the originals would read 0 while
    # every row was rewritten. The count is the only number a reviewer sees, so it is taken from the
    # serialised row, not from object identity.
    before_text = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows]
    bands = {}
    # `sliver` starts at zero instead of being absent when nothing was dropped: this file is how the
    # owner sees how many crops went missing and why, and a missing key is not a zero.
    human = human_notes(os.path.join(directory, "question_review_events.jsonl"))
    if getattr(args, "human_flagged", False):
        # `--human-flagged`：只跑**有人留過話**的題目。這是迴圈每天要跑的那一種——他的話是新的
        # 輸入，而整本的量測（1,763 秒／趟）不是每一輪都付得起的。
        args.only = sorted(human)
    stats = collections.Counter({"sliver": 0, "widened": 0, "dropped-by-note": 0,
                                 "directive-kept": 0, "directive-asked": 0,
                                 "directive-errors": 0})
    records = []
    # 人對某一題的**圖**留的話，讀一次就夠（一次引擎呼叫）。`figure_ownership.json` 留著它，
    # 所以每 10 分鐘一輪的迴圈不會一直問同一句話；note 換了才重讀，同一句問失敗最多三次
    # （引擎忙線時會回 500，那種失敗不該讓他的話永遠不被讀）。
    directives_path = os.path.join(directory, "figure_ownership.json")
    try:
        with open(directives_path, encoding="utf-8") as handle:
            directives = dict(json.load(handle).get("directives") or {})
    except (OSError, ValueError):
        directives = {}

    def directive_of(key, note, *, question=""):
        """這一題的圖，他的話是什麼意思。回 `(標籤, 原話片段)`；讀不出來就是空字串。

        `human_notes` 的形狀是 `(created_at, notes)`——**時間在前**。寫成 `text, at = note` 就是把
        兩者對調，引擎收到一個時間戳當成「他的話」，於是每一題都回 `unclear` 並把那個時間戳照抄回來
        （站上實測 2026-09-25 21:43：6 題全部 `unclear`，`quote` 是 `2026-09-24T07:55:35`）。
        """
        at, text = note[0], note[1]
        cached = directives.get(key) or {}
        if cached.get("note_at") == at and (cached.get("directive")
                                            or int(cached.get("tries") or 0) >= 3):
            return cached.get("directive") or "", cached.get("quote") or ""
        label, quote, complaint = ask_about_blocks.read_figure_directive(text, question=question)
        stats["directive-asked"] += 1
        directives[key] = {
            "note": text, "note_at": at, "directive": label, "quote": quote,
            "complaint": complaint, "asked_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "tries": (int(cached.get("tries") or 0) + 1) if cached.get("note_at") == at else 1}
        if complaint:
            stats["directive-errors"] += 1
        return label, quote

    rows_by_pdf = {}

    def rows_of(pdf, number):
        """這一題自己的列（`reread.band_rows`）：整題縫一張要用它，與讀法那一張同一個做法。"""
        if pdf not in rows_by_pdf:
            cells, _ = repair.mask_chrome(extract.extract_cells_a(pdf))
            rows_by_pdf[pdf] = cells
        return reread.band_rows(rows_by_pdf[pdf], int(number or 0)) or []

    def band_of(pdf, number):
        key = (pdf, number)
        if key not in bands:
            cells, _ = repair.mask_chrome(extract.extract_cells_a(pdf))
            bands[key] = band_extents(cells, number)
        return bands[key]

    # 這一頁上**量到的圖**（`vision.picture_boxes`，與圖版階段同一把尺）。只給 `sliver_record`
    # 一件事：留下來的那一片裡有沒有圖。`extract_images_a` 一次讀整份 PDF（站上實測 0.03-0.08s／份），
    # 所以快取的是整份的圖，不是每一頁各讀一次。
    pictures_by_pdf = {}

    def pictures_of(pdf, page):
        if pdf not in pictures_by_pdf:
            pictures_by_pdf[pdf] = vision.picture_boxes(extract.extract_images_a(pdf))
        return [box for found_page, box in pictures_by_pdf[pdf]
                if int(found_page) == int(page or 0)]

    for row in rows:
        if not matches_only(row, args.only):
            continue
        refs = [ref for ref in (row.get("image_refs") or [])
                if isinstance(ref, dict) and ref.get("asset_role") == "figure-crop"]
        if not refs:
            continue
        number = row.get("question_number") or row.get("number")
        note = human.get(str(row.get("candidate_key") or "")) \
            if getattr(args, "human_flagged", False) else None
        label, quote = ("", "")
        if note and getattr(args, "human_flagged", False):
            # 只有 `--human-flagged`（迴圈每一輪的那一趟）才讀他的話：整本的那一趟（1,763 秒）是量測，
            # 不是問句，而一句話讀一次就夠（快取在 `figure_ownership.json` 的 `directives`）。
            label, quote = directive_of(str(row.get("candidate_key") or ""), note,
                                        question=str(row.get("stem") or ""))
            if label in ("no-figure", "extra-crop"):
                # **他說這一題不要圖。**他的話是權威，所以這裡不量測、不縫一張：站上實測
                # 2026-09-25，他明確說「沒有圖／多截」的 11 題裡，紙本量得到的訊號只解釋得了 5 題；
                # 剩下 6 題的區域**確實量到 2-4 個圖物件**（選項的圖、隔壁題的圖），量測於是留著一張，
                # 而他看到的就是「他不改」。紙本量不出「這張圖該不該交給審題者」——那是他的意思。
                stats["dropped-by-note"] += 1
                for ref in refs:
                    if is_table_ref(ref):
                        continue
                    records.append({"key": row.get("candidate_key"), "number": number,
                                    "file": ref.get("raw_ref"), "note": note[1], "note_at": note[0],
                                    "quote": quote, "dropped": "human-said-" + label})
                # **表格那一張留著**：它的 `asset_role` 也是 `figure-crop`，但它不是「這一題的圖」——
                # 它是紙本表格那一張（讀法用它的文字代替被壓平的表），而他說的是圖。同一條路上刪掉它
                # 會讓那一題失去表格。
                row["image_refs"] = [ref for ref in (row.get("image_refs") or [])
                                     if not (isinstance(ref, dict)
                                             and ref.get("asset_role") == "figure-crop"
                                             and not is_table_ref(ref))]
                continue
            if label == "keep":
                stats["directive-kept"] += len(refs)
                records.append({"key": row.get("candidate_key"), "number": number,
                                "why": "human-directive-kept-existing-crop",
                                "directive": label, "quote": quote,
                                "note": note[1], "note_at": note[0]})
                continue
            if label not in ("keep", "wrong-region"):
                # An unclear, missing, or unknown directive is not permission to remove or replace
                # the existing crop. Keep the evidence attached and make the unresolved state visible.
                for ref in refs:
                    ref["ownership"] = "unverified"
                stats["unverified"] += len(refs)
                records.append({"key": row.get("candidate_key"), "number": number,
                                "ownership": "unverified",
                                "why": "human-directive-not-actionable",
                                "directive": label or "unclear", "quote": quote,
                                "note": note[1], "note_at": note[0]})
                continue
        pdf = confirm_dispute.paper_pdf_of(row)
        if not pdf:
            stats["no-paper"] += 1
            continue
        band = band_of(pdf, number)
        if not band:
            stats["unverified"] += 1
            for ref in refs:
                ref["ownership"] = "unverified"
            records.append({"key": row.get("candidate_key"), "number": number,
                            "ownership": "unverified", "why": "no-band"})
            continue
        kept = []
        for ref in refs:
            box = ref.get("box") or []
            if len(box) != 4 or any(not isinstance(value, (int, float)) for value in box):
                if not note:
                    kept.append(ref)
                    stats["unverified"] += 1
                    ref["ownership"] = "unverified"
                    continue
                # 人留過話、而這一張沒有可用的框（站上實測 2026-09-25：業主點名「沒有圖」的 11 題
                # 就是這個形狀，`box` 是空的）。以前這一種一律 `unverified` 留著——他講了 11 題，
                # 11 題一個字都沒動。現在它跟「框對不到圖」走同一條路：**量這一題自己的區域**，
                # 量得到圖就重切，量不到就不要放圖（見下面的 `dropped`）。
                box = []
            pictures = pictures_of(pdf, ref.get("page"))
            extent = (band or {}).get(int(ref.get("page") or 0))
            if note:
                stats["human-flagged"] += 1
            # 有人留過話、而框落在**這一題沒有列的那一頁**上（`107100` 的 `q068`：題目在第 11 頁、
            # 截圖在第 12 頁的局部結構）⇒ 一樣重切。沒有人留話就不動這一種，因為紙本確實有把圖
            # 放到別頁的情形，而猜錯就是把隔壁題的圖切過來。
            off_page = bool(note) and int(ref.get("page") or 0) not in (band or {})
            if not ref.get("whole_picture") and (note or off_page or not picture_of_box(pictures, box)):
                # 這一張框在它自己那一頁上**對不到任何一張紙本的圖**：它框到的是文字或空白——跨頁的
                # 題目就是這樣（圖在另一頁的頁首）。改成**這一題自己的區域裡量到的圖**，跨頁就縫成
                # 一張；區域裡沒有圖就照舊，不猜（不猜才不會掛上隔壁題的圖）。
                region = question_region(band or {})
                measured = []
                for found_page in sorted(region):
                    measured += pictures_in_region(region, found_page, pictures_of(pdf, found_page))
                # 碎片規則之前的那一份：**這一題自己的區域裡到底有沒有量到圖物件**。量不到，
                # 就是紙本這一題沒有圖（或圖完全不是圖物件），下面那一條會據此決定放不放圖。
                raw_hits = list(measured)
                hits = list(measured)
                band_height = sum(float(high) - float(low) for low, high in (band or {}).values())
                picture_height = sum(float(hit[3]) - float(hit[1]) for _page, hit in hits)
                if hits and band_height and picture_height < 0.30 * band_height:
                    # 量到的圖**遠小於這一題自己的範圍**（業主點名的 `q071`／`107100 q066`／`q068`）：
                    # 那不是圖，是圖物件被切成很多小片裡的一片（引擎回讀：「局部幾何圖形」
                    # 「局部截圖」）。照它切只會把一張好一點的窄條換成一塊小碎片 ⇒ 當成量不到，
                    # 走整題縫一張那一條。
                    stats["fragmentary-picture"] += 1
                    hits = []
                if not hits:
                    # 這一題自己的區域裡**量不到任何圖物件**（紙本的圖是向量圖、或圖物件被切成很多小片，
                    # `vision.picture_boxes` 抓不到——業主點名的 `q006`／`q056`／`q071` 就是這樣）。
                    # 那就不要再交出一條「題目文字列」的窄條：那條的右緣與頁界會把圖切掉（引擎回讀：
                    # 「右側邊緣被截斷」「邊緣有被裁切殘留的文字」）。改成**整題縫成一張**，與讀法
                    # 那一張同一個做法——它至少不會切到圖。
                    rows_here = rows_of(pdf, number)
                    blob = reread.crop_rows(pdf, rows_here) if rows_here else b""
                    target = os.path.join(queue_root, str(ref.get("path") or ""))
                    if blob and ref.get("path") and os.path.isdir(os.path.dirname(target)):
                        with open(target, "wb") as handle:
                            handle.write(blob)
                        ref["box"] = [round(float(v), 1) for v in (rows_here[0].get("bbox") or [])]
                        ref["pages"] = sorted({int(item.get("page") or 0) for item in rows_here})
                        ref["widened"] = "the-whole-question-because-no-picture-was-measured"
                        set_ownership_note(ref, None, None, whole=False)
                        ref["ownership_note"] = ("紙本這一題的圖量不到物件，所以這一張是整題縫成一張"
                                                 "（跨頁就縫；不會把圖切掉）")
                        ref["bytes"] = len(blob)
                        stats["whole-question"] += 1
                        kept.append(ref)
                        records.append({"key": row.get("candidate_key"), "number": number,
                                        "file": ref.get("raw_ref"),
                                        "whole_question": ref["pages"],
                                        "was": [round(float(v), 1) for v in box],
                                        "bytes": len(blob),
                                        "human_note": (note[1] if note else None)})
                        continue
                if hits:
                    blob = crop_pictures(pdf, ref.get("page"), hits)
                    target = os.path.join(queue_root, str(ref.get("path") or ""))
                    if not blob:
                        stats["render-failed"] += 1
                    elif not ref.get("path") or not os.path.isdir(os.path.dirname(target)):
                        stats["no-target"] += 1
                    else:
                        with open(target, "wb") as handle:
                            handle.write(blob)
                        ref["box"] = [round(value, 1) for value in hits[0][1]]
                        ref["pages"] = [page for page, _ in hits]
                        ref["page_boxes"] = {str(page): [round(value, 1) for value in hit]
                                             for page, hit in hits}
                        ref["bytes"] = len(blob)
                        ref["widened"] = "the-pictures-in-this-questions-own-part"
                        set_ownership_note(ref, None, None, whole=False)
                        ref["ownership_note"] = ("紙本這一題的圖在第 %s 頁，這一張是照紙本量到的圖"
                                                 "重切的（跨頁就縫成一張）"
                                                 % "、".join(str(page) for page, _ in hits))
                        stats["widened"] += 1
                        kept.append(ref)
                        records.append({"key": row.get("candidate_key"), "number": number,
                                        "file": ref.get("raw_ref"), "widened": hits,
                                        "was": [round(float(v), 1) for v in box],
                                        "bytes": len(blob), "pages": ref["pages"],
                                        "human_note": (note[1] if note else None),
                                        "human_note_at": (note[0] if note else None)})
                        continue
            if ref.get("whole_picture"):
                # 上一次這一支已經判定「紙本的圖物件跨過這一題的列界、整張留著」：不切回去。
                # 沒有這一條，這一趟會把它裁回半張圖、下一趟再判定一次——同一個決定兩個答案。
                # 說明照樣重寫一次：第一趟寫下它的時候，舊的那一句（「只切這一題的列」）還在，
                # 留著就是兩個矛盾的主張。
                crossing = ref.get("picture_crossing") or {}
                if not crossing:
                    crossing = dict(zip(("above", "below"), picture_crossing(
                        picture_of_box(pictures, ref.get("box") or []), extent)))
                set_ownership_note(ref, crossing.get("above"), crossing.get("below"), whole=True)
                stats["inside"] += 1
                kept.append(ref)
                continue
            got = clip_keeping_pictures(ref.get("page"), box, band, pictures)
            if got is None:
                stats["dropped"] += 1
                records.append({"key": row.get("candidate_key"), "number": number,
                                "file": ref.get("raw_ref"), "box": [round(float(v), 1) for v in box],
                                "dropped": "outside-this-question"})
                continue
            clipped, above, below, extended = got
            thin = sliver_record(box, clipped, pictures)
            if thin:
                # 裁完只剩一小片：這一題自己的列裡幾乎沒有這張圖。Measured on question 42 of
                # `1152_藥師(一)_藥學(一)`: an 11 pt sliver of text was shown to the model as this
                # question's figure, it reported the options as blank and the question was answered
                # `▢` - a crop too thin to read is worse than no crop, because it looks like the
                # paper's answer. It is dropped, and it is *counted* rather than hidden: the record
                # carries the box and the part of it that survived.
                stats["sliver"] += 1
                records.append({"key": row.get("candidate_key"), "number": number,
                                "file": ref.get("raw_ref"), **thin})
                continue
            grew = [round(float(v), 1) for v in box] != [round(float(v), 1) for v in clipped]
            if not (above or below) and not grew:
                # 框完整落在這一題的列裡：**舊的歸屬說明要拿掉**（它可能說「還蓋到隔壁題」），
                # 否則審題者讀到的是一句關於別的框的話。
                set_ownership_note(ref, None, None, whole=False)
                stats["inside"] += 1
                kept.append(ref)
                continue
            blob = vision.crop_region(pdf, ref.get("page"), clipped,
                                      dpi=vision.DEFAULT_DPI, margin=0)
            if not blob:
                stats["render-failed"] += 1
                records.append({"key": row.get("candidate_key"), "number": number,
                                "file": ref.get("raw_ref"), "box": clipped,
                                "error": "render-failed"})
                continue
            target = os.path.join(queue_root, str(ref.get("path") or ""))
            if not ref.get("path") or not os.path.isdir(os.path.dirname(target)):
                stats["no-target"] += 1
                records.append({"key": row.get("candidate_key"), "number": number,
                                "file": ref.get("raw_ref"), "error": "no-target",
                                "path": ref.get("path")})
                continue
            with open(target, "wb") as handle:
                handle.write(blob)
            ref["box"] = clipped
            ref["clipped"] = {"above": above, "below": below}
            if extended:
                # 這一張圖是同一個圖物件跨過列界，所以整張留著（`clip_keeping_pictures`）。
                # 標記它：這一列下次再被這支程式看到時不會被切回半張。
                ref["whole_picture"] = True
            ref["bytes"] = len(blob)
            if extended:
                stats["kept-whole"] += 1
                ref["picture_crossing"] = dict(zip(("above", "below"),
                                                   picture_crossing(extended, extent)))
                set_ownership_note(ref, ref["picture_crossing"]["above"],
                                   ref["picture_crossing"]["below"], whole=True)
            else:
                set_ownership_note(ref, above, below, whole=False)
            stats["clipped"] += 1
            kept.append(ref)
            record = {"key": row.get("candidate_key"), "number": number,
                      "file": ref.get("raw_ref"), "box": clipped, "was": [
                          round(float(v), 1) for v in box],
                      "above": above, "below": below, "bytes": len(blob)}
            if extended:
                record["kept_whole"] = "the-picture-object-crosses-this-questions-rows"
                record["picture"] = extended
                record["picture_crossing"] = ref["picture_crossing"]
            records.append(record)
        row["image_refs"] = [ref for ref in (row.get("image_refs") or [])
                             if not (isinstance(ref, dict)
                                     and ref.get("asset_role") == "figure-crop")
                             or ref in kept]

    changed = sum(1 for row, text in zip(rows, before_text)
                  if json.dumps(row, ensure_ascii=False, sort_keys=True) != text)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copyfile(candidates_path, candidates_path + ".before-ownership-" + stamp)
    temporary = candidates_path + ".partial"
    with open(temporary, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temporary, candidates_path)
    with open(os.path.join(directory, "figure_ownership.json"), "w", encoding="utf-8") as handle:
        json.dump({"queue": queue_root, "candidates": candidates_path, "stamp": stamp,
                   "stats": dict(stats), "rows_changed": changed, "records": records,
                   "directives": directives},
                  handle, ensure_ascii=False, indent=2)
    print("=== 圖片歸屬（每一張圖只切這一題自己的列）")
    print("  卷 %s" % queue_root)
    print("  本來就在自己列裡 %d   裁到自己的列 %d   整張在別題（移除） %d   列量不到（只標記） %d"
          % (stats["inside"], stats["clipped"], stats["dropped"], stats["unverified"]))
    print("  只剩一小片（存活 < %gpt 或不到原框一半，移除） %d   明細：records 裡 dropped=sliver"
          % (SLIVER_MIN_HEIGHT, stats["sliver"]))
    print("  照人說過的話重切（這一題有人留話） %d" % stats["human-flagged"])
    print("  依紙本量到的圖重切（框對不到圖，改成這一題自己的圖） %d" % stats["widened"])
    print("  量到的圖小得不像一張圖（< 這一題範圍的 30%%）⇒ 當成量不到 %d" % stats["fragmentary-picture"])
    print("  量不到圖物件 ⇒ 整題縫成一張（不交出會被切掉的窄條） %d" % stats["whole-question"])

    print("  他說這一題不要圖（no-figure／多截）⇒ 不放圖（移除） %d" % stats["dropped-by-note"])
    print("  明確要求保留原裁切 %d" % stats["directive-kept"])
    print("  讀他的話：這一輪問了 %d 句，讀不出來 %d 句（快取在 figure_ownership.json 的 directives）"
          % (stats["directive-asked"], stats["directive-errors"]))
    print("  其中 %d 張是紙本的圖物件跨過列界（整張留著，不切半張圖）" % stats["kept-whole"])
    print("  更新候選列 %d   明細 review-ui/figure_ownership.json" % changed)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cut figure crops into packaged runs.")
    parser.add_argument("--work", nargs="+", help="batch_package work roots")
    parser.add_argument("--queue", metavar="DIR",
                        help="a review queue: cut the table crops its own readings asked for "
                             "(`review-ui/question_ai_findings.jsonl`), instead of cutting figures "
                             "into packaged runs")
    parser.add_argument("--think", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-describe", action="store_true",
                        help="cut the crops without asking the model what they contain")
    parser.add_argument("--only", nargs="*", default=None,
                        help="run directory names in `--work` mode; candidate keys, `q65`, or "
                             "question numbers in `--queue` mode")
    parser.add_argument("--fix-figure-ownership", action="store_true",
                        help="with --queue: re-cut every figure crop to the rows its own question "
                             "owns, drop the ones that belong to the question above or below and the "
                             "ones only a sliver of which survives, and mark the ones whose rows "
                             "could not be measured")
    parser.add_argument("--human-flagged", action="store_true",
                        help="with --queue --fix-figure-ownership: only the questions somebody left "
                             "a note on (the pass the repair loop runs every cycle; the whole-book "
                             "measurement is not affordable there)")
    parser.add_argument("--reannotate-only", action="store_true",
                        help="recompute disputes on the existing rows without re-cutting any crop; "
                             "for when a detector changed and the pictures did not")
    args = parser.parse_args()

    if args.queue and args.work:
        parser.error("--queue cuts the crops a queue's readings asked for; --work cuts figures into "
                     "packaged runs. Pass one of them.")
    if args.queue and args.fix_figure_ownership:
        fix_queue_figure_ownership(args)
        return
    if args.queue:
        cut_queue_tables(args)
        return
    if not args.work:
        parser.error("one of --work (packaged runs) or --queue (a review queue) is required")

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
