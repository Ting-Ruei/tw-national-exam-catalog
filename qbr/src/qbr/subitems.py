"""The paper's own legend for its private-use marks.

Some papers print a question whose answer choices name *sets of the question's own sub-items*
rather than four alternative phrases. `1001_醫事檢驗師_臨床血清免疫學與臨床病毒學` Q49 is the
shape:

    下列那些感染是藉由老鼠傳染的？
    \ue000砂粒病毒（Arenavirus）  \ue001漢他病毒（Hantavirus）  \ue002西尼羅病毒（West Nile virus）
    \ue18c\ue000\ue001   \ue18d\ue000\ue002   \ue18e\ue001\ue002   \ue18f\ue000\ue001\ue002

`\ue18c`-`\ue18f` are the option labels A-D; `\ue000`-`\ue003` are *not* labels - they are the
marks the paper prints next to the sub-items it lists in the stem, and the options are
expressions over those marks ("Arenavirus and Hantavirus", "Arenavirus and West Nile virus", …).

A browser has no glyph for a private-use codepoint, so the Review UI drew the options as rows of
tofu boxes: the question was unreadable not because the reading was wrong but because the marks
were never resolved. **The paper states what each mark means, in its own stem, one line above.**
This module reads that statement and rewrites the marks into the words they stand for, so the
screen shows `砂粒病毒（Arenavirus）+ 漢他病毒（Hantavirus）` where the paper shows `\ue000\ue001`.

The legend is read from the stem only. A mark whose meaning the stem does not state is left as it
is, and the row is flagged, because inventing a meaning would be worse than showing the mark.
"""

import re

#: A mark followed by the words it labels, e.g. `\ue000砂粒病毒（Arenavirus）`. The words run to the
#: next mark or to a wide gap; 24 characters is longer than any sub-item label measured in the
#: corpus (the longest is `氮血症（azotemia）`, 10).
_LEGEND_ENTRY = re.compile(r"([\ue000-\uf8ff])\s*([^\ue000-\uf8ff\s][^\ue000-\uf8ff]{0,23})")


def legend_of(stem):
    """`{mark: words}` for the sub-items the stem labels, or `{}` when it labels none.

    Only marks that the stem itself spells out are returned, and a mark that occurs more than once
    keeps its first spelling: the same mark always stands for the same sub-item inside one
    question, and a second occurrence is the mark being *used* in the options rather than defined.

    **A mark standing inside a word is not a definition.** `下列那一項是丙酮酸羧\ue2c6（pyruvate
    carboxylase）的輔\ue2c6？` contains the same character twice followed by words, which reads as a
    legend - but `羧\ue2c6` is 羧酶 with the glyph lost, not a sub-item label. Measured: taking it for
    a legend made `\ue2c6` resolve to `（pyruvate carboxylase）的輔` and hid the fact that the stem has
    two unreadable characters in it. A label marks what *follows* it from a boundary; a lost glyph
    sits where a character inside a word should be, so a Chinese character immediately before it
    disqualifies it.
    """
    if not stem:
        return {}
    found = {}
    for match in _LEGEND_ENTRY.finditer(stem):
        mark, words = match.group(1), match.group(2).strip()
        if not words or mark in found:
            continue
        if match.start() and _HAN_BEFORE.match(stem[match.start() - 1]):
            continue
        found[mark] = words
    return found


def resolve(text, legend):
    """Rewrite every mark in `text` as the words the legend gives it, joined with `+`.

    A **run** of marks is one option's selection: `\ue000\ue001` is "Arenavirus and Hantavirus",
    which reads as `砂粒病毒（Arenavirus）+漢他病毒（Hantavirus）`. Words inside one expansion keep
    their own text; only the join between two different sub-items is a `+`.

    Marks are kept when the legend does not name them, and the text around a run is left exactly
    as the paper set it, because a reviewer comparing the screen against the paper needs to see
    what the paper printed.
    """
    if not text or not legend:
        return text
    out = []
    run = []

    def flush():
        if run:
            pieces = [legend.get(char, char) for char in run]
            out.append("+".join(pieces))
            run.clear()

    for char in text:
        if _is_private_use(char):
            # A mark the legend names is part of the current selection; one it does not name is a
            # lost glyph, kept exactly as printed and ended here so it is not joined to a selection.
            if char in legend:
                run.append(char)
            else:
                flush()
                out.append(char)
        else:
            flush()
            out.append(char)
    flush()
    return "".join(out)


def _is_private_use(char):
    code = ord(char)
    return 0xE000 <= code <= 0xF8FF or 0xFFF000 <= code <= 0x10FFFF


def has_resolvable_marks(text, legend):
    """Whether `text` carries at least one mark the legend can rewrite."""
    return bool(text) and any(char in legend for char in text)


#: A private-use mark standing *inside a word* - a Chinese character immediately before it. This is
#: the shape of a glyph the font failed to map: `轉氨\ue2c6（GPT）` is 轉氨酶, `單純\ue2cc疹病毒`
#: is 單純疱疹病毒, `黃素腺二核\ue2e6酸` is 黃素腺二核苷酸. The codepoint is not one of the paper's
#: option labels and the mark is not at a boundary, so nothing about it is a label.
_HAN_BEFORE = re.compile("[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def lost_glyphs(text, legend=None):
    """The positions of marks that are lost characters rather than labels.

    A lost glyph is a **real defect at a known position**: the paper prints a character there and
    the text layer cannot spell it. It is not something to guess at - a reader who has the paper
    can see which character it should be, and the engine cannot, so guessing would be inventing
    text. What the pipeline owes the reviewer is the *address* of the defect.

    Returns a list of `(index, following_text)` so a caller can point at the character and at the
    word it stands in.
    """
    if not text:
        return []
    labelled = set(legend or ())
    found = []
    for index, char in enumerate(text):
        if not _is_private_use(char) or char in labelled:
            continue
        if index and _HAN_BEFORE.match(text[index - 1]):
            found.append((index, text[index + 1:index + 3]))
    return found


def describe_lost_glyphs(text, legend=None):
    """A human-readable note for each lost glyph, or `None` when there are none.

    The wording says what the reader needs to do, because the note is read while fixing the
    question: the mark is where a character should be, and the characters around it identify which
    one.
    """
    found = lost_glyphs(text, legend)
    if not found:
        return None
    notes = []
    for index, after in found:
        before = text[max(0, index - 4):index]
        notes.append("第 %d 個字元：%s▢%s" % (index + 1, before, after))
    return "；".join(notes)
