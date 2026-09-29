# -*- coding: utf-8 -*-
"""When the text is not enough: render the page, cut the region out, and let the model look.

A text reading of these papers is not always a complete reading, and the incompleteness is not
uniform, so it cannot be answered by a rule. The three cases that matter:

    a figure      the question refers to a curve, a spectrum, a structure or an electro-
                  phoretogram, and the text layer holds only the axis labels and stray digits.
                  Nothing is wrong with the extraction; the information was never text.
    a dispute     the text is present but the reading of it is uncertain - a character that
                  renders as a box, a formula whose structure does not come through, a cell that
                  could be an option or could be content. Here the crop is what settles it,
                  because the page shows the character and the text layer cannot.
    a table       the body is a table printed as text, so what comes out is one run-on string whose
                  columns no longer line up and whose rows cannot be told apart. The characters are
                  all present and the *structure* is gone, which no crop of a character can restore -
                  the crop has to be of the table itself (`quoted_lines_region`).

The first two are triggered by a measurement of the page. The third cannot be: whether a run of
printed lines **is** a table is meaning, so the reading says so and quotes the table's own lines, and
the code locates them. See the block above `quoted_lines_region`.

All three are answered the same way: render the page at a resolution high enough to read, cut out the
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

from . import ai_findings, canon, extract

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

# The address, model and key are the **one table's** values for the default visual engine, not a
# second copy of them. A second copy is a second thing that can point somewhere else than
# `engines.ENDPOINTS` does, and "the module was imported before the environment changed" is exactly
# how this module once kept talking to `18120` after a run was pointed at another host.
#
# The legacy `QBR_MODEL_*` names still win, so every existing run command keeps working; `QBR_VISION_*`
# is the same thing spelled for this module when a caller wants to point it somewhere else.
from . import engines as _engines  # noqa: E402  (module-level, one import, no cycle)

# The eye is the same engine as the agent's brain since the designer's ruling of 2026-09-29
# (「腦與眼同一顆」), and the measured reason is the reading itself: on the 2026-09-29 sample, 6-bit
# occamy was field-exact on 53.3% of fields against 4-bit ornith's 47.8% (ceiling 78.4% vs 64.9%,
# `a2.1-vision-ceiling.md`). Two things make the swap safe for this module: the thinking switch is
# **probed** by `_thinking_forms` rather than assumed, and this module's budgets (900 / 8000 tokens)
# sit far inside the deployment's 65536-token KV.
_DEFAULT = _engines.BUILTIN_ENDPOINTS["occamy-6bit"]
BASE_URL = (os.environ.get("QBR_VISION_BASE_URL") or os.environ.get("QBR_MODEL_BASE_URL")
            or _DEFAULT["url"])
API_KEY = (os.environ.get("QBR_VISION_API_KEY") or os.environ.get("QBR_MODEL_API_KEY")
           or _DEFAULT.get("key", ""))
MODEL = (os.environ.get("QBR_VISION_MODEL") or os.environ.get("QBR_MODEL_NAME")
         or _DEFAULT["name"])

# HOW TO TURN REASONING OFF IS NOT THE SAME QUESTION ON EVERY ENGINE, AND THE WRONG SPELLING IS
# SILENT. The local engines use different controls:
#
#   MTPLX (`18120`, `18121`)  -> `chat_template_kwargs.enable_thinking=False`
#   local vLLM / Splash       -> `reasoning_effort: "none"`
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

DISPUTE_SYSTEM = (
    """你看的是一份台灣國家考試題目卷的局部截圖。系統從這份 PDF 抽出文字時，
有一個地方不確定，需要你直接看圖確認。

規則：
- 只描述你**真的看到**的字元，逐字照抄。
- """
    + ai_findings.SUBSCRIPT_MARKUP_RULE + "\n- " + ai_findings.ITALIC_MARKUP_RULE +
    """
- 看不清楚就說看不清楚，**不要猜**。猜錯比空白更糟。

只輸出這個 JSON，不要有其他文字：
{
  "text": "你看到的文字，逐字照抄",
  "characters": ["逐字列出，方便比對"],
  "resolved": true | false,
  "reason": "若 resolved 為 false，說明為什麼看不清楚",
  "confidence": 0.0 到 1.0
}"""
)


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
      * MTPLX (`18120`) and local vLLM / Splash are the other way round.

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
    """Ask what a crop contains. Returns a dict; the verdict is advisory and never applied.

    A reading needs something to read, so `None` bytes is checked here rather than left to
    `base64` - measured on `1141_藥師(一)_藥學(一)` Q41, where every picture in the question is an
    option picture, so no figure crop exists and the call site passed `None`: it raised
    `TypeError: a bytes-like object is required, not 'NoneType'` and killed the whole crop run.
    This check is the boundary's, not the caller's, because any caller can forget.
    """
    if png_bytes is None:
        return {"kind": kind, "verdict": None, "raw": "", "usage": None,
                "error": "no-crop-to-describe", "seconds": 0.0, "bytes": 0, "think": think}
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
    the page was *measured* to be there - an embedded image **it owns**, or option markers with no
    text - so a question that merely mentions 圖 is not returned, and a question with a photograph
    is not missed because its stem stayed silent.

    The box is the union of the question's rows widened to include the embedded images it owns,
    because the picture usually sits beside or below the text that refers to it and a crop of the
    text alone would cut out the very thing being asked about.

    A picture belongs to **exactly one** question, and which one is decided here rather than by each
    question's own overlap test - two overlap tests that both pass are two filings of one picture.
    """
    by_page = {}
    for page, box in picture_boxes(images, min_image_height=min_image_height):
        by_page.setdefault(int(page), []).append(box)

    numbered = list(enumerate(rows, start=1))
    rows_by_id = dict(numbered)
    # Where each question starts: the top of the cell carrying its number, per page. This is what
    # the last option's band is bounded by (`next_start`, below), so it is built from the number cell
    # alone and a continuation page contributes only the questions that actually *begin* there.
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
    # The cells of every item, read once: the band below is per question per page and so is the loop
    # that crops, and both read the same rows.
    cells_by_item = []
    for item in items:
        wanted = set(cell_ids_of(item))
        cells_by_item.append([row for number, row in numbered if number in wanted])

    # **One owner per picture.** A picture object belongs to the *page*, and a page is not a
    # question. While a question could claim any picture its band overlapped, two questions claimed a
    # straddling picture at once, and a paper that prints a picture **above** its question's number
    # had that picture claimed by the question above: the band ran from a question's own number down
    # to the *next* question's number, so it reached over the gap and took a picture that belongs to
    # the question below, which never overlapped it at all. The owner's screen is the measurement
    # (2026-09-25): 「有些題目的圖片沒有截圖正確，有些題目沒有圖片但是卻截別題的來貼上」, and the
    # ownership pass the same day found 844 of 3,263 crops reaching outside their own question's rows.
    #
    # So every picture is given to exactly one question *before* anything is cropped, and a question
    # earns `embedded-image` by owning a picture rather than by overlapping one. The band a picture is
    # measured against is the vertical extent of a question's **own rows on that page** - the same
    # ruler the ownership pass clips a crop to (`crop_run_figures.band_extents`, measured by
    # `reread.band_rows`), and not the slice down to the next question's number, which is what reached
    # over the gap. `_figure_owner` holds the rule and the measurements behind it.
    #
    # A page that continues a question cannot know where that question's content begins: the rest of
    # the previous page's option is above this page's first cell, and its picture is set above its
    # marker. Measured on question 80 of `1112_藥師(一)_藥理學與藥物化學`: `A.` is the last thing on
    # page 18 and its chemical structure is the first thing on page 19, y=29-223, while `B.` `C.` `D.`
    # start at y=232. On a continuation page the band therefore starts at the top of the page, and
    # everything down to the question's own last row there belongs to that question.
    #
    # The band is the question's own rows **except** when its options are pictures, and that
    # exception is measured, not a preference for the old rule. A question whose options are images
    # carries no option text at all, so its own rows are its stem alone while its four pictures are
    # laid out under it in a grid that reaches down to the next question's number. Measured on
    # question 40 of `1021_中醫師_中醫基礎醫學(二)(包括中醫方劑學、中醫藥物學)` page 4: its stem is
    # the only row (y=237-249), its options are `(A)` y=259-407 and `(B)` y=259-407 with `(C)`
    # y=444-575 and `(D)` y=445-567 - mineru's own captions for the four pictures - and question 41's
    # number is at y=586.8. The row band alone hands `(C)`/`(D)` to question 41, which is the
    # 「截別題的圖」 the owner reported, so for these questions the band is the old territory from the
    # question's own number down to the next question's number. Measured read-only over the live
    # queue's 3,296 figure crops 2026-09-25: of the 88 pictures the row band alone would move, 13 are
    # these and 75 sit under a question whose options *do* carry text.
    #
    # The extension is void when the next question's own stem asks for a figure (`_names_a_figure`),
    # because there the picture is what its stem is talking about. It is voided rather than stopped
    # at the picture's top edge: that edge lands exactly on the question's row bottom, and the
    # `FIGURE_ADJACENCY` test would claim it right back (0 pt below counts as adjacent).
    stem_texts = [_stem_text(item, rows_by_id) for item in items]
    # 每一頁上「哪一個題號是從哪一個 y 開始」：延伸要問下一個題號那一題的題幹，而它的帶狀在這一圈
    # 還沒被建出來，所以先在這裡一次算完（同一個 `min(y0)` 定義）。
    start_owner = {}
    for index, cells in enumerate(cells_by_item):
        if not cells:
            continue
        first = min(int(row["page"]) for row in cells)
        starts_here = [float(row["y0"]) for row in cells if int(row["page"]) == first]
        start_owner.setdefault(first, {})[min(starts_here)] = index
    bands_by_page = {}
    for index, cells in enumerate(cells_by_item):
        if not cells:
            continue
        # "Its options are pictures": no option text anywhere, whether the cells are missing
        # altogether (the four pictures carry the labels inside the image) or are blank markers.
        pictured_options = not any(body.strip() for body in option_texts(items[index]))
        first_page = min(int(row["page"]) for row in cells)
        rows_by_page = {}
        for row in cells:
            rows_by_page.setdefault(int(row["page"]), []).append(row)
        for page, page_cells in rows_by_page.items():
            bottom = max(float(row["y1"]) for row in page_cells)
            top = 0.0 if page != first_page else min(float(row["y0"]) for row in page_cells)
            if pictured_options:
                limit = _next_start(starts_on_page.get(page, ()), bottom)
                # 下一個題號那一題如果自己的題幹說要看圖（「下圖」這種），延伸就不能伸過那個間隔：
                # 間隔裡那張圖是**它**題幹要的圖。站上實測（2026-09-25，唯讀）：`moex:107100:305:11`
                # q65 沒有任何選項 cell，帶狀因此接到 q66 的題號 y=524.2，把 q66 題幹寫的「下圖化合物」
                # 兩張圖（y 484.6-520.9、491.8-518.8）整組收走；`moex:106020:305:11` q65/q66 同一個
                # 形狀。停在「上一張圖的上緣」不夠：那個上緣正好落在列底，`FIGURE_ADJACENCY` 的相鄰判定
                # 會把它再收回來（0pt 的間隔算相鄰）。所以這一條是「延伸不生效」，交給距離判定。
                if _names_a_figure(stem_texts[start_owner[page][limit]]
                                   if limit in start_owner.get(page, {}) else ""):
                    limit = bottom
                bottom = max(bottom, limit)
            bands_by_page.setdefault(page, []).append((index, top, bottom))
    # The page a question's single entry is built on: the page carrying its tallest block of own
    # rows, ties to the earlier page - the same measure `_better` chooses with. A question has one
    # entry (`figure_questions` emits one per question), so a picture handed to a question whose
    # entry is built on another page leaves every crop. The option-grid guard reads this.
    entry_pages = []
    for cells in cells_by_item:
        pages_of_cells = {}
        for row in cells or ():
            pages_of_cells.setdefault(int(row["page"]), []).append(row)
        entry_pages.append(None if not pages_of_cells else min(
            sorted(pages_of_cells),
            key=lambda page: (-_box_height(_box_of(pages_of_cells[page])), page)))
    owned = {}
    for page, boxes in by_page.items():
        bands = bands_by_page.get(page) or ()
        for box in boxes:
            owner = _figure_owner(box, bands)
            grid = _option_grid_owner(box, bands, items, stem_texts, page, entry_pages)
            if grid is not None:
                # 上一題的選項圖格（見 `_option_grid_owner`）：留在上面那一題。
                owner = grid
            if owner is not None:
                owned.setdefault((owner, page), []).append(box)

    found = []
    for index, item in enumerate(items):
        cells = cells_by_item[index]
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
            # The pictures this question **owns** on this page, decided once above: a picture appears
            # in one question's crop and in no other's. Only *pictures* count, not the fragments a
            # plot is emitted as: `by_page` is built by `picture_boxes`, which joins scan-line strips
            # and drops what the height floor rejects, so a cluster of 1-5 pt marks is not in it. The
            # raw list is still needed for `fragment_boxes` below, which is the one caller that wants
            # the marks.
            #
            # The box is the union of the question's rows and the picture's *own* box, because the
            # picture is not lined up with the text above it and is often wider than it. Measured on
            # question 68 of `1042_藥師_藥理學與藥物化學`, which asks for the major metabolite of
            # tolmetin and prints three chemical structures under its options: the marker `D.` ends
            # at x=262.5, the first structure spans x=49.1-298.8, and the next two start at x=49.6 -
            # one tenth of a point outside the markers. Matching pictures against the *text's*
            # rectangle took the first structure and dropped the other two, and the crop ended at
            # y=263.2 while the third structure ran to y=367.9. That is the truncation a reader sees
            # as a structure sliced through the middle, and it is the worst kind of error here
            # because the crop still looks like a picture.
            reasons = []
            images_here = owned.get((index, page), ())
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
    """The first question start below `top` on that page, or the foot of the page when there is none.

    `starts` is the y of every question that **begins** on the page, so a question that continues
    onto it from an earlier page is not in the list. Used only to close the band of a question whose
    options are pictures: its own rows are its stem, and its pictures reach down to the next
    question's number.
    """
    for value in starts:
        if value > top + tolerance:
            return value
    return float("inf")


#: How far below a question's own last row a picture may start and still be that question's.
#: Measured on the live queue 2026-09-25, question 68 of `100030:102:0106`: its own row is
#: y=305.1-317.3 and its three option pictures start at y=318.1 - 0.8 pt below it - while the next
#: question's row begins at y=383.1, so a rule that only measures the distance to the two bands
#: hands them to the question below. It is measurement noise, not adjacency: the same paper's
#: `106020:305:11` question 65 has a picture 53 pt below its own rows and 40.6 pt above the next
#: question's, which is a gap and not a hair. 1.0 pt is the tolerance this file already uses for
#: sub-line spacing (`_next_start`), and 2 pt keeps a line's worth of slack around it.
FIGURE_ADJACENCY = 2.0


def _band_distance(point, band):
    """How far a y lies outside a band `(item, top, bottom)`, or 0.0 when it is inside it."""
    _item, top, bottom = band
    return max(0.0, top - point, point - bottom)


#: 題幹自己說「看圖」的字樣。出現這些字，這一題的圖就是它自己的，不是下面那一題的。量到的兩個樣本都
#: 是「下圖」：`moex:106020:305:11` q66「為增加下圖化合物的抗精神病活性…」、
#: `moex:107100:305:11` q66「下列何者為下圖化合物排出人體外的最主要型態？」。要放寬請連同
#: `_option_grid_owner` 的三個條件一起量，不要只加字。
FIGURE_CUES = ("下圖", "上圖", "附圖", "如圖")

#: 選項 cell 只有標記本身（`A.` `B.` `C.` `D.`）就代表這一題的選項是圖。量到的樣本：
#: `moex:108100:305:33` q53「…下列何者最能清楚及正確顯示該藥之血漿中藥物濃度對時間之關係？
#: A. B. C. D.」——四個選項是圖，讀文裡只有標記。
_MARKER_CHARS = set("ABCDabcd.、()（）:：. ")


def _marker_only(body):
    """`A.` / `(B)` / `C、`：有標記、沒有內容的選項 cell。"""
    return not any(ch.isalnum() and ch not in _MARKER_CHARS for ch in (body or ""))


def _names_a_figure(text):
    """題幹提到圖嗎：`下圖`/`上圖`/`附圖`/`如圖`，或 `圖1` / `圖 1` 這種編號。"""
    text = text or ""
    if any(cue in text for cue in FIGURE_CUES):
        return True
    for position, char in enumerate(text):
        if char == "圖" and position + 1 < len(text):
            following = text[position + 1:position + 3].strip()[:1]
            if following.isdigit() or following in "一二三四五六七八九十":
                return True
    return False


def _stem_text(item, rows_by_id):
    """這一題的題幹文字，從讀文裡拿；`stem` 本身就是字串時直接用。"""
    raw = item.get("stem")
    if isinstance(raw, str) and raw.strip():
        return raw
    ids = (item.get("cells") or {}).get("stem") or ()
    return " ".join((rows_by_id[cell].get("text") or "") for cell in ids if cell in rows_by_id)


def _option_grid_owner(box, bands, items, stem_texts, page, entry_pages):
    """上一個題目的選項圖格，或 `None`：這張圖是不是「壓在下一題題號上面的四張選項圖」。

    三個條件都讀自紙本，不是讀自解析結果（樣本在 `FIGURE_CUES` 與 `_marker_only` 的註解裡）：

    1. 整張圖落在**上一題自己的列**與**下一題自己的列**之間的間隔裡——它印在上一題的文字之後、
       下一題的題號之前，所以只有讀兩邊的文字才知道它是誰的；
    2. 下一題的題幹沒有提到圖：`moex:108100:305:33` q54 問二室模式的斜率、`moex:100030:102:0106`
       q69 問推荐哪一種坐墊，都沒有提到圖；
    3. 上一題的選項 cell 沒有內容，只有標記：`moex:108100:305:33` q53 的讀文是 `… A. B. C. D.`，
       `moex:100030:102:0106` q68 連選項 cell 都沒有，四張手部副木圖就是它的選項；
    4. **上一題自己的那張裁圖就蓋在這一頁上**（`_better` 挑的那一頁＝圖所在的那一頁）。每一題只有
       一張裁圖，交給一張裁圖在別頁的題目，這張圖就從每一張裁圖裡消失——站上 2026-09-25 唯讀實測
       三個案例：`moex:113020:305:11` q61 p14、`moex:113090:305:11` q63 p14、
       `moex:114090:305:0401` q79 p21，三張圖都在這一條件成立時才會不見。

    三個都成立時這張圖留在**上一題**：它是那張題幹底下的選項圖格，判給下一題就是題庫上說的
    「沒圖卻貼別題的圖」。站上唯讀實測（2026-09-25）：會動的 12 張 picture-option 圖裡有 4 張是這個
    形狀；站上 3,296 張圖裡另外 8 張的上一題**有**文字選項，那 8 張判給下面那一題。這條守門規則只會
    讓圖「留在上面」，所以它只可能讓動的圖更少，不會多。
    """
    if not bands or not items:
        return None
    top = float(box[1])
    bottom = float(box[3])
    upper = lower = None
    for band in bands:
        if float(band[2]) <= top and (upper is None or float(band[2]) > float(upper[2])):
            upper = band
        if float(band[1]) >= bottom and (lower is None or float(band[1]) < float(lower[1])):
            lower = band
    if upper is None or lower is None:
        return None
    if _names_a_figure(stem_texts[lower[0]] if lower[0] < len(stem_texts) else ""):
        return None
    if any(not _marker_only(body) for body in option_texts(items[upper[0]])):
        return None
    if upper[0] < len(entry_pages) and entry_pages[upper[0]] != page:
        return None                                     # 上面那一題的裁圖在別頁：這張圖誰都收不到
    return upper[0]


def _figure_owner(box, bands):
    """The one question a picture belongs to, or `None` when no question has rows on its page.

    `bands` is every question that has cells on the picture's page, each as `(item index, top,
    bottom)`: the vertical extent of that question's **own rows**, which is the ruler the ownership
    pass cuts a crop to (`crop_run_figures.band_extents`, measured with `reread.band_rows`) - except
    for a question whose options are pictures, whose band is its whole territory down to the next
    question's number (`figure_questions` builds it). Only the vertical axis decides, because a
    page's rows belong to one column: measured over 4,633 pages of 429 papers, exactly one page
    carries two question numbers within 20 pt of each other, and the widest empty band inside a
    page's content is 0.0 pt.

    The rule, in order:

    1. the band the picture's **centre** falls in owns it, and that is the case that was wrong
       before: a picture printed above a question's number sits in the gap *below* the previous
       question's own rows (whose text is complete without it), so it belongs to the question below
       it - while the band that used to decide this ran from a question's number down to the *next*
       question's number, reached over that gap, and gave the picture to a question that has no
       figure of its own. The exception is the other way round and is measured: when the question
       above has **no option text** its pictures *are* its options, so the band is its whole
       territory and the pictures stay with it (question 40 of `1021_中醫師_中醫基礎醫學(二)`, whose
       `(A)`-`(D)` grid sits at y=259-575 under a stem that is its only row);
    2. when that ties - the centre landing exactly on the boundary between two bands, which is what
       a picture overlapping two questions by an equal amount does - or when the centre falls
       outside every band, the band the picture's **top edge** is inside owns it: that is the
       question the picture is printed *from*, its text hanging over the top of the picture;
    3. when the top edge is in no band either, the **nearest** band to that centre owns it. A
       picture printed after a question's text and before the next question's number is still that
       question's picture, measured on question 68 of
       `1152_醫事檢驗師_微生物學與臨床微生物學(包括細菌與黴菌)`: the stem ends at y=627, the two
       photographs it names as 圖1 and 圖2 sit at y=629-812, and no cell of the question touches
       either - the pictures are not beside the text, they are *after* it. The question above wins a
       tie, because the picture was printed after that question's text.

    One picture, one question - 67 of the live queue's 3,867 pictures were claimed by two questions at
    once - and the caller crops it there and nowhere else: measured on the live queue 2026-09-25, 844
    of 3,263 crops reached outside their own question's rows, and the reviewer saw a question with no
    figure of its own carrying the next question's (「有些題目沒有圖片但是卻截別題的來貼上」).
    """
    if not bands:
        return None
    centre = (float(box[1]) + float(box[3])) / 2.0
    top = float(box[1])

    def rank(band):
        if _band_distance(centre, band) <= 0.0:
            # The centre is in this band. A second band holding it too is a tie at the boundary, and
            # there the band holding the top edge is the question the picture is printed from.
            return (0, 0 if _band_distance(top, band) <= 0.0 else 1)
        # The top edge is inside this band, or a hair below its last row: that question's text ends
        # at that row and the picture is printed straight under it, which is a sub-line gap and not a
        # gap (`FIGURE_ADJACENCY`, 0.8 pt measured). No fourth rank - the inside test is read with
        # the same tolerance the rest of this file uses.
        if _band_distance(top, band) <= 0.0 or 0.0 <= top - band[2] <= FIGURE_ADJACENCY:
            return (1, 0)
        return (2, _band_distance(centre, band))

    # The upper band first on a tie (`band[1]`), then reading order (`band[0]`), so the answer never
    # depends on the order the pages happen to be walked in.
    return min(bands, key=lambda band: (rank(band), band[1], band[0]))[0]


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


# --- the table a reading reported --------------------------------------------------------------
# This crop is triggered by a **reading**, and that inverts everything above it. `figure_questions`
# measures the page - an embedded image object, option markers with nothing printed after them -
# because a figure's presence is a fact about the print form. A table is not a fact of that kind:
# whether a run of printed lines is a table, a column heading or a wrapped sentence is *meaning*, and
# this project's rule is that meaning is read and never detected (`qbr/AGENTS.md`: 凡是「讀出文字的
# 意義」都是提示詞). The measurement proposed for it instead - "three or more consecutive lines with
# two or more aligned columns" - is the rule→script→new-problem treadmill the owner refused on
# 2026-09-24 («你一直擴充腳本又會overfitting»).
#
# It is also why a flattened table carries no crop today. `moex:115020:305:0403:1:question:q065`
# prints a four-line table as text: the question holds no image object, its options are not empty, and
# no cluster of fragments covers it - so `reasons` stays empty and `figure_questions` never adds the
# entry. The table's pixels are on the page and nothing ever asked for them.
#
# So the reading decides and locates, and the code does the one thing a script can do exactly: the
# reading quotes the table's own lines verbatim (`ai_findings.TABLE_LINES_RULE`), and
# `quoted_lines_region` finds those lines among the page's **measured** lines and returns the band
# they cover. There is no density test, no column-alignment test and no minimum width below - the
# input is a list of printed lines, the output is where they are.
#
# A reading and the page it read are two cuts of the same text, and they can disagree about where
# the cuts fall and in what order the pieces were printed. Both disagreements are *comparison*
# problems rather than locating ones, and both were measured on the site trial (2026-09-24), on the
# 2 of 5 table questions that came back `not-found`:
#
#   the reading splits a row  `moex:106100:305:33:1:question:q070` is one of them: the reading
#                             reported the table as nine entries - three headings, and then `CYP2D6`,
#                             `10`, `1` and `CYP3A4`, `100`, `50` - while the paper prints those last
#                             six as the two rows `CYP2D6  10  1` and `CYP3A4  100  50`. Compared entry
#                             by entry, `containment('CYP2D6', 'CYP2D6 10 1')` is 1.0 while the reverse
#                             is 0.31, so the entry is *not found* and the table the reading had just
#                             described got no crop. `moex:107020:307:33:1:question:q036` is the same
#                             shape (`Antihistamine 50 mg`, `Directly compressible la`,
#                             `Magnesium stearate 10 mg`...).
#   the paper splits a line  on the same paper, the same table's rightmost column heading is printed
#                             over two lines - `Michaelis-Menten常數(KM)，` and `mg/L` - placed
#                             *around* the line carrying the other two columns' headings, so one
#                             entry of the reading is two lines of the page, and the two are not
#                             neighbours on the page.
#   the two disagree on order the same paper prints those three header lines in another order than
#                             the reading quoted them in, so an entry of the reading need not be
#                             printed after the entry before it.
#
# The correction is inside the comparison and never a loosening of it. Consecutive entries of the
# reading may be taken together (separated by a space, the way a printed row separates its columns)
# and compared with **one** measured line; one entry may be spelled by **several** measured lines,
# taken in the order the page printed them and each of them a piece of the entry the page can
# confirm; and the entries need not reach their rows in the order the reading quoted them. Nothing
# about the page is inferred from any of it: the reading remains the authority on what the table is
# and which lines it holds, the entries joined are the reading's own, the rows taken are rows the
# quoted line is a *piece* of, and every claim is confirmed by the same two-way `canon.AGREE` floor a
# single entry has always been held to.
#
# What is *not* compared away is where the crop ends. The rows claimed must be one contiguous run of
# the page's measured lines with every row of the run claimed by some entry, so a crop cannot grow
# over a line the reading never quoted - and the reading-order chain this replaces could, because a
# chain of increasing rows was free to skip a row between two of its steps. A reading whose entries
# do not *all* find their rows is still reported rather than cut: all of this is a way to compare two
# texts, never a way to guess a row back into place, and the controls in
# `tests/test_vision_table_crop.py` hold it to that by feeding it a row with one digit changed.

def within_band(lines, band):
    """The measured lines that fall inside the rows a question owns.

    `band` is the question's own rows - `reread.band_rows` over the extracted cells, the same
    measurement the dispute pass crops from - and this selects the page's whole printed lines
    (`extract.extract_lines_a`) lying in that vertical range. *Lines* are the search space rather
    than cells because a printed table row **is** a line: the extraction splits it into cells at its
    column gaps, and a row the reading quotes as `錠劑  口服  100  40` is those four cells joined
    again, matching no single cell.

    A line belongs to the band when its own middle is inside the band's vertical extent on that page,
    which needs no tolerance: the band's extent is the first and last row the question owns, and a
    line half in and half out of that would be a different question's line.
    """
    extent = {}
    for row in band or ():
        page = int(row.get("page") or 0)
        top, bottom = float(row.get("y0") or 0.0), float(row.get("y1") or 0.0)
        was = extent.get(page)
        extent[page] = (min(was[0], top), max(was[1], bottom)) if was else (top, bottom)
    out = []
    for row in lines or ():
        rng = extent.get(int(row.get("page") or 0))
        if not rng:
            continue
        middle = (float(row.get("y0") or 0.0) + float(row.get("y1") or 0.0)) / 2.0
        if rng[0] <= middle <= rng[1]:
            out.append(row)
    return out


def _same_printed_line(quoted, measured):
    """Whether a line the reading quoted and a line the page printed are the same text.

    `canon.comparable` is the reduction this project compares any two texts in, and
    `canon.containment` the measure for the case where one text is a *piece* of the other - here the
    paper's line, which the extraction may have joined to a neighbouring column or to the option
    marker beside it. `canon.AGREE` is the boundary `canon._similar` already uses to call two readings
    of a stem the same text, and it is required in **both** directions, because a table's rows are
    short: a single character the extraction mangled (the printed `·` stored as the full-width `．` in
    `AUC (μg·h/mL)`) is one character out of twenty and clears it, while a two-character quoted line
    found inside a paragraph's line also scores high and would bind the crop to the wrong region.
    A row that does not clear it on both sides is *not found*, and a table that is not found is
    reported rather than cut.

    Both sides are the texts as the caller holds them, which are normally already reduced by
    `canon.comparable` - `_measured_lines` reduces the page's lines once for the whole search rather
    than once per comparison. `canon.containment` applies the reduction either way, so a caller may
    pass the texts raw.

    This test is always one text against one measured text, and the measured side is one printed line
    or, for a line the paper split, the several it was printed as, joined in reading order. A reading
    that reports a printed row's cells as separate entries is met by joining a run of its entries
    *before* the test - see `quoted_lines_region` - never by loosening the test itself.
    """
    if not quoted or not measured:
        return False
    if quoted == measured:
        return True
    return (canon.containment(quoted, measured) >= canon.AGREE
            and canon.containment(measured, quoted) >= canon.AGREE)


def _measured_lines(ordered):
    """The page's measured lines in reading order, each with the canonical text it is compared in.

    Reduced once rather than once per comparison: the placement below compares every run of the
    reading's entries against the measured lines, and the reduction is the expensive half of that.
    """
    return [(position, row, canon.comparable(row.get("text") or ""))
            for position, row in enumerate(ordered)]


def _can_agree(a, b):
    """Whether two canonical texts are close enough in length for the measure to reach `AGREE`.

    Necessary, not sufficient, and a consequence of the measure rather than a tolerance of its own:
    each side needs matching blocks covering `canon.AGREE` of its own length, and no block can be
    longer than the *shorter* of the two texts, so a pair whose lengths differ by more than that
    cannot agree however it reads. The test is the measure's own division (`short / long` against
    `AGREE`, in the same rounding), so it can only refuse a pair the measure would refuse too.

    It is here to keep the search cheap: a split grows by a row with every step and a cut of the
    quoted line is tried end by end, so most pairs are nowhere near each other's length, and two
    lengths cost nothing beside a sequence match.
    """
    short, long = (len(a), len(b)) if len(a) <= len(b) else (len(b), len(a))
    return bool(long) and short / long >= canon.AGREE


def _claims(text, measured):
    """The sets of measured rows whose printed lines together are `text`, fewest rows first.

    One printed row is the common case, and the only one for a reading that quotes the table as the
    paper prints it. Several rows are the case measured on the site trial of 2026-09-24
    (`moex:106100:305:33:1:question:q070`): the paper prints the rightmost column's heading over two
    lines, placed *around* the line carrying the other two columns' headings, so the one line the
    reading quoted (`Michaelis-Menten常數(K<sub>M</sub>)，mg/L`) is the page's first and third header
    lines - and those two rows are not neighbours on the page.

    A set of rows is that line only when the line can be **cut** into one piece per row, in reading
    order, each row agreeing with its own piece under `_same_printed_line`. So every character the
    reading quoted belongs to exactly one row of the set, which is what keeps an agreement per row
    instead of an agreement over the lot: on the control in `tests/test_vision_table_crop.py`, one
    digit of a quoted row changed (`TabletOral10099` against the printed `TabletOral10040`) is *not*
    absorbed by the `SolutionOral10050` printed beside it, where comparing the two joined strings
    would have accepted it at 0.93 - the second row's length had paid for the first row's wrong
    digit. Only a row that is a piece of the quoted line can be taken at all (`canon.containment` at
    the same floor everything else here uses), and fewest rows first keeps a single-row match
    preferred over a split of the same text, so a reading that already places entry by entry is
    placed exactly as it was.
    """
    a = canon.comparable(text)
    if not a:
        return []
    pieces = [(position, row, piece) for position, row, piece in measured
              if piece and canon.containment(piece, a) >= canon.AGREE]
    found = {}

    def walk(index, taken, start):
        if taken and start == len(a):
            found[tuple(position for position, _row in taken)] = taken
            return
        for stop in range(index, len(pieces)):
            position, row, piece = pieces[stop]
            for end in range(start + 1, len(a) + 1):
                text = canon.comparable(a[start:end])
                if text and _can_agree(text, piece) and _same_printed_line(text, piece):
                    walk(stop + 1, taken + ((position, row),), end)

    walk(0, (), 0)
    return sorted(found.values(),
                  key=lambda claim: (len(claim), [position for position, _row in claim]))


def _run_claims(wanted, measured):
    """`claims(start, stop)`: the row sets the reading's run `wanted[start:stop]` is the text of.

    A cache rather than a table computed up front: the placement asks for a handful of runs when the
    reading quotes one printed row per entry, which is the common case, and each answer is reused
    across the placements that reach that run from different rows. Its lifetime is one
    `quoted_lines_region` call, which is what the placement and the complaint share.
    """
    runs = {}

    def claims(start, stop):
        key = (start, stop)
        if key not in runs:
            runs[key] = _claims(" ".join(wanted[start:stop]), measured)
        return runs[key]

    return claims


def _is_one_run(positions):
    """Whether these reading-order positions are one contiguous run of the page's measured lines.

    The property that replaces reading order, and the reason it is not a weakening of it: reading
    order was a guard on how far a crop *reaches*, and it guarded that only by accident - a chain of
    strictly increasing rows may skip any number of rows between two of its steps, so a reading that
    quotes two rows with a third printed between them was cut *over* that third row. Requiring the
    claimed rows to be one run refuses exactly that, and the run's edges are the outermost claimed
    rows by construction, so what the crop covers is what the reading named.
    """
    ordered = sorted(positions)
    return bool(ordered) and all(later == earlier + 1
                                 for earlier, later in zip(ordered, ordered[1:]))


def _reading_chain(wanted, claims):
    """The rows the reading's entries tile, or `(None, index)` and where the placement stalled.

    `wanted` are the reading's own entries and `claims` the row sets a run of them is the text of
    (`_run_claims`). The entries are consumed in the order the reading gave them and no row may be
    taken twice, so every row of the block the reading named is claimed by exactly one run of them.

    A run of consecutive entries may be placed against one row - the reading is free to report one
    printed row's cells as separate entries - or, for a line the paper split, against several rows
    (`_claims`). Runs are tried shortest first and only where a shorter one does not place, so a join
    or a split is a repair for an entry that is not one printed row on its own and never a licence to
    regroup a reading that already places entry by entry. A placement is only accepted when the
    *whole* reading places, so a run that covers one row but strands the next three is not accepted
    either.

    **The rows are not required to be in the order the reading quoted them.** The reading and the
    page are two cuts of the same text and they can disagree about the order of the pieces: measured
    on `moex:106100:305:33:1:question:q070`, the reading quoted `代謝酵素`,
    `最大排除速率(V<sub>max</sub>)，mg/h`, `Michaelis-Menten常數(K<sub>M</sub>)，mg/L` while the paper
    printed `Michaelis-Menten常數(KM)，`, then `代謝酵素  最大排除速率(Vₘₐₓ)，mg/h`, then `mg/L` -
    the reading's third heading is the page's first and third lines, printed *around* the reading's
    first two. What replaces the order is the shape of the block (`_is_one_run`): the claimed rows
    must be one contiguous run of the page's lines, so a crop cannot cover a line no entry claimed.
    """
    memo = {}

    def place(index, used):
        if (index, used) in memo:
            return memo[(index, used)]
        chain, stalled = None, index
        for stop in range(index + 1, len(wanted) + 1):
            for taken in claims(index, stop):
                positions = [position for position, _row in taken]
                if any(position in used for position in positions):
                    continue
                if stop == len(wanted):
                    if not _is_one_run(list(used) + positions):
                        continue
                    chain, stalled = [taken], index
                    break
                rest, deeper = place(stop, used | set(positions))
                if rest is not None:
                    chain, stalled = [taken] + rest, index
                    break
                stalled = max(stalled, deeper)     # the first entry the preferred run stranded
            if chain is not None:
                break
        memo[index, used] = (chain, stalled)
        return chain, stalled

    return place(0, frozenset())


def _entries_not_on_the_page(wanted, claims):
    """The reading's entries that no run of consecutive entries, joined, is a line the page printed.

    Reported instead of the stall when the placement fails: an entry the page does not print at all
    is a reading that was wrong about the text, and an entry that is printed but cannot be placed in
    the block the others claim is a reading whose lines are not one run of the page's - the two are
    different complaints and the person reading the report needs to know which one they have.
    """
    placed = [False] * len(wanted)
    for start in range(len(wanted)):
        for stop in range(start + 1, len(wanted) + 1):
            if claims(start, stop):
                for index in range(start, stop):
                    placed[index] = True
    return [wanted[index] for index, found in enumerate(placed) if not found]


def _inside_any(row, boxes, *, tolerance=2.0):
    """Whether a measured line lies inside one of `boxes`, the test `figure_region` applies."""
    if not boxes:
        return False
    x0, y0 = float(row.get("x0") or 0.0), float(row.get("y0") or 0.0)
    x1, y1 = float(row.get("x1") or x0), float(row.get("y1") or y0)
    return any(x0 >= ox0 - tolerance and y0 >= oy0 - tolerance
               and x1 <= ox1 + tolerance and y1 <= oy1 + tolerance
               for ox0, oy0, ox1, oy1 in boxes)


def quoted_lines_region(lines, quoted, *, exclude=()):
    """The band the lines a reading quoted cover, per page, or `({}, complaint)`.

    `lines` are the page's measured printed lines (the caller has already restricted them to the
    question's own band with `within_band`), `quoted` the reading's verbatim strings, and `exclude`
    the boxes whose lines are left out - the option pictures' boxes, the same parameter
    `figure_region` takes, so that a quoted line belonging to an option is not cut twice.

    Every quoted line must be found, and the crop is the measured lines the reading's entries claim -
    no more and no fewer. What the reading and the page may disagree about is where the cuts fall and
    in what order the pieces were printed, so a run of consecutive entries may be *joined* and
    compared with one measured line (a reading that reports a printed row's cells as separate
    entries: `CYP2D6` / `10` / `1` for the row `CYP2D6  10  1`), one entry may be spelled by several
    measured lines taken in reading order (the paper wrapping one quoted line over two lines, which
    is `q070`'s header), and the entries need not reach their rows in the order the reading quoted
    them (the same paper prints the header's three lines in another order). All of it stays inside
    the comparison: the entries joined are the reading's, the rows taken are rows the quoted line is
    a *piece* of, and every claim is confirmed by exactly the test a single entry has always been
    held to - so the band returned is still a union of measured lines and no line is *added* to it.

    What stops a crop from reaching further is the shape of the block: the claimed rows must be one
    contiguous run of the page's measured lines, every row of it claimed by some entry and every
    entry claiming one of them. A crop that shows the wrong question is the worst error available
    here, because it still looks like a picture of a table, and a line no entry quoted is exactly how
    one would come about - so it is refused rather than cut.
    """
    wanted = [str(line).strip() for line in quoted or () if str(line or "").strip()]
    if len(wanted) < 2:
        # No table to cut: the rule defines one as a heading row *and* data rows, so a single quoted
        # line is a line of text and there is no region that could honestly be called the table.
        return {}, "not-a-table:%d-line" % len(wanted)
    ordered = sorted(lines or (), key=lambda row: (int(row.get("page") or 0),
                                                   float(row.get("y0") or 0.0),
                                                   float(row.get("x0") or 0.0)))
    claims = _run_claims(wanted, _measured_lines(ordered))
    chain, stalled = _reading_chain(wanted, claims)
    if chain is None:
        missing = _entries_not_on_the_page(wanted, claims)
        if missing:
            return {}, "not-found:" + "｜".join(line[:24] for line in missing[:3])
        return {}, "not-a-block:%d" % (stalled + 1)
    kept = [row for taken in chain for _position, row in taken if not _inside_any(row, exclude)]
    if not kept:
        return {}, "excluded"
    bands = {}
    for row in kept:
        page = int(row.get("page") or 0)
        box = (float(row.get("x0") or 0.0), float(row.get("y0") or 0.0),
               float(row.get("x1") or 0.0), float(row.get("y1") or 0.0))
        was = bands.get(page)
        bands[page] = ((min(was[0], box[0]), min(was[1], box[1]),
                        max(was[2], box[2]), max(was[3], box[3])) if was else box)
    return bands, ""


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
