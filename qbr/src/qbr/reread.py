# -*- coding: utf-8 -*-
"""Read a question back off the page and put the two readings side by side.

Task 21 at build time. The idea is the one a reviewer already performs by hand: look at the page,
and compare it with what the pipeline wrote down. What is added here is that the comparison is
**mechanical** - the model is asked only to transcribe what it sees, and the text is diffed here,
deterministically, character by character.

The division of labour is deliberate and follows what has been measured in this project:

    the model   transcribes a crop it can see. Measured: it reads `長` off a page whose text layer
                stored `⻑` (U+2ED1), and it reads a two-column table correctly (`Time (h) | 1 | 12`).
    this module crops, compares, and reports. It never asks the model whether a question is good.
    a human     decides. `GOV-05` holds unchanged: nothing here writes a review event, and nothing
                here rewrites a character.

Why not ask the model to judge
------------------------------
Measured, and already recorded in this project: a model cannot arbitrate a crop (978 option crops
were byte-identical to the PDF's own embedded objects, and the model called 56 of them clipped), and
the same crop asked 131 times produced 39 self-contradictions. So the model is not asked for a
verdict. It is asked for a transcription, which is a different question with a checkable answer -
the page either says `長` or it does not.

What this is not
----------------
It is not a substitute for the deterministic layers, and it must not be used as one. Where a defect
can be *measured* - the raised Latin letters (`extract.py`), the substituted ideographs
(`disputes.py`), a dangling answer - there is no reason to spend a render and an inference on it,
and doing so would replace a certainty with an impression. This is for what is left over: whether
the reading a person is about to be shown is the reading the paper actually carries.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request

from . import ai_findings, engines, reflow, vision

#: Where the transcription model lives. It defaults to **Occamy**, the engine the rest of the AI
#: pass now uses (designer's ruling, 2026-09-29: 「之後要換就整套換」) - and because pointing this
#: module at an engine whose off-switch it did not know about was a real bug: `transcribe` used to
#: send MTPLX's `chat_template_kwargs.enable_thinking`, which Splash accepts with HTTP 200 and
#: ignores. Measured on 8088: 31.5s with 612 characters of reasoning, against 6.7s and none with the
#: right spelling. The spelling now lives with the engine (`qbr.engines`), so it cannot be sent to
#: the wrong one.
#:
#: Splash was the old default and its endpoint has been down since 2026-09-25 (measured: connection
#: refused on 8088). Nothing in the pipeline reaches the model through `transcribe` or
#: `reread_question` - the live page reading is `confirm_dispute.transcribe`, which is handed an
#: endpoint by its caller - so the old value broke nothing that runs today; it was a landmine for a
#: manual `reread_question` call.
#:
#: `QBR_REREAD_*` overrides the *engine* (name and URL), not just the URL, so a caller can point
#: this at another engine without the request keeping an Occamy-shaped switch.
DEFAULT_ENGINE = os.environ.get("QBR_REREAD_ENGINE", "occamy-6bit")


def engine_name() -> str:
    """The engine name to transcribe with, read **now** rather than at import.

    Reading it here means a long-running process (or a test that sets `QBR_REREAD_ENGINE` between
    calls) is not pinned to whatever the variable held when the module was first imported - the
    frozen-at-import shape is exactly why repointing this module required a restart.
    """
    return os.environ.get("QBR_REREAD_ENGINE", DEFAULT_ENGINE)


def endpoint():
    """The engine to transcribe with, as the one table spells it.

    Built from `engines.endpoints()` - the live table - so that the thinking switch travels with the
    engine and a change of address (or engine) takes effect without a restart.
    `QBR_REREAD_BASE_URL`/`_MODEL`/`_API_KEY` still override the address, but the switch comes from
    the named engine - a caller pointing this at Splash gets Splash's switch, not MTPLX's.
    """
    table = engines.endpoints()
    name = engine_name()
    if name not in table:
        raise KeyError("unknown QBR_REREAD_ENGINE %r; known: %s" % (name, ", ".join(sorted(table))))
    engine = dict(table[name])
    engine["url"] = os.environ.get("QBR_REREAD_BASE_URL") or engine["url"]
    engine["name"] = os.environ.get("QBR_REREAD_MODEL") or engine["name"]
    engine["key"] = os.environ.get("QBR_REREAD_API_KEY") or engine.get("key", "")
    return engine

#: The one instruction that matters is "do not correct what you see". A model that silently fixes a
#: typo makes this whole exercise worthless, because the disagreement being looked for is exactly
#: the place where the reading and the page differ.
#:
#: Rules 3 and 5 are the shared blocks from `ai_findings`, interpolated rather than written again:
#: the same two rules are sent by `vision.DISPUTE_SYSTEM`, and a second copy here would be a second
#: rule that nothing keeps in step. Rule 3 used to ask for the opposite - Unicode sub/superscript
#: characters and no markup at all - which cannot spell a subscript capital (`GABAA` became
#: `GABA` + `U+2090`) while the bank's own convention is markup (6,540 rows of `candidates.jsonl`
#: against 55 with Unicode characters).
SYSTEM = (
    "你是一個 OCR 工具，不是審查員。\n"
    "使用者給你一張考卷題目的截圖，你要逐字轉錄，**不要**解釋、**不要**解題、"
    "**不要**修正任何你認為是錯的字或格式。\n"
    "只輸出一個 JSON：{\"stem\":\"題幹\",\"options\":{\"A\":\"...\",\"B\":\"...\","
    "\"C\":\"...\",\"D\":\"...\"}}"
    "（題幹本身是表格時多一個欄位：{\"table_lines\":[\"表格的其中一行\"]}）\n"
    "\n"
    "規則：\n"
    "1. stem 不含題號，從題號後面開始。若題幹裡有表格，把表格的文字依序寫進去。\n"
    "2. options 的值只寫標記後面的內容，不要含「A.」。\n"
    "3. " + ai_findings.SUBSCRIPT_MARKUP_RULE + "\n"
    "4. 看不清楚的字寫 ▢，不要猜。\n"
    "5. " + ai_findings.TABLE_LINES_RULE + "\n"
    "6. 即使是明顯的錯字也要照原樣轉錄。\n"
    "7. " + ai_findings.ITALIC_MARKUP_RULE + "\n"
)

#: What the model writes when it ignores rule 3. Folding these rather than rejecting the answer
#: keeps a correct transcription usable: measured on 20 questions, the model's only deviation from
#: the instructed spelling was LaTeX for the offsets, and each one was correct in content.
_LATEX_SUP = {"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷",
              "8": "⁸", "9": "⁹", "-": "⁻", "+": "⁺", "n": "ⁿ", "i": "ⁱ"}
_LATEX_SUB = {"0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆", "7": "₇",
              "8": "₈", "9": "₉", "-": "₋", "+": "₊", "a": "ₐ", "e": "ₑ", "p": "ₚ", "t": "ₜ"}
_LATEX_TAIL = re.compile(r"(?:\^|_)\{([^{}]{1,8})\}")
_LATEX_ONE = re.compile(r"(?:\^|_)([0-9a-zA-Z+\-])")


def _latex_fold(text):
    """Turn `^{-kt}`, `_p` and `10^{-5}` into the characters a paper actually prints."""
    def tail(match):
        body = match.group(1)
        table = {**_LATEX_SUP, **_LATEX_SUB}
        return "".join(table.get(char, char) for char in body)

    value = _LATEX_TAIL.sub(tail, text or "")
    return _LATEX_ONE.sub(lambda m: {**_LATEX_SUP, **_LATEX_SUB}.get(m.group(1), m.group(1)), value)


def comparison_form(text, *, drop_leading_number=False):
    """The image two readings are compared in.

    NFKC is applied for the same reason the rest of the pipeline applies it (see `canon.fold`): these
    papers print compatibility ideographs and the Kangxi radicals, and both fold onto the ordinary
    character. Whitespace is removed because where a line broke is a typesetting detail, not content.

    NFKC also folds the raised and lowered forms back to ordinary letters (`Cₚ` -> `Cp`). That is
    the right thing here and the wrong thing in `extract.py`: the offset defect there is about
    **position**, which is read from the page's geometry, while this comparison asks only whether
    the same **characters** are present. Two questions, two rulers - which is the lesson this
    project has now recorded three times.
    """
    value = _latex_fold(text)
    if drop_leading_number:
        value = re.sub(r"^[ \t]*\d{1,3}[ \t]*[.、．]?", "", value or "", count=1)
    folded = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"[\s\u3000\u00a0]+", "", folded)


def parse(content):
    """The JSON the model was asked for, or None. A failed parse is reported, never guessed at."""
    if not content:
        return None
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < 0:
        return None
    try:
        parsed = json.loads(content[start:end + 1])
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    options = parsed.get("options")
    if isinstance(options, dict):
        parsed["options"] = {str(k).strip().upper(): v for k, v in options.items()}
    return parsed


def options_of(item):
    """A question's options as `{key: text}`, accepting both shapes the pipeline produces."""
    raw = item.get("options") or {}
    if isinstance(raw, dict):
        return {str(k): (v.get("text") if isinstance(v, dict) else (v or ""))
                for k, v in raw.items()}
    out = {}
    for entry in raw:
        if isinstance(entry, dict):
            out[str(entry.get("key"))] = entry.get("text") or ""
        else:
            out[str(entry)] = ""
    return out


def compare(pipeline, seen):
    """Character differences between the pipeline's reading and the page's.

    Returns a report, not a verdict. `stem_differs` and `option_differs` name the places they
    disagree with both readings shown, so a reviewer can put them beside the crop and decide.
    """
    report = {"stem_differs": None, "option_differs": {}, "parse_error": None}
    if seen is None:
        report["parse_error"] = "unparsed"
        return report
    left = comparison_form(pipeline.get("stem"), drop_leading_number=True)
    right = comparison_form(seen.get("stem"), drop_leading_number=True)
    if left != right:
        report["stem_differs"] = {"pipeline": left[:300], "page": right[:300],
                                  "pipeline_length": len(left), "page_length": len(right)}
    mine, theirs = pipeline.get("options") or {}, seen.get("options") or {}
    for key in sorted(set(mine) | set(theirs)):
        a = comparison_form(mine.get(key))
        b = comparison_form(theirs.get(str(key)))
        if a != b:
            report["option_differs"][key] = {"pipeline": a[:160], "page": b[:160]}
    return report


def question_starts(kept_rows, *, max_number=250):
    """`(ordered_rows, {number: row index})` — where each question's number row sits.

    The rule is **not written here**. `reflow.skeleton` already reads a paper's question numbers
    with rules that were each measured against a paper that broke them (the first cell of a question
    is the leftmost cell on its visual line, 99.995% of 76,120 questions; a number is accepted in
    reading order, expecting the next one; a cell at the body margin) and the *whole* pipeline is
    built on its answer: `item["number"]` in the reading, the candidate row's `question_number`, the
    `qNNN` crop names. This function hands that same answer to the crop.

    **That is the defect this ends.** Two rulers described the same paper and disagreed: the
    skeleton accepts a number cell by its text alone (a cell that is nothing but digits *is* a
    question number - `line_table` says so in its `number` hint), while the crop ruler demanded
    `NN.`/`NN、` in the same cell. Measured on `1011_醫事檢驗師_微生物學及臨床微生物學`: the page
    prints the number as its own cell - **80 bare number cells, 80 of them the leftmost cell on
    their visual line, 0 dotted** - so the skeleton numbered all 80 questions while the crop ruler
    found **0**, and every question on that paper was read as `no-rows` (measured in the
    2026-09-25T05:58Z round: **44 of 46 reads, 95.7%**, 37 of them `lost-glyph`). The model never saw
    a picture of those questions; the person saw 「紙本讀不到」.

    Three sources, in this order, so the answer can only ever find **more** than before:

      1. the skeleton's own starts (the authority above);
      2. the `NN.`/`NN、` form the previous version matched, kept verbatim - it is what answers for a
         paper whose numbering does not begin at 1 or is missing a number early, where the skeleton
         refuses to name any question;
      3. a cell whose `number` hint is set *and* which is the leftmost on its visual line - the same
         guard the skeleton uses, from `reflow.leftmost_cells`. It is the last resort because the
         hint alone cannot tell a question number from a table's own `100` (that measurement is in
         `reflow.skeleton`'s docstring), and neither can the leftmost test on a page whose body is
         one narrow column.
    """
    ordered = sorted(kept_rows, key=lambda row: (int(row.get("page") or 0),
                                                 float(row.get("y0") or 0.0)))
    starts = {}
    table = None
    try:
        # `line_table` is given the rows in reading order and numbers them from 1, so a cell id
        # minus one is an index into `ordered`.
        table = reflow.line_table(ordered)
        reading = reflow.skeleton(table)
        for number, entry in (reading.get("questions") or {}).items():
            stem = list((entry or {}).get("stem") or ())
            if stem and 1 <= int(number) <= max_number:
                starts[int(number)] = int(stem[0]) - 1
    except Exception:
        # A paper the skeleton cannot describe must still be croppable: the two fallbacks below are
        # the previous behaviour exactly.
        table, starts = None, {}
    # 這一條刻意保留成**無條件跑**（不是「骨架找不到才跑」）。量過的理由有兩個：它對骨架已經命名的
    # 號碼不生效（`number not in starts`），而且骨架自己的規則也收 `234.8）` 這種折行（實測：把它
    # 插在 q1 之後，`reflow.skeleton` 會給出 `question-234`）——所以「骨架比較嚴」不是事實，用它當
    # 閘門的規則會是一條量不到差別、卻讓這一支比現在更嚴的規則。`band_rows` 只會被問骨架命名過的
    # 號碼，多出來的條目問不到，留著它只是讓「不比以前差」成立。
    head = re.compile(r"^[ \t]*(\d{1,3})[ \t]*[.、．]")
    for index, row in enumerate(ordered):
        match = head.match((row.get("text") or "").strip())
        if not match:
            continue
        number = int(match.group(1))
        if 1 <= number <= max_number and number not in starts:
            starts[number] = index
    if table is not None:
        leftmost = reflow.leftmost_cells(table)
        for line_id, text, hint in table:
            stripped = (text or "").strip()
            if (hint or "") != "number" or not stripped.isdigit() or line_id not in leftmost:
                continue
            number = int(stripped)
            if 1 <= number <= max_number and number not in starts:
                starts[number] = line_id - 1
    return ordered, starts


def band_rows(kept_rows, number, *, max_number=250):
    """The rows a question owns: its own number down to the next number, across page breaks.

    Reading order, not page order, so a question whose stem continues onto the next page is read
    whole. Measured on Q49 of `1081_藥師(一)_藥劑學`: the stem ends on page 7, its table sits at the
    top of page 8, and the options are below the table - a crop that stopped at the page break would
    have shown a question with no data in it.

    Which row carries a question's number is decided by `question_starts` (the paper's own skeleton,
    with the two fallbacks that keep this from being stricter than it was).
    """
    ordered, starts = question_starts(kept_rows, max_number=max_number)
    try:
        wanted = int(number)
    except (TypeError, ValueError):
        return []
    start = starts.get(wanted)
    if start is None:
        return []
    end = len(ordered)
    for value in starts.values():
        if start < value < end:
            end = value
    return ordered[start:end]


def page_extents(rows, boxes=()):
    """Per page, the region a question's band actually covers: `{page: [x0, y0, x1, y1]}`.

    **The rows alone are not the question.** A question whose options are pictures has almost no
    text where the pictures are: the markers `B.`/`C.`/`D.` were extracted as narrow rows (x 39.2 to
    50.3 on page 9 of `1152_藥師(一)_藥學(一)`) while the pictures beside them reach x 158.4 and run
    y 34.6 to 537.1. A crop built from the rows' own bounding box is therefore an 11-point-wide
    sliver that shows the labels and **none of the pictures** - measured on that paper, the vision
    model reported options B/C/D as blank white space and answered `▢` ("cannot read") for them,
    which is the reading that then gets proposed as a "repair" (127 such changes in the stream, 96 of
    them with an empty extraction side).

    So the region is the rows **unioned with the question's own picture boxes** (the row's
    `image_refs`: step ⑤ measured them, and they carry `page` + `box`). Only this question's boxes
    are taken, so a crop cannot inherit a neighbour's picture by this route. A page carrying one of
    these boxes but none of the band's rows contributes **the box alone**, never that page's text.
    """
    extents = {}
    for row in rows:
        page = int(row.get("page") or 0)
        x0, y0 = float(row.get("x0") or 0.0), float(row.get("y0") or 0.0)
        x1 = float(row.get("x1") or row.get("x0") or 0.0)
        y1 = float(row.get("y1") or row.get("y0") or 0.0)
        current = extents.get(page)
        extents[page] = [min(current[0], x0), min(current[1], y0),
                         max(current[2], x1), max(current[3], y1)] if current else [x0, y0, x1, y1]
    for entry in boxes or ():
        if not isinstance(entry, dict):
            continue
        box = entry.get("box")
        page = int(entry.get("page") or 0)
        if not box or page <= 0 or len(box) != 4:
            continue
        x0, y0, x1, y1 = (float(value) for value in box)
        current = extents.get(page)
        extents[page] = [min(current[0], x0), min(current[1], y0),
                         max(current[2], x1), max(current[3], y1)] if current else [x0, y0, x1, y1]
    return extents


def crop_rows(pdf_path, rows, *, dpi=vision.DEFAULT_DPI, margin=vision.MARGIN, gap=8, boxes=()):
    """One PNG for a question, one page's region at a time, stitched top to bottom.

    `boxes` are the question's own picture boxes (see `page_extents`); omitting them is the old
    behaviour, kept as the default because a caller with no picture evidence has nothing to add.
    """
    from PIL import Image

    pieces = []
    for page, box in sorted(page_extents(rows, boxes).items()):
        if page <= 0:
            continue
        png = vision.crop_region(pdf_path, page, box, dpi=dpi, margin=margin)
        pieces.append(Image.open(io.BytesIO(png)).convert("RGB"))
    if not pieces:
        return b""
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


def transcribe(png_bytes, *, think=False, max_tokens=2000, timeout=300):
    """Ask the model what the crop says. Returns `(content, seconds)`; raises only on transport error.

    `think=True` is only meaningful for an engine that can think; when the engine has no on-switch
    recorded, the request is sent as-is rather than guessed at. The default engine (Splash) is asked
    with its own off-switch, which is what makes a transcription a five-second call rather than a
    thirty-second one.
    """
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text": "請轉錄這張截圖。"},
            {"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(png_bytes).decode()}}]},
    ]
    engine = endpoint()
    if think and "thinking_on" in engine:
        engine = {**engine, "reasoning": None, "thinking": engine["thinking_on"]}
    request = urllib.request.Request(
        engines.endpoint_url(engine["url"]),
        data=json.dumps(engines.body_for(engine, messages, max_tokens=max_tokens)).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + engine.get("key", "")})
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as handle:
            payload = json.loads(handle.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError("HTTP %s: %s" % (exc.code, exc.read().decode()[:200]))
    return engines.content_of(payload), round(time.time() - started, 1)


def reread_question(pdf_path, number, item, kept_rows, *, out_png=None, think=False,
                    dpi=vision.DEFAULT_DPI):
    """One question, read back off the page and compared. Never applies anything."""
    rows = band_rows(kept_rows, number)
    record = {"number": number, "rows": len(rows)}
    if not rows:
        record["error"] = "no-rows"
        return record
    png = crop_rows(pdf_path, rows, dpi=dpi)
    record["png_bytes"] = len(png)
    if not png:
        record["error"] = "no-crop"
        return record
    if out_png:
        with open(out_png, "wb") as handle:
            handle.write(png)
        record["png"] = out_png
    try:
        content, seconds = transcribe(png, think=think)
    except Exception as exc:
        record["error"] = "%s: %s" % (type(exc).__name__, exc)
        return record
    seen = parse(content)
    record.update({"seconds": seconds, "seen": seen, "raw": content[:800],
                   "diff": compare({"stem": item.get("stem") or "",
                                    "options": options_of(item)}, seen)})
    return record
