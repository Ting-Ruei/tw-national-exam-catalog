"""The text-level machinery: positions, substitutions, signatures and the log readers.

Extracted verbatim from `scripts/apply_dispute_repairs.py` so one file is no longer 2,321 lines.
`apply_dispute_repairs` re-exports every name here; that indirection is deliberate and is why the
test files that load the script by path keep working unchanged. No behaviour was changed.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path

from qbr import cjk


#: 這份普查只認中日韓表意文字（`癇`、`値`、`症`）：CJK 統一表意文字與相容表意文字。
CJK_IDEOGRAPH_BLOCKS = ((0x3400, 0x9FFF), (0xF900, 0xFAFF))

#: Unicode 的下標／上標字元：**這一輪要擋掉的那一類**。平台的排版法是 `<sub>`／`<sup>` 標記
#: （站上實測 2026-09-24：6540 筆已經這樣寫，只有 55 筆帶著 Unicode 上下標字元），而 Unicode 的
#: 上下標字元在頁面上會掉到別的字型（主人報的就是「字型異常」），而且根本沒有下標的大寫 A 或 M
#: ——所以 `GABAA`→`GABAₐ`、`KM`→`Kₘ` 是把大寫悄悄換成小寫，連原文的字元都不是了。
SUBSCRIPT_CHARS = "ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ₀₁₂₃₄₅₆₇₈₉"
SUPERSCRIPT_CHARS = "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿⁱ"
UNICODE_SUB_SUP = frozenset(SUBSCRIPT_CHARS + SUPERSCRIPT_CHARS)


def _sub_sup_char(char: str) -> bool:
    """這個字元是不是 Unicode 說的上下標字元。

    兩層判定，理由跟 `_compatibility_fold` 一樣（表會過期，Unicode 的定義不會）：

        `ₐ`（U+2090）、`⁻`（U+207B）在主人指名的那份清單裡；
        `ᵗ`（U+1D57 MODIFIER LETTER SMALL T）**不在**清單裡，但它是同一個東西——Unicode 自己的
        相容分解說 `<super> 0074`，判讀寫的 `e⁻⁰·⁴ᵗ` 用的就是它。清單加得上這一層才不會有一半的
        上下標從縫裡過去（站上 18:06 那些欄位用的正是這兩種混寫）。
    """
    if char in UNICODE_SUB_SUP:
        return True
    if len(char) != 1:
        return False
    decomposition = unicodedata.decomposition(char)
    return decomposition.startswith("<sub>") or decomposition.startswith("<super>")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_candidates(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def latest_events(path: Path) -> dict[str, dict]:
    """The latest event per key, for the re-check that keeps a repair from undoing a decision.

    Read the same way the server reads it (`latest` wins), so "what action stands" cannot be one
    thing here and another there.
    """
    latest: dict[str, dict] = {}
    if not path.exists():
        return latest
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = event.get("candidate_key")
            if key:
                latest[key] = event
    return latest


def queue_relative(path, queue_root):
    """`path` as the queue spells it, so it resolves on the laptop and in the container alike.

    The container mounts the queue at `/queue` and the laptop at wherever the queue lives; only a
    queue-relative path (`review-ui/crops/<paper>/...`) resolves against both. A finding already
    stores its crop that way (`confirm_dispute.queue_relative`), and such a path is returned
    **untouched**: running `relpath` on a relative path resolves it against the working directory and
    would hand the reviewer's screen something like `../../../..`. An absolute path inside the queue
    is made relative; anything else is returned unchanged, so a crop from outside the queue is still
    a path rather than an exception.
    """
    if not path:
        return None
    text = str(path)
    if not os.path.isabs(text):
        return text.replace(os.sep, "/")
    try:
        relative = os.path.relpath(text, queue_root)
    except ValueError:
        return text
    if relative.startswith(".."):
        return text
    return relative.replace(os.sep, "/")


def field_text(question: dict, field: str) -> str:
    if field == "stem":
        return str(question.get("stem") or "")
    for option in question.get("options") or []:
        if "option %s" % option.get("key") == field:
            return str(option.get("text") or "")
    return ""


def substitutions_for(question: dict) -> list[dict]:
    """Every deterministic substitution this question's disputes imply, in field order.

    One `{field, position, before, after}` per place. `position` comes from the dispute, which
    measures it on the stored text, so it can be sliced directly. `substituted-ideograph` is the
    only kind here: its dispute carries `means` (the character the paper meant) beside the character
    the text layer stored, which is what makes the repair a substitution rather than a decision.

    `flat-offset` is deliberately not read, even though its dispute also carries a target - see the
    module docstring; that class is repaired at the reading (`extract._body_centre`), so substituting
    the characters here would fix the symptom and leave the cause in place.
    """
    out = []
    for dispute in question.get("disputes") or []:
        if not isinstance(dispute, dict):
            continue
        kind = dispute.get("kind")
        if kind == "substituted-ideograph":
            for sub in dispute.get("substitutions") or []:
                if sub.get("position") is None or not sub.get("char") or not sub.get("means"):
                    continue
                out.append({"field": sub.get("field"), "position": int(sub["position"]),
                            "before": sub["char"], "after": sub["means"], "rule": kind})
    return out


def flagged_positions(question: dict, field: str) -> dict:
    """`position -> substitution` for one field, from the question's own disputes.

    This is the anchor the page-read path is allowed to edit: the places a detector *already measured*
    as carrying a character that is not what the paper prints. Everything else in the field is text
    nobody has doubted, and a repair has no business rewriting it.
    """
    out = {}
    for dispute in question.get("disputes") or []:
        if not isinstance(dispute, dict):
            continue
        for sub in dispute.get("substitutions") or []:
            if sub.get("field") == field and sub.get("position") is not None:
                out[int(sub["position"])] = sub
    return out


def _without_whitespace(text: str):
    """The text's non-whitespace characters, each with the index it came from.

    Whitespace is dropped because the page read is a second *reading* of the same line: a model that
    collapses the two spaces the extractor kept between numbered items has not changed the content,
    and refusing the whole repair over a space would put the one character that does matter out of
    reach. The original indices are kept so the anchor check compares positions on the stored text.
    """
    chars, origins = [], []
    for index, char in enumerate(text):
        if char.isspace():
            continue
        chars.append(char)
        origins.append(index)
    return chars, origins


def _compatibility_fold(before_char: str, after_char: str) -> bool:
    """這兩個字是 Unicode 說的同一個字嗎（相容分解後相同）。

    `⽣`（U+2F63 KANGXI RADICAL LIFE）與 `生`、`⽽` 與 `而`、`⼗` 與 `十`：NFKC 把前者分解成後者，
    所以「換過去」在 Unicode 自己的定義裡不是改字，是把同一個字寫成另一種形式。這一條讓紙本判讀
    可以順手修掉這些相容字——**而模型不能靠它偷改句子**：句子裡的任何改寫都不會剛好是相容分解。

    刻意**不**用一張自己維護的表：表會過期，而 NFKC 是這批字元的定義。`⻑`→`長` 不在相容分解裡
    （CJK Radicals Supplement 沒有分解），那種字仍然只由偵測器自己的表提供。
    """
    if not before_char or not after_char or before_char == after_char:
        return False
    return unicodedata.normalize("NFKC", before_char) == unicodedata.normalize("NFKC", after_char)


def aligned_changes(before: str, after: str) -> list[tuple[str, str]]:
    """兩段文字之間「前→後」的字元配對，空字串那一邊代表新寫進來的或刪掉的。

    長度相同就逐位比；長度不同就用 `difflib.SequenceMatcher` 的 opcode 對齊（`replace` 段逐位配對，
    多出來的那一邊配 `""`）。整欄替換的三條新規則都用它，所以它只做對齊、不做判斷。
    """
    if before == after:
        return []
    if len(before) == len(after):
        return [(a, b) for a, b in zip(before, after) if a != b]
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, before, after,
                                                        autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        left, right = before[i1:i2], after[j1:j2]
        for index in range(max(len(left), len(right))):
            out.append((left[index] if index < len(left) else "",
                        right[index] if index < len(right) else ""))
    return out


def _cjk_ideograph(char: str) -> bool:
    """這個字元是不是中日韓表意文字（普查只認這一類）。"""
    if len(char) != 1:
        return False
    return any(low <= ord(char) <= high for low, high in CJK_IDEOGRAPH_BLOCKS)


def _unique(characters) -> str:
    """去重但保留順序，給理由文字用（`ₕ₂ₒ` 而不是 `ₒ₂ₕ`）。"""
    seen, out = set(), []
    for char in characters:
        if char not in seen:
            seen.add(char)
            out.append(char)
    return "".join(out)


#: 只換碼位、字形看起來一模一樣的那兩塊字元：CJK 部首補充與康熙部首。
#:
#: 兩塊的行為**不同**，而那個不同決定了它們為什麼都可以寫，所以兩塊要分開講：
#:
#:     U+2F00–U+2FDF（康熙部首）在 Unicode 自己有相容分解：`NFKC("⾎") == "血"`、`NFKC("⽣") == "生"`。
#:       換過去在 Unicode 的定義裡不是改字，是把同一個字寫成另一種形式。
#:     U+2E80–U+2EFF（CJK 部首補充）**沒有**分解：`NFKC("⻑") == "⻑"`、`NFKC("⻄") == "⻄"`。
#:       這一塊之所以能寫，**只因為 dispute 自己帶著量測到的目標字**（`means` 說紙本印的是 `長`），
#:       不是因為 Unicode 說它們相同。所以 `⻑` 這一半永遠不可以被「改良」成一次通用的 NFKC
#:       掃描：那會把一個量測換成一個猜測，而這個專案為此付過代價。
RADICAL_BLOCKS = ((0x2E80, 0x2EFF), (0x2F00, 0x2FDF))
#: The Kangxi half, which is the half Unicode itself maps.
KANGXI_BLOCK = (0x2F00, 0x2FDF)


def _radical_glyph(char: str) -> bool:
    """這個字元是不是那兩塊裡「看起來跟另一個字一樣」的部首字形。

    用 Unicode 的區塊與名字判定，不自己維護一張表：`unicodedata.name` 的前綴就是區塊的定義
    （`CJK RADICAL LONG ONE`、`KANGXI RADICAL LIFE`），一張自製的表則會在下一批字元到來時過期。
    """
    if len(char) != 1:
        return False
    if not any(low <= ord(char) <= high for low, high in RADICAL_BLOCKS):
        return False
    try:
        name = unicodedata.name(char)
    except ValueError:
        return False
    return name.startswith("CJK RADICAL") or name.startswith("KANGXI RADICAL")


#: 原文裡的 HTML 標記（`<sub>`、`<sup>`、`<br>`…）。**平台用標記排版**，紙本判讀則把同一段數學
#: 寫成 Unicode 下標（`C<sub>p</sub>` → `Cₚ`）——所以整欄替換一旦把標記拆掉，就是把平台讀得懂的形式
#: 換成模型選的形式。這一份定義是**唯一**的一份（`page_read` 用它，`page_read.MARKUP` 是它的別名）
#: ——平台的標記法只有一份，兩個模組各自寫一條正規表示式就是兩個可以不一致的地方。
MARKUP_RE = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")

#: 同一族的排版變體：族內互換算「同一個符號的另一種排法」。
#:
#: 刻意**不放**的兩類，與理由：`。`（句號，與 `.` 不同義）、大小寫（`s` → `S` 在單位與化學式裡
#: 會改意思——站上 4 欄的實例就是它）。跨族（`，` → `.`）也不算，那是換標點不是換排法。
_TYPOGRAPHIC_FAMILIES = (
    "-−–—‐―－",            # 連字號／減號／破折號
    "'’‘‛`´",              # 單引號與重音號
    '"“”„',                # 雙引號
    ".．·・‧•",            # 句點與間隔號（十進位守門見下）
    "，,",
    "（(", "）)",
    "：:", "；;", "！!", "？?",
    "％%", "＋+", "＝=", "／/", "＼\\", "＜<", "＞>", "～~",
    "　 ",                 # 全形空白與半形空白（只有空白差別的那些配對在上面的過濾就被丟掉了，
                           # 所以這一族只在同一欄另有非空白的變體時才用到）
)

#: `.`／`．` 夾在兩個數字中間時是**小數點**，不是間隔號：這種欄位（`P2.5`）不准動 `.` 那一族，
#: 不然 `P2.5` 會被讀成 `P2·5`，那是改數字而不是改排法。
_DECIMAL_BETWEEN_DIGITS = re.compile(r"\d[.．]\d")


def _family_of(char: str) -> str:
    for family in _TYPOGRAPHIC_FAMILIES:
        if char in family:
            return family
    return ""


def _typographic_pair(before_char: str, after_char: str, stored: str) -> bool:
    if len(before_char) != 1 or len(after_char) != 1 or before_char == after_char:
        return False
    family = _family_of(before_char)
    if not family or family != _family_of(after_char):
        return False
    if "." in family and (before_char in ".．" or after_char in ".．"):
        return not _DECIMAL_BETWEEN_DIGITS.search(stored)
    return True


def _lost_glyph_pair(before_char: str, after_char: str) -> bool:
    """壞掉的字元被還原：`stored` 那一邊是私用區字元，紙本那一邊是真字元（方向固定）。"""
    return (len(before_char) == 1 and len(after_char) == 1
            and cjk.is_pua(before_char) and not cjk.is_pua(after_char))


def _cjk_shape(char: str) -> bool:
    """漢字或部首字形：`⽣`／`⼗` 這類相容字要算，`①`／`㎏` 這類不算（那兩種 NFKC 也會說「同一個字」，
    但換過去會改掉題目的讀法）。"""
    return _cjk_ideograph(char) or _radical_glyph(char)


#: 一欄的整組改動屬於「可檢查的機械還原」時，是哪一種。
#:
#:   * `lost_glyph` —— `stored` 那一邊是 Unicode 私用區字元（`cjk.is_pua`），紙本判讀那一邊是真字元。
#:     私用區字元站在題目文字中間時**永遠不是合法文字**（PDF 字型沒有對映到，渲染出來才是對的字），
#:     這是 `qbr.cjk` 自己寫下的判準（`audit_text` 的 `lost_glyph_characters`）。待辦題裡最大的
#:     單一模式就是它：`\ue2c6` → `酶` 出現在 **33 題**；全佇列量到 53 題含 48 個這種字元（另有
#:     53 個是良性的選項標記 `\ue18c`…，所以判準是「站在字中間」而不是「是不是私用區」）。
#:   * `typographic_variant` —— 整組差異都是同一族的排版變體（破折號、引號、間隔號、全形半形標點）。
#:     族由 `_TYPOGRAPHIC_FAMILIES` 一張表定義；`.`／`．` 另有一條十進位守門（見該表下方註解）。
#:     實測例子：`．` → `·` 17 題、`–` → `-` 3 題、`’` → `'` 2 題。
#:   * `compatibility_fold` —— Unicode 自己說的同一個字（`⽣` → `生`、`⼗` → `十`、`⽊` → `木`）。定義借用
#:     `_compatibility_fold`（NFKC 相容分解相同），而且**兩邊都得是漢字或部首字形**——不這樣限，
#:     `①` → `1` 這種也會算進來，那會改掉題目的讀法。
DETERMINISTIC_FORMS = ("lost_glyph", "typographic_variant", "compatibility_fold")


def deterministic_form(stored, page) -> str:
    """這一欄的整組改動是不是**可檢查的機械還原**，是的話回它的類別，否則回 `""`。

    「可檢查」的定義：不必有人看過這一題，判準就在字元自己身上（私用區、同一族的排版變體、
    Unicode 的相容分解）。整組改動同類才算數——混到一個真正改字的就整欄回 `""`，那要人看過。

    標記的組成必須原樣（`KM` → `K<sub>M</sub>` 的標記是**多出來的**，算同組；把原文的 `<sub>` 拆掉
    就不是同一組了）。這一支只回答「這一組差異是不是機械性的」，不回答「誰有權寫」——那是
    `apply_experience_repairs.form_of` 的事。
    """
    before, after = str(stored or ""), str(page or "")
    if not before or not after or before == after:
        return ""
    if MARKUP_RE.findall(before) != MARKUP_RE.findall(after):
        return ""
    pairs = aligned_changes(before, after)
    # 空白不算改動：紙本判讀是同一行的**第二次讀**，把抽取器留下的兩個空格收成一個並不是改了內容
    # （`_without_whitespace` 對同一件事寫過同樣的理由）。這一條只放行「兩邊都是空白或其中一邊是
    # 空白」的配對；插進一個真的字元仍然留下來，也就仍然會讓整欄失格。
    pairs = [(a, b) for a, b in pairs if a.strip() or b.strip()]
    if not pairs:
        return ""
    if all(_lost_glyph_pair(a, b) for a, b in pairs):
        return "lost_glyph"
    if all(_typographic_pair(a, b, before) for a, b in pairs):
        return "typographic_variant"
    if all(_cjk_shape(a) and _cjk_shape(b) and _compatibility_fold(a, b) for a, b in pairs):
        return "compatibility_fold"
    return ""


def _normalisation_pairs(before: str, after: str):
    """一筆修復裡「對不上的字元」配對清單；不是單純換碼位時回傳 `None`。

    長度會變的（插入／刪除）不可能是換碼位；字元數一樣但位置對不上的也不是。
    """
    if before == after:
        return []
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    pairs = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag != "replace" or (i2 - i1) != (j2 - j1):
            return None
        pairs.extend(zip(before[i1:i2], after[j1:j2]))
    return pairs


def is_normalisation(subs: list[dict]) -> bool:
    """這一筆修復是不是「只有碼位錯了、螢幕上的字一模一樣」。

    實測（站上，2026-09-24）：354 筆機器修復事件裡有 **345** 筆正好是這一類——`⻑`→`長`、
    `⻄`→`西`、`⺠`→`民`。字形一樣，所以審題的人不需要逐題去看；UI 要把這一類跟「真的要看的修復」
    分開數，而它無法只從 `changes` 反推（CJK 部首補充那一半沒有 NFKC 對應），所以旗標寫在事件上。

    成立條件（每一筆 `changes` 都必須成立）：對不上的字元裡，**原文那一邊**是部首字形，**紙本那一邊**
    是它代表的一般字；康熙部首那一半另外要求 Unicode 自己說得通（`NFKC(原文) == 紙本`），CJK 部首
    補充那一半沒有這種對應，所以只由「原文是部首字形、紙本是那個一般字」成立——它之所以可寫，
    是 dispute 已經量到了目標字，而不是這條規則在放寬什麼。
    """
    saw = False
    for sub in subs:
        pairs = _normalisation_pairs(str(sub["before"]), str(sub["after"]))
        if pairs is None:
            return False
        for stored_char, page_char in pairs:
            saw = True
            if not _radical_glyph(stored_char) or _radical_glyph(page_char):
                return False
            if KANGXI_BLOCK[0] <= ord(stored_char) <= KANGXI_BLOCK[1] \
                    and unicodedata.normalize("NFKC", stored_char) != page_char:
                return False
    return saw


def anchored_page_changes(stored: str, page: str, flagged: set) -> list[dict] | None:
    """The substitutions a page reading implies, or `None` if it is not a repair but a rewrite.

    The rule, and every clause of it is a measured refusal from this corpus:

    * **Equal length per run.** A run that changes the *number* of characters is not a substitution:
      it moved every later position, and the diff that follows it is about a line that has become a
      different line. Measured: `115090 q053`'s read inserted 93 characters and deleted the four
      options, `114020 q060` inserted 80.
    * **Replace only.** An `insert`/`delete` run is the same failure expressed as a boundary instead
      of a length change. Measured: `113020 q076`'s read appended a table, `113020 q050` a figure
      description, `105020 q045` a whole compartment diagram with invented arrow labels.
    * **Every flagged position must be fixed.** Leaving a doubted character untouched is a partial
      repair that would reopen the question with the defect still in it.
    * **Nothing else may change - except a compatibility fold.** 2026-09-24: this clause used to read
      "every position must already be flagged", and that made the whole `--page-read` path
      inapplicable to almost every reading. Measured on the station: `apply_dispute_repairs --page-read`
      found **0** repairs while `confirm_dispute.py` was writing findings with `changes` on every
      round, because the detector's own table (`RADICAL_SUPPLEMENT_MEANS`) covers only the **CJK
      Radicals Supplement** (`⻑` `⻄` …) and the model's transcription also repairs the **Kangxi
      Radicals** next to them (`⽣`→`生`, `⽽`→`而`, `⼗`→`十`, `⾁`→`肉`, `⼀`→`一`), which are not
      flagged by anything. Those extra edits are allowed **only** when Unicode itself says the two
      characters are the same character (equal NFKC form - see `_compatibility_fold`), so the extra
      edit can never be the model editing prose. Everything else still refuses: the `flattened-offset`
      class has **no** flagged position at all, and reading it as a repair would rewrite physics
      formulas on a model's word alone - it is repaired at the reading, not here.
    """
    if not stored or not page:
        return None
    if stored == page:
        return None
    stored_chars, stored_origins = _without_whitespace(stored)
    page_chars, _ = _without_whitespace(page)
    matcher = difflib.SequenceMatcher(None, "".join(stored_chars), "".join(page_chars),
                                      autojunk=False)
    touched, changes = set(), []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag != "replace" or (i2 - i1) != (j2 - j1):
            return None
        touched |= set(range(i1, i2))
        changes.append({"positions": (i1, i2), "from": "".join(stored_chars[i1:i2]),
                        "to": "".join(page_chars[j1:j2])})
    if not changes:
        return None
    if not flagged or not set(flagged) <= {stored_origins[i] for i in touched}:
        return None
    # 逐字檢查「多改的」那幾個位置：只有相容分解相同（Unicode 說這兩個是同一個字）才放行。
    for change in changes:
        i1, _ = change["positions"]
        for offset, (before_char, after_char) in enumerate(zip(change["from"], change["to"])):
            origin = stored_origins[i1 + offset]
            if origin in flagged:
                continue
            if not _compatibility_fold(before_char, after_char):
                return None
    # Back to the stored field's own coordinates. `verify` and `apply_substitutions` slice the
    # original text, not the folded one - a position in the folded text would edit the wrong
    # character whenever the field contains a space, which the corpus's formulas and numbered
    # option lists always do. A run whose original indices are not contiguous cannot be expressed as
    # one `{position, before}` pair, so it is refused rather than silently split.
    located = []
    for change in changes:
        i1, i2 = change["positions"]
        originals = [stored_origins[i] for i in range(i1, i2)]
        if originals != list(range(originals[0], originals[-1] + 1)):
            return None
        located.append({"position": originals[0],
                        "before": stored[originals[0]:originals[-1] + 1],
                        "after": change["to"]})
    return located


def verify(question: dict, subs: list[dict]) -> list[str]:
    """Why a substitution cannot be applied, as a list of complaints (empty = safe).

    Checks the *stored* text at the claimed position, because a dispute is a claim about the storage
    and the claim has to still hold at write time. A stale dispute (the text changed since it was
    measured) must refuse rather than replace the wrong character.

    A whole-field replacement is checked the same way and for the same reason, on the whole field: its
    `before` is the text the reading was taken against, so exact equality is what says the reading is
    still about this field. This is the check that makes a **stale finding** (a sentence was prepended
    since the read) refuse instead of overwriting text nobody read.
    """
    complaints = []
    for sub in subs:
        if satisfied_sub(question, sub):
            # Nothing to write and nothing to complain about - see `satisfied_sub` for the measured
            # defect this fixes (192 questions refused for being *already correct*).
            continue
        text = field_text(question, sub["field"])
        if sub.get("replace"):
            if text != sub["before"]:
                complaints.append("%s 已改動（判讀是對 %r… 做的，現在是 %r…）（判讀已過期）"
                                  % (sub["field"], sub["before"][:24], text[:24]))
            continue
        at = sub["position"]
        actual = text[at:at + len(sub["before"])]
        if actual != sub["before"]:
            complaints.append("%s[%d] 是 %r，不是 %r（dispute 已過期）"
                              % (sub["field"], at, actual, sub["before"]))
    return complaints


def satisfied_sub(question: dict, sub: dict) -> bool:
    """Is the substitution's target already in the field? Then this claim is *done*, not stale.

    2026-09-25, measured on the station: `to repair 0 / REFUSED 324`, of which **192 questions** were
    refused with `dispute 已過期`, and **186 of those had a `TRUST` reading that had written the fix**.
    The reason was not that the text was wrong - it was that the text was *already right*: the row's
    `disputes` list is written when the queue is built and is **never re-measured after a repair**, so
    a question whose field an earlier run already corrected (`parser_original` holds `⻑`, the field
    holds `長`) still carries the claim `⻑ 應為 長`. `verify` sliced the stored field, saw `長` where
    the dispute claimed `⻑`, and called it 「dispute 已過期」 - a refusal - which then blocked **the
    whole question** (atomicity), so the reading's *other*, real edits could never be written, round
    after round. The owner saw exactly that: 「你明明很多題目都有自己寫應該怎麼改…但是他們還是卡在
    這邊」.

    The distinction this draws is the one that matters: 「這一格已經是紙本那個字」 is *nothing to do*,
    and 「這一格既不是舊字也不是新字」 is the genuine staleness `verify` must still refuse (for
    example a position that moved by three characters: the text there is some other character).
    """
    text = field_text(question, str(sub.get("field") or ""))
    after = str(sub.get("after") or "")
    if not after:
        return False
    if sub.get("replace"):
        return text == after
    at = sub.get("position")
    if at is None:
        return False
    at = int(at)
    return text[at:at + len(after)] == after


def apply_substitutions(text: str, subs: list[dict]) -> str:
    """Replace right-to-left so an earlier replacement cannot move a later position.

    Order is not cosmetic: `h-1` at 88 and another run at 12 must both land at the index the dispute
    measured, and replacing left-to-right would shift everything after the first edit.
    """
    value = text
    for sub in sorted(subs, key=lambda s: -s["position"]):
        at = sub["position"]
        value = value[:at] + sub["after"] + value[at + len(sub["before"]):]
    return value


def corrected_field(text: str, subs: list[dict]) -> str:
    """One field as the repair leaves it. The only place that decides between the two forms.

    A `replace` entry states the whole field from the page read, so it wins outright - the character
    positions of a substitution are positions in the *stored* text, and applying them to text that
    came from somewhere else would cut at the wrong offset.
    """
    for sub in subs:
        if sub.get("replace"):
            return sub["after"]
    return apply_substitutions(text, subs)


def build_correction(question: dict, subs: list[dict]) -> dict:
    """The `correction` payload the server understands: `stem` and/or `options`, whole.

    Whole fields rather than a diff, because that is the shape `normalized_correction` accepts and
    the overlay applies - and because a partial field would leave the reader unable to tell which
    text is the corrected one.

    The payload shape does not change for a whole-field replacement: the field's text *is* the page's
    text, which is what `corrected_field` returns.
    """
    by_field: dict[str, list[dict]] = {}
    for sub in subs:
        by_field.setdefault(sub["field"], []).append(sub)
    correction: dict = {}
    if "stem" in by_field:
        correction["stem"] = corrected_field(field_text(question, "stem"), by_field["stem"])
    touched = {f for f in by_field if f != "stem"}
    if touched:
        options = []
        for option in question.get("options") or []:
            name = "option %s" % option.get("key")
            row = {"key": option.get("key"), "text": str(option.get("text") or "")}
            if name in touched:
                row["text"] = corrected_field(row["text"], by_field[name])
            options.append(row)
        correction["options"] = options
    return correction


def applied_signature(event: dict):
    """What a repair event already changed, as an order-independent frozenset of edits.

    The queue's candidate text is **not** rewritten by design - a correction is an event that overlays
    the field, so the original reading survives. That means `substitutions_for` still finds the same
    `⻑ -> 長` on the next run, and without this the tool would append a second identical repair for
    every question it had already fixed (measured: 192 such events were already in the log). Comparing
    the edits, not the count, is what makes a *re*-repair possible: if the text has moved since, the
    signature differs and the question is repaired again, which is correct.
    """
    if not event or event.get("source") != "qbr_dispute_apply":
        return None
    return frozenset((str(c.get("field")), str(c.get("from")), str(c.get("to")))
                     for c in event.get("changes") or [])


def last_repair_signature(path) -> dict:
    """`candidate_key -> edits` of the **most recent `qbr_dispute_apply`** event for that question.

    Not the latest event overall, and the difference was measured. A person can review a question
    after the repair - the station's clock is UTC while the laptop's is UTC+8, so a human `accept`
    stamped `03:28:55` is appended **after** a repair stamped `11:25:44` - and the projection is
    last-line-wins, so the repair stops being the latest event. Reading the signature off the latest
    event then returned `None`, and the next `--page-read` run would have appended a *second*
    identical repair, re-resetting a question a person had just accepted. That is the one outcome
    this tool must never produce: it would silently undo a human decision.

    Scanning for the last repair event instead of the last event keeps the check about the tool's own
    work, and a human decision in between no longer erases the memory of it.
    """
    out = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = event.get("candidate_key")
            if key and event.get("source") == "qbr_dispute_apply":
                out[key] = event
    return out


def repair_signature(subs: list[dict]):
    return frozenset((str(s["field"]), str(s["before"]), str(s["after"])) for s in subs)


#: 服務器把這些前綴的 reviewer 排除在「人做的決定」之外（`review_ui/constants.py`
#: `REPAIR_REVIEWER_PREFIXES`）。這裡照抄一份而不是 import：這支工具不該為了讀一個字串常數把整個
#: review_ui 拉進來。撤銷事件要靠它分辨「人的那一筆」與「機器自己寫的那一筆」。
REPAIR_REVIEWER_PREFIXES = (
    "repair_",
    "backfill_",
    "parser_global_refresh",
    "codex-repair",
    "codex-text-normalization-repair",
)


def is_machine_event(event: dict) -> bool:
    """這一筆事件是不是機器寫的（不是人的決定）。

    用服務器自己的定義（`review_ui/constants.REPAIR_REVIEWER_PREFIXES`）：帶著 `repair_`／
    `backfill_`… 前綴的 reviewer 一律不算人的決定——這一支工具自己寫的事件正是這一類。事件形狀有
    兩種要分開處理（契約的撤銷事件沒有 `source`，只有 `reviewer`），所以兩個都認。
    """
    if event.get("source") == "qbr_dispute_apply":
        return True
    reviewer = str(event.get("reviewer") or "")
    return any(reviewer.startswith(prefix) for prefix in REPAIR_REVIEWER_PREFIXES)


def merge_substitutions(subs: list[dict]) -> list[dict] | None:
    """同一位置只留一筆；兩筆說法不同時回傳 `None`（＝兩個量測不一致，交給人）。

    兩個來源會指到同一個位置：dispute 自己帶的目標字元（策展的表 `RADICAL_SUPPLEMENT_MEANS`），以及
    紙本判讀的轉錄。實測最常見的是兩者說同一件事（`⻑`→`長`），那只是一筆——不先去重，`changes`
    與收據裡就會出現重複的編輯，而且 `build_correction` 會對同一個位置套兩次。
    兩者在同一個位置上說**不同**的事就不是「哪一筆對」的問題，而是兩個量測彼此矛盾；那種題目不該
    由這支工具靜默選邊，所以整題拒絕（人會在看紙本時決定）。

    整欄替換（`replace=True`）沒有 `position`：它說的是「這一欄就是紙本讀到的樣子」，所以它**涵蓋**
    同一欄的字元級修復——兩筆都留會讓同一欄出現兩筆 `changes`，而產檔那一邊的 `from` 就有了兩種
    意思。涵蓋的前提是替換沒有漏掉任何一個被標記的位置，那件事在 `_flagged_conflict` 裡量過了：
    漏掉就整欄拒絕，不會走到這裡把它吃掉。同一欄兩筆**不同**的整欄說法同樣是兩個量測不一致。
    """
    replaced: dict[str, dict] = {}
    for sub in subs:
        if not sub.get("replace"):
            continue
        seen = replaced.get(sub["field"])
        if seen is not None and (seen["before"], seen["after"]) != (sub["before"], sub["after"]):
            return None
        replaced[sub["field"]] = sub
    merged: dict[tuple, dict] = {}
    for sub in subs:
        if sub.get("replace") or sub["field"] in replaced:
            continue
        key = (sub["field"], sub["position"])
        seen = merged.get(key)
        if seen is None:
            merged[key] = sub
            continue
        if (seen["before"], seen["after"]) != (sub["before"], sub["after"]):
            return None
        # 同一件事：留紙本判讀那一筆。它的 `before` 取自欄位原文，而且它剛通過錨定檢查。
        if seen.get("rule") != "page-read" and sub.get("rule") == "page-read":
            merged[key] = sub
    out = [merged[key] for key in sorted(merged)]
    out.extend(sorted(replaced.values(), key=lambda s: s["field"]))
    return out
