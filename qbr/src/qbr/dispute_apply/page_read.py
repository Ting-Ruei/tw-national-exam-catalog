"""The page-reading half: the measurements, the class clauses and the two paths in.

Extracted verbatim from `scripts/apply_dispute_repairs.py` so one file is no longer 2,321 lines.
`apply_dispute_repairs` re-exports every name here; that indirection is deliberate and is why the
test files that load the script by path keep working unchanged. No behaviour was changed.
"""

from __future__ import annotations

import difflib
import re

from qbr.ai_findings import UNREADABLE_MARK

from qbr.dispute_apply.text import (
    DETERMINISTIC_FORMS,
    MARKUP_RE,
    _cjk_ideograph,
    _compatibility_fold,
    _sub_sup_char,
    _unique,
    _without_whitespace,
    aligned_changes,
    anchored_page_changes,
    deterministic_form,
    field_text,
    flagged_positions,
)


#: The two measurements a whole-field page read must pass. **Both numbers are measured, not chosen.**
#:
#: Population: the local mirror `data/review-queues/live`, 2026-09-24 - 204 latest findings carrying
#: `changes`, 509 field differences. 55 questions with such a reading carry a standing human rejection
#: (a `block`/`needs_review` with no later `accept`; 323 questions carry a `block`, 286 still stand, 37
#: were accepted afterwards), and 85 of their field differences are the population below; 66 of those
#: have a text on both sides to measure, 19 have an empty page or an empty field.
#:
#:     alignment ratio (common prefix + common suffix) / max(len), on non-whitespace characters:
#:
#:         0.05 + length [0.625, 1.6]  -> 29 of the 66 fields applied
#:         0.10 + length [0.625, 1.6]  -> 29    <- the line
#:         0.15 + length [0.625, 1.6]  -> 27
#:
#:     The line is at 0.10 and not 0.05 because nothing lies between them in this population: both
#:     admit the same 29 fields, since a genuine reading that shares less than a tenth of its ends is
#:     not observed. It is not raised to 0.15 because the fields it would lose are real repairs of
#:     Radicals/Kangxi folds (0.12-0.15).
#:
#:     length ratio len(page) / len(stored), window [0.625, 1.6] (i.e. +-60%), alignment >= 0.10:
#:
#:         1.45 -> 29 of the 66 fields applied
#:         1.6  -> 29    <- the window
#:         1.8  -> 29
#:         2.0  -> 30
#:
#:     The window is 1.6 rather than the loosest of these because of what it has to refuse: the four
#:     known hallucination readings measure **0.417** (`115090 q053`, stem truncated), **2.020**
#:     (`113020 q050`, a figure description), **2.108** (`105020 q045`, a whole compartment diagram
#:     with invented arrow labels) and **2.714** (`113020 q076`, the acceptance-criteria table
#:     appended). All four are refused by the length line, and the narrowest of them (2.020) clears 1.6
#:     by **26%**; at 1.8 that margin would be 12% and at 2.0 it would be 1% - a line drawn through the
#:     noise, refusing a hallucination by a rounding error. Loosening from 1.6 to 2.0 buys exactly one
#:     field in this population, which is the other half of the same argument.
#:
#:     **Alignment cannot do this job alone.** The same four readings measure 0.153, 0.309, 0.368 and
#:     0.414 on the alignment scale - all above any usable line, because a genuine reading of a
#:     reflowed formula shares only its two ends as well. Alignment is the cheap sanity check (is this
#:     even the same document at both ends); the length is what separates a reading from a rewrite.
#:
#:     What the two lines refuse, on the 66 measurable fields (37 refused, 29 applied): 22 fields that
#:     would strip HTML markup the platform renders (`紙本判讀會拆掉標記` - a clause of its own, added
#:     after a dress rehearsal showed `104090 q052`'s replacement had destroyed `<sub>`/`<sup>`),
#:     12 fields of the wrong length, 3 that share almost nothing with the stored text.
PAGE_READ_ALIGN_MIN = 0.10
PAGE_READ_LENGTH_FACTOR = 1.6

#: 同一個字對要出現在幾題上，才算「判讀在這裡系統性看錯」。**量出來的，不是選的。**
#:
#: 2026-09-24 18:06 那一輪寫進 66 筆整欄替換（90 個欄位），其中 癇→癲 出現在 **4** 題上
#: （`115090:305:0401 q037` 的題幹、`113090:305:11 q053`、`108100:305:11 q035` 的選項 A、
#: `108030:305:11 q061`），而同一輪裡其他字對最多只出現 1 題——所以線畫在 3：它把 4 題那一對
#: 擋掉，而 1-2 題的字對（`値`→`值`、`症`→`病`、`當`→`宜`、`及`→`口`、`中`→`可`）照舊可以寫。
#: 2 會開始誤傷單題的真修復，4 會放過一半的 癇→癲，兩個都更差。
SYSTEMATIC_PAIR_MIN = 3


def systematic_pairs(stored: str, page: str) -> list[tuple[str, str]]:
    """一筆整欄替換裡「表意文字被讀成另一個表意文字」的字對。

    只認兩邊都是表意文字、而且 Unicode 不認為它們是同一個字的配對（`_compatibility_fold` 擋掉
    相容還原那一半：`値`→`值` 是不同字，所以算；`⽣`→`生` 是同一個字，所以不算）。這是普查的單位：
    同一個字對在同一輪裡出現在越多題上，就越不像單題的誤讀，越像判讀在這裡系統性看錯。
    """
    return [(before, after) for before, after in aligned_changes(stored, page)
            if _cjk_ideograph(before) and _cjk_ideograph(after)
            and not _compatibility_fold(before, after)]


def unicode_sub_sup_introduced(stored: str, page: str) -> list[str]:
    """紙本判讀**新寫進來**的 Unicode 下標／上標字元，照出現順序。

    「新寫進來」是相對原文量的：原文在對應位置本來就是一個 Unicode 上下標字元，判讀留著它，那不是
    引進（這條只管方向，不管那個字元本身好不好）；原文是普通字而判讀換成上下標字元，那就是把排版
    寫成了字元——平台用 `<sub>`／`<sup>` 排版，而這些字元在頁面上會掉到別的字型（主人報的「字型異常」）。
    """
    out = []
    for before, after in aligned_changes(stored, page):
        if _sub_sup_char(after) and not _sub_sup_char(before):
            out.append(after)
    return out


#: 原文裡的 HTML 標記（`<sub>`、`<sup>`、`<br>`…）。**平台用標記排版**，紙本判讀則把同一段數學
#: 寫成 Unicode 下標（`C<sub>p</sub>` → `Cₚ`）——所以整欄替換會把標記拆掉，把平台讀得懂的形式
#: 換成模型選的形式。實測（唯讀，2026-09-24）：站上的日誌裡 225 筆帶著 correction 的事件 **0 筆**
#: 拆掉標記，本地鏡像的 247 筆也是 **0 筆**；也就是說這道門只在新的整欄路上有洞，字元級那兩條
#: 路仍然可以修這些欄位（它們只換量到的字元，標記原封不動）。**擋的是判讀真的拆掉標記**
#: （`markup_dropped_complaint`：原文每一種標記的數量都要在判讀裡找得到），不是「原文有沒有標記」。
#: 定義在 `text.MARKUP_RE`（那一份是唯一的一份，這裡只是名字）。
MARKUP = MARKUP_RE

#: 第二次判讀的結論（`orchestration.verdict`，`orchestrator.py:46-52`）：只有 `TRUST`（差異是機械性
#: 符號還原、不動語意）才足以支撐整欄改寫。`CARE`（可能改變答案、或判讀自相矛盾）、`DOUBT`（本地
#: 判讀明顯有錯或編造）與「根本沒有第二次判讀」都是「要人看」，而那些題目本來就躺在人的佇列裡。
TRUST_VERDICT = "TRUST"


#: 這條柵欄量到的代價（站上，唯讀，2026-09-24）：站得住退件、兩個量測與標記／`▢` 都過的整欄候選
#: 146 筆裡，`TRUST` 81、`CARE` 44、沒有第二次判讀 17、`DOUBT` 4——它砍掉整欄路的一半。同一份普查
#: 在當晚稍早（佇列還小）讀到 117 = 59／37／17／4；這一圈還在寫新的判讀，所以數字只會往上跑。
#: 它只管整欄那一條路，字元級那條（偵測器標記過的位置）不受影響。

def _verdict_complaint(verdict) -> str | None:
    """Why the second engine's verdict does not support a whole-field rewrite, or `None` for `TRUST`.

    The three refusals are named separately because they mean different things to the person reading
    the report: `CARE` is a reading that may have changed the answer, `DOUBT` is one the second engine
    thinks is wrong or invented, and an absent verdict is a question no second read ever happened for.
    """
    if verdict == TRUST_VERDICT:
        return None
    if verdict == "CARE":
        return "第二次判讀的結論是 CARE（差異可能改變答案或判讀自相矛盾）：整欄改寫要等人看"
    if verdict == "DOUBT":
        return "第二次判讀的結論是 DOUBT（本地判讀明顯有錯或編造）：整欄改寫要等人看"
    if verdict:
        return "第二次判讀的結論是 %s：整欄改寫要等人看" % verdict
    return "沒有第二次判讀（finding 沒有 orchestration.verdict）：整欄改寫要等人看"


def second_read_verdict(record: dict):
    """`orchestration.verdict` off a finding record, or `None` when there was no second read.

    Defensive about the shape rather than about the value: `orchestration` is absent on every finding
    written before the orchestrator existed (measured on the mirror: 291 of 301 dispute findings), and
    absent means "no second read", which is exactly what the caller must not treat as `TRUST`.
    """
    orchestration = (record or {}).get("orchestration")
    if not isinstance(orchestration, dict):
        return None
    verdict = orchestration.get("verdict")
    return verdict if isinstance(verdict, str) and verdict else None


def _page_read_shape(stored: str, page: str):
    """`(alignment ratio, length factor)` of a page reading against the stored field.

    Both measurements are taken on the **non-whitespace** characters, for the same reason
    `_without_whitespace` exists: the page read is a second *reading* of the same line, and a
    transcription that spaces out `0.8` or keeps two spaces between numbered items has not changed
    the content. A measurement that counted those spaces would refuse a correct reading of an
    unchanged line.

    * alignment = (common prefix + common suffix) / max(len(stored), len(page)) - "how much of the
      text is at the same place at both ends". It is low for a reflowed formula and low for a
      different document, which is exactly why it cannot be the only line - see the constants.
    * length factor = len(page) / len(stored) - a reading that appends a table or drops a field
      changes this, and neither a reflow nor a fold does.

    Markup is not content: `<sub>`／`<sup>` is how the platform typesets a subscript, and in a **short**
    field the tags alone overpower both numbers. Measured 2026-09-24: `藥理作用標的是GABAA 受體` →
    `藥理作用標的是 GABA<sub>A</sub> 受體` has a raw length factor of 1.79 (over `PAGE_READ_LENGTH_FACTOR`
    = 1.60, so the fence refused a *wanted* reading) and an alignment of 0.52; with the tags removed the
    same pair measures `(1.00, 1.00)`. So both numbers are measured on the characters the markup wraps.
    """
    stored_chars = "".join(MARKUP.sub("", stored).split())
    page_chars = "".join(MARKUP.sub("", page).split())
    if not stored_chars or not page_chars:
        return None, None
    limit = min(len(stored_chars), len(page_chars))
    prefix = 0
    while prefix < limit and stored_chars[prefix] == page_chars[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and stored_chars[-1 - suffix] == page_chars[-1 - suffix]:
        suffix += 1
    return (prefix + suffix) / max(len(stored_chars), len(page_chars)), \
        len(page_chars) / len(stored_chars)


def _flagged_survivors(stored: str, page: str, flagged: dict) -> list:
    """The flagged positions a page reading leaves exactly as they were.

    A whole-field replacement says what the whole field is, so it *subsumes* the character-level
    repairs of the same field - and it must not subsume one it does not perform, or the field would be
    written from the page with the character the detector already measured as wrong still in it.
    Positions come from `flagged_positions` (raw indices into `stored`); "unchanged" is measured on
    the folded comparison, and a flagged index that no longer holds its own character is returned too
    - the reading cannot be checked against a position that has moved.
    """
    if not flagged:
        return []
    stored_chars, origins = _without_whitespace(stored)
    page_chars, _ = _without_whitespace(page)
    matcher = difflib.SequenceMatcher(None, "".join(stored_chars), "".join(page_chars),
                                      autojunk=False)
    unchanged = set()
    for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
        if tag == "equal":
            unchanged.update(origins[i1:i2])
    out = []
    for position, sub in sorted(flagged.items()):
        char = str(sub.get("char") or "")
        if not char or stored[position:position + len(char)] != char or position in unchanged:
            out.append((position, char))
    return out


def _flagged_conflict(field: str, stored: str, page: str, flagged: dict):
    """A one-line reason when the page read and a dispute disagree about a flagged position.

    Two shapes, and both are the same rule `merge_substitutions` applies on the character-level path:
    two measurements that contradict each other about one position are not decided by the tool.

    * the page read is a pure anchored repair (`anchored_page_changes` returns edits) and reads the
      flagged character as something other than the dispute's `means`;
    * the page read is a bigger change and leaves the flagged character untouched.

    Measured 2026-09-24: 0 of the mirror's 509 field readings hit either shape - the four questions
    that carry both a dispute and a passing whole-field replacement (`108030 q013`/`q022`/`q026`/
    `q080`) all read the flagged character the way the dispute says. So this clause costs nothing
    today and keeps the one case that matters from being written the wrong way round: a model reading
    must never overrule the detector's own table about the character the paper prints.
    """
    if not flagged:
        return None
    edits = anchored_page_changes(stored, page, set(flagged))
    if edits is not None:
        for position, sub in sorted(flagged.items()):
            means = sub.get("means")
            char = str(sub.get("char") or "")
            if not means or not char:
                continue
            for edit in edits:
                span = len(edit["before"])
                if not edit["position"] <= position < edit["position"] + span:
                    continue
                offset = position - edit["position"]
                read = edit["after"][offset:offset + len(str(means))]
                if read and read != means:
                    return "%s[%d]：紙本判讀讀成 %r，dispute 說是 %r（兩個量測不一致）" % (
                        field, position, read, means)
        return None
    survivors = _flagged_survivors(stored, page, flagged)
    if survivors:
        return "紙本判讀沒有改到偵測器標記的位置（%s）" % \
            "、".join("%s[%d] %r" % (field, p, c) for p, c in survivors)
    return None


def page_read_shape_complaint(stored: str, page: str) -> str | None:
    """The measurement fence on one field, or `None` when the two numbers pass.

    This is the half of the whole-field path that says whether the model's text is even a *reading*
    of this field: a non-empty text on both sides, an alignment ratio at least `PAGE_READ_ALIGN_MIN`,
    and a length within `PAGE_READ_LENGTH_FACTOR` either way. It is split out from the class clauses
    below because the systematic-pair census has to run over exactly this population - the fields
    whose two numbers pass - including the ones a class clause then refuses: a reading that misreads
    one character systematically has to be visible as such even when it is refused for another reason.
    """
    if not page:
        return "紙本判讀沒有讀到這一欄（空字串）"
    if not stored:
        return "原文這一欄本來是空的，沒有東西可以量對齊（讀到 %r）" % page[:20]
    alignment, factor = _page_read_shape(stored, page)
    if alignment is None:
        return "這一欄與原文無法比對（量不出對齊）"
    if alignment < PAGE_READ_ALIGN_MIN:
        return "對齊率 %.2f < %.2f（讀起來像另一份文件）" % (alignment, PAGE_READ_ALIGN_MIN)
    if not 1.0 / PAGE_READ_LENGTH_FACTOR <= factor <= PAGE_READ_LENGTH_FACTOR:
        return "長度比 %.2f 不在 [%.2f, %.2f]（讀進來或刪掉了整段內容）" % (
            factor, 1.0 / PAGE_READ_LENGTH_FACTOR, PAGE_READ_LENGTH_FACTOR)
    return None


def markup_introduced(stored: str, page: str) -> bool:
    """這份判讀是不是把字包進了 `<sub>`／`<sup>` 標記。

    **這個方向是想要的**：平台的排版法就是標記（站上實測 2026-09-24：6540 筆已經這樣寫，只有 55
    筆帶著 Unicode 上下標字元），而原文是平的只因為抽取器把排版丢了（`KM`、`Vmax`、`GABAA`
    在原文本來就是下標）。所以標記本身不是缺陷——缺陷是標記**裡面**的字被動過，那一條在
    `markup_fidelity_complaints`。原文自己帶著標記的欄位由 `markup_dropped_complaint` 量（那條問的
    是判讀有沒有把原文的標記帶回來），這一條則量「原文沒有而判讀多出來的」；兩者可以同時成立
    （原文有 `<sub>`，判讀留著它又另外包了一層 `<i>`），那時多出來的那一層照樣要包著原本那幾個字。
    """
    return len(MARKUP.findall(page)) > len(MARKUP.findall(stored))


def markup_fidelity_complaints(stored: str, page: str) -> list[str]:
    """把標記拿掉之後，判讀還動到的字元。

    標記可以加，裡面的字必須**就是原文那幾個字、同一個順序**（`KM`→`K<sub>M</sub>`、
    `GABAA`→`GABA<sub>A</sub>`、`e-0.35t`→`e<sup>-0.35t</sup>` 都成立）。動到任何一個字就不是
    排版問題了，是判讀在改字——實測 2026-09-24 那一輪：`GABAA`→`GABAₐ` 把大寫 A 換成小寫、
    `KM`→`Kₘ` 把大寫 M 換成小寫、`AUC0-∞`→`AUC₀.∞` 把連字號換成句點、`I_KR`→`Iₖᵣ` 把兩個字母
    都換成小寫。空格不算（`_without_whitespace` 的理由與兩個量測相同：第二次判讀重新排版同一行的
    空白不是改內容），字元本身算。
    """
    stored_chars = "".join(MARKUP.sub("", str(stored)).split())
    flat_chars = "".join(MARKUP.sub("", str(page)).split())
    named = []
    for before, after in aligned_changes(stored_chars, flat_chars):
        # 可檢查的機械還原不算「標記裡的字被動過」：同一族的排版變體（`＋`→`+`、`．`→`·`、`－`→`−`）、
        # Unicode 的相容分解（`⼒`→`力`、`若`→`若`）與私用區還原都是判準就在字元自己身上的那一類，
        # 別的地方本來就放行它們（`text.deterministic_form`）。站上實測 2026-09-25：被這一條擋下的
        # 283 欄裡，**63 欄（46 題）** 的每一對差異都是這一類——它們被擋只因為同一欄裡多引進了標記。
        # 真正改字的照舊擋：`o`→`0`、`s`→`S`、`為`→`爲`、多出來的 `m` 都不是上面任何一類。
        if before and after and deterministic_form(before, after) in DETERMINISTIC_FORMS:
            continue
        if not before:
            named.append("多出 %r" % after)
        elif not after:
            named.append("少了 %r" % before)
        else:
            named.append("%r→%r" % (before, after))
    if not named:
        return []
    return ["判讀動到了上下標以外的字元（%s）" % "、".join(named)]


def unicode_sub_sup_complaint(stored: str, page: str) -> str | None:
    """判讀把上下標寫成 Unicode 上下標字元時的拒絕理由，或 `None`。

    平台的排版法是 `<sub>`／`<sup>`；Unicode 的上下標字元在頁面上會掉到別的字型（主人的原話是
    「字型異常」），而且沒有下標的大寫 A 或 M——所以 `GABAA`→`GABAₐ`、`KM`→`Kₘ` 連原文的字元都
    不是了。站上實測 2026-09-24：6540 筆已經用標記，只有 55 筆帶著這些字元。這條只管整欄那一條路，
    字元級那條（偵測器標記過的位置）不受影響。
    """
    introduced = unicode_sub_sup_introduced(stored, page)
    if not introduced:
        return None
    return "判讀把上下標寫成 Unicode 上下標字元（%s）：平台用 <sub>/<sup> 排版，這些字元的字型不對" \
        % _unique(introduced)


def _markup_tally(text: str) -> dict[str, int]:
    """這份文字裡每一種標記各出現幾次（`{"<sub>": 2, "</sub>": 2}`）。"""
    counted: dict[str, int] = {}
    for tag in MARKUP.findall(str(text or "")):
        counted[tag] = counted.get(tag, 0) + 1
    return counted


def _tally_text(counted: dict[str, int]) -> str:
    if not counted:
        return "沒有標記"
    return "、".join("%s×%d" % (tag, count)
                     for tag, count in sorted(counted.items(), key=lambda item: (-item[1], item[0])))


def markup_dropped_complaint(stored: str, page: str) -> str | None:
    """判讀拆掉原文**已經在用的**標記時的理由，或 `None`。

    **這一條量的是判讀有沒有把原文的標記帶回來，不是「原文有沒有標記」。** 舊的寫法是後者——`原文
    含 <sub>` 就整欄拒絕——而它的代價量得出來（站上 2026-09-25，每題最新一筆判讀）：判讀與原文標記
    逐字相同的有 **141 欄**，其中 **135 欄**的可見字元真的不同，也就是真的有東西要修
    （`KP`→`K<sub>sp</sub>`、`KM`→`K<sub>M</sub>`），卻只因為原文含標記被丟進「AI無法判斷」——
    而丟它的理由（「紙本判讀會拆掉標記」）在那些判讀上根本不是事實。

    判準是**標記的組成**（每一種標記各幾個）：掉了就是掉了（`104090 q052` 的判讀把 10 個標記讀成
    0 個，仍然拒絕）；判讀另外多包一層不算掉（多出來的標記由 `markup_fidelity_complaints` 要求它
    包著原本那幾個字）。**位置不列入判準**，這是量出來的取捨：同樣那 141 欄裡，標記逐字相同的有 141
    欄、連前後字元都完全相同只有 **58** 欄——因為 83 欄的標記裡面或旁邊的字真的變了（那正是要修的東西）。
    用前後字元當判準會把 83 筆真修復擋掉，換不到任何保護，所以這一條只認組成。
    """
    stored_marks, page_marks = _markup_tally(stored), _markup_tally(page)
    if not stored_marks:
        return None
    lost = {tag: count for tag, count in stored_marks.items() if page_marks.get(tag, 0) < count}
    if not lost:
        return None
    return "紙本判讀會拆掉標記（原文 %s，判讀 %s；少了 %s）" % (
        _tally_text(stored_marks), _tally_text(page_marks), _tally_text(lost))


#: 判讀規範（`reread.SYSTEM` 第 4 條）叫模型在讀不出某個字時寫 `▢`。把「我讀不出來」寫進文字比現在
#: 的文字更差——現在的文字至少是抽取器真的讀出來的。站上實測（唯讀，2026-09-24）：站得住退件的整欄
#: 候選 370 筆裡有 **10** 筆把 `▢` 讀進來（`115090:305:0402 q075` 選項 C、`113020:308:11 q021`
#: 選項 A 是其中兩筆），其中 3 筆通過其他柵欄，其餘的題目 verdict 不是 `TRUST`，會被那一條先擋掉。
def whole_field_refusals(question: dict, field: str, stored: str, page: str,
                         pairs=None) -> list[tuple[str, str]]:
    """`(trigger, reason)` for every clause that refuses this reading of this whole field.

    The caller runs this over the fields whose two measurements pass (`page_read_shape_complaint`),
    so every refusal here is a *content* refusal. `trigger` names the measured class, which is what
    decides whether the machine's earlier write of this field is now withdrawn:

        systematic-pair   the same character pair changed on SYSTEMATIC_PAIR_MIN questions in this run
        unicode-sub-sup   the reading writes a Unicode sub/superscript character the stored text had
                          as a plain character
        markup-fidelity   the reading wraps characters in `<sub>`/`<sup>` but changes or reorders them
        ""                a refusal that says nothing about the machine's earlier write (the reading
                          drops markup the stored text already carries, brings in the unreadable
                          mark, or contradicts a detector's own measured position) - those never
                          withdraw anything

    `pairs` is the run's systematic census (`{pair: question count}`); without it the pair rule is
    neutralised, which is what the negative-control test does.
    """
    out = []
    for pair, count in sorted((pairs or {}).items(), key=lambda item: (-item[1], item[0])):
        if pair in systematic_pairs(stored, page):
            out.append(("systematic-pair",
                        "同一個字對 %s→%s 在這一輪出現 %d 題：判讀在這裡系統性看錯"
                        % (pair[0], pair[1], count)))
    markup_why = markup_dropped_complaint(stored, page)
    if markup_why:
        out.append(("", markup_why))
    unicode_why = unicode_sub_sup_complaint(stored, page)
    if unicode_why:
        out.append(("unicode-sub-sup", unicode_why))
    if markup_introduced(stored, page):
        for reason in markup_fidelity_complaints(stored, page):
            out.append(("markup-fidelity", reason))
    if UNREADABLE_MARK in page and UNREADABLE_MARK not in stored:
        out.append(("", "紙本判讀說這一格讀不出來（%s），比現在的文字更差" % UNREADABLE_MARK))
    conflict = _flagged_conflict(field, stored, page, flagged_positions(question, field))
    if conflict:
        out.append(("", conflict))
    return out


def page_read_fields(question: dict, changes: list[dict]) -> tuple[list[dict], list[dict]]:
    """`(fields, shape refusals)` - the reading's fields that pass the two measurements, and the ones
    that do not.

    The population the census, the class clauses and the atomicity rule all work on, gathered in one
    place so the four agree by construction: a field whose reading is empty, or of the wrong length,
    or that shares almost nothing with the stored text, is not a *reading* of that field and never
    enters. `stored` is the finding's own text when it carries one (`change["stored"]`), because that
    is the text the reading was taken against. Each refusal is a record (`{field, why}`) so the caller
    can put it through the same atomicity rule as the class clauses.
    """
    fields, refusals = [], []
    for change in changes or []:
        field = change.get("field")
        if not field:
            continue
        stored = change.get("stored")
        if stored is None:
            stored = field_text(question, field)
        stored = str(stored)
        page = change.get("page")
        if page is None:
            page = change.get("to")
        page = str(page or "")
        if stored == page:
            continue
        why = page_read_shape_complaint(stored, page)
        if why:
            refusals.append({"field": field, "why": why})
            continue
        fields.append({"field": field, "stored": stored, "page": page})
    return fields, refusals


def _whole_field_decision(question: dict, changes: list[dict], pairs=None):
    """`(replacements, records, order)` for one reading - the one place the whole-field rules are run.

    Every field of the reading is evaluated first and nothing is decided field by field, because the
    subject of the decision is the **question** (see `page_read_replacements`): one refusal drops the
    whole set. `records` carries every refusal with its measured class (`triggers`) and whether it was
    a refusal of its own field or the atomicity drop of a sibling, which is what the withdrawal and the
    report need; `order` is the reading's own field order, so "the failing field" is stable.
    """
    fields, shape = page_read_fields(question, changes or [])
    failing: dict[str, list[tuple[str, str]]] = {}
    order: list[str] = []
    for item in shape:
        failing[item["field"]] = [("", item["why"])]
        order.append(item["field"])
    for candidate in fields:
        order.append(candidate["field"])
        reasons = whole_field_refusals(question, candidate["field"], candidate["stored"],
                                       candidate["page"], pairs)
        if reasons:
            failing[candidate["field"]] = reasons
    if not failing:
        return [{"field": candidate["field"], "replace": True, "before": candidate["stored"],
                 "after": candidate["page"], "rule": "page-read-field"} for candidate in fields], [], order
    first = next(field for field in order if field in failing)
    atomic_why = failing[first][0][1]
    atomic = "這一題的 %s 沒過（%s）：整題一起不動，不然同一題會出現兩種寫法" % (first, atomic_why)
    inherited = _first_trigger(failing[first])
    records = []
    for field in order:
        if field in failing:
            for trigger, reason in failing[field]:
                _record(records, field, [trigger], reason, atomicity=False)
            continue
        _record(records, field, [inherited], atomic, atomicity=True)
    return [], records, order


def page_read_replacements(question: dict, changes: list[dict], *, error=None, refused=None,
                           verdict=None, pairs=None, refused_fields=None) -> list[dict]:
    """The whole-field replacements a *confirmed page reading* justifies, field by field.

    This is path 3 of the module docstring: it exists because a detector can only flag the characters
    it knows about, so path 2 (the anchored one) is silent for most real readings. The anchor is moved
    from the detector to the measurements and to the person's own rejection - the caller has already
    established the rejection, because this function is only reached from there.

    `verdict` is the second engine's opinion of the reading (`orchestration.verdict`). A whole-field
    rewrite is the most invasive thing this loop does, so it needs that engine to have said `TRUST`;
    `CARE`, `DOUBT` and "no second read" all mean a person should look, and those questions are already
    in a person's queue. It is a keyword with a `None` default so the call sites stay readable, and it
    gates only this path: the character-level ones run for every question, verdict or not.

    `pairs` is this run's systematic census (`{pair: question count}`, `SYSTEMATIC_PAIR_MIN` and up):
    a whole-field reading that changes the same character pair the same way on that many questions is
    systematically misreading it, so the field is refused with the pair named.

    **One refused field refuses the question.** The guards are per field, but the subject is per
    question: the owner's report was 「某些 KM 四個選項都有，結果只改某些，還改錯」 - the paper's `KM`
    appears in all four options, an option the reading failed on was skipped, and the row was left
    holding two representations of the same constant. So every field is evaluated first and a single
    refusal drops the question's whole set with the failing field named; the producer applies the same
    rule at its end ("one refused field ⇒ the whole row is not written"), and this is what makes the
    two ends agree. The detector-anchored path (path 2) is not affected: there, a string that occurs
    in only some options is the normal case, not a split representation.

    `before` is the finding's own `stored` text, not the current field: it is the text the reading was
    taken against, so a stale finding (the field moved since) fails `verify` instead of overwriting a
    field nobody read. Everything refused is appended to `refused`, one line per refusal, because the
    refusals are half of the decision - a person has to be able to see which readings stayed out.
    `refused_fields` collects the same refusals as records (`{field, triggers, why, atomicity}`) for
    the caller that has to decide what to withdraw; a refusal whose trigger is empty says nothing
    about the machine's earlier write.
    """
    out = []
    if error:
        _refuse(refused, "這一輪的判讀本身失敗（error=%s）" % error)
        return out
    verdict_why = _verdict_complaint(verdict)
    if verdict_why:
        _refuse(refused, verdict_why)
        return out
    replacements, records, _order = _whole_field_decision(question, changes or [], pairs)
    for record in records:
        _refuse(refused, "%s：%s" % (record["field"], record["why"]))
        if refused_fields is not None:
            refused_fields.append(record)
    for candidate in replacements:
        out.append(dict(candidate))
    return out


def _first_trigger(reasons: list[tuple[str, str]]) -> str:
    """這一組拒絕裡第一個說得出「機器先前寫錯了」的類別（撤銷要沿用的就是它）。

    空字串的 trigger 是「這一輪不要寫」那一類（量測不過、偵測器衝突、`▢`），它們不撤銷任何東西；
    所以同一題別的欄位因為它們被連帶放下時，也不該撤銷——只有條文本身的類別才撤銷。
    """
    for trigger, _reason in reasons:
        if trigger:
            return trigger
    return ""


def _refuse(refused, reason: str) -> None:
    """Collect one refusal line, when the caller asked for them."""
    if refused is not None:
        refused.append(reason)


def _record(records, field: str, triggers: list[str], why: str, *, atomicity: bool) -> None:
    """Collect one structured refusal, when the caller asked for them."""
    if records is not None:
        records.append({"field": field, "triggers": [t for t in triggers if t], "why": why,
                        "atomicity": atomicity})


def reading_refused_fields(question: dict, changes: list[dict], pairs=None) -> list[dict]:
    """The structured refusals of one reading, for the caller that decides what to withdraw.

    The same evaluation `page_read_replacements` performs, minus the printing and minus the
    reading-level gates: the withdrawal triggers have to stay visible even when the reading is refused
    before the per-field clauses (a `CARE` verdict, an `error`, nobody rejected the question) or when
    the question's whole set was dropped by atomicity - a pair that was misread systematically is a
    fact about the reading, not about which fence stopped it first.
    """
    _replacements, records, _order = _whole_field_decision(question, changes or [], pairs)
    return records


def page_read_substitutions(question: dict, changes: list[dict], *, human_rejected=False,
                            error=None, refused=None, verdict=None, pairs=None,
                            refused_fields=None) -> list[dict]:
    """The edits a *confirmed page reading* justifies, in one of two measured forms.

    Path 2 first, and it keeps its old behaviour exactly: every edit anchored on a detector's own
    flagged position (`anchored_page_changes`), all fields or none - a reading that anchors one field
    but rewrites another is the "partial anchoring" failure the docstring measures.

    Path 3 only when path 2 yields nothing **and** a person has rejected the question: the whole field
    as the reading has it, one edit per field the reading covers, each one passing the measurement
    fence and the class clauses (`whole_field_refusals`) and the second engine's `verdict` (`TRUST`,
    see `_verdict_complaint`), and all of them dropped when any one of them is refused. The two paths
    never mix, so an event's `applied` is one value. The verdict gates path 3 only: path 2 runs on
    every question regardless of it.

    The `before` text is taken from the field (or from the finding's own `stored`) rather than from the
    model's prose, so a stale finding refuses in `verify` instead of applying an edit to the wrong
    place.
    """
    anchored = []
    for change in changes or []:
        field = change.get("field")
        if not field:
            continue
        flagged = set(flagged_positions(question, field))
        page = str(change.get("page") or "")
        edits = anchored_page_changes(field_text(question, field), page, flagged)
        if not edits:
            anchored = []
            break
        for edit in edits:
            anchored.append({"field": field, "position": edit["position"],
                             "before": edit["before"], "after": edit["after"],
                             "rule": "page-read"})
    if anchored:
        return anchored
    if not human_rejected:
        return []
    return page_read_replacements(question, changes or [], error=error, refused=refused,
                                  verdict=verdict, pairs=pairs, refused_fields=refused_fields)
