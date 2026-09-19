# -*- coding: utf-8 -*-
"""Read a paper by asking the local model to *repartition* the lines, not to rewrite them.

The deterministic reader works by geometry: it groups spans by where they sit on the page. That
is exact and free for the part of a paper that is printed as lines of text, and it is the part
worth keeping. It fails on the part that is a *reading* rather than a measurement - where a
stem ends, which run of characters is an option boundary, whether a flattened fragment belongs
to the word beside it. Those failures do not share a shape, so each one that was met in the
corpus was answered with a rule, and the rules did not converge: `option_alphabet` in `repair`
is the last of them, and it exists because a flattened superscript happened to sit in front of
an option marker, so the marker looked like a lost glyph. A rule about the engine's output will
always have a next case.

So the division of labour is by *kind of question*, not by convenience:

    the script measures what a paper is      the model reads what a paper says
    --------------------------------------   --------------------------------------------
    pages, page numbers, coordinates         where the stem ends
    the size and baseline of every span      which characters are a superscript
    1..N is unique and in order              which characters are a marker, not content
    the option markers' codepoints           whether a question needs a figure
    the question count on the answer sheet   how a table's cells read

What makes this safe is that the model is given no room to invent. It receives the paper as
*numbered lines of text, already extracted*, and its whole output is an assignment: which lines
make up question 1, which lines are its options, which lines are page furniture. It may also
give, for a single line, a *reordering* of that line's own characters - which is how a flattened
`H PO 2 4 -` becomes `H₂PO₄⁻`. Both operations are checkable without knowing anything about the
subject:

    * a `perm` may only drop characters, never add them  -> multiset containment
    * every input line is claimed exactly once           -> total coverage, no duplication

A fabricated character cannot pass the first check and a skipped question cannot pass the
second, whatever the question was about. That is what makes one prompt work for text questions,
table questions and figure questions alike: the check does not ask whether the content is right,
only whether anything was invented or lost. `verify()` is that check, and it is the reason this
stage can be trusted without a rule per question type.

The model is advisory in the governance sense as well: it proposes a reading, `verify()` decides
whether the reading is admissible, and a reading that fails is not repaired but reported. A
paper that does not come back admissible keeps its deterministic reading and is flagged.
"""
from __future__ import annotations

import collections
import json
import os
import re
import unicodedata
import urllib.error
import urllib.request

from . import canon, extract, repair

# --- the endpoint ------------------------------------------------------------------------
# A local engine on this machine, never a service: the address is in the environment because
# the architecture is meant to be machine-independent. `AI395` is switched off and is not
# referred to anywhere in this module.
BASE_URL = os.environ.get("QBR_MODEL_BASE_URL", "http://127.0.0.1:18120")
API_KEY = os.environ.get("QBR_MODEL_API_KEY", "mtplx")
MODEL = os.environ.get("QBR_MODEL_NAME", "ornith-1.5-mtplx-35b")

# A reasoning model spends most of its budget thinking before it answers, and the thinking is
# charged to `max_tokens`. Measured on this engine: a one-question verdict spent 630 reasoning
# tokens of a 1651-token answer. The first attempt at a whole paper spent *all* 22,950 tokens of
# a 90-per-line budget on reasoning and never emitted a character of the answer - the budget has
# to be sized from the work, and the work is per question, and by a wide margin. Cost here is
# local and near zero; a truncated answer is the expensive outcome, because the whole paper has
# to be asked again.
MIN_COMPLETION_TOKENS = 24000
COMPLETION_TOKENS_PER_QUESTION = 900
COMPLETION_TOKENS_PER_INPUT_LINE = 200
# Reasoning and the answer are drawn from the *same* `max_tokens`. Leaving that as one number is
# what produced the worst result measured in this project, and it is worth writing down exactly:
#
#   thinking off    5,085 completion tokens, 44 s, every cell accounted for
#   thinking on   115,628 tokens, half an hour, 111,182 of them reasoning
#   thinking on   156,400 tokens, forty minutes, and **zero** tokens of answer - the budget was
#                 exhausted by reasoning before a single question was written.
#
# The third figure is exactly the budget that was offered, so the ceiling was the binding
# constraint and not the model's own stopping. Two budgets are therefore computed and added:
# an answer that scales with the paper, and a ceiling for reasoning that scales with the input.
# Neither is a prediction; the ceiling is what keeps the worst case finite, and a paper that
# exceeds it is escalated on rather than paid for twice.
REASONING_TOKENS_PER_LINE = 500
MIN_REASONING_TOKENS = 48000


def answer_budget(count, lines):
    """What the answer alone needs, in completion tokens.

    Measured over twelve 80-question papers read with thinking off: 4,955 to 5,426 completion
    tokens each, which is about 65 tokens a question. The per-question and per-line terms below
    are several times that, because under-sizing the answer is the one outcome that costs a whole
    re-run - the answer is a JSON object naming a cell id per stem and per option, so it grows
    with the paper rather than with its length in characters.
    """
    return max(MIN_COMPLETION_TOKENS,
               COMPLETION_TOKENS_PER_QUESTION * (count or 40)
               + COMPLETION_TOKENS_PER_INPUT_LINE * lines)


def reasoning_budget(lines):
    """A ceiling for the thinking channel, which is not part of the answer.

    Measured reasoning on the same kind of paper: 17,145 tokens (41 per cell) on one that
    produced a perfect reading, 111,182 (264 per cell) on a hard one, and one that reached 156,398
    and still had nothing to say. The ceiling is set above the highest of those so that a paper
    which *can* finish is not cut off, and finite so that one which cannot does not run forever.
    """
    return max(MIN_REASONING_TOKENS, REASONING_TOKENS_PER_LINE * lines)


def completion_budget(count, lines, *, think=True):
    """What to ask the engine for: the answer's room plus, when thinking, the reasoning's."""
    answer = answer_budget(count, lines)
    return answer + reasoning_budget(lines) if think else answer


def _decimal(text):
    """Whether text is digits that `int()` can read.

    `str.isdigit()` is True for `①`, `②`, `²` and `٣`, and `int()` raises on the first three. The
    skeleton walks every cell of every paper, so a single circled numeral - which these papers use
    as a *sub-item* marker inside a stem, measured on `1102_醫事檢驗師_臨床生理學與病理學` - took
    down a whole batch with `invalid literal for int() with base 10: '①'`.
    `isdecimal()` is the predicate that matches what `int()` accepts.
    """
    return bool(text) and text.isdecimal()


def _endpoint(base, path="/chat/completions"):
    """Join a base URL and an OpenAI path without caring whether `/v1` was written down."""
    base = base.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base + path


# --- the line table: what the model is shown ---------------------------------------------
# The paper is handed over as numbered lines. The number is the only handle the model has, and
# it is the provenance of every character in the answer, which is what makes the containment
# check possible at all.

_SIZE_HINT_RATIO = 0.85      # a span this much smaller than the body is a hint, not a decision


def _body_size(rows):
    """The size most of the paper's characters are set in."""
    weight = collections.Counter()
    for row in rows:
        weight[round(float(row.get("size") or 0.0), 1)] += len(row.get("text") or "")
    return weight.most_common(1)[0][0] if weight else 0.0


class CellTable(list):
    """A list of `(cell_id, text, hint)` triples that also carries each cell's box.

    The list is the interface every caller already uses - it is iterated, indexed and compared as a
    plain list of triples - and `geometry` holds `{cell_id: (page, x0, y0, y1)}` for the rules that
    need to know where a cell sits. Two questions need it and neither can be answered from the text:
    whether a cell begins its line, and how tall the thing beside it is.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.geometry = {}


def line_table(rows, *, body_size=None, alphabet=()):
    """The paper as [(cell_id, text, hint)], one entry per cell, in reading order.

    A cell is the smallest unit a reader can point at in the page - a question number, a line of
    a stem, one option, one entry of a row that carries four short options. Handing over cells
    rather than lines is what removes the need for a rule per layout: a row that carries two
    options is two cells, so "these two things are two options" is expressible, where at line
    level the only possible answer was to name the same line twice.

    The hint tells the model what the script measured and therefore cannot be argued with:

        `opt:X`   the cell begins with one of the paper's option markers, and X is the label
                  that marker stands for. The markers are the consecutive private-use codepoints
                  the whole paper uses once per question - a measured property of the print form,
                  not a guess - so which cells are options, and which option each is, is settled
                  before the reading begins.
        `number`  the whole cell is digits, which is the shape of a question number. It is
                  offered because a cell beginning with an already-used number is a measured trap
                  (`7.5×10⁹/L`, `53.33歲油漆工`) and the model should see which cells could be
                  numbers at all.
        `small`   the cell is set smaller than the body of the paper, which is the geometry of a
                  superscript or subscript that the reader pushed out of its host line.

    None of the three decides anything by itself. Whether a small run is a superscript, an
    exponent or a page number is a reading, and the reading is the model's; the script
    contributes only the measurements, which the model cannot make for itself.
    """
    body_size = body_size or _body_size(rows)
    labels = {code: canon.LABELS[index] for index, code in enumerate(alphabet)
              if index < len(canon.LABELS)}
    table = CellTable()
    for index, row in enumerate(rows, start=1):
        text = (row.get("text") or "").rstrip()
        size = float(row.get("size") or 0.0)
        stripped = text.strip()
        lead = ord(stripped[0]) if stripped else 0
        if lead in labels:
            hint = "opt:" + labels[lead]
        elif _decimal(stripped):
            hint = "number"
        elif body_size and size < body_size * _SIZE_HINT_RATIO:
            hint = "small"
        else:
            hint = ""
        table.append((index, text, hint))
        # The geometry travels with the table because the table is what the reader is given, and a
        # reader that cannot see where a cell sits cannot tell a question number from a number in a
        # table column. It is attached rather than passed alongside so that every existing caller,
        # which treats the table as a plain list of triples, keeps working unchanged.
        table.geometry[index] = (row.get("page"), float(row.get("x0") or 0.0),
                                 float(row.get("y0") or 0.0), float(row.get("y1") or 0.0))
    return table


def render_table(table, *, pages=None, blank_limit=0):
    """The numbered cells as the text the model reads, grouped by page.

    Page boundaries are kept because they are a property of the paper and they are what a
    running head and a page number are recognised against: the same string in the middle of a
    page is content, in the cell that opens a page it is furniture. The hint is printed beside
    the number where there is one, so that the model reads a measured fact and not an inference.
    """
    out, current = [], None
    for position, (cell_id, text, hint) in enumerate(table):
        if pages:
            page = pages[position]
            if page != current:
                out.append(f"第 {page} 頁")
                current = page
        if not text.strip():
            if not blank_limit:
                continue
            out.append(f"{cell_id}:")
            continue
        mark = f"[{hint}] " if hint else ""
        out.append(f"{cell_id}: {mark}{text}")
    return "\n".join(out)


def lines_with_pages(rows):
    """The table and the page of each entry, from one reading of the paper."""
    table = line_table(rows)
    return table, [row.get("page") for row in rows]


def cells_with_pages(rows, *, alphabet=()):
    """The rows as a numbered cell table, with the page of each cell.

    The option alphabet is worked out from the rows themselves when the caller has not measured
    it: `repair.option_alphabet` reads the paper's own use of private-use codepoints, which is a
    property of the print form, so the same answer comes out of any paper that uses one.
    """
    alphabet = tuple(alphabet) or tuple(sorted(repair.option_alphabet(
        "".join(row.get("text") or "" for row in rows))))
    table = line_table(rows, alphabet=alphabet)
    return table, [row.get("page") for row in rows], alphabet


# --- what the model is asked --------------------------------------------------------------
# Everything below this line was learned from the corpus, and each part of it is here because a
# question type was measured going wrong without it. This text is the asset: a rule that used to
# be Python is now a sentence the model reads, and a sentence costs nothing when it is wrong,
# where a rule costs a false rejection.

SYSTEM = """你是一台「排版還原器」，不是解題者，也不是編輯。

你收到的是一份台灣國家考試題目卷，已經由程式抽取成「編號的儲存格（cell）」。抽取做了三種測量，
每一格前面會標示測量結果：

- `[opt:A]` / `[opt:B]` / `[opt:C]` / `[opt:D]`：這一格的開頭是**選項標記**，
  這個標記代表哪個選項已經量好了。這是紙張印表機的事實，不是猜測。
- `[number]`：這一格整格都是數字，**有可能**是題號（不一定，見規則 1）。
- `[small]`：這一格的字級比正文小，這是**上標或下標常見的幾何特徵**（不一定，見規則 3）。

測量之外的部分——哪些格屬於同一題、小字是哪個字的上下標、哪裡是圖——由你判讀。

# 你唯一被允許做的事

把收到的每一格**指派**到一個位置。你可以：
1. 指定某些格構成某一題的題幹（照閱讀順序排列格號）。
2. 指定某些格是某一題的 A、B、C、D 選項。
3. 指定某些格是頁面雜訊（頁碼、浮水印、重複的頁首頁尾、座號欄）。
4. 對**單一格**給出該格字元的重新排列（`perm`），用來把被推出去的上下標放回原位。

# 你絕對不可以做的事

- **不可以新增任何字元。** 不可以改錯字、不可以補字、不可以翻譯、不可以改寫語意、
  不可以自己造題目或選項。`perm` 只能是原本那一格字元的重新排列（可以刪掉多餘空白，
  不能無中生有）。系統會逐字比對，多一個字整卷就不被採用。
- **不可以回答題目。** 答案由答案卷決定，與你無關。
- **不可以跳過任何一格。** 每一格都要被指派到某處。沒有用到的格會被列為「未指派」，
  那是要被看見的異常。

# 判讀規則（依重要性排序）

**1. 題號是紙張的性質。** 一份卷子的題號是 1 到 N，**每個號碼只出現一次，且依序遞增**。
若某一格以「已經用過的號碼」開頭，那它不是新題目，而是**同一題的內容**——最常見的是以
數值開頭的敘述，例如 `7.5×10⁹/L`、`11.3～14.6秒`、`1.2 mg/dL`、`4.8%`、`53.33歲油漆工`。
題號通常單獨佔一格（會標 `[number]`），而**單獨一格的題號後面接的下一格才是題幹**。

**2. 選項以 `[opt:X]` 標記為準。** 標記字元本身**不是內容**，它是 A/B/C/D 的位置。
把 `[opt:A]` 那一格指派給 `options.A`（該格文字**包含開頭的標記字元**，照抄即可）。
一題裡應該恰好有 A、B、C、D 各一個。
若某格**不含**標記、卻是同一列上與選項並排的另一個選項，那也是選項：
同一列的四個短選項會是四個獨立的格，各自指派給 A、B、C、D。
另有一組 `\ue000 \ue001 \ue002 \ue003` 是「①②③④」的**子項目編號**，不是選項，
要留在它所屬的題幹或選項文字裡。

**3. 上下標被推出去。** 抽取器會把上下標的字元推到**格尾**或**獨立成一格**，
且字級通常比正文小（會標 `[small]`）。這是化學式、離子式、數學式最常見的損壞：
- 紙本 `H₂PO₄⁻` 可能抽成 `H PO` 加上格尾的 `2 4 -`。
- 紙本 `PO₄³⁻` 可能抽成 `PO4 3-`。
- 紙本 `10⁻⁵`、`Vmax`、`Km`、`HbA₂`、`CO₂` 都可能被拆開。
遇到這種情形，**用 `perm` 把該格的字元重排回紙本的樣子**，或把那幾個獨立的小格
直接接在它應該屬於的那一格後面（順序上相鄰即可）。你只能重排與移動，**不能補字**；
不確定就保持原樣，不要猜。

**4. 表格是圖片。** 題幹或選項裡出現成排的數字、單位、欄位標題（例如血壓、檢驗值、
濃度對照表），那是紙本上的表格。表格在文字層只剩一堆數字，**語意已經流失**。
這種情形在該題加上 `"figure": "table"`。

**5. 圖形是圖片。** 題幹提到「附圖」、「下圖」、「圖中」、「曲線」、「光譜」、「結構」、
「腦波圖」，或文字層出現座標軸的殘骸（例如 `saturation (%)`、`ppm`、`1 sec`、
成串的 `¹ ² ³`），那是紙本上的圖。在該題加上 `"figure": "image"`。
**圖的殘骸格仍然要指派給該題的題幹**，不要丟掉。

**6. 頁面雜訊。** 每頁重複出現的頁首、頁尾、頁碼（如 `頁次：4－3`）、浮水印、
准考證號碼欄位，都屬於 `chrome`。但**只有整頁性質的東西**才算：
同一段文字若只出現一次且位在題目中，那就是內容。

**7. 失去映射的字元要保留。** 私用區字元若**夾在詞的中間**（例如 `轉胺\ue2c6`，紙本上
顯示為「轉胺酶」），那是這份 PDF 的字型沒有對應表，字在紙上**顯示正常**，只是文字層
沒有對應到 Unicode。**保留原字元，不要猜測、不要替換、不要移除**，系統會把該題標記出來
讓人工確認。

# 輸出格式

只輸出一個 JSON 物件，不要有其他文字、不要用 markdown 圍籬：

{
  "questions": [
    {
      "number": 1,
      "stem": [12, 13],
      "options": {"A": [15], "B": [16], "C": [17], "D": [18]},
      "figure": "image" 或 "table"（沒有就省略）
    }
  ],
  "chrome": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
  "perm": {"27": "H₂PO₄⁻"},
  "notes": "一行說明你不確定之處，沒有就省略"
}

- `stem` / `options` 的值是**格號的陣列**，依閱讀順序排列。跨格就以多個格號表示。
- 一格的選項可以跨多格（被排版折行時）：`"C": [24, 25]`。
- `perm` 的鍵是格號（字串），值是該格重排後的字串。**只放真的需要重排的格。**
- 題號請用整數。若紙本題號有缺漏，照紙本給，不要自己補號。
"""

USER = """這一卷的題數（由答案卷確認）：{count}
總格數：{total}
全文體字級：{body_size}
本卷的選項標記碼：{alphabet}

以下是編號的題目卷內容：

{table}

請把每一格指派到題幹、選項、或頁面雜訊，並輸出上述 JSON。
記得：不可以新增字元、每一格都要被指派、題號 1 到 {count} 每個只出現一次。"""


def build_messages(table, *, subject="", year="", count=None, body_size=None, pages=None,
                   alphabet=()):
    """The messages for one paper, and the table they refer to."""
    codes = "、".join(f"\\u{code:04x}={canon.LABELS[index]}"
                     for index, code in enumerate(alphabet) if index < len(canon.LABELS))
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": USER.format(
            count=count if count else "（未確認，請照紙本判讀）",
            total=len(table), body_size=body_size or "（未知）",
            alphabet=codes or "（本卷無私用區標記，選項以 A. B. C. D. 印刷）",
            table=render_table(table, pages=pages))},
    ]


# --- the call ------------------------------------------------------------------------------
# Thinking is a knob, and it is not free. Measured on one crop through the vision prompt, four
# spellings of "do not think" were tried against the default: `reasoning_effort` (both at the top
# level and nested), and `thinking_budget: 0` - **all three were ignored**, the engine returned the
# identical 465 completion tokens and 345 reasoning tokens every time. `enable_thinking: false`
# was obeyed: 173 tokens, no reasoning tokens, 1.7 s against 4.4 s.
#
# So the choice is not "think or not" in the abstract. It is a question per task, and the honest
# way to settle it is to run the task both ways and compare the *answer*, because a faster wrong
# answer is the most expensive thing here.
THINK_CHAT_TEMPLATE = {"enable_thinking": False}


def ask(messages, *, max_tokens=None, timeout=3600, think=True):
    """One call. Returns (parsed_or_None, raw_text, usage, seconds).

    The timeout is long on purpose. A whole paper is a long answer from a reasoning model and
    the engine is one local process: cutting it off is not saving time, it is throwing away the
    work and paying for it twice. Measured: a single-question verdict took 5.4 s, so the budget
    for a paper is set by its length rather than by a fixed ceiling.

    `think=False` asks the engine to answer without the reasoning channel. The budget is not
    reduced with it, because reasoning tokens are drawn from the same `max_tokens` and a budget
    that only fits the thinking is a budget that returns nothing at all - measured: a paper given
    22,950 tokens to think with spent every one of them on thinking and emitted no answer.
    """
    import time
    body = {"model": MODEL, "messages": messages, "temperature": 0,
            "max_tokens": max_tokens or MIN_COMPLETION_TOKENS}
    if not think:
        body["chat_template_kwargs"] = dict(THINK_CHAT_TEMPLATE)
    request = urllib.request.Request(
        _endpoint(BASE_URL), data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + API_KEY})
    started = time.time()
    try:
        raw = json.loads(urllib.request.urlopen(request, timeout=timeout).read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        return None, f"request failed: {exc}", {}, time.time() - started
    elapsed = time.time() - started
    choice = (raw.get("choices") or [{}])[0].get("message") or {}
    content = choice.get("content") or ""
    return _parse(content), content, raw.get("usage") or {}, elapsed


def reasoning_tokens(usage):
    """How much of the completion was thinking, however this engine spells it."""
    details = usage.get("completion_tokens_details") or {}
    for source in (details.get("reasoning_tokens"), usage.get("reasoning_tokens")):
        if source is not None:
            return int(source)
    return 0


def _parse(content):
    """Pull the JSON object out of whatever the model wrapped it in."""
    text = (content or "").strip()
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            candidate = part.lstrip("json").strip()
            if candidate.startswith("{"):
                text = candidate
                break
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


# --- the check: this is what makes one prompt enough ---------------------------------------
_WHITESPACE = re.compile(r"\s+")


def _char_multiset(text):
    """The characters of a line, with the layout whitespace taken out of the question.

    A reflowed line differs from its source in spacing by construction - that is part of what
    reflowing is - so spacing is not compared. What is compared is every other character, which
    is the whole of what a reader would see as the text.
    """
    return collections.Counter(_WHITESPACE.sub("", unicodedata.normalize("NFC", text or "")))


def _tokens(entry):
    """A content slot as [(line_id, perm_or_None)], accepting the plain and the reflowed form."""
    out = []
    for token in entry or []:
        if isinstance(token, int):
            out.append((token, None))
        elif isinstance(token, str) and _decimal(token.strip()):
            out.append((int(token.strip()), None))
        elif isinstance(token, dict) and "line" in token:
            out.append((int(token["line"]), token.get("perm")))
        else:
            out.append((None, None))
    return out


def verify(reading, table):
    """Is this reading an admissible repartition of the paper? Returns (ok, report).

    Two properties are checked, and neither of them knows anything about the subject of the
    paper:

        containment   a `perm` may drop characters but may never introduce one, so a fabricated
                      character cannot survive however plausible it looks
        coverage      every line is claimed exactly once across the whole paper, so a dropped
                      question cannot hide and a line cannot be counted twice

    Both are properties of a *repartition*, which is the only kind of output the model is asked
    for, so the same check is the right one for a chemistry question, a table question and a
    figure question. Nothing here was written for a particular question type.
    """
    text_of = {line_id: text for line_id, text, _ in table}
    known = set(text_of)
    used = collections.Counter()
    invented, dropped, unknown, malformed = {}, {}, [], []
    perms = {str(k): v for k, v in (reading.get("perm") or {}).items()}

    def slot(entry, where):
        for line_id, perm in _tokens(entry):
            if line_id is None:
                malformed.append(where)
                continue
            if line_id not in known:
                unknown.append((where, line_id))
                continue
            used[line_id] += 1
            if perm is None:
                continue
            want, got = _char_multiset(text_of[line_id]), _char_multiset(perm)
            extra = got - want
            if extra:
                invented[line_id] = "".join(sorted(extra.elements()))
            missing = want - got
            if missing:
                dropped[line_id] = "".join(sorted(missing.elements()))

    questions = reading.get("questions") or []
    for question in questions:
        number = question.get("number")
        slot(question.get("stem"), f"Q{number}.stem")
        for label, entry in (question.get("options") or {}).items():
            slot(entry, f"Q{number}.{label}")
    for index, entry in enumerate(reading.get("chrome") or []):
        slot([entry] if not isinstance(entry, list) else entry, f"chrome[{index}]")
    # The top-level `perm` map is checked here and not per token, because it is addressed by cell
    # id rather than attached to a slot, and a cell may be reflowed without being named by any
    # slot at all. Every entry is compared against the cell it claims to reflow, whether or not
    # that cell was assigned anywhere: a perm that reorders a cell nobody used is still a
    # statement about that cell, and a fabricated character in it is still a fabricated
    # character. It claims nothing in the coverage count, because reordering a cell is not
    # placing it - the slot that names the cell does that.
    for key, value in perms.items():
        if not _decimal(key):
            malformed.append(f"perm[{key}]")
            continue
        line_id = int(key)
        if line_id not in known:
            unknown.append((f"perm[{key}]", line_id))
            continue
        want, got = _char_multiset(text_of[line_id]), _char_multiset(value)
        extra = got - want
        if extra:
            invented[line_id] = "".join(sorted(extra.elements()))
        missing = want - got
        if missing:
            dropped[line_id] = "".join(sorted(missing.elements()))

    duplicated = {line_id: n for line_id, n in used.items() if n > 1}
    unassigned = [line_id for line_id in sorted(known)
                  if used.get(line_id, 0) == 0 and text_of[line_id].strip()]

    report = {
        "questions": len(questions),
        "lines": len(table),
        "assigned": sum(1 for line_id in known if used.get(line_id, 0) == 1),
        "invented_characters": invented,
        "dropped_characters": dropped,
        "unknown_lines": unknown[:20],
        "malformed_tokens": malformed[:20],
        "duplicated_lines": duplicated,
        "unassigned_lines": unassigned[:40],
        "unassigned_count": len(unassigned),
        "perm_lines": len(perms),
    }
    # A reading is admissible when nothing was invented, no line was claimed twice, and nothing
    # but blank lines went unclaimed. Dropped characters are *not* fatal and are not hidden:
    # a page number may be dropped from a line that also carries content, and the audit says
    # exactly which characters went, so the loss is visible rather than silent.
    report["ok"] = bool(
        questions and not invented and not duplicated and not unknown and not malformed
        and not report["unassigned_count"])
    return report["ok"], report


# --- composing the reading into items ------------------------------------------------------
def _compose(entry, text_of, perms):
    """The text of a content slot, joined the way the paper joins its lines."""
    pieces = []
    for line_id, perm in _tokens(entry):
        if line_id is None or line_id not in text_of:
            continue
        # The per-slot form (`{"line": 3, "perm": "…"}`) wins over the top-level map, so a slot
        # can carry its own reflow. Both are checked by `verify` before composing happens.
        pieces.append(perm if perm is not None else perms.get(str(line_id), text_of[line_id]))
    joined = ""
    for piece in pieces:
        joined = repair.join_lines(joined, piece.strip())
    return joined.strip()


# --- the part the paper states outright ----------------------------------------------------
# A question number is printed, unique and in order; an option marker is printed. Both are facts
# about the page, so both can be read without reading the text - and if they can be read, they
# should not be guessed at. The skeleton is that reading: it says which cell each question starts
# at and which cell carries each option label, and it says nothing about what the words mean.
#
# This is what the model is not asked to work out. Measured on `1152_醫事檢驗師_生物化學與臨床
# 生化學`: asked to assign every cell, the model produced a reading with no invented character,
# no lost cell and no duplicate - and still slipped by one at question 55, because question 55's
# stem is printed over two cells and the model put the second of them into option A. Every cell
# was placed, so coverage saw nothing, and every question after it was shifted by one. The paper
# had already printed that boundary; nothing was gained by inferring it.
_QUESTION_LEAD = re.compile(r"^(\d{1,3})\s*[.\u3001]")


_MARGIN_TOLERANCE = 4.0


def _x0_of(table, line_id):
    geometry = getattr(table, "geometry", None) or {}
    box = geometry.get(line_id)
    return float(box[1]) if box else None


def _margin_of(table, leftmost):
    """The x of the paper's left body margin, or None when it cannot be measured.

    Measured as the leftmost cell that could begin question 1 - the first cell on the page that
    opens with a `1.` mark and is at the left of its line. Taking the leftmost cell of *any* kind
    would find the page's own margin rule or a running head; taking the cell that begins question 1
    finds the margin that questions are set to, which is what the comparison needs.

    Returning None is a real answer: a caller that built a table by hand has no geometry, and a
    paper whose question 1 is not where the others are cannot be measured this way. None means the
    rule does not apply, and the reading proceeds exactly as it did before the rule existed.
    """
    for line_id, text, _hint in table:
        if line_id not in leftmost:
            continue
        stripped = (text or "").strip()
        match = _QUESTION_LEAD.match(stripped)
        if not match or int(match.group(1)) != 1:
            continue
        return _x0_of(table, line_id)
    return None


def _leftmost_cells(table):
    """The cells that begin their visual line: no other cell on the same line starts further left.

    The lines are not passed in, so they are recovered from the geometry the table carries - the
    cells that the table was built from are in reading order, and two cells share a line when they
    are on the same page and their vertical extents overlap. That is the same relation
    `group_visual_lines` used to build the table, applied here to the cells it produced.

    A cell at the left margin is the only candidate for a question number, a heading or a section
    title; anything indented is inside something - a table column, a continuation, an option body.
    """
    rows = table
    # Only cells that could be a question number are tested, and only against cells that start to
    # the left of them, so the cost is one pass over the cells plus a comparison per cell pair that
    # shares a line. On a 400-cell paper that is a few thousand comparisons and it happens once.
    spans = [(line_id, text or "") for line_id, text, _ in rows]
    return {line_id for line_id, _ in spans if not _something_left(rows, line_id)}


def _something_left(rows, line_id):
    """Whether any other cell on the same line begins further left than this one."""
    # When the table carries no geometry - a caller that built one by hand for a test - every cell
    # is leftmost, which is the same as the old behaviour and keeps this from refusing a paper it
    # cannot measure.
    geometry = getattr(rows, "geometry", None)
    if not geometry:
        return False
    box = geometry.get(line_id)
    if not box:
        return False
    page, x0, y0, y1 = box
    for other, (o_page, o_x0, o_y0, o_y1) in geometry.items():
        if other == line_id or o_page != page:
            continue
        if min(y1, o_y1) - max(y0, o_y0) <= 0:
            continue
        if o_x0 < x0 - 0.5:
            return True
    return False


def skeleton(table):
    """The reading the paper itself states: question starts, option cells, and the rest.

    Returns `{"questions": {number: {"stem": [...], "options": {label: [...]}}}, "chrome":
    [...], "missing": [...], "complete": bool, "count": n}`.

    Read in one pass, because both marks are printed in reading order and the order is part of
    what makes them readable:

        a question starts at a cell whose text opens with the number being looked for
        an option starts at a cell carrying an `opt:X` hint, which the paper printed itself
        a cell with neither mark continues whatever it follows - a wrapped stem, a wrapped option

    `complete` is False when the numbers do not run `1..N` or a question is missing one of its
    four labels, and `missing` names what was not found. An incomplete skeleton is not repaired
    and not guessed at: it is reported, because a paper whose shape its own marks do not describe
    is a finding about the paper, and this is the class that needs a person.
    """
    # Two ways an option marks itself, and both are printed. A private-use marker is the one the
    # paper's font assigns to the label; an ASCII `A.` is the label itself. A paper uses one or
    # the other, and which one is a property of that paper's print form.
    option_cells = {}
    for line_id, text, hint in table:
        if (hint or "").startswith("opt:"):
            option_cells[line_id] = hint[4:5]
            continue
        label = repair.option_label(text or "")
        if label:
            option_cells[line_id] = label

    # A question number is read in reading order, expecting the next one. The paper prints 1..N
    # in order, so a cell that says `7` is the start of question 7 only when questions 1 to 6 have
    # already been found - which is what stops a heading, a year, a room number or a page number
    # from being taken for a question number merely because it is a number.
    order = [line_id for line_id, _, _ in table]
    position = {line_id: index for index, line_id in enumerate(order)}
    # A cell that is not the leftmost on its line is not a question number, whatever it says.
    #
    # This is the rule that the reading-order test could not supply, and it was found by a paper
    # rather than reasoned about. `1151_藥師(一)_藥學(三)` prints a table inside question 65 whose
    # dose column holds `100`; with `65` found and `100` next in reading order, the table's own
    # data was taken for question 100. That ended question 65 early - at the table, before its four
    # options - and lost questions 66 to 80 entirely, 15 questions, while the answer sheet still
    # said 80. The subject failed on exactly one paper every year for the same reason.
    #
    # Measured over 1,045 papers and 76,120 questions in ten subjects: the first cell of a question
    # is the leftmost cell on its visual line in 76,116 cases, 99.995%. A number inside a table is
    # in a column, so there is a cell or a rule to its left. This is a property of the print form -
    # a question begins at the left margin - and not of any engine or subject.
    leftmost = _leftmost_cells(table)
    # The second geometric rule, and the one the leftmost rule cannot supply: a question number
    # begins at the paper's own body margin, and a wrapped line of a stem or an option begins in
    # the option column.
    #
    # A cell alone on its line has nothing to its left, so `leftmost` passes it whatever it says.
    # Measured on `1142_藥師(一)_藥學(二)(包括藥物分析與生藥學(含中藥學))`: question 3's stem reads
    # `3.碘化銀飽和溶液於25℃時的濃度為1.23×10⁻⁸ 莫耳／升…`, the formula wraps, and the wrapped
    # line is the cell `234.8）` - which reading order takes for question 234 and which ends
    # question 3 before its options. The paper lost questions 4 to 80, 77 of 80 questions, and
    # `1091_醫事檢驗師_臨床血液學與血庫學` lost 56 the same way on the cell `90.1 fL、reticulocyte
    # 0.7`.
    #
    # The margin is measurable from the paper itself: question 1 is the first question, so the
    # cell beginning it is the left margin. Measured over these four categories, every candidate
    # number the skeleton did *not* accept sits at least 10.4 pt to the right of that margin, and
    # no paper has a rejected candidate within 2 pt of it. The tolerance is what absorbs the
    # fractions of a point a font's left side bearing adds; it is not fitted to the failures.
    margin = _margin_of(table, leftmost)
    starts, missing = [], []
    expected = 1
    for line_id, text, hint in table:
        if line_id not in leftmost:
            continue
        stripped = (text or "").strip()
        match = _QUESTION_LEAD.match(stripped)
        value = int(match.group(1)) if match else (int(stripped) if _decimal(stripped) else None)
        if value is None:
            continue
        if value != expected:
            # The margin is only consulted for a number that is not the one being looked for. A
            # cell that says exactly what comes next *is* the next question, and refusing it
            # because of where it sits would lose real questions - measured, 864 question starts
            # sit at x=31 while their paper's question 1 is at x=22, because the paper indents
            # after the first page. The test is for a number that would otherwise be taken as a
            # jump, which is the case that can silently swallow everything after it.
            if margin is not None and _x0_of(table, line_id) > margin + _MARGIN_TOLERANCE:
                continue
        if value == expected:
            starts.append((position[line_id], value))
            expected += 1
        elif value > expected:
            # A number further ahead than the next one means the paper skipped, which is a fact
            # about the paper worth reporting rather than a reason to keep looking for the gap.
            missing.append(f"question-{expected}")
            starts.append((position[line_id], value))
            expected = value + 1
    # A leading `1` is required for the numbers found to be the paper's own numbering.
    if not starts or starts[0][1] != 1:
        return {"questions": {}, "chrome": list(order), "missing": ["question-1"],
                "complete": False, "count": 0}

    questions = {}
    for index, (start, number) in enumerate(starts):
        stop = starts[index + 1][0] if index + 1 < len(starts) else len(order)
        window = order[start:stop]
        first_option = next((cell for cell in window if cell in option_cells
                             and option_cells[cell] == "A"), None)
        if first_option is None:
            missing.append(f"question-{number}-options")
            questions[number] = {"stem": window, "options": {}}
            continue
        split = window.index(first_option)
        options, current = {}, None
        for cell in window[split:]:
            label = option_cells.get(cell)
            if label and label > (current or ""):
                current = label
                options.setdefault(current, []).append(cell)
            elif current:
                options[current].append(cell)      # a wrapped option continues its own label
        questions[number] = {"stem": window[:split], "options": options}

    for number in range(1, len(starts) + 1):
        if number not in questions:
            missing.append(f"question-{number}")
        elif sorted(questions[number]["options"]) != list(canon.LABELS[:4]):
            missing.append(f"question-{number}-labels")
    claimed = {cell for question in questions.values()
               for cell in question["stem"] + [c for cells in question["options"].values() for c in cells]}
    chrome = [cell for cell in order if cell not in claimed]
    return {"questions": questions, "chrome": chrome, "missing": sorted(set(missing)),
            "complete": not missing, "count": len(questions)}


def compare_skeleton(reading, paper, table):
    """Where a model reading and the paper's own skeleton disagree. Returns a report.

    This is the second check, and it asks a different question from `verify`. `verify` asks
    whether the reading is a repartition at all - nothing invented, nothing lost. This asks
    whether it is *this paper's* repartition, which is knowable because the paper printed its
    question numbers and its option markers and both are readable.

    Both are needed. A reading can pass `verify` and still be wrong: on `1152_醫事檢驗師_
    生物化學與臨床生化學` the model's reading had no invented character and no lost cell, and
    had shifted every question from 55 onward by one cell. What caught that here was the option
    markers, which the paper printed and the model had not been asked to notice.

    A disagreement is not a failure of the model and is not "repaired" by preferring one side.
    It is a finding: either the skeleton misread the paper, or the model did, and a person decides
    which. What is reported is therefore the disagreement itself, per question, with the cells.
    """
    rows = []
    for number in sorted(set(reading) | set(paper)):
        mine, theirs = reading.get(number), paper.get(number)
        if mine is None:
            rows.append((number, "model-missing", [], []))
            continue
        if theirs is None:
            rows.append((number, "paper-missing", [], []))
            continue
        if list(mine["stem"]) != list(theirs["stem"]):
            rows.append((number, "stem", mine["stem"], theirs["stem"]))
        if {label: list(cells) for label, cells in mine["options"].items()} != \
                {label: list(cells) for label, cells in theirs["options"].items()}:
            rows.append((number, "options", mine["options"], theirs["options"]))
    return {"disagreements": rows, "agree": len(reading) - len({row[0] for row in rows})}


def _strip_number(text, number):
    """The stem without the printed question number, which is a position rather than content.

    `segment_mixed` does the same thing, and this function claims to produce its shape: it reads
    `1.下列那一個電泳技術…` and returns `下列那一個電泳技術…`. The number is removed only when it is
    *this* question's number and stands at the head, so a stem that opens with a number as content
    (`53.33歲油漆工`) is untouched. The reading itself keeps the number, and must - the model may
    not drop a character, and `verify` refuses a reading that does.
    """
    stripped = (text or "").strip()
    match = _QUESTION_LEAD.match(stripped)
    if match and int(match.group(1)) == number:
        return stripped[match.end():].strip()
    return stripped


def to_items(reading, table):
    """The reading as items in the shape `repair.segment_mixed` produces.

    The same shape on purpose: everything downstream - the answer sheet, the corrections sheet,
    the four-option gate, the package - reads items and does not care how they were produced,
    so a model reading and a geometric reading are interchangeable and can be compared.
    """
    text_of = {line_id: text for line_id, text, _ in table}
    perms = {str(k): v for k, v in (reading.get("perm") or {}).items()}
    items = []
    for question in reading.get("questions") or []:
        number = question.get("number")
        if not isinstance(number, int):
            try:
                number = int(str(number).strip())
            except (TypeError, ValueError):
                continue
        options = {}
        for label, entry in (question.get("options") or {}).items():
            label = str(label).strip().upper()[:1]
            body = _compose(entry, text_of, perms)
            # The printed marker is a position and is dropped here, because this function claims
            # to produce the shape `repair.segment_mixed` produces and that shape has no marker in
            # it: `segment_mixed` reads `A.瓊脂糖凝膠電泳` and returns `瓊脂糖凝膠電泳` under the key
            # `A`. Keeping it was a real defect, not a cosmetic one - the marker would have been
            # packaged as part of the option body, and the option text would have read `A.瓊脂…`
            # in the question bank. The reading keeps it, and must: the model is told never to
            # drop a character, and `verify` would refuse a reading that did.
            if label and body:
                options[label] = repair.option_body(body)
        flags = []
        figure = (question.get("figure") or "").strip()
        if figure:
            flags.append(f"figure-{figure}")
        items.append({
            "number": number,
            "stem": _strip_number(_compose(question.get("stem"), text_of, perms), number),
            "options": options,
            "option_order": [label for label in canon.LABELS if label in options],
            "flags": flags,
            "source": "model-reflow",
            "source_lines": sorted({line_id for line_id, _ in _tokens(question.get("stem"))
                                    if line_id} |
                                   {line_id for label in options
                                    for line_id, _ in _tokens((question.get("options") or {}).get(label))}),
            "figure": figure or None,
        })
    items.sort(key=lambda item: item["number"])
    return items


# --- the whole paper ------------------------------------------------------------------------
def _sheet_count(pdf_path, count=None):
    """The question count the answer sheet itself prints, when the sheet is beside the paper.

    The corpus keeps a paper and its sheets as siblings (`..._ANS.pdf` beside `.pdf`), so the
    sheet is found from the paper. The sheet is the authority for the count and not the question
    paper's own numbering, because counting anchors in the question paper is an inference from
    the same page that produced the items: using it as the standard cannot catch an item invented
    from a figure's axis label.
    """
    if count:
        return count
    import glob
    base = pdf_path[:-4] if pdf_path.lower().endswith(".pdf") else pdf_path
    try:
        from . import answer_sheets, canon
        for role, suffix in (("answer", "_ANS.pdf"), ("corrected", "_MOD.pdf")):
            for sibling in sorted(glob.glob(base + suffix)):
                table = canon.parse_answer_table(answer_sheets.read_table_text(sibling, role=role))
                if not table:
                    continue
                numbers = sorted(int(number) for number in table)
                if numbers and numbers[0] == 1:
                    return numbers[-1]
    except Exception:
        return None
    return None


def read_paper(pdf_path, *, subject="", year="", count=None, rows=None, emit_raw=False,
               think=True):
    """Read one paper by model reflow. Returns a dict with items, the check, and the raw answer.

    The paper is read once, deterministically, to produce the numbered lines; those lines are
    what the model is given and what its answer is checked against. The model is never asked to
    read the PDF, because reading the PDF is the measured part and the model's job starts where
    measurement stops.
    """
    rows = rows if rows is not None else extract.extract_cells_a(pdf_path)
    kept, _ = repair.mask_chrome(rows)
    table, pages, alphabet = cells_with_pages(kept)
    body_size = _body_size(kept)
    count = _sheet_count(pdf_path, count)
    budget = completion_budget(count, len(table), think=think)
    messages = build_messages(table, subject=subject, year=year, count=count,
                              body_size=body_size, pages=pages, alphabet=alphabet)
    parsed, raw, usage, seconds = ask(messages, max_tokens=budget, think=think)
    result = {"pdf": pdf_path, "subject": subject, "year": year, "count": count,
              "lines": len(table), "alphabet": [f"{code:04x}" for code in alphabet],
              "seconds": round(seconds, 1), "usage": usage, "think": think,
              "reasoning_tokens": reasoning_tokens(usage),
              "body_size": body_size, "parsed": parsed is not None}
    if parsed is None:
        result.update({"admissible": False, "report": {"error": "unparsed"},
                       "items": [], "raw": raw if emit_raw else raw[-2000:]})
        return result
    ok, report = verify(parsed, table)
    # The items are composed whether or not the reading was admissible, and `admissible` is what
    # says whether they may be used. Suppressing them was a mistake worth recording: it made a
    # refused reading indistinguishable from an unparsed one, so a report could show `0 items`
    # while the model had in fact read all 80 questions and only mislabelled two cells. The two
    # are different findings - one is a model that did not answer, the other is a model that
    # answered and was refused - and a comparison against the skeleton needs the second kind.
    items = to_items(parsed, table)
    result.update({"admissible": ok, "report": report, "items": items,
                   "notes": parsed.get("notes"), "raw": raw if emit_raw else None})
    return result


# --- which reading to pay for: what the measurements do and do not support ----------------
# The first version of this section held a rule: count the questions whose stem wraps, and read
# without thinking when few do. It was a fitted rule and it was wrong, and the way it was wrong is
# worth keeping, because it is the same mistake this project has made before - a rule that asks
# about the *engine's* behaviour rather than about the *paper's*.
#
# The rule predicted that `1152_醫事檢驗師_臨床生理學與病理學` was safe to read without thinking,
# because only 7 of its 80 questions have a wrapping stem - the lowest ratio of the six papers
# measured. It is in fact the worst case in the corpus: thinking off read 13 of 80 questions
# correctly, thinking on read 80.
#
# The measurements, six papers, same prompt:
#
#   paper                                     stem wraps   thinking off   thinking on
#   1152_臨床生理學與病理學                        7/80          13/80         80/80
#   1151_臨床血液學與血庫學                       11/80          24/80          -
#   1151_臨床血清免疫學與臨床病毒學                 10/80          14/80          -
#   1151_生物化學與臨床生化學                     13/80          80/80          -
#   1152_生物化學與臨床生化學                     13/80          75/80         54/80
#   1152_臨床血清免疫學與臨床病毒學                 11/80          69/80         79/80
#
# Two things follow, and neither is a rule. First, thinking off is not uniformly worse
# (1152_生物化學 read 75 with it off and 54 with it on) and thinking on is not uniformly better.
# Second, no property of the paper separates the 13/80 case from the 80/80 case in this table:
# the two papers have the same wrap ratio to within one question. With six points and no
# separating feature, any threshold would be fitted to these six and would be a claim about the
# engine dressed up as a claim about the paper.
#
# So the decision is made on the *outcome* rather than predicted, and it costs nothing to do so.
# The skeleton is free, exact, and complete on 95.7% of papers; the model reading is checked
# against it (`compare_skeleton`). A reading that agrees with the skeleton on every question needs
# no further thought; a reading that disagrees is a reading to re-ask with thinking on, and the
# cells that disagree are what the second ask is about. That is a decision procedure with a
# measurable trigger, not a threshold.
def disagreements(reading_items, paper_items):
    """The questions where a reading and the paper's skeleton do not say the same thing.

    Returns the numbers, so the second ask can be about those questions rather than about the
    paper again. Both sides being wrong in the same way is possible and is not detectable here;
    what is detectable is where they differ, and that is what a second reading is for.
    """
    mine = {item["number"]: item for item in reading_items}
    theirs = {item["number"]: item for item in paper_items}
    out = []
    for number in sorted(set(mine) | set(theirs)):
        left, right = mine.get(number), theirs.get(number)
        if left is None or right is None:
            out.append(number)
            continue
        if (_flat(left.get("stem")) != _flat(right.get("stem"))
                or _flat_options(left) != _flat_options(right)):
            out.append(number)
    return out


def _flat(text):
    import re
    return re.sub(r"[\s\u3000\u00a0]+", "", (text or "").strip())


def _flat_options(item):
    options = item.get("options") or {}
    if isinstance(options, dict):
        return {label: _flat(body) for label, body in options.items()}
    return {str((entry or {}).get("label") or ""): _flat((entry or {}).get("text"))
            for entry in options if isinstance(entry, dict)}
