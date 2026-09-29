# -*- coding: utf-8 -*-
"""The reading's typography: one rule, in one place, sent by every transcription prompt.

The complaint this file pins (owner, 2026-09-24) is one defect seen four times: `GABAA` came back
as `GABA` + `U+2090`, `KM` as `K` + `U+2098`, `Sol_H2O` as `Sol` + `U+2095U+2082U+2092`,
`AUC0-∞` as `AUC` + `U+2080` + an invented `.`. Every one of them is the transcription prompt
asking for Unicode sub/superscript characters and forbidding markup - and Unicode has no subscript
capital `A` and no subscript `M`, so the instruction *cannot* preserve what the paper prints. The
bank's own convention is the opposite: measured on the station's `candidates.jsonl`, 6,540 rows
spell sub/superscripts as `<sub>`/`<sup>` markup and 55 carry Unicode characters.

What is asserted here, and nothing else:

  * both prompts carry the **same string** - the constant from `ai_findings` - and a prompt that
    keeps its own copy is caught (the negative controls below run against the real checks);
  * the rule mandates the markup and forbids the Unicode sub/superscript ranges, and no character
    from those ranges survives anywhere in the prompts that are actually sent;
  * the table channel asks for the table's own lines and says nothing when there is no table - and
    the reading is never told that a crop exists, because the reading is not the side that cuts.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

import confirm_dispute  # noqa: E402
from qbr import ai_findings, reread, vision  # noqa: E402


#: The ranges the rule forbids: Superscripts and Subscripts (`U+2070-U+209F`, which holds both the
#: raised and the lowered forms), the superscript modifier letters of Phonetic Extensions
#: (`U+1D2C-U+1D6A`; the LATIN SUBSCRIPT SMALL LETTERs `U+1D62-U+1D6A` sit inside it), and the
#: three superscripts that predate them in Latin-1 (`²`, `³`, `¹`).
SUBSUP_RANGES = ((0x2070, 0x209F), (0x1D2C, 0x1D6A), (0x00B2, 0x00B3), (0x00B9, 0x00B9))


def prompts():
    """Every system prompt that asks a model to transcribe a paper, as it is actually sent.

    `transcribe_system` is called with its defaults because that is how the loop sends it, and it
    is `reread.SYSTEM` plus the human channels - so it is the text a page read really carries.
    """
    return {
        "reread.SYSTEM": reread.SYSTEM,
        "vision.DISPUTE_SYSTEM": vision.DISPUTE_SYSTEM,
        "confirm_dispute.transcribe_system()": confirm_dispute.transcribe_system(),
    }


def test_the_italic_rule_is_in_every_transcription_prompt():
    """業主 2026-09-25：「管線好像開始抓『斜體字』……這部分可以加入」。

    斜體是排版而不是字：紙本把學名排成斜體（那一本微生物學紙本 157 個 `Helvetica-Oblique` 片段），
    抽取器把排版丢了，而判讀是唯一看得到那一頁的東西。所以「這裡是斜體」寫在提示詞裡，且只加標記、
    字元一個都不動——與上下標同一條紅線。三個送出去的提示詞都要有它（漏掉一個就是那條路仍然收不到）。
    """
    for name, prompt in prompts().items():
        assert "<i>" in prompt and "斜體" in prompt, name
        assert ai_findings.ITALIC_MARKUP_RULE in prompt, name
    # 負對照：把那一條拿掉之後，這條檢查真的會紅（不是靠別的句子裡的斜體字樣通過）。
    stripped = {name: prompt.replace(ai_findings.ITALIC_MARKUP_RULE, "")
                for name, prompt in prompts().items()}
    assert not any(ai_findings.ITALIC_MARKUP_RULE in prompt for prompt in stripped.values())


def carriers(root, name):
    """The `.py` files under `root` whose source **defines** `name = …`, as `src/...` paths.

    A prompt that spells the rule out instead of importing it names nothing, which is the failure
    the test below is for; so what is counted is the module that *owns* the text, by definition.
    """
    found = []
    for dirpath, _dirs, files in os.walk(root):
        for filename in sorted(files):
            if not filename.endswith(".py"):
                continue
            path = os.path.join(dirpath, filename)
            with open(path, encoding="utf-8") as handle:
                if re.search(r"^%s\s*=" % name, handle.read(), re.M):
                    found.append(os.path.relpath(path, root))
    return sorted(found)


def references(root, name):
    """The `.py` files under `root` whose source **names** `name` (`ai_findings.NAME`)."""
    found = []
    for dirpath, _dirs, files in os.walk(root):
        for filename in sorted(files):
            if not filename.endswith(".py"):
                continue
            path = os.path.join(dirpath, filename)
            with open(path, encoding="utf-8") as handle:
                if "ai_findings.%s" % name in handle.read():
                    found.append(os.path.relpath(path, root))
    return sorted(found)


# ------------------------------------------------- one rule, carried verbatim by every prompt

def test_every_transcription_prompt_carries_the_same_rule():
    rule = ai_findings.SUBSCRIPT_MARKUP_RULE
    for name, text in prompts().items():
        assert text.count(rule) == 1, name


def test_the_negative_control_a_prompt_with_its_own_copy_is_caught():
    """The failure this file exists for, run against the same check the test above uses.

    A copy is not a copy because it is meant to be one; it becomes one by being edited in place -
    here by re-spelling one range the way a second author would, exactly the drift that made
    `reread.SYSTEM` and the dispute prompt's old rule 3 disagree about markup.
    """
    rule = ai_findings.SUBSCRIPT_MARKUP_RULE
    drifted = prompts()
    drifted["vision.DISPUTE_SYSTEM"] = drifted["vision.DISPUTE_SYSTEM"].replace(
        rule, rule.replace("U+2070–U+209F", "U+2070-209F"))
    assert drifted["vision.DISPUTE_SYSTEM"] != prompts()["vision.DISPUTE_SYSTEM"]
    missing = [name for name, text in drifted.items() if rule not in text]
    assert missing == ["vision.DISPUTE_SYSTEM"]


def test_the_rule_is_defined_once_and_only_the_owning_module_holds_it():
    """One string in one module. The prompts must name it, not spell it out."""
    src = os.path.join(PKG, "src")
    assert carriers(src, "SUBSCRIPT_MARKUP_RULE") == [os.path.join("qbr", "ai_findings.py")]
    assert carriers(os.path.join(PKG, "scripts"), "SUBSCRIPT_MARKUP_RULE") == []
    # The two modules that own a transcription prompt are the two that use the constant - a prompt
    # built from its own text would not appear here at all.
    assert references(src, "SUBSCRIPT_MARKUP_RULE") == [os.path.join("qbr", "reread.py"),
                                                        os.path.join("qbr", "vision.py")]
    assert carriers(src, "TABLE_LINES_RULE") == [os.path.join("qbr", "ai_findings.py")]


def test_the_negative_control_a_prompt_holding_the_text_is_caught():
    """The check above, on a tree where `vision.py` spells the rule out instead of naming it."""
    fake = tempfile.mkdtemp()
    try:
        fake_src = os.path.join(fake, "src")
        os.makedirs(os.path.join(fake_src, "qbr"))
        for name in ("ai_findings.py", "reread.py", "vision.py"):
            shutil.copy(os.path.join(PKG, "src", "qbr", name),
                        os.path.join(fake_src, "qbr", name))
        assert references(fake_src, "SUBSCRIPT_MARKUP_RULE") == [os.path.join("qbr", "reread.py"),
                                                                 os.path.join("qbr", "vision.py")]
        # Point one prompt at a copy: the constant is gone from that prompt's module and its own
        # literal takes its place, which is exactly the drift the one-copy rule forbids.
        vision_path = os.path.join(fake_src, "qbr", "vision.py")
        with open(vision_path, encoding="utf-8") as handle:
            body = handle.read()
        body = body.replace("ai_findings.SUBSCRIPT_MARKUP_RULE",
                            '"""%s"""' % ai_findings.SUBSCRIPT_MARKUP_RULE)
        with open(vision_path, "w", encoding="utf-8") as handle:
            handle.write(body)
        assert references(fake_src, "SUBSCRIPT_MARKUP_RULE") == [os.path.join("qbr", "reread.py")]
    finally:
        shutil.rmtree(fake, ignore_errors=True)


# ----------------------------------------------- what the rule says, and what it forbids

def test_the_rule_mandates_markup_and_forbids_the_unicode_ranges():
    rule = ai_findings.SUBSCRIPT_MARKUP_RULE
    for wanted in ("<sub>", "</sub>", "<sup>", "</sup>"):
        assert wanted in rule
    for bound in ("U+2070", "U+209F", "U+1D2C", "U+1D6A"):
        assert bound in rule
    assert "大小寫" in rule            # a subscript capital stays a capital
    assert "∞" in rule                 # a symbol is never spelled out
    assert "不可以自己加標點" in rule   # and punctuation is not invented


def test_no_prompt_contains_a_unicode_subsuperscript_character():
    """The rule is a property of the text that was sent, not only of the sentence about it."""
    for name, text in prompts().items():
        offenders = sorted({char for char in text
                            if any(low <= ord(char) <= high for low, high in SUBSUP_RANGES)})
        assert offenders == [], (name, offenders)


# ------------------------------------------------------------- the table channel, and its scope

def test_the_table_channel_asks_for_the_table_s_own_lines():
    rule = ai_findings.TABLE_LINES_RULE
    assert "table_lines" in rule
    for name in ("reread.SYSTEM", "confirm_dispute.transcribe_system()"):
        assert prompts()[name].count(rule) == 1, name
    # The reading must not learn that a crop exists: the cutter is the only side that knows that,
    # and a prompt that asks for a crop asks the model about something the model cannot see.
    assert "裁" not in rule and "crop" not in rule.lower()
    # And it must say nothing at all when the question's body is not a table.
    assert "不要" in rule and "不是表" in rule
    # 一條是紙本的一整列，不是一格一條。站上實測 2026-09-24：`q070` 的讀法把一整張表的九格
    # 寫成九條（`CYP2D6`、`10`、`1`…），紙本量到的是一列一列，於是這張最該截圖的表回 not-found。
    assert "一整列" in rule and "一格" in rule and "行數" in rule


def test_the_reading_passes_the_quoted_lines_through_untouched():
    """The record keeps the reading verbatim (`finding.transcription`), so the channel the cutter
    reads is the model's own list - the parse must not drop or reshape it."""
    reading = reread.parse('{"stem":"下表","options":{"A":"1"},"table_lines":["項目 數值","甲 1"]}')
    assert reading["table_lines"] == ["項目 數值", "甲 1"]
