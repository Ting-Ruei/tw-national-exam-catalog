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

from . import engines, vision

#: Where the transcription model lives. It defaults to the **Splash** endpoint, because that is the
#: engine the rest of the AI pass uses - and because pointing this module at an engine whose
#: off-switch it did not know about was a real bug: `transcribe` used to send MTPLX's
#: `chat_template_kwargs.enable_thinking`, which Splash accepts with HTTP 200 and ignores. Measured
#: on 8088: 31.5s with 612 characters of reasoning, against 6.7s and none with the right spelling.
#: The spelling now lives with the engine (`qbr.engines`), so it cannot be sent to the wrong one.
#:
#: `QBR_REREAD_*` overrides the *engine* (name and URL), not just the URL, so a caller can point
#: this at MTPLX without the request keeping a Splash-shaped switch.
ENGINE = os.environ.get("QBR_REREAD_ENGINE", "splash")
BASE_URL = os.environ.get("QBR_REREAD_BASE_URL", engines.ENDPOINTS[ENGINE]["url"])
MODEL = os.environ.get("QBR_REREAD_MODEL", engines.ENDPOINTS[ENGINE]["name"])
API_KEY = os.environ.get("QBR_REREAD_API_KEY", engines.ENDPOINTS[ENGINE].get("key", ""))


def endpoint():
    """The engine to transcribe with, as the one table spells it.

    Built from the table rather than kept as a module constant so that the thinking switch travels
    with the engine. `QBR_REREAD_BASE_URL`/`_MODEL` still override the address, but the switch comes
    from the named engine - a caller pointing this at Splash gets Splash's switch, not MTPLX's.
    """
    engine = dict(engines.ENDPOINTS[ENGINE])
    engine["url"], engine["name"], engine["key"] = BASE_URL, MODEL, API_KEY
    return engine

#: The one instruction that matters is "do not correct what you see". A model that silently fixes a
#: typo makes this whole exercise worthless, because the disagreement being looked for is exactly
#: the place where the reading and the page differ.
SYSTEM = (
    "你是一個 OCR 工具，不是審查員。\n"
    "使用者給你一張考卷題目的截圖，你要逐字轉錄，**不要**解釋、**不要**解題、"
    "**不要**修正任何你認為是錯的字或格式。\n"
    "只輸出一個 JSON：{\"stem\":\"題幹\",\"options\":{\"A\":\"...\",\"B\":\"...\","
    "\"C\":\"...\",\"D\":\"...\"}}\n"
    "\n"
    "規則：\n"
    "1. stem 不含題號，從題號後面開始。若題幹裡有表格，把表格的文字依序寫進去。\n"
    "2. options 的值只寫標記後面的內容，不要含「A.」。\n"
    "3. 輸出**純文字**，不要用 LaTeX 或任何標記法。上標寫成一般符號（ₚ 不是 _p，"
    "⁻ᵏᵗ 不是 ^{-kt}）。\n"
    "4. 看不清楚的字寫 ▢，不要猜。\n"
    "5. 即使是明顯的錯字也要照原樣轉錄。\n"
)

#: What the model writes when it ignores rule 3. Folding these rather than rejecting the answer
#: keeps a correct transcription usable: measured on 20 questions, the model's only deviation from
#: plain text was LaTeX for the offsets, and each one was correct in content.
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


def band_rows(kept_rows, number, *, max_number=250):
    """The rows a question owns: its own number down to the next number, across page breaks.

    Reading order, not page order, so a question whose stem continues onto the next page is read
    whole. Measured on Q49 of `1081_藥師(一)_藥劑學`: the stem ends on page 7, its table sits at the
    top of page 8, and the options are below the table - a crop that stopped at the page break would
    have shown a question with no data in it.
    """
    ordered = sorted(kept_rows, key=lambda row: (int(row.get("page") or 0),
                                                 float(row.get("y0") or 0.0)))
    head = re.compile(r"^[ \t]*(\d{1,3})[ \t]*[.、．]")
    start = None
    for index, row in enumerate(ordered):
        match = head.match((row.get("text") or "").strip())
        if match and int(match.group(1)) == number:
            start = index
            break
    if start is None:
        return []
    end = len(ordered)
    for index in range(start + 1, len(ordered)):
        match = head.match((ordered[index].get("text") or "").strip())
        if match and 1 <= int(match.group(1)) <= max_number and int(match.group(1)) != number:
            end = index
            break
    return ordered[start:end]


def crop_rows(pdf_path, rows, *, dpi=vision.DEFAULT_DPI, margin=vision.MARGIN, gap=8):
    """One PNG for a question, one page's region at a time, stitched top to bottom."""
    from PIL import Image

    by_page = {}
    for row in rows:
        by_page.setdefault(int(row.get("page") or 0), []).append(row)
    pieces = []
    for page in sorted(by_page):
        page_rows = by_page[page]
        x0 = min(float(row.get("x0") or 0.0) for row in page_rows)
        x1 = max(float(row.get("x1") or row.get("x0") or 0.0) for row in page_rows)
        y0 = min(float(row.get("y0") or 0.0) for row in page_rows)
        y1 = max(float(row.get("y1") or row.get("y0") or 0.0) for row in page_rows)
        png = vision.crop_region(pdf_path, page, [x0, y0, x1, y1], dpi=dpi, margin=margin)
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
