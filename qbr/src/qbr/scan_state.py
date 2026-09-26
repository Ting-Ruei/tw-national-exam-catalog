# -*- coding: utf-8 -*-
"""掃描：找出「上一次檢查之後，狀態有變的題」，而不是「符合某個條件的題」。

**為什麼是狀態變化，不是條件。** 這一條是從一個實測過的失敗推出來的，`repair_daemon.sh:12-23`
自己記著它：第一版的工作清單是「沒有任何偵測器能解釋的 block」。三條新爭議規則上線之後，
**304 題全部都有偵測器了**——清單變成空的，迴圈每 30 分鐘跑一次卻什麼都不做。

條件式清單的問題是它會**縮到零**：它問的是「還有沒有人沒被分類」，而分類成功了這個問題就沒有
答案了。而「有沒有人還沒被處理」不會縮到零。

所以掃描記的是**指紋**：一題的（爭議種類、讀法、審核狀態）算成一個值，存進
`scan_state.json`，只有值變了才重新排進佇列。這樣：

  * 新題進來 → 指紋沒見過 → 選中
  * 題目被修好 → 讀法變了 → 指紋變了 → 選中（不必有人記得重設旗標）
  * 一輪沒做完 → **指紋沒變，但 `pending` 還在** → 下一輪繼續（這是 owner 要求的「掃到跟做完
    是兩回事」：掃描完成不等於修復完成）
  * 什麼都沒變 → 零題，而且這是**正確的零**，不是「條件找不到東西」的假零

**它不做的事：** 不呼叫模型、不改題、不寫審核紀錄。它只決定「有哪些題值得看」。
"""
from __future__ import annotations

import hashlib
import json
import os

#: Where the fingerprints live. Inside the queue, because a queue that is rebuilt gets a fresh
#: history — a fingerprint store that survived a rebuild while the queue did not would claim
#: questions were seen when the records that proved it are gone.
SCAN_STATE = "scan_state.json"


def notes_text(notes) -> str:
    """The reviewer's 註解 on one question, as one stable string: the texts, oldest first, joined.

    Accepts what the callers actually hold — one sentence, the rows `repair_loop.notes_by_key`
    produces (`[{notes, created_at}]`, oldest first), or a mapping — so the scan never has to reshape
    what the loop already read. The **concatenation**, not just the newest sentence: a question the
    person came back to and annotated twice has changed twice, and a fingerprint that kept only the
    latest note would call the second visit "no change".
    """
    if not notes:
        return ""
    if isinstance(notes, str):
        return notes.strip()
    if isinstance(notes, dict):
        rows = [notes]
    else:
        rows = list(notes)
    parts = []
    for row in rows:
        text = row if isinstance(row, str) else str((row or {}).get("notes") or "")
        text = text.strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def question_fingerprint(question: dict, review_action: str = "", notes=None,
                         rejections: int = 0, answers=None, principles=None,
                         prompt_context: str = "") -> str:
    """What "this question has not changed" means.

    Built from the inputs that decide whether a re-read is worth anything:
      * dispute kinds and the text being read
      * the human's standing action and notes
      * machine repairs refused since the question was last accepted
      * candidate-scoped human answers, approved principles and question-specific figure facts

    The notes are part of it because they are part of what is sent to the model: a question whose only
    change is that the reviewer wrote "檢查上下標" is a question the loop would otherwise never look at
    again, and the note is exactly what the prompt is missing. Measured 2026-09-24 in the live log:
    every 註解 there was written **after** the last scan, and none of them scheduled anything.

    The count of refusals is part of it for the same reason one step further in: a **second** refusal
    of the same repair changes nothing the remaining inputs can see - same action (`block`), same text (the
    machine reverted it), and usually no note (measured 2026-09-25: 62 questions blocked twice, none of
    them carrying a sentence). Without this, "we tried and he said no again" is exactly the state that
    reads as "nothing changed", and the owner's 「被重複拒絕的題目要讓它再進入掃描的迴圈」 silently
    does not happen.

    Deliberately **not** built from the model's own findings: those are the output of the pass this
    fingerprint schedules, so including them would make every processed question look changed and the
    loop would never converge.
    """
    payload = {
        "kinds": sorted(question.get("kinds") or []),
        "stem": question.get("stem") or "",
        "options": [str((option or {}).get("text") or "") for option in (question.get("options") or [])],
        "review": review_action or "",
        "notes": notes_text(notes),
        "rejections": int(rejections or 0),
        "answers": [
            {"candidate_key": row.get("candidate_key"),
             "question": row.get("question") or "",
             "answer_text": str(row.get("answer_text") or "").strip()}
            for row in ((answers or {}).get("questions") or [])
            if str(row.get("answer_text") or "").strip()
        ],
        "principles": [str(value).strip() for value in (principles or []) if str(value).strip()],
        "prompt_context": prompt_context or "",
    }
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def load_state(queue_dir: str) -> dict:
    path = os.path.join(queue_dir, SCAN_STATE)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError):
        # A corrupt store is rebuilt, not fatal: the cost is one round of redundant reads, and the
        # alternative (refusing to scan) is a daemon that silently stops.
        return {}
    return data if isinstance(data, dict) else {}


def save_state(queue_dir: str, state: dict) -> None:
    """Written whole, not appended: it is a cache of what has been seen, and the newest value wins.

    Atomic via a temporary file in the same directory, because a half-written JSON here means the
    next round re-scans everything (recoverable) while a *truncated* one that still parses as an
    empty object would mean the same thing — so there is no reason to risk the unreadable case.
    """
    path = os.path.join(queue_dir, SCAN_STATE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, sort_keys=True, indent=0)
    os.replace(tmp, path)


def new_work(questions, state: dict, review_actions=None, notes=None, rejections=None,
             answers=None, principles=None, prompt_contexts=None) -> tuple[list, dict]:
    """The questions whose fingerprint is unseen, and the state to store afterwards.

    `review_actions` is `{candidate_key: action}` and `notes` is `{candidate_key: <what the reviewer
    wrote>}` (`repair_loop.notes_by_key`'s rows, or one sentence) — both are halves of the same
    question ("has anything about this question changed since it was scanned"), so they travel
    together and neither is optional in practice: a scan that passes only the action is blind to a
    註解 added after the last scan, which is the state the live queue was in.

    `rejections` is `{candidate_key: {"count", "fields"}}` (`repair_loop.rejections_by_key`) and only
    its `count` goes into the fingerprint. It is the third half for the same reason: a machine repair
    that was refused **again** leaves the action, the text and the note exactly as they were, so this
    is the only thing that changes — and without it the refusal is not work the scan can see.

    `answers` maps candidate keys to the human answers to that question's asks;
    `principles` contains only approved standing instructions; `prompt_contexts` maps each key to its
    question-specific figure facts. They are included because they change the actual read prompt even
    when the candidate text and review action do not change.

    `state` is passed in and returned rather than mutated in place so a caller can decide to discard
    the result (a dry run), which is the owner's 乾跑試算: "先算這條規則會命中多少題而不真的改".

    A question that was selected but **not yet processed** stays selected on the next call even though
    its fingerprint has not changed — the caller signals that by leaving it out of the returned state
    (see `mark_processed`). Selection and completion are two different things.
    """
    review_actions = review_actions or {}
    notes = notes or {}
    rejections = rejections or {}
    answers = answers or {}
    prompt_contexts = prompt_contexts or {}
    selected = []
    updated = dict(state)
    for question in questions:
        key = question.get("candidate_key")
        if not key:
            continue
        count = int((rejections.get(key) or {}).get("count") or 0)
        fingerprint = question_fingerprint(
            question, review_actions.get(key, ""), notes.get(key), count,
            answers.get(key), principles, prompt_contexts.get(key, ""))
        if updated.get(key) == fingerprint:
            continue
        selected.append(question)
        updated[key] = fingerprint
    return selected, updated


def pending_keys(queue_dir: str) -> list:
    """Questions selected by an earlier round that never reported back.

    This is the half that the owner named: 「掃描到跟做完了是兩回事」. A round that found 100
    questions and processed 5 must not report the other 95 as done — and must not re-scan for them
    either, because they are already known. They are pending, and pending is its own state.
    """
    state = load_state(queue_dir)
    pending = state.get("pending")
    return list(pending) if isinstance(pending, list) else []


def mark_pending(queue_dir: str, keys) -> None:
    state = load_state(queue_dir)
    state["pending"] = sorted(set(keys))
    save_state(queue_dir, state)


def mark_processed(queue_dir: str, key: str) -> None:
    """A question that finished a repair pass leaves `pending`.

    Its fingerprint stays, so it will not be re-scanned until its content actually changes — that is
    the whole point of keeping the two states apart.
    """
    state = load_state(queue_dir)
    pending = [item for item in (state.get("pending") or []) if item != key]
    state["pending"] = sorted(set(pending))
    save_state(queue_dir, state)
