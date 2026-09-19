# -*- coding: utf-8 -*-
"""When the text is not enough: render the page, cut the region out, and let the model look.

A text reading of these papers is not always a complete reading, and the incompleteness is not
uniform, so it cannot be answered by a rule. The two cases that matter:

    a figure      the question refers to a curve, a spectrum, a structure or an electro-
                  phoretogram, and the text layer holds only the axis labels and stray digits.
                  Nothing is wrong with the extraction; the information was never text.
    a dispute     the text is present but the reading of it is uncertain - a character that
                  renders as a box, a formula whose structure does not come through, a cell that
                  could be an option or could be content. Here the crop is what settles it,
                  because the page shows the character and the text layer cannot.

Both are answered the same way: render the page at a resolution high enough to read, cut out the
region in question, and ask a model that can see. The answer is recorded beside the question and
is never applied by itself - a crop is evidence for a person, and the reviewer decides. That is
the same governance position as the text side (`GOV-05`), and it is also the only position that
keeps the two readings honest: if the vision answer were applied automatically, a wrong crop
would silently become a wrong question.

Coordinates are points, measured from the top-left of the page, which is what both PDF engines
report. Nothing here writes into a package; it returns an image and a verdict.
"""
from __future__ import annotations

import base64
import io
import json
import os
import urllib.error
import urllib.request

from . import extract

# 200 dpi is the resolution a person reads these papers at. The text of a question is set in
# 11 pt, so 200 dpi renders it at about 30 px tall - enough for a vision model to read a
# subscript, which is the smallest thing being asked about. Rendering higher costs time and
# gains nothing: measured, a 300 dpi crop of the same region produced the same reading.
DEFAULT_DPI = 200
MARGIN = 6.0            # points of margin around a crop, so a character is not clipped

# What counts as a figure, measured on the corpus rather than chosen.
#
# A first version of this used an area floor of 400 pt², and the controls caught it: question 23 of
# `1121_醫事檢驗師_臨床生理學與病理學` was cropped and the model said `text` at 0.99 confidence,
# and it was right. The paper renders the subscript of `PCO₂` as a tiny image object, 29 x 15 pt =
# 439 pt², which cleared the floor - so the question was reported as figure-bearing and the crop
# held nothing but its own text. Seventeen questions were wrong this way, every one of them in that
# one subject, which is the subject that prints blood-gas values.
#
# The whole corpus says the two populations do not overlap. Of the image objects that fall inside a
# question's region:
#
#   height < 24 pt    7,190   glyphs - subscripts, superscripts, single symbols
#   height >= 24 pt     663   figures - blood smears, ECG traces, spectra, tables
#
# So height is the measurement that separates them, and 24 pt is where the gap is: it is about two
# lines of body text, so nothing that a reader would call a picture is shorter. Area could not
# separate them because a subscript is small in both directions while a rule is long and flat.
MIN_FIGURE_HEIGHT = 24.0

BASE_URL = os.environ.get("QBR_MODEL_BASE_URL", "http://127.0.0.1:18120")
API_KEY = os.environ.get("QBR_MODEL_API_KEY", "mtplx")
MODEL = os.environ.get("QBR_MODEL_NAME", "ornith-1.5-mtplx-35b")

# HOW TO TURN REASONING OFF IS NOT THE SAME QUESTION ON EVERY ENGINE, AND THE WRONG SPELLING IS
# SILENT. Measured on three servers with the same model family:
#
#   MTPLX (`18120`, `18121`)  -> `chat_template_kwargs.enable_thinking=False`
#   vLLM  (`192.168.10.90:8888`) -> that spelling *and* top-level `reasoning_effort: "none"`
#   mlx-vlm (`127.0.0.1:8082`) -> ONLY top-level `enable_thinking`; `chat_template_kwargs` is
#                                 accepted, returns HTTP 200, and changes nothing at all
#
# The last one is why this is a table and not a constant. A request carrying the wrong spelling
# succeeds, so a run that "turned thinking off" against mlx-vlm was still thinking, and the only
# way to notice is to read `reasoning_content` back rather than to trust the request.
THINKING_CONTROLS = (
    {"chat_template_kwargs": {"enable_thinking": False}},
    {"enable_thinking": False},
    {"reasoning_effort": "none"},
)
THINKING_ON_CONTROLS = (
    {"enable_thinking": True},
    {"chat_template_kwargs": {"enable_thinking": True}},
)


def _endpoint(base, path="/chat/completions"):
    base = base.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base + path


def render_page(pdf_path, page_number, *, dpi=DEFAULT_DPI):
    """One page as PNG bytes, at a resolution a person can read."""
    module = __import__("pymu" + "pdf")
    document = module.open(filename=pdf_path)
    try:
        page = document[page_number - 1]
        pixmap = page.get_pixmap(dpi=dpi)
        return pixmap.tobytes("png")
    finally:
        document.close()


def crop_region(pdf_path, page_number, box, *, dpi=DEFAULT_DPI, margin=MARGIN):
    """The region `box` of one page as PNG bytes. `box` is (x0, y0, x1, y1) in points.

    The margin matters more than it looks: a crop that clips the top of a subscript removes the
    very thing that was in dispute, and the model then answers confidently about the wrong
    question. `get_pixmap(clip=...)` uses a top-left origin, the same origin the extraction rows
    carry, so a row's own box can be passed straight through.
    """
    module = __import__("pymu" + "pdf")
    document = module.open(filename=pdf_path)
    try:
        page = document[page_number - 1]
        x0, y0, x1, y1 = (float(value) for value in box)
        rect = module.Rect(max(x0 - margin, 0), max(y0 - margin, 0),
                           x1 + margin, y1 + margin)
        pixmap = page.get_pixmap(dpi=dpi, clip=rect)
        return pixmap.tobytes("png")
    finally:
        document.close()


def crop_cell(pdf_path, page_number, rows, *, dpi=DEFAULT_DPI, pad=0.0):
    """The region covering several extracted rows, as one crop.

    A question's figure is rarely one row: it is a caption, an axis label, a scale and a legend,
    each of which was extracted as its own line. The union of their boxes is the region a person
    would point at, so that is what is cut.
    """
    if not rows:
        raise ValueError("no rows to crop")
    x0 = min(float(row["x0"]) for row in rows) - pad
    y0 = min(float(row["y0"]) for row in rows) - pad
    x1 = max(float(row["x1"]) for row in rows) + pad
    y1 = max(float(row["y1"]) for row in rows) + pad
    return crop_region(pdf_path, page_number, (x0, y0, x1, y1), dpi=dpi)


# --- the question asked of a crop ----------------------------------------------------------
# Two questions, because they have different answers and mixing them produces an answer that is
# right about neither. `FIGURE` asks what is in the picture; `DISPUTE` asks what the characters
# say. Both require the model to quote what it saw, because a verdict without a quotation cannot
# be checked by the person reading it, and a verdict that cannot be checked is not evidence.

FIGURE_SYSTEM = """你看的是一份台灣國家考試題目卷的局部截圖。

你的工作只有一件：判斷這張截圖裡有什麼，以及它是否包含題目作答所必需的資訊。

只輸出這個 JSON，不要有其他文字：
{
  "contains": "figure" | "table" | "text" | "empty",
  "describes": "你看到什麼（一句話，具體描述圖形種類、座標軸、表格欄位）",
  "axis_labels": ["你看到的軸標題或欄位名稱"],
  "readable_values": ["你看到且能確定的數值或文字，逐項列出"],
  "uncertain": ["你看不清楚、無法確定的地方"],
  "confidence": 0.0 到 1.0
}

不要回答題目的正確答案。不要推測看不到的內容。看不清楚就放進 uncertain。"""

DISPUTE_SYSTEM = """你看的是一份台灣國家考試題目卷的局部截圖。系統從這份 PDF 抽出文字時，
有一個地方不確定，需要你直接看圖確認。

規則：
- 只描述你**真的看到**的字元，逐字照抄。
- 上下標要標明：寫成 H₂PO₄⁻ 這樣的形式。
- 看不清楚就說看不清楚，**不要猜**。猜錯比空白更糟。

只輸出這個 JSON，不要有其他文字：
{
  "text": "你看到的文字，逐字照抄",
  "characters": ["逐字列出，方便比對"],
  "resolved": true | false,
  "reason": "若 resolved 為 false，說明為什麼看不清楚",
  "confidence": 0.0 到 1.0
}"""


def _image_message(png_bytes, question):
    encoded = base64.b64encode(png_bytes).decode()
    return {"role": "user", "content": [
        {"type": "text", "text": question},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}},
    ]}


_THINKING_FORMS = {}


def _reasoning_length(payload):
    """How much reasoning the engine actually returned, whichever field it uses."""
    message = ((payload or {}).get("choices") or [{}])[0].get("message") or {}
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return len(value)
    details = ((payload or {}).get("usage") or {}).get("completion_tokens_details") or {}
    return int(details.get("reasoning_tokens") or 0)


def _probe_reasoning(form):
    """Reasoning length for one control spelling, or `None` if the request failed."""
    import json as _json
    body = {"model": MODEL, "temperature": 0, "max_tokens": 900, "messages": [{
        "role": "user",
        "content": "9.11 和 9.9 哪個大？請說明理由。"}]}
    body.update(form)
    try:
        request = urllib.request.Request(
            _endpoint(BASE_URL), data=_json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + API_KEY})
        with urllib.request.urlopen(request, timeout=180) as handle:
            return _reasoning_length(_json.loads(handle.read().decode()))
    except Exception:
        return None


def _thinking_forms():
    """Which spelling turns reasoning on, and which turns it off, decided by measurement.

    Both directions are probed, because a spelling that the engine ignores is *silent* and the two
    engines disagree about which spelling works:

      * mlx-vlm (`8082`) obeys top-level `enable_thinking`; `chat_template_kwargs` is accepted with
        HTTP 200 and does nothing. Measured: with the flag, `9.11 和 9.9` gives 272 characters of
        reasoning and the right answer; without it, no reasoning and the wrong answer.
      * MTPLX (`18120`) and vLLM (`8888`) are the other way round.

    The probe question is arithmetic-with-a-trap rather than a friendly sentence, because a form can
    only be shown to be *off* by removing reasoning that was there. Probing with a question that
    never triggers reasoning makes every spelling look like it works, which is exactly the mistake
    that let a "thinking off" run keep thinking.
    """
    if "off" in _THINKING_FORMS and "on" in _THINKING_FORMS:
        return _THINKING_FORMS
    on_form, on_length = dict(THINKING_ON_CONTROLS[0]), None
    for form in THINKING_ON_CONTROLS:
        length = _probe_reasoning(form)
        if length is None:
            continue
        if on_length is None or length > on_length:
            on_form, on_length = form, length
        if length:
            break
    off_form, off_length = dict(THINKING_CONTROLS[0]), None
    for form in THINKING_CONTROLS:
        length = _probe_reasoning(form)
        if length is None:
            continue
        if off_length is None or length < off_length:
            off_form, off_length = form, length
        if not length:
            break
    _THINKING_FORMS.update({"on": on_form, "off": off_form,
                            "reasoning_on": on_length, "reasoning_off": off_length})
    return _THINKING_FORMS


def _thinking_off_body():
    return _thinking_forms()["off"]


def _thinking_on_body():
    return _thinking_forms()["on"]


def _ask(messages, *, max_tokens=8000, timeout=900, think=True, attempts=3, expect=("contains",)):
    """One request, with a bounded number of retries.

    The retry is not for correctness, it is for the engine. Measured over 338 crops: three came
    back as `HTTP Error 500` and the identical request succeeded a moment later. The engine is a
    single local process and it returns 500 when it is busy with another request, so a 500 says
    something about the run rather than about the crop. Retrying is bounded and the last error is
    kept, because a request that never succeeds must be visible in the report and not silently
    become an empty description.
    """
    import time
    body = {"model": MODEL, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
    # The switch is chosen by what the engine ANSWERS, not by what the request asks for, and the
    # choice is remembered for the life of the process. Sending a spelling the engine ignores is
    # not free: the request succeeds and the reasoning silently stays on, which made every
    # "thinking off" crop against mlx-vlm a thinking one.
    body.update(_thinking_on_body() if think else _thinking_off_body())
    request = urllib.request.Request(
        _endpoint(BASE_URL), data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + API_KEY})
    started = time.time()
    raw, failure = None, None
    for attempt in range(max(1, attempts)):
        try:
            raw = json.loads(urllib.request.urlopen(request, timeout=timeout).read().decode())
            failure = None
            break
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            failure = exc
            # Only a busy engine is retried; a request the server understood and refused is not
            # made again, because it will be refused again and the wait would be wasted.
            code = getattr(exc, "code", None)
            if code is not None and code not in (500, 502, 503, 504):
                break
            if attempt + 1 < attempts:
                time.sleep(2.0 * (attempt + 1))
    if raw is None:
        return None, str(failure), f"request-failed: {failure}", {}, time.time() - started
    # The failure is returned rather than raised, and the caller must record it. That was not
    # enough: two of seven crops came back with no verdict and no error either, because the record
    # kept `ok` but not the reason, so the report showed `unread` with nothing to act on. The
    # cause was contention - the engine was still busy with an earlier batch and the request was
    # refused - and it was invisible precisely because the reason was dropped. Every path below
    # now carries `error`.
    elapsed = time.time() - started
    content = ((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    parsed, complaint = _json_of(content, expect=expect)
    return parsed, content, complaint, raw.get("usage") or {}, elapsed


def _json_of(content, *, expect=("contains",)):
    """The verdict, or a reason there is none. Returns `(parsed, complaint)`.

    Returning only `None` was not enough. Measured on question 22 of
    `1092_藥師(二)_調劑學與臨床藥學`: the engine emitted a complete, correct verdict - including
    all three clean-room diagrams - and then ran out of tokens in the middle of the last array, so
    the fenced JSON never closed. The parse failed, `parsed` was `None`, and `content` *did* start
    with a fence, so the old error test called it an error and the old record showed `contains:
    null` with nothing to act on. The crop was right and the budget was wrong, and the report could
    not say so.

    `expect` is the key the caller's prompt asked for. It used to be the literal `contains`, which
    is the *figure* schema - so the crop audit, whose prompt asks for `same`, got every well-formed
    answer rejected as `verdict-missing-contains` and looked like a model failure when it was this
    line. A schema belongs to the prompt that states it, so the caller passes it.
    """
    text = (content or "").strip()
    if not text:
        return None, "empty-response"
    fenced = "```" in text
    if fenced:
        for part in text.split("```"):
            candidate = part.lstrip("json").strip()
            if candidate.startswith("{"):
                text = candidate
                break
    start, end = text.find("{"), text.rfind("}")
    if start < 0:
        return None, "no-json-in-response" if not fenced else "no-json-in-fence"
    if end <= start:
        # No closing brace at all: an object was opened and the engine stopped. With a fence around
        # it that is a budget that ran out; without one it is a response in prose.
        return None, "truncated-json" if fenced else "truncated-before-first-key"
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        return None, f"bad-json: {exc}"
    if not isinstance(parsed, dict) or not any(key in parsed for key in expect):
        # A verdict that does not carry the key its own prompt asked for cannot answer the question
        # it was asked, so it is not a partial verdict; it is a response in the wrong shape.
        return None, ("verdict-missing-" + "/".join(expect))
    return parsed, None


def describe_crop(png_bytes, *, subject="", question="", kind="figure", think=True):
    """Ask what a crop contains. Returns a dict; the verdict is advisory and never applied."""
    system = FIGURE_SYSTEM if kind == "figure" else DISPUTE_SYSTEM
    opening = f"科目：{subject}\n" if subject else ""
    if question:
        opening += f"這張截圖來自這一題附近：\n{question[:400]}\n"
    parsed, raw, complaint, usage, seconds = _ask([
        {"role": "system", "content": system},
        _image_message(png_bytes, opening + "\n請看這張截圖並輸出 JSON。"),
    ], think=think)
    # Every failed path carries a reason, and the two reasons mean different things: a request that
    # never arrived is a finding about the run, and a response that arrived in the wrong shape is a
    # finding about the budget or the prompt. `complaint` is what tells them apart, and it is kept
    # verbatim in the record so a report can be acted on instead of merely noticed.
    error = None if parsed is not None else (complaint or "unparsed")
    return {"kind": kind, "verdict": parsed, "raw": raw, "usage": usage, "error": error,
            "seconds": round(seconds, 1), "bytes": len(png_bytes), "think": think}


def inspect_question(pdf_path, page_number, rows, *, subject="", stem="", kind="figure",
                     dpi=DEFAULT_DPI, out_png=None, think=True):
    """Crop the region a question's rows cover and ask what is there.

    `out_png` writes the crop to disk, which is what a reviewer needs: the verdict and the image
    it came from have to be readable side by side, or the reviewer is trusting a summary they
    cannot check.
    """
    png = crop_cell(pdf_path, page_number, rows, dpi=dpi)
    if out_png:
        with open(out_png, "wb") as handle:
            handle.write(png)
    result = describe_crop(png, subject=subject, question=stem, kind=kind, think=think)
    result.update({"pdf": pdf_path, "page": page_number, "png": out_png, "dpi": dpi})
    return result


# --- what the text reading flags, and what gets cropped for it ------------------------------
# The flags are produced by the text stage and are the only trigger for a crop. A crop costs a
# render and a model call, so it is reserved for a question that was *measured* to be incomplete
# rather than for every question: the text reading says a figure is referenced, and the crop is
# how that claim is confirmed and described.
def crop_plan(items, rows, *, kinds=("figure-image", "figure-table")):
    """For each item carrying a figure flag, the rows a crop should cover.

    The rows offered are the question's own rows, taken from the item's `source_lines` when the
    reading recorded them. A question whose rows cannot be found is reported as such rather than
    cropped blind, because a crop of the wrong region produces a confident answer about the
    wrong thing.
    """
    by_id = {}
    for index, row in enumerate(rows, start=1):
        by_id[index] = row
    plan = []
    for item in items:
        wanted = [flag for flag in (item.get("flags") or []) if flag in kinds]
        if not wanted:
            continue
        covered = [by_id[line_id] for line_id in (item.get("source_lines") or [])
                   if line_id in by_id]
        plan.append({"number": item["number"], "flags": wanted, "rows": covered,
                     "page": covered[0]["page"] if covered else None,
                     "ready": bool(covered)})
    return plan


# --- locating the figure: measured, not guessed --------------------------------------------
# The first version of this module cropped the union of a question's *text* rows and asked what
# was there. Measured on `1031_醫事檢驗師_臨床心電圖` Q7: the model read the axis labels perfectly
# at 0.99 confidence and reported `contains: "text"` - and it was right. A figure's pixels produce
# no text rows, so a crop built from text rows covers everything *except* the figure.
#
# The correction is in what the crop is built from. There are two measured ways a question can
# carry a figure, and both are read off the page rather than inferred from the words:
#
#   an embedded image    the PDF holds the picture as an object with its own bbox. This is the
#                        authority when it exists - `extract.extract_images_a` already reads it,
#                        and it is the region a person would point at.
#   an empty option      the option markers are printed (`A.` `B.` `C.` `D.`) and nothing follows
#                        them. Measured on `1152_醫事檢驗師_臨床血液學與血庫學` Q16: four cells
#                        holding only markers, because the four options are blood-smear
#                        photographs. Across the packaged corpus this is 88 questions in 38
#                        papers (0.4%), which is small enough to crop every one.
#
# Neither depends on the stem containing the word 圖, which is why the legacy `figure-*` word flags
# are not used as the trigger: 775 questions in range mention a figure and only a fraction of them
# need a crop, while these 88 need one and some of them never say so.

def _overlap(a, b):
    """The area two boxes share, in square points."""
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    return width * height if width > 0 and height > 0 else 0.0


def _box_of(rows):
    return (min(float(r["x0"]) for r in rows), min(float(r["y0"]) for r in rows),
            max(float(r["x1"]) for r in rows), max(float(r["y1"]) for r in rows))


def cell_ids_of(item):
    """The cells an item claims, whichever of the three shapes it arrived in.

    There are three, and they are all in use: the model reading (`reflow.to_items`) records cell
    ids under `source_lines`; the paper's skeleton records them under `cells`, split into `stem`
    and `options`; the geometric reading records its own `lines`, which are text lines rather
    than cells. Accepting all three is not untidiness - the figure triggers have to be measurable
    against whichever reading is being checked, and a trigger that only worked on one of them
    would silently find nothing, which is exactly what it did before this was written: 0 figures
    reported across 162 papers that between them hold 5,260 embedded images.
    """
    ids = set()
    for line in item.get("source_lines") or ():
        if isinstance(line, int):
            ids.add(line)
    if isinstance(item.get("lines"), (list, tuple)):
        for line in item["lines"]:
            if isinstance(line, int):
                ids.add(line)
    cells = item.get("cells") or {}
    if isinstance(cells, dict):
        for line in cells.get("stem") or ():
            if isinstance(line, int):
                ids.add(line)
        for lines in (cells.get("options") or {}).values():
            for line in lines if isinstance(lines, (list, tuple)) else ():
                if isinstance(line, int):
                    ids.add(line)
    return sorted(ids)


def option_cell_ids(item):
    """Just the option cells of an item, for the one trigger that is about options.

    Kept separate from `cell_ids_of` because a question's stem and its options can be on different
    pages and `options-without-text` is a statement about the options alone. Attaching it to every
    page the question touches was wrong in a way the controls caught: question 16 of
    `1152_醫事檢驗師_臨床血液學與血庫學` has its stem on page 3 and its four blood smears on page 4,
    so page 3 was cropped, found to hold only text, and scored as a false positive - the model was
    right and the trigger had pointed at the wrong page.
    """
    ids = set()
    cells = item.get("cells") or {}
    if isinstance(cells, dict):
        for lines in (cells.get("options") or {}).values():
            for line in lines if isinstance(lines, (list, tuple)) else ():
                if isinstance(line, int):
                    ids.add(line)
    return sorted(ids)


def option_texts(item):
    """An item's option bodies, whether they are a label-to-text map or a list of records."""
    options = item.get("options") or {}
    if isinstance(options, dict):
        return [str(body or "") for body in options.values()]
    return [str((entry or {}).get("text") or "") for entry in options
            if isinstance(entry, dict)]


# A crop with less ink than this is not a picture of anything. Measured as the fraction of pixels
# that are not near-white, at a low render resolution because this is only asking "is there anything
# here at all". The floor is deliberately far below any real figure: a chemical structure on white
# measures 0.01-0.04, a photograph 0.5 or more, and an empty region 0.000.
MIN_INK = 0.002


def rendered_ink(pdf_path, page, box, *, dpi=50):
    """The fraction of a region's pixels that are not near-white, or `None` if it cannot be read.

    This is the check that a crop shows something. It exists because an embedded image object can be
    present on the page and *not drawn*: measured on `1022_醫事檢驗師_臨床血液學與血庫學` page 2, where
    `xref` 24 is placed at x=243.7-349.7, y=393.9-462.9 and the page renders pure white there, while
    the object's own pixels hold `(A) AML M0 (B) AML M1 (C) AML M3 (D) AML M4`. Seven questions came
    back as figure questions on the strength of boxes that render blank. A crop the reader cannot see
    anything in is not evidence, and the way to know is to look at the render rather than at the
    object list.
    """
    try:
        png = crop_region(pdf_path, int(page), box, dpi=dpi, margin=0.0)
    except Exception:
        return None
    try:
        from PIL import Image
        import io
        image = Image.open(io.BytesIO(png)).convert("L")
        if image.width * image.height > 400_000:
            image.thumbnail((500, 500))
        pixels = list(image.getdata())
    except Exception:
        return None
    if not pixels:
        return None
    return sum(1 for value in pixels if value < 235) / len(pixels)


def ink_without_text(pdf_path, page, box, *, dpi=100, text_pad=0.5):
    """Ink density in `box`, ignoring the pixels that belong to printed text.

    `rendered_ink` counts every non-white pixel, so a ring drawn outside a crop cannot tell a
    structure that continues from the next question's line of Chinese. Measured on
    `1012_藥師_藥理學與藥物化學` Q46 option D: the ring below the box holds 0.18 of ink and every
    bit of it is the stem of the following question (`下列何者為下圖化合物的主要作用標的？`),
    so the crop was sent back for a defect it did not have.

    A drawing's pixels are not part of any text span, and a page's text spans are known exactly, so
    the text's own rectangles are cut out first and what is left is the drawing. A word's box does
    not cover its glyphs' full extent, hence the small padding outward.
    """
    import io

    import pymupdf
    from PIL import Image, ImageDraw

    x0, y0, x1, y1 = (float(value) for value in box)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    png = crop_region(pdf_path, int(page), box, dpi=dpi, margin=0.0)
    if not png:
        return None
    image = Image.open(io.BytesIO(png)).convert("L")
    # The mask is painted on a canvas the same size as the render, so the two line up exactly and no
    # coordinate conversion is needed.
    mask = Image.new("L", image.size, 0)
    pen = ImageDraw.Draw(mask)
    document = pymupdf.open(filename=pdf_path)
    try:
        target = document[int(page) - 1]
        scale = dpi / 72.0
        for word in target.get_text("words"):
            wx0, wy0, wx1, wy1 = (float(v) for v in word[:4])
            if wx1 < x0 or wx0 > x1 or wy1 < y0 or wy0 > y1:
                continue
            pen.rectangle([(wx0 - x0 - text_pad) * scale, (wy0 - y0 - text_pad) * scale,
                           (wx1 - x0 + text_pad) * scale, (wy1 - y0 + text_pad) * scale],
                          fill=255)
    finally:
        document.close()
    ink = image.load()
    cover = mask.load()
    drawn = total = 0
    for y in range(image.height):
        for x in range(image.width):
            if cover[x, y]:
                continue
            total += 1
            if ink[x, y] < 235:
                drawn += 1
    return (drawn / total) if total else 0.0


def figure_questions(items, rows, images=(), *, alphabet=(), min_image_height=MIN_FIGURE_HEIGHT):
    """The questions that carry a figure, with the measured reason and the region to crop.

    `items` are the reading's items, `rows` the extracted cells they refer to, `images` the native
    image objects from `extract.extract_images_a`. A question is returned only when something on
    the page was *measured* to be there - an embedded image overlapping it, or option markers with
    no text - so a question that merely mentions 圖 is not returned, and a question with a
    photograph is not missed because its stem stayed silent.

    The box is the union of the question's rows widened to include any embedded image it overlaps,
    because the picture usually sits beside or below the text that refers to it and a crop of the
    text alone would cut out the very thing being asked about.
    """
    by_page = {}
    for box in picture_boxes(images, min_image_height=min_image_height):
        by_page.setdefault(int(box[0]), []).append(box[1])

    numbered = list(enumerate(rows, start=1))
    rows_by_id = dict(numbered)
    # Where each question starts, per page: the top of the cell carrying its number. A question owns
    # the page from its own number down to the next question's number, and this map draws that
    # boundary. Built from the number cell alone, so a continuation page contributes only the
    # questions that actually *begin* there.
    starts_on_page = {}
    for item in items:
        # The cell carrying the question number, which the paper states as the first stem cell.
        # Read from the stated structure rather than from the lowest cell id, because ids are in
        # extraction order and a question that begins on an earlier page has a lower id there.
        stated = item.get("cells")
        stem = list(stated.get("stem") or ()) if isinstance(stated, dict) else []
        first = rows_by_id.get(stem[0]) if stem else None
        if first is None:
            ids = cell_ids_of(item)
            first = rows_by_id.get(min(ids)) if ids else None
        if first is None:
            continue
        starts_on_page.setdefault(int(first["page"]), []).append(float(first["y0"]))
    for values in starts_on_page.values():
        values.sort()
    # Every question start in reading order, not only those on one page. A question's options can
    # run past the foot of its page - measured on question 8 of
    # `1031_醫事檢驗師_臨床血清免疫學與臨床病毒學`, whose four diagrams end at the foot of page 1
    # while question 9 begins at the head of page 2 - so the bound that ends the last option's band
    # has to be a position in the document, not a y on a page.
    every_start = sorted((int(page), float(y)) for page, values in starts_on_page.items()
                         for y in values)
    found = []
    page_x = {}
    for row in rows:
        page = int(row["page"])
        x0, x1 = float(row["x0"]), float(row["x1"])
        was = page_x.get(page)
        page_x[page] = (min(was[0], x0), max(was[1], x1)) if was else (x0, x1)
    for page, boxes in by_page.items():
        for box in boxes:
            was = page_x.get(page)
            page_x[page] = (min(was[0], box[0]), max(was[1], box[2])) if was else (box[0], box[2])
    found = []
    for item in items:
        cells = [row for number, row in numbered if number in set(cell_ids_of(item))]
        if not cells:
            continue
        # Grouped by page before anything is measured, because a box is a rectangle on *one* page
        # and a question's cells are not always on one. Measured on `1011_醫事檢驗師_臨床血液學與血庫學`
        # question 27: the option-D continuation was on page 3 while the stem was on page 2, so the
        # union spanned the full height of both pages and overlapped every image on either page.
        by_cell_page = {}
        for row in cells:
            by_cell_page.setdefault(int(row["page"]), []).append(row)

        texts = option_texts(item)
        empty_options = bool(texts) and all(not body.strip() for body in texts)
        # The page the options are on, when they are on one. Measured: of the 19 questions whose
        # options print as bare markers, 6 have their markers split across a page break - question
        # 35 of `1051_醫事檢驗師_臨床血清免疫學與臨床病毒學` prints `A.` at the foot of page 4 and
        # `B.` `C.` `D.` at the head of page 5. A crop is one rectangle on one page, so such a
        # question cannot be covered by a single honest crop and is reported as split rather than
        # cropped from a page that shows a quarter of it.
        option_pages = sorted({int(rows_by_id[cell]["page"]) for cell in option_cell_ids(item)
                               if cell in rows_by_id})

        # One entry per question, not one per page. A question that appears twice would be cropped
        # twice and described twice, and the second description would be of a region holding
        # nothing but the stem - which is how this was caught: the model called that crop `text` at
        # 0.99 confidence and was right about the crop and wrong about the question.
        best = None
        first_page = min(by_cell_page) if by_cell_page else 0
        for page, page_cells in sorted(by_cell_page.items()):
            box = _box_of(page_cells)
            # A page that continues a question carries the rest of the previous page's option, and
            # the picture for that option is set at the head of this page - above this page's first
            # marker, so no band test can reach it. Measured on question 80 of
            # `1112_藥師(一)_藥理學與藥物化學`: `A.` is the last thing on page 18 and its chemical
            # structure is the first thing on page 19 (y=29-223), while `B.` `C.` `D.` start at
            # y=232. A box drawn from the markers alone began at y=229 and cut the structure off.
            # Everything between the top of a continuation page and the question's own cells on it
            # belongs to the question, because the question started on an earlier page.
            if page != first_page:
                box = (box[0], 0.0, box[2], box[3])
            # The band this question owns on this page: from its top down to the next question's
            # number on the same page, or to the foot of the page when it is the last one there.
            #
            # The band spans the whole body *width*, and that is not a convenience. A question owns
            # a horizontal slice of the page; its pictures are laid out in that slice, and they do
            # not line up with the text above them. Measured on question 68 of
            # `1042_藥師_藥理學與藥物化學`, which asks for the major metabolite of tolmetin and
            # prints three chemical structures under its options: the marker `D.` ends at x=262.5,
            # the first structure spans x=49.1-298.8, and the next two start at x=49.6 - one tenth
            # of a point outside the markers. Matching pictures against the *text's* rectangle took
            # the first structure and dropped the other two, and the crop ended at y=263.2 while
            # the third structure ran to y=367.9. That is the truncation a reader sees as a
            # structure sliced through the middle, and it is the worst kind of error here because
            # the crop still looks like a picture.
            #
            # The x range is measured from everything printed on the page - cells and pictures -
            # rather than set to the page box, so nothing is claimed from a margin that holds no
            # content at all. Measured over 4,633 pages of 429 papers: exactly one page carries two
            # question numbers within 20 pt of each other, so a page's rows belong to one column
            # and a slice of that page belongs to one question.
            band_x0, band_x1 = page_x.get(page, (box[0], box[2]))
            band = (band_x0, box[1], band_x1,
                    _next_start(starts_on_page.get(page, ()), box[1]))
            reasons = []
            # Only *pictures* count, not the fragments a plot is emitted as: `by_page` is built by
            # `picture_boxes`, which joins scan-line strips and drops what the height floor rejects,
            # so a cluster of 1-5 pt marks is not in it. The raw list is still needed for
            # `fragment_boxes` below, which is the one caller that wants the marks.
            images_here = [candidate for candidate in by_page.get(page, [])
                           if _overlap(band, candidate) > 0]
            if images_here:
                reasons.append("embedded-image")
                for candidate in images_here:
                    box = (min(box[0], candidate[0]), min(box[1], candidate[1]),
                           max(box[2], candidate[2]), max(box[3], candidate[3]))
            # Markers printed with nothing after them. "The text is empty" is the fact; that the
            # marker is a private codepoint is a property of the font, and is not what is tested.
            if empty_options and not reasons and page in option_pages:
                reasons.append("options-without-text")
                # The box must reach the pictures before the page is scored, because the score is
                # how much of the figure the crop will contain. Measuring on question 80 of
                # `1112_藥師(一)_藥理學與藥物化學`: the marker for option A is at the foot of
                # page 18 and its structure is the first thing on page 19, so page 18 has the
                # question number and page 19 has every one of the four structures. Choosing the
                # page that carries the question number is the natural mistake and it produces a
                # crop of one bare letter, which the model then describes, correctly, as text.
                box = _claim_band(box, page_cells, by_page.get(page, []))
                # The marker and its picture sit side by side and they touch: measured on question
                # 16 of `1152_醫事檢驗師_臨床血液學與血庫學`, the marker `A.` ends at x=50 and the
                # blood smear starts at x=50. An overlap test finds nothing, so the crop would show
                # four bare letters with every picture outside the frame. An empty option therefore
                # claims the images sharing its vertical band, which is how the paper reads: the
                # picture is the option and the letter is only its name.
                box = _claim_band(box, page_cells, by_page.get(page, []))
            if reasons and _better(best, box, reasons, page, by_page):
                # The pictures' own region, kept apart from the box that also holds the text. The
                # reference bank stores the picture and nothing else (`assets/...__figure_001.jpg`,
                # 549 of 556 byte-identical to their object), and this is what lets a crop be of
                # the figure rather than of the question - the text is already in the reader's
                # hands as text, and the only thing a crop has to add is the part text cannot
                # carry.
                best = {"number": item["number"], "reasons": reasons, "box": box, "page": page,
                        "pages": sorted(by_cell_page), "cards": len(texts),
                        "option_pages": option_pages,
                        # Where the next question begins, so the last option's band has an end.
                        "next_start": _after(every_start, (page, box[1])),
                        "figure_boxes": [list(candidate) for candidate in images_here],
                        "split": empty_options and len(option_pages) > 1,
                        "stem": (item.get("stem") or "")[:200]}
                # A figure the producer emitted as **one bitmap per glyph** has no proper image
                # object: `figure_boxes` then holds a single 1.4 pt fragment and the crop is a
                # sliver of the plot. Measured on `1051_藥師(一)_藥劑學` Q68 - 290 fragments with
                # `xref` 0 across x=44-148 y=710-789, and a crop of 10x102 px where the plot is
                # 200x120 pt. **Every real figure region measured over 250 papers is at least 24 pt
                # wide** (183 of 183), so a region narrower than that is not a figure and the
                # question's own band is searched for the cluster instead.
                if _region_is_too_small(best["figure_boxes"]):
                    fragments = fragment_boxes(images, box)
                    if fragments:
                        best["figure_boxes"] = [list(candidate) for candidate in fragments]
                        best["fragmented_figure"] = True
                        if "embedded-image" not in best["reasons"]:
                            best["reasons"].append("fragmented-image")
        if best:
            found.append(best)
    return found


# How far above its marker a picture may start and still belong to that option. The marker is
# printed *beside* the top of the picture, not above it, so the picture's top is usually a little
# higher than the marker's - measured from 0.1 pt to 8.2 pt across the questions whose options sit
# beside pictures. The value is not reasoned to: it is scored against the hand-cut bank, which names
# the option every one of its 556 assets belongs to. Measured over the binding sweep:
#
#     reach   correct   unbound   mis-bound
#       4.0        16         3           5
#       6.0        17         3           4
#      10.0        20         3           1
#      12.0+       20         3           1
#
# 10 pt is where it stops improving, and every larger value scores the same - so 10 pt is the
# smallest reach that gets all of them, which is the one that leaves the most room before a reach
# starts taking the previous option's picture.
OPTION_REACH = 10.0


def option_figures(item, rows, images, *, reach=OPTION_REACH, min_image_height=MIN_FIGURE_HEIGHT,
                   limit=None):
    """Which pictures belong to which option, for a question whose options are pictures.

    The reference bank is explicit about this (`40_exports/question_bank_packages/.../assets/`): a
    picture that is an option is filed as `__option_image_00N` with an `option_key` of A/B/C/D, and
    the answer slot shows *that* picture. 98 of its 556 assets are of this kind, and the ones
    measured are as small as 88x93 pixels - the picture's own boundary. A single crop of the whole
    question cannot serve that: the answer slot has to hold one option, and a reader checking the
    answer needs to see the option the key names, not the question.

    The binding is geometry, not reading. A question numbers its options in order down the page, so
    marker N owns the band from its own top to the next marker's top; a picture whose top falls in
    that band is that option's picture. Measured on question 46 of `1012_藥師_藥理學與藥物化學`
    ("下列何者的鎮靜活性最強？", all four option bodies empty): markers at y=53.0, 188.5, 316.9,
    456.1 and one structure starting under each - y=51.2, 186.7, 318.8, 454.3. Each structure is
    0.1-2.6 pt *above* its marker, which is why the band is measured from the marker's top rather
    than below it: the marker is printed beside the top of the picture, not above it.

    The band is measured in **reading order**, not page by page, because a question's options are
    not always on one page. Measured on question 68 of `1042_藥師_藥理學與藥物化學`: `A.` and `B.`
    print at the foot of page 8, `C.` and `D.` at the head of page 9, and the three structures sit
    on page 9 at y=28-137 / 139-248 / 250-367. A per-page band gives page 9's markers only the
    third structure; read as one ordered sequence, the first structure falls after `B.` and before
    `C.` and is therefore `B`'s picture, which is what the paper shows.

    `limit` is `(page, y)` where the *next question* begins, and it is required for the last option
    to mean anything. The last marker has no following marker to stop it, so without a limit its
    band runs to the end of the document. Measured on question 8 of
    `1031_醫事檢驗師_臨床血清免疫學與臨床病毒學`: four T-cell-receptor diagrams with `D.` as the
    last marker, whose band reached three pictures on pages 2, 3 and 6 - pictures belonging to
    questions 9 and 12. With the limit at question 9's first line (page 2, y=77.2) it claims the one
    picture at page 2 y=27-74, which is the fourth diagram.

    Returns a list of `{key, page, box, image_index, xref}` in option order, or `[]` when the
    question's options do not sit beside pictures. `image_index` is the picture's position in
    `images`, which is what lets the caller write the picture object out instead of rendering a
    region of the page.
    """
    by_id = dict(enumerate(rows, start=1))
    marks = []
    cells = (item.get("cells") or {}) if isinstance(item, dict) else {}
    for key, ids in ((cells.get("options") or {}) or {}).items():
        for cell in (ids if isinstance(ids, (list, tuple)) else ()):
            row = by_id.get(cell)
            if row is None:
                continue
            marks.append({"key": str(key), "page": int(row["page"]),
                          "y0": float(row["y0"]), "y1": float(row["y1"]),
                          "x0": float(row["x0"]), "x1": float(row["x1"])})
    if len(marks) < 2:
        return []
    # Reading order: the page first, then the position down it. The option order the paper prints
    # is this order, so sorting by it keeps each key with its own marker without trusting the
    # dictionary's insertion order.
    marks.sort(key=lambda mark: (mark["page"], mark["y0"]))
    # Bound to the raw objects, one option band at a time, and only then joined. Grouping the whole
    # page first is wrong in the other direction: on `1051_醫事檢驗師_臨床血清免疫學與臨床病毒學` Q20
    # the four options are four diagrams printed directly under each other with the *same* width, so
    # a page-wide "same width, touching" join fuses all four into one 70-471 pt picture and every
    # option loses its picture. A band is what says which pieces belong to the option being bound.
    tall = []
    every = []
    for index, image in enumerate(images):
        box = (float(image["x0"]), float(image["y0"]),
               float(image["x1"]), float(image["y1"]))
        piece = {"index": index, "page": int(image["page"]), "box": box,
                 "xref": image.get("xref")}
        every.append(piece)
        if (box[3] - box[1]) >= min_image_height:
            tall.append(piece)
    hard_stop = (int(limit[0]), float(limit[1])) if limit else None
    by_row = dict(enumerate(rows, start=1))
    owned = []
    for position, mark in enumerate(marks):
        after = (mark["page"], mark["y0"] - reach)
        # The rows this option owns: its marker and whatever the skeleton bound to it. They are
        # what the crop has to carry beside the picture.
        local = [row for row in (by_row.get(cell) for cell in
                                 ((cells.get("options") or {}).get(mark["key"]) or ()))
                 if row is not None]
        if position + 1 < len(marks):
            follower = marks[position + 1]
            before = (follower["page"], follower["y0"] - reach)
        else:
            before = hard_stop
        got = []
        for picture in tall:
            where = (picture["page"], picture["box"][1])
            if where < after:
                continue
            if before is not None and where >= before:
                continue
            got.append(picture)
        if got:
            got.sort(key=lambda picture: (picture["page"], picture["box"][1]))
            # One option is one picture. A band that catches two is a measurement that has stopped
            # describing an option - the first is taken, because it is the one printed beside the
            # marker, and the rest are left for the option that owns them.
            got = got[:1]
            # ...but one picture may be several objects. On `1151_藥師(一)_藥學(一)(包括藥理學與藥物化學)`
            # Q41 option C is one structure stored as `xref` 16 and 17 - the same x-range, touching
            # at y=392.4 - and option D as `xref` 18, 19, 20. Serving `got[0]` alone served the top
            # strip: C lost the `Cytotoxic drug` ellipse and D lost the left antibody, while every
            # automated check passed, because the bytes really were a placed object. The pieces are
            # joined inside this band only, on the same measured rule `picture_boxes` uses.
            # A piece is taken even when it is shorter than the floor, because the floor's job is
            # to choose *which* picture the option has - not to trim that picture once chosen.
            # Measured on `1081_藥師(一)_藥理學與藥物化學` Q63 option B: the structure is `xref`
            # 110..114 at y=27.8-143.2, and its last two strips are 13.7 and 13.8 pt tall. Applying
            # the floor to the join cut the bottom of the molecule off, which is the very defect
            # this is fixing. Applying it to the anchor is what keeps a stray 2.8 pt rule from being
            # chosen as a picture in the first place.
            pieces = [picture for picture in every
                      if picture["page"] == got[0]["page"]
                      and (picture["page"], picture["box"][1]) >= after
                      and (before is None
                           or (picture["page"], picture["box"][1]) < before)]
            pieces.sort(key=lambda picture: (picture["box"][1], picture["box"][0]))
            joined = []
            for piece in pieces:
                for kept in joined:
                    if _same_picture(kept, piece):
                        kept["box"] = (min(kept["box"][0], piece["box"][0]),
                                       min(kept["box"][1], piece["box"][1]),
                                       max(kept["box"][2], piece["box"][2]),
                                       max(kept["box"][3], piece["box"][3]))
                        kept["pieces"].append(piece)
                        break
                else:
                    joined.append({"box": piece["box"], "xref": piece["xref"],
                                   "pieces": [piece]})
            changed = True
            while changed:
                changed = False
                settled = []
                for item in joined:
                    for kept in settled:
                        if _same_picture(kept, item):
                            kept["box"] = (min(kept["box"][0], item["box"][0]),
                                           min(kept["box"][1], item["box"][1]),
                                           max(kept["box"][2], item["box"][2]),
                                           max(kept["box"][3], item["box"][3]))
                            kept["pieces"].extend(item["pieces"])
                            changed = True
                            break
                    else:
                        settled.append(item)
                joined = settled
            # The option's picture is the group the *anchor* is in - the first piece tall enough to
            # be a picture. Taking the topmost group instead picks up whatever short rule happens to
            # sit above it: measured on `1081_藥師(一)_藥理學與藥物化學` Q61 option D, where `xref` 97
            # is a 2.8 pt strip at the very bottom of option C's structure (same 154.6 pt width as
            # C's pieces 94-96), and the topmost-group rule handed D that stray instead of its own
            # 146.4 pt structure at `xref` 98 and 99.
            group = None
            for item in joined:
                if any(piece["index"] == got[0]["index"] for piece in item["pieces"]):
                    group = item
                    break
            if group is None:
                group = min(joined, key=lambda item: item["box"][1])
            # A picture of one object is served from the object itself, which is its own boundary.
            # A picture that is several objects has no single `xref`, so the caller renders the
            # region - serving one strip and calling it the object is the defect this fixes.
            # The crop must be cut from the page the *picture* is on, not the page the marker is
            # on. Measured on `1081_藥師(一)_藥理學與藥物化學` Q63: markers A and B print at the foot of
            # page 11 and their structures are at the head of page 12, so rendering the marker's
            # page put the picture on a rectangle that means nothing there. The marker is folded in
            # only when it shares the picture's page, because on another page its y is a different
            # coordinate system.
            page = got[0]["page"]
            single = len(group["pieces"]) == 1
            # The crop carries the option's **own printed text**, not only the marker.
            #
            # Measured on `1152_藥師(一)_藥學(一)` Q53: the text layer is `A.` on its own line and
            # `alfuzosin` on the next, and a crop of the picture alone showed the structure with
            # **no drug name in it** - the reader could not tell which of the four molecules they
            # were looking at, and the option's text was not on screen anywhere either. Every row
            # the skeleton assigned to this option is folded in, which is the marker *and* its
            # body, so the same standard holds wherever the typesetter put them.
            #
            # Only rows sharing the picture's page are folded in, because on another page a y is a
            # different coordinate system. Measured on `1081_藥師(一)_藥理學與藥物化學` Q63: markers A
            # and B print at the foot of page 11 and their structures at the head of page 12.
            own_rows = [row for row in local if row["page"] == page]
            xs0 = [group["box"][0]] + [row["x0"] for row in own_rows]
            xs1 = [group["box"][2]] + [row["x1"] for row in own_rows]
            ys0 = [group["box"][1]] + [row["y0"] for row in own_rows]
            ys1 = [group["box"][3]] + [row["y1"] for row in own_rows]
            owned.append({"key": mark["key"], "page": page,
                          "box": (min(xs0), min(ys0), max(xs1), max(ys1)),
                          "picture_box": group["box"],
                          "image_index": group["pieces"][0]["index"],
                          "xref": group["xref"] if single else None,
                          "pictures": 1, "pages": [page],
                          "strips": len(group["pieces"])})
    # The class only matters when the answer slot has to show a picture, which takes at least two
    # options carrying one. A single option beside a picture is a figure the stem refers to.
    return owned if len(owned) >= 2 else []


def options_cover_the_figure(entry, owned, *, tolerance=2.0):
    """Whether the option pictures together are the whole figure, so one crop of it adds nothing.

    Measured on question 46 of `1012_藥師_藥理學與藥物化學`: the entry's pictures are the four
    structures and each one belongs to an option, so a crop of the question is the four option
    crops stacked. Showing both puts the same four structures on the screen twice and makes the
    reviewer check whether the two disagree - which they cannot, being the same objects.

    The test is coverage, not count: a question with a picture in its stem *and* pictures for its
    options has more pictures than options, and then the whole-question crop is the only place the
    stem's picture appears, so it must stay.

    **This is a test about the entry's pictures, not about the crop.** Measured on
    `1141_藥師(一)_藥學(一)` Q41: the entry has five pictures - the stem's ring plus one structure per
    option - so this answers `False` and a crop *is* made. That is right, because the stem's picture
    appears nowhere else; what was wrong there was the crop's *contents*, which included the four
    option structures as well and put every option on screen twice. `figure_region`'s `exclude`
    removes them, so the crop this test asks for holds only the stem's picture. Narrowing *this*
    test instead - treating the stem picture as "already covered" - would have answered `True` and
    dropped the stem's picture from the pack altogether.
    """
    boxes = [tuple(box) for box in (entry.get("figure_boxes") or ())]
    if not boxes or not owned:
        return False
    covered = 0
    for box in boxes:
        for option in owned:
            x0, y0, x1, y1 = option["box"]
            cx0, cy0, cx1, cy1 = box
            if (cx0 >= x0 - tolerance and cy0 >= y0 - tolerance
                    and cx1 <= x1 + tolerance and cy1 <= y1 + tolerance):
                covered += 1
                break
    return covered == len(boxes)


def _after(starts, where, *, tolerance=1.0):
    """The first question start in reading order after `where`, or `None` when there is none.

    `starts` is every question's `(page, y0)` sorted, and `where` is this question's own start, so
    the answer is the next question in the document - across a page break when that is where it is.
    """
    import bisect
    position = bisect.bisect_right(starts, where)
    while position < len(starts) and starts[position] <= where:
        position += 1
    if position >= len(starts):
        return None
    return starts[position]


def _grouped_pictures(images, *, join=3.0):
    """The placed objects grouped into pictures, each with the members it was built from.

    One picture may be several objects - a producer that re-encodes each strip gives every strip
    its own `xref` and the identity is then in the geometry. Measured on `1151_藥師(一)_藥學(一)` Q41:
    option C is `xref` 16 and 17, both x=50.4-225.4 and touching at y=392.4; option D is 18, 19, 20,
    all x=50.4-388.1 and touching at y=489.6 and 529.9.
    """
    grouped = {}
    for index, image in enumerate(images):
        page = int(image["page"])
        box = (float(image["x0"]), float(image["y0"]), float(image["x1"]), float(image["y1"]))
        grouped.setdefault(page, []).append(
            {"box": box, "xref": image.get("xref"), "index": index})
    out = []
    for page, pictures in grouped.items():
        pictures.sort(key=lambda item: (item["box"][1], item["box"][0]))
        merged = []
        for item in pictures:
            for kept in merged:
                if _same_picture(kept, item, join=join):
                    kept["box"] = (min(kept["box"][0], item["box"][0]),
                                   min(kept["box"][1], item["box"][1]),
                                   max(kept["box"][2], item["box"][2]),
                                   max(kept["box"][3], item["box"][3]))
                    kept["members"].append(item)
                    break
            else:
                merged.append({"box": item["box"], "xref": item["xref"], "members": [item]})
        changed = True
        while changed:
            changed = False
            settled = []
            for item in merged:
                for kept in settled:
                    if _same_picture(kept, item, join=join):
                        kept["box"] = (min(kept["box"][0], item["box"][0]),
                                       min(kept["box"][1], item["box"][1]),
                                       max(kept["box"][2], item["box"][2]),
                                       max(kept["box"][3], item["box"][3]))
                        kept["members"].extend(item["members"])
                        changed = True
                        break
                else:
                    settled.append(item)
            merged = settled
        for item in merged:
            xrefs = {member.get("xref") for member in item["members"] if member.get("xref")}
            out.append({"page": page, "box": item["box"],
                        "xref": item["xref"] if len(item["members"]) == 1 else None,
                        "members": [member["index"] for member in item["members"]],
                        "member_xrefs": sorted(xrefs)})
    return out


def picture_boxes(images, *, min_image_height=MIN_FIGURE_HEIGHT, join=3.0):
    """The pictures on the pages, each as `(page, (x0, y0, x1, y1))`.

    A height floor keeps out glyphs, rules and bullets, and on its own it silently throws away the
    real thing. Measured on question 20 of `1041_醫事檢驗師_臨床血液學與血庫學`: the blood-cell
    photograph its stem calls 圖中 is **73 image objects**, every one 199.9 pt wide and exactly 2.0
    pt tall, all the same `xref` - one bitmap that the producer emitted as a set of scan lines. Not
    one of them is 24 pt tall, so the floor discarded the whole figure and the question came back
    with no picture while the reference bank says it has one. Measured across the corpus the same
    way: 249 objects of 0.7 pt on page 4 of the same paper, and 308 objects on `1032` page 2.

    So the pieces are put back together before anything is measured, and two pieces are joined on
    either of two measured signals - not on adjacency alone:

    **the same `xref`.** One bitmap emitted as scan lines appears many times with one `xref`.
    Measured on page 3 of `1041_醫事檢驗師_臨床血液學與血庫學`: 73 objects, all `xref` 1, all
    199.9 pt wide and 2.0 pt tall.

    **the same width, touching.** A producer that re-encodes each strip gives every strip its own
    `xref` and the identity is then in the geometry. Measured on question 68 of
    `1152_醫事檢驗師_微生物學與臨床微生物學(包括細菌與黴菌)`: six strips of 722x43 at `xref`
    22..27, y=629/660/690/721/752/783 - identical width, each starting exactly where the last
    ended.

    Adjacency alone is not enough, and that is what this replaces. Measured on question 77 of
    `1102_醫事檢驗師_醫學分子檢驗學與臨床鏡檢學(包括寄生蟲學)`: four electrophoresis lanes,
    `xref` 21/22/25/26, each 78-84 pt wide, printed with only 20.7 pt between them. Adjacency joined
    all four into a single 135x480 picture, so two options shared one image and the binding lost a
    lane. Two pictures that differ in width are two pictures.

    A single short object that shares nothing is left as it is and is then dropped by the floor -
    which is the job the floor is actually for.
    """
    out = []
    for picture in _grouped_pictures(images, join=join):
        box = picture["box"]
        if (box[3] - box[1]) < min_image_height:
            continue                 # a glyph, a rule, a stray bullet: not a figure
        out.append((picture["page"], box))
    return out


def _same_picture(kept, item, *, join=3.0, width_tolerance=0.5):
    """Whether two placed objects are pieces of one picture.

    The pieces must first be **placed next to each other**, and then either the file says they are
    one object (`xref`) or the geometry says so (the same width). Both halves are needed and each
    excludes a different mistake:

    - adjacency alone joins four electrophoresis lanes printed end to end, measured on `1102`
      question 77, because they are adjacent but 78-84 pt wide.
    - a shared `xref` alone joins one object to itself, measured on `1022` page 2 where `xref` 24 is
      placed twice - once at y=393.9-462.9 and once at y=750.3-818.8, 287 pt apart. That merged the
      two placements into a single 425 pt box, and every question between them then measured as
      overlapping a picture: five questions that the reference bank records as having no figure came
      back as figure questions.
    """
    left, right = kept["box"], item["box"]
    adjacent = (left[0] < right[2] + join and right[0] < left[2] + join
                and left[1] <= right[3] + join and right[1] <= left[3] + join)
    if not adjacent:
        return False
    if kept.get("xref") and item.get("xref") and kept["xref"] == item["xref"]:
        return True
    return abs((left[2] - left[0]) - (right[2] - right[0])) <= width_tolerance


def _next_start(starts, top, *, tolerance=1.0):
    """The y of the next question that begins below `top` on this page, or the foot of the page.

    A question owns everything between its own number and the next question's number, which is how
    the paper reads and how a picture printed under a stem belongs to that stem. Measured on
    question 68 of `1152_醫事檢驗師_微生物學與臨床微生物學(包括細菌與黴菌)`: the stem ends at
    y=627, two photographs - the lesion and the KOH smear it names as 圖1 and 圖2 - sit at
    y=629-812, and the four options print at the head of the next page. No cell of the question
    touches a picture, so an overlap test found nothing and the question came back with no figure
    at all, while its own text refers to two. The pictures are not beside the text; they are
    *after* it, and the band is the only thing that says so.

    The tolerance keeps a question from being read as its own successor: the number cell's top is
    exactly `top`, and a strict comparison already excludes it, but cells that share a top to
    within a rounding error should not count as a new question either.
    """
    for value in starts:
        if value > top + tolerance:
            return value
    return float("inf")


def _box_height(box):
    return float(box[3]) - float(box[1])


def _better(best, box, reasons, page, by_page):
    """Whether this page is a better one to crop than the one already chosen.

    A question can earn the same reason on two pages, and the pages are not equal. Measured on
    question 35 of `1051_醫事檢驗師_臨床血清免疫學與臨床病毒學`: the four option markers print as
    bare `A.` `B.` `C.` `D.`, and the stem and `A.` are at the foot of page 4 while `B.` `C.` `D.`
    and all four diagrams are at the head of page 5. Both pages are cropped by the same rule, and
    the first one chosen was page 4 - a region holding a stem and one letter, which the model
    described, correctly, as containing no figure at all.

    So a page is preferred when it holds an image object, because that is a measurement of the
    thing being asked about rather than a consequence of where a page happened to break.
    """
    if best is None:
        return True
    if len(reasons) != len(best["reasons"]):
        return len(reasons) > len(best["reasons"])
    # How much of the figure this page's box actually contains. A page whose markers are beside
    # their pictures yields a box as tall as the pictures; a page holding only markers yields a box
    # the height of a line. Comparing the two is what picks page 19 over page 18.
    return _box_height(box) > _box_height(best["box"])


def _claim_band(box, cells, images, *, reach=8.0):
    """Widen a box to take in the pictures that belong to the option cells it covers.

    Each cell is matched to its own picture, rather than the whole question being matched to every
    picture on the page. That distinction is what a second subject made necessary. Measured on
    question 80 of `1112_藥師(一)_藥理學與藥物化學`: the four option markers are at x=45-58 and
    their four chemical structures are at x=57-332, so the marker and its structure touch or
    overlap by one point and a whole-question test on the *union* of the markers has no vertical
    extent to match against - the options are printed one per page-third, and the union spans the
    page. The union test claimed nothing, the crop showed four bare letters, and the model said so
    - correctly, and that is the third time this stage has been caught by its own output.

    A picture belongs to a marker when it starts within `reach` points of where the marker ends and
    their vertical extents overlap. `reach` is generous because the gap is a printing gap - the
    structure is set in the column beside the letter - and it is bounded so that a picture in the
    next column of a table is not claimed.
    """
    if not cells:
        return box
    for row in cells:
        top, bottom = float(row["y0"]), float(row["y1"])
        right = float(row["x1"])
        for image in images:
            if min(bottom, image[3]) - max(top, image[1]) <= 0:
                continue
            if image[0] - right > reach:
                continue
            if image[0] < float(row["x0"]) - reach:
                continue                      # to the left of the marker: another column
            box = (min(box[0], image[0]), min(box[1], image[1]),
                   max(box[2], image[2]), max(box[3], image[3]))
    return box


#: A drawing object smaller than this in either direction is not a figure. Measured on the
#: The narrowest a figure region can be and still be a figure. Measured over 250 papers: every
#: one of the 183 figure regions `figure_questions` returns is at least 24 pt wide (minimum 26.6 pt),
#: while the fragment artifact that prompted this is 1.4 pt. The floor is a *shape* fact about the
#: corpus, not a tuned value: it sits an order of magnitude away from both populations.
MIN_FIGURE_WIDTH = 24.0


def _region_is_too_small(boxes):
    """Whether the measured figure region is too narrow to be a figure."""
    if not boxes:
        return True
    return (max(box[2] for box in boxes) - min(box[0] for box in boxes)) < MIN_FIGURE_WIDTH


#: A cluster of tiny image fragments this large is a figure. A question's stem is text and is
#: already extracted as text; a figure is a *density* of marks that no text run produces. Measured
#: on `1051_藥師(一)_藥劑學` Q68: 290 fragments inside a 200x120 pt band. One stray fragment is not
#: a figure, so the floor keeps a single mis-decoded glyph from being cropped as a picture.
MIN_FRAGMENT_COUNT = 25


def fragment_boxes(images, band):
    """The region inside `band` covered by a cluster of tiny image fragments, or `[]`.

    A figure drawn with a plotting library can be emitted as **one bitmap per glyph** - hundreds of
    image objects, each a few points across, with no `xref` at all. Measured on
    `1051_藥師(一)_藥劑學` Q68: 290 objects with `xref` 0, spanning x=44-148 y=710-789, while the
    question's own band is x=26-412 y=663-808. The crop took the tallest fragment and came out
    10x102 px - a sliver of the plot's axis - which is exactly the report of a crop that "cuts off
    the bottom figure".

    The cluster is returned as the union of the fragments, and the band already bounds the question,
    so nothing from another question leaks in. The text the plot is labelled with (`1/V`, `1/C`) is
    inside that union because the label fragments are part of the same cluster.
    """
    inside = []
    for image in images or ():
        try:
            box = (float(image["x0"]), float(image["y0"]),
                   float(image["x1"]), float(image["y1"]))
        except (KeyError, TypeError, ValueError):
            continue
        if image.get("xref"):
            continue                                  # a real object: `picture_boxes` handles it
        if max(box[2] - box[0], box[3] - box[1]) > 12.0:
            continue                                  # not a fragment
        if _overlap(tuple(band), box) <= 0 and not (box[1] >= band[1] and box[3] <= band[3]):
            continue
        inside.append(box)
    if len(inside) < MIN_FRAGMENT_COUNT:
        return []
    return [(min(b[0] for b in inside), min(b[1] for b in inside),
             max(b[2] for b in inside), max(b[3] for b in inside))]


def figure_region(entry, *, exclude=()):
    """The pictures' own region of an entry, or `None` when the entry holds no measured picture.

    A question's entry box is the text and the pictures together, because that is what identifies
    the question. A crop is a different job: the text is already available as text, and the crop
    exists to carry what text cannot. Returning the pictures alone is therefore the honest crop,
    and it is what the reference bank stores.

    `exclude` is the option pictures' boxes, and leaving them out is what keeps the crop honest.
    Measured on `1141_藥師(一)_藥學(一)` Q41: the question has five pictures - the stem's ring drawing
    plus one structure per option - and a crop of all five put **every option on the screen a
    second time**, the answer among them, so the reviewer had to work out which copy to read. A
    figure crop exists to show what the option crops do not, so the option pictures are removed
    rather than duplicated. It also makes the two decisions consistent: a question whose pictures
    are *all* option pictures now yields no figure crop at all, which is exactly what
    `options_cover_the_figure` says.
    """
    boxes = [tuple(box) for box in (entry.get("figure_boxes") or ())]
    if exclude:
        kept = []
        for box in boxes:
            x0, y0, x1, y1 = box
            if any(x0 >= ox0 - 2.0 and y0 >= oy0 - 2.0 and x1 <= ox1 + 2.0 and y1 <= oy1 + 2.0
                   for ox0, oy0, ox1, oy1 in exclude):
                continue
            kept.append(box)
        boxes = kept
    if not boxes:
        return None
    return (min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes))


def crop_figure(entry, pdf_path, *, dpi=DEFAULT_DPI, pad=1.0, exclude=()):
    """The crop for one entry: its pictures when any were measured, else the whole region.

    A question whose figure is one object yields a crop of that object; a question whose figure is
    several objects - measured on question 68 of
    `1152_醫事檢驗師_微生物學與臨床微生物學(包括細菌與黴菌)`, whose two photographs are six strips
    of one raster - yields the union of those strips, which is still the picture and still excludes
    the stem above it.

    **No text margin is added**, and that is the difference between this and `crop_region`'s default.
    A margin exists so a crop of *text* does not clip a subscript, and `MARGIN` is 6 pt for that
    reason. A figure's own boundary is already exact, so the same margin only reaches back into the
    line above. Measured on question 46 of `1012_藥師_藥理學與藥物化學`: the stem's box ends at
    y=49.18, the first structure's top is y=51.18, and a 4 pt pad plus the 6 pt margin put the crop's
    top edge at y=41.18 - through the middle of the stem, which is how `下列何者的鎮靜活性最強？`
    ended up printed above a crop that was supposed to hold only pictures. The measured gap between
    a stem and its figure is 2 pt here, so the pad is 1 pt and the margin is zero.
    """
    region = figure_region(entry, exclude=exclude)
    if region is None:
        # The entry names pictures and every one of them is excluded, so there is nothing left for
        # this crop to carry. Falling back to the entry's box here - which is what `figure_region`
        # returning `None` used to mean - would cut the whole question, text and option pictures
        # included, and that is the redundancy `exclude` exists to remove.
        return None, "no-figure"
    page = entry.get("page")
    if not page:
        return None, "no-page"
    x0, y0, x1, y1 = region
    png = crop_region(pdf_path, int(page), (x0 - pad, y0 - pad, x1 + pad, y1 + pad),
                      dpi=dpi, margin=0.0)
    return png, int(page)


def crop_for(entry, pdf_path, *, dpi=DEFAULT_DPI, pad=4.0):
    """The crop for one entry from `figure_questions`. One page, so one image.

    A crop is of the page the entry names, which is the page its box was measured on. A box is
    never carried across pages: the region is a rectangle in one page's coordinates, and mixing
    two pages' coordinates produces a rectangle that means nothing on either.
    """
    page = entry.get("page")
    if not page:
        return None, "no-page"
    x0, y0, x1, y1 = entry["box"]
    png = crop_region(pdf_path, int(page), (x0 - pad, y0 - pad, x1 + pad, y1 + pad), dpi=dpi)
    return png, int(page)


def read_figures(pdf_path, items, rows, *, images=None, subject="", dpi=DEFAULT_DPI,
                 think=True, out_dir=None, numbers=None, limit=0, entries=None):
    """Describe the figure-bearing questions of one paper.

    Returns one record per question: the crop's page, the measured reason it was cropped, the
    model's verdict, and the timing. The verdict is never applied to the package - it is recorded
    beside the question for a reviewer, which is the same position the text side takes (`GOV-05`).
    A figure that the model cannot read is reported as such rather than as an empty description.

    `entries` may be passed in when the caller has already run `figure_questions`; that is not an
    optimisation, it is what keeps the caller able to report the crop plan *before* paying for the
    descriptions. Running it twice also silently produced an empty result, because the second run
    was given items filtered down to a question number and re-derived the plan from them.
    """
    import time
    if entries is None:
        if images is None:
            images = extract_images_a(pdf_path)
        entries = figure_questions(items, rows, images)
    if numbers:
        wanted = set(numbers)
        entries = [entry for entry in entries if entry["number"] in wanted]
    if limit:
        entries = entries[:limit]
    started = time.time()
    records = []
    for entry in entries:
        png, page = crop_for(entry, pdf_path, dpi=dpi)
        record = {"number": entry["number"], "reasons": entry["reasons"], "page": page,
                  "box": [round(value, 1) for value in entry["box"]], "stem": entry["stem"]}
        if png is None:
            record.update({"ok": False, "error": page, "verdict": None})
            records.append(record)
            continue
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, f"q{entry['number']:03d}.png")
            with open(path, "wb") as handle:
                handle.write(png)
            record["png"] = path
        result = describe_crop(png, subject=subject, question=entry["stem"], think=think)
        verdict = result.get("verdict") or {}
        record.update({
            "ok": bool(verdict),
            "error": result.get("error"),
            "seconds": result.get("seconds"),
            "contains": verdict.get("contains"),
            "describes": verdict.get("describes"),
            "readable_values": verdict.get("readable_values"),
            "uncertain": verdict.get("uncertain"),
            "confidence": verdict.get("confidence"),
            "usage": result.get("usage"),
            "verdict": verdict or None,
        })
        records.append(record)
    by_kind = {}
    for record in records:
        by_kind[record.get("contains") or "unread"] = by_kind.get(record.get("contains")
                                                                 or "unread", 0) + 1
    return {"pdf": pdf_path, "subject": subject, "questions": len(records),
            "seconds": round(time.time() - started, 1), "think": think,
            "by_contains": by_kind, "records": records}
