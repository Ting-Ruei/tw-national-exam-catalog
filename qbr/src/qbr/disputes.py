# -*- coding: utf-8 -*-
"""Disputes: the places where the reading is uncertain, named and addressed.

A pipeline has three honest outcomes, not two:

    data       the reading is settled; it goes into the bank.
    defect     the reading is settled *and* wrong; the address is reported (`lost_glyphs`).
    dispute    the reading is **not settled** - something is known to be uncertain, and the
               uncertainty has a location.

The third was designed (`SKELETON_VS_MODEL_DECISION.md` §2) and never built: the served queue
carried **no `disputes` field at all**, `issues.csv` was empty, and every one of the 33,150 rows
said `quality_status: pass`. That is not the same as "there are no disputes" - it is the same as
"nobody looked", and the two are indistinguishable from the data.

What a dispute is, and what it is not
-------------------------------------
A dispute is raised by a **measurement on the paper**, never by a model's opinion. Each kind below
is a condition the deterministic layers can state exactly, and each carries the address that makes
it checkable:

  * **lost-glyph** - the paper prints a character the text layer cannot spell. The position is
    known and the word it stands in is known; the character is not guessed.
  * **dangling-answer** - the answer sheet names a letter that is not among the options. Either
    the options were read wrongly or the answer was; the two disagree and neither is privileged.
  * **option-shape** - the paper's declared option count and the number read do not agree.
  * **empty-option** - an option came out with no text. Either the paper printed nothing there, or
    the text went somewhere else. The two look identical in the data.
  * **unresolved-mark** - the paper defines a private-use mark and the reading cannot resolve it.
  * **substituted-ideograph** - the paper prints one ideograph and the text layer stores a
    different codepoint that NFKC does not fold back, so the reader sees the wrong character.
  * **engine-disagreement** - the two engines do not agree on a paper's question count.

What is deliberately NOT a dispute
----------------------------------
**A model saying it is unsure.** A model's uncertainty is not evidence about the paper - it is
evidence about the model, and it is not reproducible from the source. Disputes here are computed
from the artifacts, so re-running the pipeline reproduces the same list. A model's reading enters
`ai_review` as a *suggestion on a dispute*, which is where it can be compared against the paper.

**Nothing is decided here.** A dispute is not an acceptance and not a rejection; it is the claim
"this needs a person", with the reason and the address attached. `GOV-05` still holds: only a human
writes a review event.
"""
from __future__ import annotations

#: Severity, worst first, and the ranking is what the UI sorts by. `blocker` means the question
#: cannot be trusted as it stands; `review` means it can be shown but a person should look.
SEVERITY_ORDER = ("blocker", "review", "info")

#: The rare codepoints this corpus actually uses, and the character the paper means by each.
#:
#: These are the **CJK Radicals Supplement** block (U+2E80..U+2EFF), and the distinction that makes
#: them a defect where the Kangxi Radicals are not is measurable rather than stylistic:
#:
#:   Kangxi Radicals (U+2F00..U+2FDF)      NFKC folds them onto the ordinary ideograph
#:                                         (U+2F00 -> U+4E00, U+2F8E -> U+8840). The reader sees
#:                                         the right character, so there is nothing to report.
#:   Radicals Supplement (U+2E80..U+2EFF)  NFKC leaves them **unchanged**.
#:
#: Measured over the served corpus (33,150 questions): 3,420 occurrences of Kangxi radicals, of
#: which 3,341 (97.7%) fold and are harmless; and 79 occurrences of Radicals Supplement forms,
#: of which **none** fold. So the whole block is a real defect and the block next to it is not,
#: which is why this is a block test and not a list of characters.
#:
#: Confirmed against the page rather than by comparing codepoints: rendered at 300 dpi, question
#: 40 of `1081_藥師(一)_藥劑學與生物藥劑學` prints U+9577 and the text layer stores U+2ED1.
#: Three distinct characters occur in the whole corpus, and they are listed with the character
#: the paper means so a reviewer is told what to look for - the reading itself is never rewritten,
#: because a stored character is not changed by this pipeline (the rule of the sandbox, section 3).
#:
#: A reviewer blocked question 34 of that paper - the option `延U+2ED1藥物於黏膜之作用時間` - which
#: is one of the questions this measures. It was blocked with a bare `block`, so the reason was not
#: recorded; what can be said is that the reading in front of that reviewer had this character in it.
RADICAL_SUPPLEMENT_MEANS = {
    "\u2ed1": "\u9577",   # CJK RADICAL LONG ONE      -> the ordinary ideograph (62 occurrences)
    "\u2ea0": "\u6c11",   # CJK RADICAL CIVILIAN      -> the ordinary ideograph ( 9 occurrences)
    "\u2ec4": "\u897f",   # CJK RADICAL WEST TWO      -> the ordinary ideograph ( 8 occurrences)
}

#: Each kind, with the severity it carries and the one-line meaning shown to a reviewer. Keeping
#: the meaning here rather than in the UI means there is one place to change it, and a new kind
#: cannot be added without deciding what it means to a person.
KINDS = {
    "dangling-answer": ("blocker", "答案指到的選項不存在"),
    "option-shape": ("blocker", "選項數與紙本宣示不符"),
    "lost-glyph": ("review", "紙本有字，文字層拼不出來"),
    "substituted-ideograph": ("review", "文字層存的是另一個字，讀者看到錯的字"),
    "substituted-script": ("review", "文字層存的是別國字元，讀者看到亂碼"),
    "unresolved-mark": ("review", "紙本定義的記號無法對照"),
    "empty-option": ("review", "選項沒有文字"),
    "engine-disagreement": ("review", "兩個引擎對題數不一致"),
}

#: Options the paper declares. A multiple-choice national-exam question is four; a handful are
#: five or three, so the check is "the count the paper's own alphabet implies", which the caller
#: passes when it knows it.
OPTIONS_MIN = 2
OPTIONS_MAX = 6


#: Alphabets a Taiwanese exam paper does not print, but whose letters appear in the text layer
#: anyway, sitting *inside a Chinese word*. These are the first word of the Unicode character name,
#: so what is listed is a script and not a character list - the rule generalises to letters nobody
#: has hit yet.
#:
#: Why a *script* rule and not an offset rule: the tempting story is that these fonts number their
#: glyphs a fixed distance from the printed symbol, and 22 characters in the corpus do fit exactly
#: that (+0x2005: U+045B `ћ` -> U+2460 `①`, U+04D8 `Ә` -> U+24DD `ⓝ`). **But it is not a law, and
#: building on it would be building on a coincidence.** Measured: U+090B `ऋ`, U+0F4A `ཊ`, U+0E25
#: `ล` and eleven others also appear and do *not* fit any offset. So the rule names what is true of
#: all 28 - the script is one this paper never prints - and the reviewer is told which script, not
#: which symbol it might have been.
#:
#: A "must sit inside a Chinese word" condition was tried and **removed**: English and Greek are
#: `LATIN` and `GREEK`, which `NATIVE_SCRIPT_PREFIXES` already allows, so the condition never
#: filtered them and instead discarded 54% of the genuine occurrences (including option texts that
#: are entirely Cyrillic). The full measurement is in the docstring of `foreign_script_characters`,
#: which is also its own name - the old `..._in_chinese` said a condition the body no longer has.
#:
#: Measured over the served 79,090-question queue: **110 questions**, of which **0 have been accepted
#: by a person** (so the rule does not reopen judged work) and **3 of the 16 human blocks** are hits.
FOREIGN_SCRIPT_PREFIXES = (
    "CYRILLIC", "DEVANAGARI", "THAI", "TIBETAN", "BENGALI", "SAMARITAN", "ARMENIAN",
    "LAO", "ARABIC", "MALAYALAM", "GUJARATI", "NKO", "MANDAIC", "THAANA", "GURMUKHI",
    "SINHALA", "KHMER", "MYANMAR", "ETHIOPIC", "CHEROKEE", "OGHAM", "RUNIC", "SYRIAC",
    "HEBREW", "GEORGIAN",
)

#: Scripts the paper *does* print, so a letter from them is not evidence of anything. Kept here
#: rather than inline so the two lists are read together and a new script forces a decision.
NATIVE_SCRIPT_PREFIXES = (
    "LATIN", "GREEK", "COMMON", "INHERITED", "BOPOMOFO", "HANGUL", "HIRAGANA", "KATAKANA",
    "CJK", "IDEOGRAPHIC", "KANGXI",
)


def _unicode_name(character):
    import unicodedata
    return unicodedata.name(character, "")


def foreign_script_characters(question):
    """Characters from a script a Taiwan exam paper never prints, with their address.

    Every occurrence is returned. There is **no** "must sit inside a Chinese word" condition, and
    removing it was a measurement, not a relaxation.

    The condition was added for a stated reason - "papers legitimately print English and Greek, so
    require a Han neighbour to report the shape of a wrong character" - and the reason was wrong.
    English and Greek are `LATIN` and `GREEK`, which `NATIVE_SCRIPT_PREFIXES` already excludes, so the
    neighbour test never filtered them. What it did filter, measured over 79,090 questions, was 567
    of the 1,042 genuine occurrences (54%), because the defect does not know where a Chinese word is:

      * `ӧ` in `Waldenstrӧm's` / `Henoch-Schӧnlein` - inside a Latin word (2),
      * `Г` in `（²²⁶Ra Г=8.25 R-cm²/mg-h）` - the paper's `Γ`, next to a space (1),
      * option texts that are *entirely* Cyrillic, e.g. `ћќѝўџ` for `①②③④⑤` - the neighbour is
        another Cyrillic letter, so no Han character is anywhere near it (555, incl. 19 questions
        where the option is a bare `ћќѝ` sequence).

    Those 555 are the most broken questions in the corpus - an option whose whole text is line noise
    - and the condition was hiding them because they looked nothing like "one wrong character in a
    Chinese word". That is precisely the trap the charter warns about: a rule written from the shape
    of the first example rejects the second. The script lists stay, because they are what makes the
    rule narrow: measured, the corpus prints 4.6M CJK, 3.3M Latin and 6,653 Greek characters, and
    every one of the 1,042 hits is in a script with zero legitimate occurrences.

    The old name said `..._in_chinese`, which was accurate when the Han condition existed and became
    a lie the moment it left. Naming it after a condition the code no longer has is how the condition
    gets re-added by somebody reading the name instead of the body.
    """
    found = []
    options = question.get("options") or []
    for field, value in (("stem", question.get("stem")),
                         ) + tuple(("option %s" % option.get("key"), option.get("text"))
                                   for option in options):
        for position, character in enumerate(value or ""):
            name = _unicode_name(character)
            if not name:
                continue
            script = name.split()[0]
            if script in NATIVE_SCRIPT_PREFIXES or script not in FOREIGN_SCRIPT_PREFIXES:
                continue
            found.append({"char": character, "script": script, "field": field, "position": position,
                          "context": "%s%s%s" % ((value or "")[max(0, position - 6):position],
                                                 character,
                                                 (value or "")[position + 1:position + 7])})
    return found


def _d(kind, detail, **address):
    """One dispute. `address` is what makes it checkable, so it is never empty."""
    return {"kind": kind, "severity": KINDS.get(kind, ("info", ""))[0],
            "note": KINDS.get(kind, ("info", ""))[1], "detail": detail, **address}


def summary_row(question, *, option_images=()):
    """A compact row for the queue index: what is disputed and how badly.

    The UI sorts and counts by dispute without reading every dispute in the browser, so the index
    carries the severity and the kinds. A question with none carries `None`, which is a different
    state from `""` - "not looked at" and "looked at, nothing found" must stay distinguishable.
    """
    found = of_question(question, option_images=option_images)
    return {"severity": worst_severity(found) or None,
            "kinds": [dispute.get("kind") for dispute in found],
            "count": len(found)}


def of_question(question, *, alphabet_size=None, engine_counts=None, option_images=()):
    """Every dispute one question carries, measured from the artifacts.

    `question` is a packaged question as `review_queue` sees it: it has `question_number`, `stem`,
    `options` (each `{key, text}`), `answer_payload.accepted_values`, `lost_glyphs`,
    `subitem_legend`. Nothing here reads the PDF - these are disputes *about the reading that was
    made*, and reading the page again is what a reviewer or a model does afterwards.

    `option_images` is the set of option keys that have a picture bound to them, and it matters more
    than it looks: measured over the corpus, **251 of the 274 questions with an empty option are
    correct** - the option *is* the picture - and only **23** are the real defect where the text went
    somewhere else. Without this set the module raises 274 disputes to find 23, and a list that is
    92% noise is a list nobody reads.
    """
    out = []
    number = question.get("question_number")

    # 1. The answer sheet names a letter the options do not contain.
    #
    # This is the strongest signal in the set and the only one that is a *contradiction* rather
    # than an absence: two official artifacts disagree, so one of them was read wrongly. It is
    # never auto-resolved, because choosing which one to believe is exactly the judgement being
    # asked for. Measured over the corpus: 10 questions.
    options = question.get("options") or []
    keys = {str(option.get("key") or "").upper() for option in options}
    accepted = [str(value).upper() for value in
                ((question.get("answer_payload") or {}).get("accepted_values") or [])]
    dangling = [value for value in accepted if value and value not in keys]
    # `keys` may be empty - 10 questions read as having no options at all - and that is the same
    # contradiction, not an absence of one. Measured: those 10 are exactly the questions whose
    # answer sheet names a letter, so guarding on `keys` being non-empty hid the strongest signal
    # in the module behind the worst instance of it.
    if accepted and dangling:
        out.append(_d("dangling-answer",
                      "答案卷給 %s，但選項只有 %s" % ("、".join(dangling),
                                                 "、".join(sorted(keys)) or "（沒有讀到選項）"),
                      answer=dangling))

    # 2. The number of options is not the number the paper's alphabet implies.
    #
    # `alphabet_size` is the count of distinct private-use marks the paper printed for its options,
    # which is the paper's own statement of how many there are. When it is unknown the generic
    # bounds are used, and the dispute is raised only for a count outside them.
    if alphabet_size:
        if len(keys) != int(alphabet_size):
            out.append(_d("option-shape",
                          "紙本的字母表有 %d 個，讀到 %d 個選項" % (alphabet_size, len(keys)),
                          expected=int(alphabet_size), found=len(keys)))
    elif not (OPTIONS_MIN <= len(keys) <= OPTIONS_MAX):
        out.append(_d("option-shape", "選項數 %d 不在合理範圍" % len(keys), found=len(keys)))

    # 3. An option came out with no text.
    #
    # An empty option is ambiguous in a way that matters: the paper may print the option as a
    # picture (`image` set), or the text may have been attached to the stem or to a neighbour
    # (wrong, and the `1152 Q53` drug-name leak is exactly this). The data cannot tell the two
    # apart, which is why it is a dispute and not a defect.
    #
    # When **every** option is empty and none has a picture, the options were not read at all: that
    # is not an uncertainty about one option, it is a missing question body, and it is reported once
    # as a shape rather than four times as four empty options. Measured: 10 questions are like this
    # and they are the same 10 as the dangling answers, so reporting both is intended - they are two
    # statements about one breakage, and a reviewer needs both to see what happened.
    empty = [str(option.get("key") or "") for option in options
             if not str(option.get("text") or "").strip()
             and not option.get("image")
             and str(option.get("key") or "").upper() not in option_images]
    if empty and len(empty) < len(options):
        out.append(_d("empty-option", "選項 %s 沒有文字也沒有圖" % "、".join(empty), options=empty))
    elif options and len(empty) == len(options):
        out.append(_d("option-shape", "四個選項都沒有文字" , found=0, expected=len(options)))

    # 4. The paper prints a character the text layer cannot spell.
    lost = question.get("lost_glyphs") or []
    if lost:
        out.append(_d("lost-glyph", question.get("lost_glyph_note") or "字形遺失",
                      glyphs=lost))

    # 5. The paper defines a mark that the reading could not resolve into the paper's own words.
    legend = question.get("subitem_legend") or {}
    unresolved = sorted(set(question.get("unresolved_marks") or []) - set(legend))
    if unresolved:
        out.append(_d("unresolved-mark", "記號 %s 沒有對照" % "、".join(unresolved),
                      marks=unresolved))

    # 6. The paper prints one ideograph and the text layer stores a different codepoint.
    #
    # Different from `lost-glyph`: there the character is *absent* and the word it stands in is
    # known. Here a character is present and is the wrong one, and NFKC does not bring it back -
    # which is exactly what separates this block from the Kangxi Radicals next to it (see
    # `RADICAL_SUPPLEMENT_MEANS`). The address is the character's position in the field it was
    # found in, so a reviewer can look at the page at that word rather than search the question.
    substituted = []
    for field, value in (("stem", question.get("stem")),
                         ) + tuple(("option %s" % option.get("key"), option.get("text"))
                                   for option in options):
        for position, char in enumerate(value or ""):
            if char in RADICAL_SUPPLEMENT_MEANS:
                left = (value or "")[max(0, position - 6):position]
                right = (value or "")[position + 1:position + 7]
                substituted.append({"char": char, "means": RADICAL_SUPPLEMENT_MEANS[char],
                                    "field": field, "position": position,
                                    "context": "%s%s%s" % (left, char, right)})
    if substituted:
        out.append(_d("substituted-ideograph",
                      "、".join("%s 應為 %s" % (item["char"], item["means"])
                                for item in substituted),
                      substitutions=substituted))

    # 7. The paper prints a symbol and the text layer stores a *letter of another alphabet*.
    #
    # Same defect family as 6 (the reader sees the wrong character) but a different mechanism: the
    # font's glyph is mapped to a letter from a script this paper never prints, so `①` comes out as
    # Cyrillic `ћ` and the option markers turn into line noise. It is a separate kind rather than a
    # wider `substituted-ideograph` because the *evidence* differs: there the intended codepoint is
    # known from `RADICAL_SUPPLEMENT_MEANS`, here it is not, so this dispute deliberately does not
    # guess what the paper printed - it reports the script and the address and leaves the symbol to
    # the reviewer. That is why it can catch scripts nobody has catalogued yet.
    foreign = foreign_script_characters(question)
    if foreign:
        scripts = sorted({item["script"] for item in foreign})
        out.append(_d("substituted-script",
                      "、".join("%s（%s 字母，共 %d 處）" % (script.lower(), script, sum(
                          1 for item in foreign if item["script"] == script)) for script in scripts),
                      substitutions=foreign))

    # 6. The two engines do not agree about the paper this question belongs to.
    if engine_counts:
        left, right = engine_counts
        out.append(_d("engine-disagreement",
                      "引擎 A 讀 %s 題，引擎 B 讀 %s 題" % (left, right),
                      engine_a=left, engine_b=right))

    for dispute in out:
        dispute.setdefault("question_number", number)
    return out


def summarise(rows):
    """Counts by kind and severity, for the queue index and for a report."""
    by_kind = {}
    by_severity = {}
    questions = 0
    for row in rows:
        found = row.get("disputes") or []
        if found:
            questions += 1
        for dispute in found:
            kind = dispute.get("kind")
            by_kind[kind] = by_kind.get(kind, 0) + 1
            severity = dispute.get("severity")
            by_severity[severity] = by_severity.get(severity, 0) + 1
    return {"questions_with_disputes": questions,
            "disputes": sum(by_kind.values()),
            "by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
            "by_severity": {k: by_severity.get(k, 0) for k in SEVERITY_ORDER}}


def worst_severity(found):
    """The worst severity among a question's disputes, or `""` when there are none.

    Used for the list's sort order, so a `blocker` cannot sit below a `review` and be missed on a
    fast pass.
    """
    for severity in SEVERITY_ORDER:
        if any(dispute.get("severity") == severity for dispute in found or ()):
            return severity
    return ""
