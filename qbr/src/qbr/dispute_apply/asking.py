"""The ask channel: the questions a person is asked about readings nothing was written for.

Extracted verbatim from `scripts/apply_dispute_repairs.py` so one file is no longer 2,321 lines.
`apply_dispute_repairs` re-exports every name here; that indirection is deliberate and is why the
test files that load the script by path keep working unchanged. No behaviour was changed.
"""

from __future__ import annotations

from qbr import ai_findings, discuss


#: How many questions one run may ask a person about. The loop runs every 30 minutes forever and the
#: stream is read by a person, not by the machine: 40 a round surfaces a measured backlog in a few
#: rounds without turning the discussion area into a wall of identical questions. 0 = ask nobody.
ASK_LIMIT_DEFAULT = 40

#: How much of a field's text an ask shows. Enough to decide on, short enough that the person sees
#: where the two readings differ inside one screen line.
ASK_EXCERPT = 140


def _reading_refusal(key: str, record: dict | None, why: list[str], records: list[dict]) -> dict:
    """One entry for the ask channel: the question, the fence's own words, and the fields it refused.

    The fields come from the per-field refusal records when the fence made them (that is where a
    person can see *which* field the reading would have moved); `refusal_ask` falls back to the
    reading's own `changes` when the refusal was about the reading rather than about one field.
    """
    return {"candidate_key": key, "record": record, "why": list(why),
            "fields": [entry.get("field") for entry in (records or []) if entry.get("field")]}


def ask_id(key: str, question: dict) -> str:
    """The ask stream's id for *this question at this text* - the same shape `confirm_dispute._escalate`
    writes, reused on purpose.

    One id shape means one answer to "have we already asked about this": the reader writes some asks
    (a reading that failed, a reading that agrees with the text while the person still blocks it) and
    this applier writes the rest (a reading the fences would not let through), and a question must not
    collect one question per producer. `ai_findings.reading_fingerprint` is a hash of the stored text,
    so a repaired question is a different id (it gets asked afresh about its new text) and an
    unanswered refusal keeps the same id (the loop runs forever, and the same unreadable question must
    not spawn a new question every 30 minutes).
    """
    return "rq-%s-%s" % (str(key).replace("moex:", "").replace(":", "-"),
                         ai_findings.reading_fingerprint(question)[:8])


def _clip(text: str, limit: int = ASK_EXCERPT) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def refusal_ask(question: dict, record: dict, why: list[str], fields: list[str]) -> tuple[str, str]:
    """The reason line and the question a person reads about a reading the machine would not write.

    The body carries the two things a decision needs and the machine already has: **which fields the
    reading would move** and **what the page says there against what the stored text says**. Without
    them the person has to re-read the paper to find out what the machine even saw - which is exactly
    the round trip the owner described ("這些卡在 block 裡面很多題目…你自己明明就有寫應該怎麼改").

    `why` is the fence's own words, not a second vocabulary: the person reads the same sentence the
    census prints, so the ask and the report cannot say different things about one question.
    """
    changes = {str(change.get("field")): change for change in (record.get("changes") or [])}
    lines = []
    for field in list(dict.fromkeys(fields)) or list(changes):
        change = changes.get(field)
        if not change:
            continue
        lines.append("・%s\n  現在的抽取：%s\n  紙本讀到　：%s"
                     % (field, _clip(change.get("stored")), _clip(change.get("page"))))
        if len(lines) >= 2:
            break
    reason = "紙本判讀被閘門擋住：%s" % _clip(why[0] if why else "沒有東西可以寫", 60)
    ask = ("這一題的紙本我讀到了，但我不敢自己改（%s）。\n%s\n"
           "請你看一眼紙本：紙本才是對的，就說一句該怎麼改（或在討論區直接改文字）；"
           "紙本這樣寫沒關係、是抽取要改的，就按重新審核。你的回答會在下一輪讀到。"
           % ("；".join(_clip(reason_text, 60) for reason_text in why[:2]), "\n".join(lines)))
    return reason, ask


def questions_to_ask(refusals: list[dict], skip_keys: set) -> list[dict]:
    """The refusals worth a person's attention.

    Every entry here is a question where a reading **was** taken and **moved nothing**, with the
    fence's reasons attached. Three kinds are deliberately not in it:

    * a question the fences never evaluated (no reading, no `changes`) - the scan queues those for a
      reading, and asking about them would ask the person to do the machine's reading for it;
    * a question whose newest human word is a **bounce of a machine repair** (`closed_questions`) -
      he ruled, the reader already asked that family ("紙本與抽取一致，人仍阻擋"), and the machine's
      next move there is a different repair, not a second question in a second channel;
    * a refusal whose only reason is that the reading is **stale** (`判讀已過期`, the text moved since
      it was read). Re-reading is the fix and it is the loop's own work, not the person's.
    """
    kept = []
    for item in refusals:
        if item["candidate_key"] in skip_keys:
            continue
        why = [reason for reason in item["why"] if "已過期" not in reason]
        if not why:
            continue
        kept.append(dict(item, why=why))
    return kept


def ask_for_refusals(path: str, by_key: dict, wanted: list[dict], limit: int) -> tuple[int, int, int]:
    """Append one ask per question the machine read and would not write.

    Returns `(asked, waiting, already)` - asked now, still waiting for a free round, and already in the
    stream (asked by the reader or by an earlier round). The stream is append-only and read by the
    server by file signature, so a question appears in the interface without a restart.
    """
    if limit <= 0:
        return 0, len(wanted), 0
    asked_ids = {str(event.get("question_id")) for event in discuss.load_events(path)}
    asked = waiting = already = 0
    for item in wanted:
        key = item["candidate_key"]
        question = by_key.get(key)
        if not question:
            continue
        question_id = ask_id(key, question)
        if question_id in asked_ids:
            already += 1
            continue
        if asked >= limit:
            waiting += 1
            continue
        reason, ask = refusal_ask(question, item["record"], item["why"], item.get("fields") or [])
        discuss.append_event(path, {
            "action": "ask", "question_id": question_id, "candidate_key": key,
            "question": ask, "reason": reason,
            "model": None, "endpoint": None,
            "reviewer": "repair_dispute_apply",
        })
        asked_ids.add(question_id)
        asked += 1
    return asked, waiting, already
