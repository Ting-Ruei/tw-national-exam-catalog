"""逐張驗證裁切是否完整——用紙張自己回答，不用模型。

## 為什麼不是叫模型看

使用者要求「每張圖都要用模型確認」。這一關做過了，而且量測結果是**模型做不到**：

- 40 組已知壞裁切（舊版）＋ 40 組已知好裁切（新版），模型對**全部 80 張**都回答 `same=true`。
  召回 0%、誤報 0% —— 不是它判斷正確，是它只會說同一個答案。
- 對照組再往前推：把左圖換成該結構**最上面 8 pt 的細條**（幾乎什麼都沒有），
  模型仍然 7/8 回答 `same=true`。
- 這與先前的量測一致：978 個選項裁切與 PDF 內嵌物件**位元組完全相同**（不可能被裁壞），
  模型把 56 個判成 clipped；同一張圖重複問 131 次，模型自我矛盾 39 次。

單張圖沒有參照點，模型只能憑印象，而印象不是證據。所以「有沒有裁壞」這一題必須由紙張回答。

## 這一關問的問題

**「這張裁切的框，是不是紙本上那個圖的完整範圍？」**

這是可以量測的，而且有兩個互相獨立的訊號：

1. **同一張圖的其它切片。** PDF 常把一個圖拆成好幾條等寬、上下相接的物件。
   若框外面緊接著一張等寬且相接的物件，這個框就只是那個圖的一部份。
   量測在 `1151_藥師(一)_藥學(一)` Q41：選項 C 的框是 `xref` 16（y=341.3-392.4），
   而 `xref` 17（y=392.4-443.5）與它同寬且相接 —— 框少了半個結構。

2. **框外的墨跡。** 就算圖不是由多個物件組成，紙本也可能在同一個 x 範圍、
   緊貼著框的下緣或上緣繼續畫。框外的墨跡密度就是那個圖被切掉的量。

兩個訊號都指向同一件事：**框太小**。這正是使用者回報的那一類缺陷
（第 41、46、51 題），而兩個訊號都不需要模型。
"""

import os

from . import extract
from . import vision

#: A neighbouring object counts as the same picture's next strip when its width matches this
#: closely. Measured on Q41 of `1151_藥師(一)_藥學(一)`: the strips are identical to 0.0 pt
#: (50.4-225.4 for C, 50.4-388.1 for D), while genuinely different pictures in the corpus differ
#: by 12 pt or more.
WIDTH_TOLERANCE = 0.5

#: How far outside the bound box a neighbouring object may start and still count as touching.
TOUCH = 1.0

#: Ink density just outside the box that means the picture continues. The ink measure is the
#: fraction of non-background pixels, so a rule or a table line is well under this and a drawn
#: structure or a photograph is well over it. Measured on the Q41 strips: the ring below C's
#: truncated box holds 0.11, while a ring below a complete crop holds 0.001 or less.
MIN_RING_INK = 0.02


def _ring_ink(pdf_path, page, box, *, side, thickness=6.0, dpi=100):
    """Drawing ink in the strip just outside one edge of `box`, with printed text excluded.

    Measured on `1012_藥師_藥理學與藥物化學` Q46 option D: counting every non-white pixel put this
    ring at 0.18, and all of it was the following question's stem. With the text spans removed the
    same ring holds 0.000 - the structure really does end where the box ends.
    """
    x0, y0, x1, y1 = (float(value) for value in box)
    if side == "below":
        ring = (x0, y1, x1, y1 + thickness)
    else:
        ring = (x0, y0 - thickness, x1, y0)
    if ring[3] <= ring[1] or ring[2] <= ring[0]:
        return 0.0
    # A ring at the very foot of a page can fall outside it, and rendering a rectangle that is not
    # on the page fails outright. Clipping to the page keeps the measurement to what the paper can
    # actually show: there is no ink past the edge because there is no page.
    try:
        import pymupdf
        document = pymupdf.open(filename=pdf_path)
        try:
            bounds = document[int(page) - 1].rect
        finally:
            document.close()
        ring = (max(ring[0], bounds.x0), max(ring[1], bounds.y0),
                min(ring[2], bounds.x1), min(ring[3], bounds.y1))
        if ring[3] <= ring[1] or ring[2] <= ring[0]:
            return 0.0
    except Exception:
        pass
    # `ink_without_text` returns `None` when the render itself fails, which is a finding about the
    # run and not about the crop. Treating it as zero keeps one bad page from voiding a report.
    value = vision.ink_without_text(pdf_path, int(page), ring, dpi=dpi)
    return 0.0 if value is None else value


def sibling_strips(images, page, box, *, width_tolerance=WIDTH_TOLERANCE, touch=TOUCH,
                   min_height=None):
    """Placed objects that continue `box` as the next strip of the same picture.

    Returns a list of `{"xref", "side", "box"}`. An empty list means the box is not one slice of a
    multi-object picture.
    """
    if min_height is None:
        # A *sibling* has a lower floor than a picture does. The floor exists to keep a glyph or a
        # rule from being mistaken for a figure, and that is about choosing an option's picture, not
        # about recognising the next strip of one already chosen. Measured on
        # `1081_藥師(一)_藥理學與藥物化學` Q61 option A: its second strip is 22.8 pt tall, 1.2 pt under
        # the picture floor, and excluding it hid a real defect.
        from .vision import MIN_FIGURE_HEIGHT
        min_height = MIN_FIGURE_HEIGHT / 2.0
    x0, y0, x1, y1 = (float(value) for value in box)
    width = x1 - x0
    found = []
    for image in images:
        if int(image["page"]) != int(page):
            continue
        ix0, iy0 = float(image["x0"]), float(image["y0"])
        ix1, iy1 = float(image["x1"]), float(image["y1"])
        if abs((ix1 - ix0) - width) > width_tolerance:
            continue
        # A sibling has to be a picture. Measured on `1091_藥師(一)_藥劑學與生物藥劑學` Q53 option C
        # and `1082_藥師(一)_藥理學與藥物化學` Q73 option C: the strip below is 0.8 and 0.9 pt tall,
        # a rule the producer emitted as an object, and joining it to the option's box adds nothing
        # while reporting a defect that does not exist.
        if (iy1 - iy0) < min_height:
            continue
        # Same x range, and starting where the box ends (or ending where it starts).
        if abs(ix0 - x0) > touch:
            continue
        if abs(iy0 - y1) <= touch:
            found.append({"xref": image.get("xref"), "side": "below",
                          "box": (ix0, iy0, ix1, iy1)})
        elif abs(iy1 - y0) <= touch:
            found.append({"xref": image.get("xref"), "side": "above",
                          "box": (ix0, iy0, ix1, iy1)})
    return found


def verify_one(pdf_path, page, box, *, images=None, sides=("below", "above"),
               min_ring_ink=None, band=None, next_question=None):
    """Whether the box holds the whole picture the paper draws there.

    The `siblings` test is the whole check by default, and the ring-ink test is off because it was
    measured to be wrong: see `MIN_RING_INK`.

    `band` is `(page, top, bottom, next_page, next_marker)` - the option's own band, ending at the
    next option's marker, plus that marker's page so a cross-page marker is not mistaken for a bound
    on this page. It is what makes the sibling test mean anything. Without it, a sibling below the box is just as
    likely to be the **next option's** picture: measured on
    `1091_藥師(一)_藥劑學與生物藥劑學` Q53 option C, whose sibling is the formula belonging to option
    D, and on `1071_藥師(一)_藥理學與藥物化學` Q76 option B, whose sibling is the structure printed
    under option C. With the band required, those are excluded while the real defect on
    `1151_藥師(一)_藥學(一)` Q41 is kept, because there the next strip lies inside the same option's
    band.

    The band is compared by **the next marker's own position**, not by the binder's reach-shortened
    value, and a band on another page constrains nothing. Both were measured on
    `1081_藥師(一)_藥理學與藥物化學`: Q61 option A's marker prints at the foot of page 10 while its
    structure is at the head of page 11, and Q63 option C's second strip ends at y=213 while the
    next marker starts at y=217.9 - 5 pt past the reach-shortened bound, yet clearly still this
    option's own picture. Neither is a defect and both were reported as one.

    Returns a dict with `complete` plus the evidence for the verdict, so a report can show *why* a
    crop was sent back rather than only that it was.
    """
    if images is None:
        images = extract.extract_images_a(pdf_path)
    siblings = sibling_strips(images, page, box)
    if band is not None:
        band_page, band_top, band_bottom, next_page, next_marker = band
        kept = []
        for item in siblings:
            start, end = item["box"][1], item["box"][3]
            if int(band_page) == int(page) and start < float(band_top):
                continue
            # The bound is the next option's marker, but only when that marker is on the *picture's*
            # page. Measured on `1071_藥師(一)_藥理學與藥物化學` Q76 option B: its marker is on page 12
            # and its structure at the head of page 13, where `C.` prints at y=133.2 - so the strip
            # running to y=245.4 is C's own picture, not B's second half.
            #
            # When the marker and the picture are on different pages, the option's own band is not a
            # bound at all, and the strip below is this option's picture until the next *question*
            # begins. Measured on `1081_藥師(一)_藥理學與藥物化學` Q61 option A: `A.` prints at the foot
            # of page 10 and its structure runs down page 11 as `xref` 89 and 90, with the next
            # question starting at page 11 y=541.7.
            if next_page is not None and int(next_page) == int(page):
                limit = next_marker
            elif int(band_page) != int(page):
                limit = next_question
            else:
                limit = band_bottom
            if limit is not None and end > float(limit) + 1.0:
                continue
            kept.append(item)
        siblings = kept
    reasons = []
    if siblings:
        reasons.append("same-picture-strips:" + ",".join(
            f"{item['side']}/{item['xref']}" for item in siblings))
    rings = {}
    if min_ring_ink is not None:
        rings = {side: round(_ring_ink(pdf_path, page, box, side=side), 5) for side in sides}
        worst = max(rings.values()) if rings else 0.0
        if worst >= min_ring_ink:
            side = max(rings, key=lambda key: rings[key])
            reasons.append(f"ink-continues-{side}:{worst}")
    return {"complete": not reasons, "page": int(page),
            "box": [round(float(value), 1) for value in box],
            "siblings": siblings, "ring_ink": rings, "reasons": reasons}


def option_bands(kept, item, alphabet=(), *, reach=None):
    """Each option's band as `{key: (page, top, bottom, next_marker)}`, or `{}` when none stated.

    The band is the region an option's own picture may occupy: from its marker down to the next
    marker. `bottom` is the binder's reach-shortened bound and `next_marker` is the following
    marker's own top - the verifier uses the latter, because "does this picture reach the next
    option's marker" is the question, and the reach is a binding margin rather than a picture edge.
    For the last option `bottom` is `None` and `next_marker` is too, since nothing follows it.

    This is the measurement `option_figures` binds with, restated here so the verifier can ask the
    same question. The two must not diverge: a verifier that uses a different band than the binder
    will report defects the binder cannot produce and miss the ones it does.
    """
    if reach is None:
        from .vision import OPTION_REACH
        reach = OPTION_REACH
    by_id = dict(enumerate(kept, start=1))
    cells = (item.get("cells") or {}) if isinstance(item, dict) else {}
    marks = []
    for key, ids in ((cells.get("options") or {}) or {}).items():
        for cell in (ids if isinstance(ids, (list, tuple)) else ()):
            row = by_id.get(cell)
            if row is None:
                continue
            marks.append((str(key), int(row["page"]), float(row["y0"])))
    if len(marks) < 2:
        return {}
    marks.sort(key=lambda mark: (mark[1], mark[2]))
    out = {}
    for position, (key, page, y0) in enumerate(marks):
        follower = marks[position + 1] if position + 1 < len(marks) else None
        same_page = follower is not None and follower[1] == page
        bottom = (follower[2] - reach) if same_page else None
        out[key] = (page, y0 - reach, bottom,
                    follower[1] if follower is not None else None,
                    follower[2] if follower is not None else None)
    return out
