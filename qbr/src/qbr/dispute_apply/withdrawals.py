"""Withdrawals, standing rejections and the repair event shape.

Extracted verbatim from `scripts/apply_dispute_repairs.py` so one file is no longer 2,321 lines.
`apply_dispute_repairs` re-exports every name here; that indirection is deliberate and is why the
test files that load the script by path keep working unchanged. No behaviour was changed.
"""

from __future__ import annotations

import json
from pathlib import Path

from qbr.dispute_apply.text import (
    is_machine_event,
)


#: Kinds whose target character is carried by the dispute itself.
#: `flat-offset` is deliberately absent - it is repaired in `extract._body_centre`, at the reading.
APPLICABLE = ("substituted-ideograph",)
#: The actions a person's decision can stand at. Used to *report* the previous state, and by
#: `standing_rejections` to find the questions path 3 (the whole-field page read) may touch: a
#: `reset_review` is correct for a judged and an unjudged question alike, because it never claims a
#: verdict - but a whole-field rewrite is only allowed where a person already said the text was wrong.
HUMAN_ACTIONS = {"accept", "block", "needs_review", "exclude", "unblock", "reviewed", "correct"}
#: A person's own decisions about the text, as the review log spells them: "this is not right yet".
#: `reset_review` (this tool's own action) and `comment` are deliberately absent.
REJECTING_ACTIONS = ("block", "needs_review")
#: What closes the gate again. The server treats `accept` and `unblock` as the same thing
#: (`action in ('accept', 'unblock')` throughout `review_state.py`), so they are the same thing here.
CLEARING_ACTIONS = ("accept", "unblock")
#: 上限（`MAX_ATTEMPTS`）這一條只數 `block`：業主的循環指令說的是「錯就註解＋**block**」，而
#: `needs_review` 說的是「我無法決定」——那是另一種輸入（`repair_loop.BLOCKING_ACTIONS` 是同一組，
#: 兩邊由 `test_the_cap_counts_the_same_events_the_loop_counts` 釘在一起）。`REJECTING_ACTIONS` 多收
#: `needs_review`，因為那一個問題問的是「這一題現在可不可以被機器整欄改寫」。
#: 兩個問題、兩個集合——把它們混成一個，就會出現「人說無法決定三次，機器就宣告無法判斷」。
ATTEMPT_REJECTING_ACTIONS = ("block",)

#: 業主 2026-09-25 的循環指令：「機器嘗試修改→落到 AI已修改→人審，錯就註解＋block→機器再讀一次並依
#: 註解引導改→再送回 AI已修改→**循環三次之後才送入 AI無法判斷**。」
#:
#: 一次循環＝一筆站著的機器修復被人打回，所以上限是**數出來的**（`rejection_counts`／
#: `repair_loop.fold_review_events` 的 `rejections`），不是另立一個計數器：多一個計數器就是多一個
#: 可以和事件流不一致的數字。人在題目上按接受或放回都會把次數歸零，門就再開（`CLEARING_ACTIONS`）。
MAX_ATTEMPTS = 3

#: 逐欄的拒絕理由裡，哪幾種算「機器先前寫的這一欄現在站不住了」——也就是要撤銷的。
#: 其他的拒絕（過期、偵測器衝突、讀不出來的 `▢`、兩個量測不過）說的是「這一輪不要寫」，
#: 不是「上一次寫錯了」，所以不撤銷任何東西。
FIELD_WITHDRAW_TRIGGERS = ("systematic-pair", "unicode-sub-sup", "markup-fidelity")

#: 撤銷事件裡 `withdraw` 那一欄的欄位名，跟 `correction` 的欄位名一樣（`stem`／`option A`）。
WITHDRAWN_LABEL = "已還原（機器改錯）"


def standing_rejections(path) -> set[str]:
    """The questions a person rejected and nobody has accepted since.

    This is the gate on the whole-field page read (path 3 in the module docstring), and it is a scan
    rather than "the latest event", for two measured reasons:

    * this tool writes a `reset_review` **after** a person's `block` - that is what it is for - and the
      projection is last-line-wins, so the latest event of an already-repaired question is the
      machine's own reset, not the `block`;
    * `accept` really does close the gate again. Measured 2026-09-24 on the mirror: 396 `block`
      events over 323 questions, of which 37 were later `accept`ed, plus 2 `needs_review`. Rewriting
      the text of one of those 37 would be this tool undoing a person's acceptance.

    `needs_review` counts exactly like `block` - both say "a person looked and this is not right yet".
    `accept` and `unblock` are the same thing to the server (`review_state.py` treats
    `action in ('accept', 'unblock')` as accepted everywhere), so they are the same thing here.
    """
    standing: dict[str, str] = {}
    path = Path(path)
    if not path.exists():
        return set()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key, action = event.get("candidate_key"), event.get("action")
            if key and action in REJECTING_ACTIONS + CLEARING_ACTIONS:
                standing[key] = action
    return {key for key, action in standing.items() if action in REJECTING_ACTIONS}


def rejection_counts(path) -> dict[str, int]:
    """`{candidate_key: 已退次數}`：機器修復站過、被人打回、而還沒被人接受或放回的次數。

    這是 `repair_loop.fold_review_events` 的 `rejections` 的同一個定義（同一個「先有一筆站著的機器
    修復」前置、同一組 `ATTEMPT_REJECTING_ACTIONS`／`CLEARING_ACTIONS`），寫在這裡而不是借用那份
    折疊，是因為寫入端（`dispute_apply.cli`）只拿得到事件流：折疊住在 `qbr/scripts/`（迴圈那一層），
    這一支住在 `qbr/src/`。兩者必須數出同一個數字，這一條由
    `qbr/tests/test_apply_dispute_repairs.py::test_the_cap_counts_the_same_events_the_loop_counts`
    在混合事件流上釘住（數錯了，上限就會在錯誤的題目上關門）。
    """
    counts: dict[str, int] = {}
    standing: dict[str, bool] = {}
    path = Path(path)
    if not path.exists():
        return counts
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key, action = event.get("candidate_key"), event.get("action")
            if not key:
                continue
            applied = str(event.get("applied") or "").strip().lower()
            if applied and applied != "withdrawn":
                standing[key] = True
            if action in ATTEMPT_REJECTING_ACTIONS and standing.get(key):
                counts[key] = counts.get(key, 0) + 1
            elif action in CLEARING_ACTIONS:
                counts[key] = 0
    return {key: count for key, count in counts.items() if count}


def build_repair_event(question_key: str, subs: list[dict], correction: dict,
                       previous: dict | None, reviewer: str, created_at: str,
                       crop=None, applied: str | None = None,
                       normalisation: bool = False, lead: str | None = None,
                       source: str = "qbr_dispute_apply") -> dict:
    """One `reset_review` event that carries the repair.

    A single event of this action, and not a `correct` followed by a `reset_review`, and the reason
    is measured rather than stylistic. `load_review_events` puts every action that is not a group or
    reset action into `latest`, and `review_projection` calls that "reviewed". A machine `correct`
    on a question nobody has judged therefore makes it read as **已看過** - a machine event
    presented as a person's decision, which is the one thing AGENTS.md forbids outright ("an agent
    must never impersonate a human reviewer").

    `reset_review` is the action that does the two things the repair needs and nothing more: the
    server reads the correction from `latest_reset_review` as well as `latest` (so the fixed text is
    served), and the projection puts the question in `repair_pending` - **修復後待複核**, "repaired,
    waiting for a person" - which is what actually happened. It neither claims a decision nor hides
    the question.

    `previous_action` and its notes travel with the event so the reviewer can see what they had
    decided before the text moved underneath them; `_reaffirm_standing_action` is not involved
    because no verdict is being written here.

    Two fields exist for the reader of the event rather than for the server:

    * `crop` is the picture the reading was made from, queue-relative, or `None` when the reading had
      none ("unseen evidence is not evidence": the reviewer opening this event has to be able to look
      at the same page);
    * `applied` is `"field"` when the whole field came from the page read and `"substitution"` when
      character-level substitutions were applied. The two never mix in one event, by construction:
      `page_read_substitutions` returns one form or the other, never both. Whoever writes the
      extraction file from this event needs to know which one it is reading, and so does a person.

    Each `changes[]` entry keeps the `{field, from, to}` triple. For a replacement `from` is the whole
    stored field and `to` the whole page field (the shape the UI already renders); for a substitution
    it stays the character pair. The producer that applies these to `candidates.jsonl` verifies
    `from` against the file it is editing, which is why `from` must be exactly the text that was
    there before this repair.

    `lead` and `source` name the **producer** of the repair: the default pair is this tool's own
    (`依紙本判讀整欄替換：` / `qbr_dispute_apply`), and a producer that repairs from a different
    authority passes its own (`apply_experience_repairs.py` repairs from a *person's confirmed
    change* and says so, because the reviewer opening the event has to be able to tell which of the
    two ways this text moved). The event's shape stays this function's either way: one builder, so a
    reader that understands one repair understands all of them.

    `normalisation` is written **only when it holds** (see `is_normalisation`): the repair rewrites a
    radical-block glyph as the character it stands for (`⻑`→`長`), the glyph on screen is identical,
    and only the codepoint was wrong. Measured: 345 of the 354 machine repair events on the station are
    this class, so the reviewer's screen counts them apart from the repairs a person has to look at.
    Absent means "not that class" - the event is not claiming the repair is invisible, only that it is
    not this measured shape.
    """
    changes = [{"field": s["field"], "from": s["before"], "to": s["after"]} for s in subs]
    summary = "；".join("(%s→%s)" % (s["before"][:24], s["after"][:24]) for s in subs)[:120]
    if lead is None:
        lead = "依紙本判讀整欄替換：" if applied == "field" else "依 dispute 的機械證據修復："
    event = {
        "candidate_key": question_key,
        "action": "reset_review",
        "correction": correction,
        "reviewer": reviewer,
        "source": source,
        "repair_kind": "content_change",
        "applied": applied,
        "crop": crop,
        "notes": lead + summary,
        "reset_notes": lead + summary,
        "previous_action": (previous or {}).get("action"),
        "previous_notes": (previous or {}).get("notes") or "",
        "previous_reviewed_at": (previous or {}).get("created_at"),
        "changes": changes,
        "created_at": created_at,
    }
    if normalisation:
        event["normalisation"] = True
    return event


def withdrawal_state(path) -> dict:
    """`candidate_key -> {"human": …, "machine": …}`，每一邊都是日誌裡**最後**的那一筆。

    順序是日誌自己的行序，不是 `created_at`。這是量出來的，不是偏好：工作站的時鐘是 UTC，而這支
    工具蓋的是筆電當地時間，所以今晚 18:06 寫下的修復事件與**在它之後**才發生的人的退件
    （`10:22:39`）比字串會比出相反的結論——字串比較會說人的退件比較舊。日誌是 append-only，
    它的行序才是事情發生的順序（同一個理由寫在 `last_repair_signature`，那裡比的是「最後一筆修復」
    而不是「最後一筆事件」）。

    `human` 只記不是機器寫的事件（`is_machine_event`），`machine` 只記 `applied == "field"` 的
    整欄修復與它寫過的欄位——那是撤銷要還原的那一筆。
    """
    state: dict[str, dict] = {}
    path = Path(path)
    if not path.exists():
        return state
    with path.open(encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = event.get("candidate_key")
            if not key:
                continue
            entry = state.setdefault(key, {"human": None, "machine": None, "repairs": []})
            if not is_machine_event(event):
                entry["human"] = {"index": index, "event": event}
                continue
            if event.get("applied") == "field":
                fields = [str(change.get("field")) for change in event.get("changes") or []
                          if change.get("field")]
                entry["machine"] = {"index": index, "event": event, "fields": fields}
                entry["repairs"].append((index, fields))
    return state


def closed_questions(state: dict) -> dict:
    """`candidate_key -> {"fields", "human"}`：人把機器的整欄改動打回了，機器就該把它還原。

    條件有兩個，缺一不可：這一題**最新**的人的事件是 `block`／`needs_review`，而且在那之前有
    `applied == "field"` 的機器修復（行序）。主人的原話是「剛才上下標亂改的我全部都block」——退件
    本身就是訊號，所以這一條不看判讀、不看條文，只看日誌。

    兩個形狀都會中：人退在最後（`block → 修復 → block`，今晚 49 題），和人退在中間而機器又自己改了
    一次（`block → 修復 → block → 修復`，站上實測 `110101:305:55 q061`）。第二種是回彈：人剛說不要，
    機器就用「文字不同＝簽章不同」的理由又寫了一次。

    **這裡只有一個後果：撤銷。** It used to have two - this same comparison also refused to plan any
    later repair for the question, "這一題等新的決定，機器不再自己改" - and that second half was wrong in
    the way the owner named on 2026-09-24: 「機器改錯就給我重改，為什麼還給我還原回去原本錯的地方」.
    Withdrawing a wrong change restores `parser_original`, which is *also* wrong (it is the flattened
    text the machine was trying to fix), so refusing to try again parks the row on the wrong text
    forever and the reviewer's list stops changing. Bouncing says the *change* was wrong, not that the
    question may never be touched again: the next round re-reads the paper, and a repair that comes
    back as markup that touches nothing but the sub/superscripts is exactly the repair the person was
    asking for. What stops a bounce from becoming a loop is not a permanent refusal; it is that the
    same change is never written twice (`last_repair_signature`, whole-record compare) and that a
    Unicode-form change is refused outright, which the gates above already do.

    人在退件之後又說話（`correct`、`accept`）就不再是這一條的事了，那是他們的下一句話，機器不必猜。

    `fields` 是這一題**所有**整欄修復寫過的欄位：機器在這一題的整欄工作整批還原（生產者會把這些欄位
    還原成該列的 `parser_original`）。
    """
    out = {}
    for key, entry in state.items():
        human, repairs = entry.get("human"), entry.get("repairs") or []
        if not human or not repairs:
            continue
        if human["event"].get("action") not in REJECTING_ACTIONS:
            continue
        if not any(index < human["index"] for index, _fields in repairs):
            continue
        out[key] = {"fields": sorted({field for _index, fields in repairs for field in fields}),
                    "human": human["event"]}
    return out


def existing_withdrawals(path) -> dict:
    """`candidate_key -> {"repair_index", "events"}` for append-only repair/withdrawal history.

    A field set is only an idempotency key for the repair generation it actually reversed. If a later
    machine repair touches the same field and a reviewer rejects it again, the later repair needs a
    new withdrawal event. Each prior withdrawal is therefore associated with the latest whole-field
    machine repair before it, using append order rather than timestamps.
    """
    out: dict[str, dict] = {}
    path = Path(path)
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = event.get("candidate_key")
            if not key:
                continue
            entry = out.setdefault(key, {"repair_index": None, "events": []})
            if is_machine_event(event) and event.get("applied") == "field":
                entry["repair_index"] = index
            elif str(event.get("applied") or "") == "withdrawn":
                fields = frozenset(str(field) for field in event.get("withdraw") or [])
                entry["events"].append({
                    "fields": fields,
                    "repair_index": entry["repair_index"],
                })
    return out


def _withdrawal_is_new(withdrawn_before: dict, key: str, fields) -> bool:
    """Whether the latest machine repair generation needs a withdrawal for these fields."""
    wanted = set(fields)
    entry = withdrawn_before.get(key) or {}
    repair_index = entry.get("repair_index")
    return not any(
        event.get("repair_index") == repair_index
        and wanted <= set(event.get("fields") or ())
        for event in entry.get("events") or ()
    )


def build_withdrawal_event(question_key: str, fields, why: str, reviewer: str,
                           created_at: str) -> dict:
    """One withdrawal event - the shape in the shared contract, and nothing else.

    契約（兩端共用的那一份）說的是：一筆 `reset_review`，`applied == "withdrawn"`，`withdraw` 列出
    要還原的欄位（欄位名跟 `correction` 一樣：`stem`／`option A`），`correction` 是 `null`，`why`
    是一行說明機器的證據。所以這裡不多不少就這幾個鍵——多寫 `source` 或 `changes` 會讓產檔那一邊
    的讀法多一個要猜的東西（它的規則是 `reviewer == "repair_dispute_apply"` 且
    `applied == "withdrawn"`）。

    生產者規則是：某一題**最後**一筆 `repair_dispute_apply` 事件是 `applied == "withdrawn"` 時，
    把它 `withdraw` 的每個欄位還原成該列 `parser_original` 的同一欄，`parser_original` 留著當記錄，
    不寫任何審核狀態，而且是幂等的。所以撤銷事件要排在這一輪所有修復事件**後面**（日誌的順序就
    是生產者讀的順序）。
    """
    return {
        "reviewer": reviewer,
        "action": "reset_review",
        "candidate_key": question_key,
        "created_at": created_at,
        "applied": "withdrawn",
        "withdraw": sorted(str(field) for field in fields),
        "correction": None,
        "why": why,
    }


#: 印與寫的時候，一個題目取哪一條理由當代表（人的退件最重要，其次是系統性字對）。
WITHDRAW_PRIORITY = ("bounce-back", "manual", "systematic-pair", "unicode-sub-sup", "markup-fidelity")


def add_withdrawal(withdrawals: dict, key: str, trigger: str, fields, why: str, note: str = "") -> None:
    """這一題要撤銷的欄位與理由（同一題可以有好幾個觸發，最後合併成一筆事件）。"""
    entry = withdrawals.setdefault(key, {"fields": set(), "triggers": {}, "whys": {}, "notes": {}})
    entry["fields"] |= {str(field) for field in fields}
    seen = entry["triggers"].setdefault(trigger, set())
    seen |= {str(field) for field in fields}
    entry["whys"].setdefault(trigger, why)
    if note:
        entry["notes"].setdefault(trigger, note)


def withdrawal_reason(entry: dict) -> str:
    """這一題事件裡的 `why`：按 `WITHDRAW_PRIORITY` 取第一條觸發的理由。"""
    for trigger in WITHDRAW_PRIORITY:
        if trigger in entry["whys"]:
            return entry["whys"][trigger]
    return "機器的整欄改寫要還原"
