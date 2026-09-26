# -*- coding: utf-8 -*-
"""把已經寫成 review event 的 correction **寫進抽取檔本身**（文字 → 抽取檔）。

為什麼要有這一支（2026-09-24，owner 的決定）
--------------------------------------------
在它之前，鏈路只通到一半：

    `apply_dispute_repairs.py` / `confirm_dispute.py` 讀紙本，寫一個 append-only 的
    `reset_review` 事件（`correction` + `changes`）；
    `review_state.candidate_payload` 把那個 correction **疊在它回給介面的 copy 上**，所以螢幕上
    看到的字是對的；
    `candidates.jsonl` **從來沒有被改過**——抽取檔裡還是壞的那一份。

後果是可量測的：同一題每一輪都被重新讀到同一個缺陷（事件裡的 `changes` 就是從檔案原文量出來的），
而畫面上的「已解決」只是一個覆蓋顯示，底下那一欄還是壞的。owner 的原話：
「判讀→文字 跟 文字→抽取檔要打通，並且改標籤送到『AI已解決』，我才能知道有沒有改過。」

這一支就是那條沒接上的線：把事件裡的 `correction` 折進 `candidates.jsonl` 的那一欄。

事件的兩種形狀（`applied`）
---------------------------
事件是機器（`repair_dispute_apply`）與人（介面上的 `correct`）共用的同一種紀錄，所以要分得開：

    `applied: "field"`         整欄替換。`changes` 一欄一筆，`from` 是磁碟上那一欄的**全文**、
                               `to` 是紙本讀到的全文；`correction` 帶著同一份新文字。
    `applied: "substitution"`  字元替換。`changes[].from`/`.to` 是一對字元（沒有位置），
                               `correction` 仍然帶著整個欄位的新文字。
    沒有 `applied`（人自己的 `correct`，或 2026-09-24 之前寫的事件）：看它有沒有宣稱「改之前是
                               什麼」。`from != to` 就是一筆宣稱；`from == to`（或根本沒有
                               `changes`）只是一個值，不是一個關於檔案的宣稱。

三道閘門——通過才寫，理由都印在 manifest（`plan_correction`）
-------------------------------------------------------------
1. **整欄替換**：`changes[].from` 必須**等於磁碟上現在那一欄的文字**。不等就拒絕——事件是從
   另一份文字量出來的，照著寫會改掉沒人看過的字（`apply_dispute_repairs.verify` 是同一條規則，
   這裡是它在檔案這一側的版本）。
2. **字元替換**：磁碟上的文字與 `correction` 的差，必須**剛好**是事件宣稱的那幾對字元
   （逐字比對、右對右），而且每一段等長、只有替換（沒有插入／刪除）。文字動過（多一句、少一段、
   改了別的字）都會讓這一條不成立而拒絕。
3. **只有值、沒有宣稱**（人在介面上存的那一種）：值必須是**同一行的另一種讀法**——把空白收攏成
   一個之後，{值, 檔案文字} 中短的那個要**連續出現**在長的那個裡面，而且短的要 ≥ 8 個字、且
   至少占長的 40%。兩個門檻都是必要的：一個字元的值（`A`）是任何字串的子字串，不設門檻它就會
   變成整個欄位。

   **一列的任一欄位被拒，整列不寫**。套一半的 correction 會把題目留在「一半修好」的狀態，而那
   正是 `anchored_page_changes` 拒絕部分修復的同一條理由。

原始文字留在列裡（`parser_original`）
--------------------------------------
被改掉的原文寫進**這一列自己的** `parser_original`（只寫 correction 碰到的欄位），因為伺服器
顯示「原始抽取」時讀的就是它（`review_state.candidate_payload`）。已經有存過原文的欄位**不覆蓋**：
再修一次的時候，磁碟上的文字已經是改過的字，把它存成「原文」等於把唯一一份證據換掉。列裡有
`parser_original` 的欄位由伺服器優先採用，兩邊對「原文」的定義就只有這一個。

撤銷（withdrawal）——機器改錯的字要能退回原狀
--------------------------------------------
2026-09-24 晚上量到的形狀：折進去的 291 列裡有 4 題把 `癇` 讀成 `癲`（`抗癲癇` → `抗癲癲`），
另外五列各換了一個 CJK 字（`症`→`病`、`當`→`宜`、`及`→`口`、`中`→`可`），而兩個引擎都說
`TRUST`。改已經落地的字需要一個反向動作，而那個動作**是一個事件**（append-only，由
`apply_dispute_repairs.py` 寫；這一支不寫事件）：

    {"reviewer": "repair_dispute_apply", "action": "reset_review",
     "candidate_key": "<key>", "applied": "withdrawn",
     "withdraw": ["stem", "option A"], "correction": null,
     "created_at": "<station-local ISO seconds>", "why": "<one line naming the machine evidence>"}

這一支做的是它這一側的那一半：把 `withdraw` 列出的每一欄**還原成這一列 `parser_original` 裡同一欄
的值**，`parser_original` 原樣留著——它同時是「抽取器讀到什麼」與「機器當初錯寫成什麼」的紀錄，
而還原的 `before` 正是那個錯的字，永遠不可以被存成原文。欄位已經等於原文 → 這一列沒有東西要寫
（所以同一份 log 的第二次跑是 0 筆，不是拒絕）；`withdraw` 指名了這一列沒有的欄位、或這一列根本
沒有 `parser_original`（或那一欄沒有存到原文）→ **整列拒絕**，理由印在 manifest，不是丟例外。

順序就是這一條：log 是 append-only，所以**行的位置就是時間**，折疊是「最後一筆關於這一列文字的
事件為準」。修好之後又撤銷 → 撤銷贏（還原）；撤銷之後又有更新的修復 → 修復贏（照原本的三道閘門
寫）；撤銷之後人自己改的字比撤銷晚 → 那一筆贏（人自己的修正才是最後一句話，跟 `block` 抹不掉
correction 是同一條規則）。列上的標籤（`applied`／`applied_kind`）是**伺服器投影**的決定
（`review_ui.queue_view.machine_applied_kind`），這一支不寫標籤、不寫任何 review 狀態，只把文字
落到那一欄。

寫入是原子的
------------
同一個目錄裡的臨時檔 + `os.replace`：抽取檔不是就地截斷再寫，所以中途斷掉不會留下一份半截的
候選檔（79090 列的檔案，沒有這一步就沒有重跑以外的復原方式）。沒有東西要寫的時候**不建臨時檔**，
所以「這一輪沒有 correction」時整個目錄連一個位元組都沒動。除了抽取檔與那個臨時檔，這一支不碰
佇列裡的任何檔案，也不刪任何既有的備份。

治理界線
--------
    G3. 這一支**會改抽取檔本身**（`candidates.jsonl`），所以 `--apply` 是必要的；預設是 dry run，
    而且它改的是**內容**、不是判定：它不寫任何 review event，不 accept、不 block，也不碰
    `reviewer` 欄位。事件才是判定，這一支只是把已經決定的文字落地。

    人的 guardrail 沒有被繞過：機器寫進去的題目落在「AI已修改」（`repair_pending`），人看到不對
    就 `block` + 註解打回來；人自己的 `correct` 走同一條路（事件 → 檔案），所以人的修正也會落到
    檔案裡，而不是只疊在畫面上。
"""
from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
ROOT = os.path.dirname(PKG)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, HERE)

from scripts.serve_question_review_ui import REPAIR_REVIEWER_PREFIXES, normalized_correction  # noqa: E402

#: The two shapes the writer tags its event with. Absent means "look at what the event claims".
APPLIED_FIELD = "field"
APPLIED_SUBSTITUTION = "substitution"

#: `applied == "withdrawn"`: the machine is taking back text it wrote, and `withdraw` names the fields
#: it takes back. The value doubles as the plan's `mode` for a restore; the UI's applied-kind table
#: uses the same value.
APPLIED_WITHDRAWN = "withdrawn"

#: A withdrawal is recognised by the machine's reviewer **prefixes** plus `applied`, so a person's
#: event can never withdraw a repair by accident - and this tool never writes one (the producers do).
#:
#: Prefixes rather than one id, and that is a measured correction (2026-09-25): the first version of
#: this rule named `repair_dispute_apply` alone, which was right while the dispute applier was the
#: only producer. `apply_experience_repairs.py` writes repairs too (from a *person's confirmed
#: change* rather than from a detector), so an id-shaped rule would have landed its repairs but
#: silently ignored its withdrawals - the machine's own correction of its own wrong repair would be
#: the one thing the file refused to write. The list is imported from the server's public
#: `REPAIR_REVIEWER_PREFIXES` export; a person's ids (`local`, `linear_v2`) never match it.


#: The other kind of line this tool folds: an event that carries text (a machine repair, or a person's
#: own `correct`). Named so `latest_revisions` and `plan_row` cannot drift apart on a string literal.
REVISION_CORRECTION = "correction"

#: A value-only correction (a person's `correct` event carries the value they typed, not a claim
#: about what the file held) has to pass both floors before it may replace a field. The substring
#: clause alone would let a one-character value through - `A` is a substring of everything - and the
#: coverage clause alone would let an unrelated line through when the field is short.
MIN_CONTAINED = 8
MIN_COVERAGE = 0.40

_WHITESPACE_RUN = re.compile(r"\s+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queue", required=True,
                        help="queue root; candidates and the review log live under its review-ui/")
    parser.add_argument("--candidates", help="the extraction file; default <queue>/review-ui/"
                                             "candidates.jsonl")
    parser.add_argument("--events", help="review-event log; default <queue>/review-ui/"
                                         "question_review_events.jsonl")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true",
                      help="write the extraction file (temp file in the same directory + os.replace)")
    mode.add_argument("--dry-run", action="store_true",
                      help="plan only and print the manifest; this is the default")
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def latest_revisions(path: Path) -> dict[str, dict]:
    """`candidate_key -> {"kind", "event"}`: the last line of the log about that row's text.

    Two kinds of line count, and both are folds over the **same append-only log in file order** (a
    line's position is its time; the log is never rewritten):

    * `"correction"` — an event that carries a correction: a machine repair, or a person's own
      `correct`. Not "the latest event", and the difference matters: a `block` a person wrote after a
      repair is the guardrail working (they looked at the repaired text and bounced it back), not a
      withdrawal of the text change - so it must not erase the correction. What supersedes a
      correction is another correction, and a person's `correct` is exactly that: it is the last line
      of this fold, so the reviewer's own fix lands in the file too.
    * `"withdrawn"` — a machine (`repair_`-prefixed reviewer) event with `applied == "withdrawn"`: it is
      taking back text it wrote, and `withdraw` names the fields. It is a revision like any other, so
      the same "last line wins" applies: a repair followed by a withdrawal restores, and a withdrawal
      followed by a newer repair (or by a person's `correct`) keeps that later text.

    The correction is normalized here, with the same function the server normalizes with, so an event
    whose `correction` is empty after normalization (an image/asset action) is not a correction at all
    for either of us.
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
            if not isinstance(event, dict):
                continue
            key = event.get("candidate_key")
            if not key:
                continue
            if str(event.get("reviewer") or "").strip().startswith(REPAIR_REVIEWER_PREFIXES) and \
                    str(event.get("applied") or "").strip().lower() == APPLIED_WITHDRAWN:
                latest[str(key)] = {"kind": APPLIED_WITHDRAWN, "event": event}
            elif normalized_correction(event.get("correction")):
                latest[str(key)] = {"kind": REVISION_CORRECTION, "event": event}
    return latest


def spaced(text: str) -> str:
    """The text with every run of whitespace collapsed to one space.

    The person's value and the stored text are two renderings of the same line, and where a line
    broke is a typesetting detail: `請選出\n最適合` and `請選出 最適合` are the same line. Folding
    the runs (rather than removing the whitespace) keeps the characters that are actually there in
    the comparison, so a value that only differs in spacing still matches and a value that differs
    in content still does not.
    """
    return _WHITESPACE_RUN.sub(" ", str(text or "")).strip()


def containment_reason(stored: str, value: str):
    """`(ok, why)` for a value that claims nothing about the text it is replacing.

    A person edits the line they were shown, so the value is the same line at a second reading: the
    extraction invented a run the paper does not print (their value is a piece of the stored text) or
    dropped a run the paper prints (the stored text is a piece of their value). Two strings where
    neither is a piece of the other are not one line at two readings - the row has moved since the
    event was written, and replacing a field nobody has looked at with text about something else is
    the one outcome this must never produce.

    `why` names the clause that failed, because the manifest is read by a person deciding whether to
    redo the edit.
    """
    left, right = spaced(stored), spaced(value)
    if not left or not right:
        return False, "其中一邊是空的（空的欄位沒有「同一行」可言）"
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    if short not in long:
        return False, ("短的字串沒有連續出現在長的字串裡（%d 與 %d 個字）——兩段不是同一行的兩種讀法"
                       % (len(short), len(long)))
    if len(short) < MIN_CONTAINED:
        return False, ("短的字串只有 %d 個字（< %d）：一個太短的值可以出現在任何地方"
                       % (len(short), MIN_CONTAINED))
    if len(short) < MIN_COVERAGE * len(long):
        return False, ("短的字串只占長的字串的 %d%%（< %d%%）：留下來的部分太少，不足以說是同一行"
                       % (round(100.0 * len(short) / len(long)), round(100 * MIN_COVERAGE)))
    return True, "值連續出現在檔案文字的裡面（%d/%d 個字）" % (len(short), len(long))


def diff_pairs(before: str, after: str):
    """The per-character `(from, to)` pairs a pure substitution explains, or `None` if it is not one.

    Equal length per run and replace-only, the same two clauses `anchored_page_changes` uses on the
    page-read path: an insert or a delete moved every later position, so what follows it is a
    different line and a diff about it is not evidence about this one.
    """
    pairs = set()
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag != "replace" or (i2 - i1) != (j2 - j1):
            return None
        pairs.update(zip(before[i1:i2], after[j1:j2]))
    return pairs


def declared_pairs(changes: list[dict]):
    """The per-character pairs the event declares, or `None` when a run is not a substitution."""
    pairs = set()
    for change in changes:
        before = str(change.get("from") or "")
        after = str(change.get("to") or "")
        if not before or not after or len(before) != len(after):
            return None
        pairs.update(zip(before, after))
    return pairs


def plan_mode(applied, field_changes: list[dict], current: str) -> str:
    """Which of the three gates this field's changes put it behind - chosen by what the event claims.

    `applied` is the writer's own label and wins when it is there. Without it the claim is read off
    `changes`: a single change whose `from` is the file's own text is a whole-field replacement, any
    other pair of different strings is a character substitution, and `from == to` (or no changes at
    all) is a value with no claim about the file - never guessed at, only classified.
    """
    if applied == APPLIED_FIELD:
        return APPLIED_FIELD
    if applied == APPLIED_SUBSTITUTION:
        return APPLIED_SUBSTITUTION
    claims = [c for c in field_changes
              if str(c.get("from") or "") != str(c.get("to") or "")]
    if not claims:
        return "value"
    if len(claims) == 1 and str(claims[0].get("from") or "") == current:
        return APPLIED_FIELD
    return APPLIED_SUBSTITUTION


def option_key(field: str) -> str:
    return field[len("option "):].strip().upper()[:1]


def row_field(row: dict, field: str):
    """The row's own text for a correction field, or `None` when the row has no such field."""
    if field == "stem":
        value = row.get("stem")
        return None if value is None else str(value)
    for option in row.get("options") or []:
        if not isinstance(option, dict):
            continue
        if str(option.get("key") or "").strip().upper()[:1] == option_key(field):
            return "" if option.get("text") is None else str(option["text"])
    return None


def correction_fields(correction: dict) -> dict:
    """`field -> the new text` for the fields this file is actually made of: the stem and the options.

    The rest of `normalized_correction`'s payload (answer, group, images, visual review) is a
    projection detail or another artifact's business; this tool writes the text a reader reads, and
    the two contract fields are `stem` and `options`.
    """
    fields = {}
    if "stem" in correction:
        fields["stem"] = str(correction["stem"])
    for option in correction.get("options") or []:
        if not isinstance(option, dict):
            continue
        key = str(option.get("key") or "").strip().upper()[:1]
        if key:
            fields["option %s" % key] = "" if option.get("text") is None else str(option["text"])
    return fields


def plan_correction(row: dict, event: dict) -> dict:
    """What one correction event does to one row, as `{writes, skipped, refused, notes}`.

    Every refusal names the field and the clause that failed. A row with any refusal gets no writes:
    a half-applied correction leaves the question in a state nobody asked for.
    """
    plan = {"writes": {}, "skipped": [], "refused": [], "notes": [], "withdraw": []}
    correction = normalized_correction(event.get("correction"))
    fields = correction_fields(correction)
    if not fields:
        return plan
    applied = event.get("applied")
    if applied not in (None, APPLIED_FIELD, APPLIED_SUBSTITUTION):
        plan["refused"].append("事件帶了不認識的 applied 值 %r——這一支不敢猜它的意思" % (applied,))
        return plan
    changes = [c for c in (event.get("changes") or [])
               if isinstance(c, dict) and c.get("field")]
    for field, corrected in fields.items():
        field_changes = [c for c in changes if c.get("field") == field]
        if changes and not field_changes:
            # The event declares exactly which fields it edits. A field it does not name is context
            # carried by the whole-field correction, not a claim - writing it would let an old
            # correction overwrite a field that has been rebuilt since. Context still has to *agree*
            # with disk, though: a whole-field reading carries the values it read for the fields it
            # did not edit (the `correction` the server folds is the complete list), so a
            # disagreement there means the reading was measured against another version of the file.
            if applied == APPLIED_FIELD:
                context = row_field(row, field)
                if context is None:
                    plan["refused"].append("%s：這一行沒有這個欄位" % field)
                elif context != corrected:
                    plan["refused"].append(
                        "%s：applied=field 沒有列出這一欄的 changes，而它帶來的值與磁碟不符"
                        "（磁碟「%s」≠ 事件「%s」）"
                        % (field, excerpt(context), excerpt(corrected)))
                else:
                    plan["skipped"].append(field)
            continue
        current = row_field(row, field)
        if current is None:
            plan["refused"].append("%s：這一行沒有這個欄位" % field)
            continue
        if current == corrected:
            plan["skipped"].append(field)
            continue
        mode = plan_mode(applied, field_changes, current)
        if mode == APPLIED_FIELD:
            if len(field_changes) != 1:
                plan["refused"].append("%s：整欄替換帶了 %d 筆 changes（一欄只能有一筆）"
                                       % (field, len(field_changes)))
                continue
            claimed = str(field_changes[0].get("from") or "")
            if claimed != current:
                plan["refused"].append(
                    "%s：磁碟上是「%s」，不是事件說的「%s」（事件已過期）"
                    % (field, excerpt(current), excerpt(claimed)))
                continue
        elif mode == APPLIED_SUBSTITUTION:
            measured = diff_pairs(current, corrected)
            wanted = declared_pairs(field_changes)
            if measured is None:
                plan["refused"].append(
                    "%s：磁碟文字與 correction 的差不是逐字替換（長度改變／插入／刪除）——事件已過期"
                    % field)
                continue
            if wanted is None:
                plan["refused"].append("%s：changes 裡有一段不是等長替換，不能當成字元替換" % field)
                continue
            if measured != wanted:
                plan["refused"].append(
                    "%s：磁碟上的差是 %s，事件說的是 %s（事件已過期）"
                    % (field, pairs_text(measured), pairs_text(wanted)))
                continue
        else:
            ok, why = containment_reason(current, corrected)
            if not ok:
                plan["refused"].append("%s：%s" % (field, why))
                continue
            plan["notes"].append("%s：%s" % (field, why))
        plan["writes"][field] = {"before": current, "after": corrected, "mode": mode}
    if plan["refused"]:
        plan["writes"] = {}
        plan["notes"] = []
    return plan


def parser_original_field(stored: dict, field: str):
    """`parser_original`'s own text for a correction field, or `None` when it never stored one.

    The two shapes are the row's own two: `stem` sits under its own name, an option is an entry of the
    stored option list, keyed like the row's. Only a string counts - a missing key, `null` and any
    other type all mean "this field's original was never recorded", which is a refusal, not a value.
    """
    if field == "stem":
        value = stored.get("stem")
        return value if isinstance(value, str) else None
    for option in stored.get("options") or []:
        if isinstance(option, dict) and \
                str(option.get("key") or "").strip().upper()[:1] == option_key(field):
            text = option.get("text")
            return text if isinstance(text, str) else None
    return None


def plan_withdrawal(row: dict, event: dict) -> dict:
    """What one withdrawal does to one row, as `{writes, skipped, refused, notes, withdraw}`.

    A withdrawal is the machine taking back text it wrote, so the target is not a new reading: it is
    `parser_original`, the row's own record of what the extractor read. That is why no text gate
    applies here - there is no second reading to compare against, only a step back to a value the row
    already holds, and the three gates exist to decide whether a *reading* may replace text.

    What can refuse is the record itself, because a restore with nothing to restore from must be
    visible rather than silent: a field this row does not have, a row with no `parser_original` at all,
    or a named field that was never stored as an original (the fold stores only the fields a correction
    touched, so a withdrawal may name one the file never recorded). A field that already equals its
    original is `skipped`, and skipping is what makes the second run of the same log a no-op instead of
    a refusal.

    Like `plan_correction`, **one refusal empties the whole row**: restoring half of a withdrawal leaves
    the row in a state nobody asked for, and half of a step back is worse than none of it.
    """
    plan = {"writes": {}, "skipped": [], "refused": [], "notes": [], "withdraw": []}
    named = list(dict.fromkeys(
        field for field in (str(name or "").strip() for name in (event.get("withdraw") or []))
        if field))
    if not named:
        plan["refused"].append("這一筆撤銷沒有列出任何欄位（withdraw 是空的）——不知道要還原什麼")
        return plan
    stored = row.get("parser_original")
    stored = stored if isinstance(stored, dict) else {}
    for field in named:
        current = row_field(row, field)
        if current is None:
            plan["refused"].append("%s：這一行沒有這個欄位，不能還原" % field)
            continue
        original = parser_original_field(stored, field)
        if original is None:
            plan["refused"].append(
                "%s：這一列沒有存到這一欄的 parser_original 原文，沒有東西可以還原" % field)
            continue
        if current == original:
            plan["skipped"].append(field)
            continue
        plan["writes"][field] = {"before": current, "after": original, "mode": APPLIED_WITHDRAWN}
        plan["withdraw"].append(field)
    if plan["refused"]:
        plan["writes"] = {}
        plan["notes"] = []
        plan["withdraw"] = []
    return plan


def plan_row(row: dict, revision: dict) -> dict:
    """What the log's last word about this row does to it.

    A correction writes text into the field; a withdrawal takes the machine's text back to
    `parser_original`. The two are the same kind of plan (`writes`／`skipped`／`refused`), so
    everything downstream - the manifest, the all-or-nothing refusal, the atomic write - has one path.
    """
    if revision.get("kind") == APPLIED_WITHDRAWN:
        return plan_withdrawal(row, revision["event"])
    return plan_correction(row, revision["event"])


def excerpt(text: str, limit: int = 40) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "…"


def pairs_text(pairs) -> str:
    return "；".join("%s→%s" % pair for pair in sorted(pairs)) or "（沒有差）"


def _set_field_text(row: dict, field: str, text: str) -> None:
    """Write one field's text into the row: the stem, or the option the field names."""
    if field == "stem":
        row["stem"] = text
        return
    for option in row.get("options") or []:
        if isinstance(option, dict) and \
                str(option.get("key") or "").strip().upper()[:1] == option_key(field):
            option["text"] = text


def rewrite_row(row: dict, plan: dict) -> dict:
    """The row with the correction's text written in, and the replaced text kept as `parser_original`.

    Only the fields this correction touches get an original, because the original is the evidence for
    *this* edit; the rest of the row still says what the extractor read. A field that already has a
    stored original keeps it: by now the row's own text is the corrected one, so re-storing it would
    overwrite the only record of what the extractor produced with text that is no longer original.

    A withdrawal is a step *back*: the text goes to the value `parser_original` already records, so
    there is nothing new to remember and the record is left exactly as it is. Storing a restore's
    `before` would be the worst case of all - it is the machine's **wrong** repair, and
    `parser_original` is the one record of what the extractor read.
    """
    if plan.get("withdraw"):
        for field, edit in plan["writes"].items():
            _set_field_text(row, field, edit["after"])
        return row
    additions = {}
    if "stem" in plan["writes"]:
        additions["stem"] = plan["writes"]["stem"]["before"]
    if any(field.startswith("option ") for field in plan["writes"]):
        additions["options"] = copy.deepcopy(row.get("options") or [])
    for field, edit in plan["writes"].items():
        _set_field_text(row, field, edit["after"])
    stored = row.get("parser_original")
    stored = dict(stored) if isinstance(stored, dict) else {}
    for name, value in additions.items():
        if name not in stored:
            stored[name] = value
    if stored:
        row["parser_original"] = stored
    return row


def _write_row(handle, row: dict, ending: str) -> None:
    # `sort_keys` matches the queue builder's own writer (`build_review_queue`), so a rewritten row
    # sits in the file in the same shape as every row around it.
    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + ending)


def _ending_of(line: str) -> tuple[str, str]:
    """`(line without its ending, the ending)`, so a rewritten row keeps the file's own line ending."""
    ending = ""
    if line.endswith("\n"):
        ending = "\r\n" if line.endswith("\r\n") else "\n"
    return line[:len(line) - len(ending)], ending


def write_corrected(path: Path, plans: dict) -> int:
    """Rewrite `path` with the planned rows' text, **atomically**; returns the rows it wrote.

    Same directory, temp file, `os.replace`: the extraction file is never truncated in place, so an
    interrupted run leaves either the old file or the new one and never a half-written queue. Only the
    rows that have a plan are re-dumped; every other line - including blank ones and a line that
    cannot be parsed - is copied through byte for byte, so a rewritten queue differs from the old one
    exactly where the corrections say it should.
    """
    descriptor, temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    written = 0
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as out:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    body, ending = _ending_of(line)
                    row = None
                    if body.strip():
                        try:
                            row = json.loads(body)
                        except json.JSONDecodeError:
                            row = None
                    plan = plans.get(str(row.get("candidate_key"))) if isinstance(row, dict) else None
                    if plan:
                        _write_row(out, rewrite_row(row, plan), ending)
                        written += 1
                    else:
                        out.write(line)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
    return written


def main() -> int:
    args = parse_args()
    queue_dir = os.path.join(args.queue, "review-ui")
    candidates_path = Path(args.candidates or os.path.join(queue_dir, "candidates.jsonl"))
    events_path = Path(args.events or os.path.join(queue_dir, "question_review_events.jsonl"))
    if not candidates_path.is_file():
        print("找不到 candidates.jsonl：%s" % candidates_path, file=sys.stderr)
        return 2
    if not events_path.is_file():
        print("找不到 review event log：%s" % events_path, file=sys.stderr)
        return 2

    revisions = latest_revisions(events_path)
    corrections = sum(1 for revision in revisions.values()
                      if revision["kind"] == REVISION_CORRECTION)
    withdrawals = len(revisions) - corrections
    before = sha256_file(candidates_path)
    plans: dict[str, dict] = {}
    written, already, refused = [], [], []
    scanned = unparsable = 0
    # The planning pass reads the file and writes nothing. The temp file is only created - on the
    # second pass - when there is at least one row to write, so a round with nothing to write leaves
    # the directory and the file's inode untouched (measured: the loop runs every 30 minutes forever
    # and almost every round has nothing to do).
    with candidates_path.open(encoding="utf-8") as handle:
        for line in handle:
            body, _ending = _ending_of(line)
            if not body.strip():
                continue
            scanned += 1
            try:
                row = json.loads(body)
            except json.JSONDecodeError:
                unparsable += 1
                continue
            if not isinstance(row, dict):
                continue
            revision = revisions.get(str(row.get("candidate_key")))
            if not revision:
                continue
            plan = plan_row(row, revision)
            if plan["refused"]:
                refused.append((row.get("candidate_key"), plan["refused"]))
                continue
            if not plan["writes"]:
                already.append((row.get("candidate_key"), plan["skipped"]))
                continue
            key = str(row.get("candidate_key"))
            plans[key] = plan
            written.append((key, plan))

    print("queue        : %s" % args.queue)
    print("candidates   : %s" % candidates_path)
    print("events file  : %s" % events_path)
    print("sha256 before: %s" % before)
    print("rows scanned : %d" % scanned)
    print("with a correction: %d" % corrections)
    print("with a withdrawal: %d" % withdrawals)
    if unparsable:
        print("unparsable lines : %d（原樣保留）" % unparsable)
    print("to write     : %d" % len(written))
    for key, plan in written:
        for field, edit in plan["writes"].items():
            if edit["mode"] == APPLIED_WITHDRAWN:
                # A restore is not a substitution: its two sides have different lengths as a rule
                # (`diff_pairs` would be `None`), and what a reader needs to see is the direction.
                detail = "還原成 parser_original（%d→%d 個字）" % (len(edit["before"]),
                                                                    len(edit["after"]))
            elif edit["mode"] == APPLIED_FIELD:
                detail = "整欄替換（%d→%d 個字）" % (len(edit["before"]), len(edit["after"]))
            else:
                detail = pairs_text(diff_pairs(edit["before"], edit["after"]) or set())
            print("  %-44s %-12s [%s] %s"
                  % (str(key).replace("moex:", ""), field, edit["mode"], detail))
        for note in plan["notes"]:
            print("  %-44s %s" % ("", note))
    restored = [(key, plan) for key, plan in written if plan["withdraw"]]
    if restored:
        print("withdrawn    : %d 欄／%d 列（機器改錯，還原成 parser_original；標籤由投影決定）"
              % (sum(len(plan["withdraw"]) for _key, plan in restored), len(restored)))
        for key, plan in restored:
            print("  %-44s %s" % (str(key).replace("moex:", ""), "、".join(plan["withdraw"])))
    if already:
        print("already correct: %d（沒有東西要寫）" % len(already))
    if refused:
        print()
        print("REFUSED (%d) - not written:" % len(refused))
        for key, why in refused:
            print("  %-44s %s" % (str(key).replace("moex:", ""), "；".join(why)[:200]))

    if not args.apply:
        print()
        print("dry run；pass --apply to write the file")
        return 0
    print()
    if not written:
        print("沒有東西要寫：%s 一個位元組都沒動" % candidates_path)
        print("sha256 after : %s" % before)
        return 0
    rows = write_corrected(candidates_path, plans)
    print("atomic write : %s（%d 列，同目錄臨時檔 + os.replace）" % (candidates_path, rows))
    print("sha256 after : %s" % sha256_file(candidates_path))
    print("寫入的是內容，不是判定：這一支不寫 review event、不動 reviewer。原文留在每一列的 "
          "parser_original。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
