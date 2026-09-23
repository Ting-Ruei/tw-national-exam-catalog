# -*- coding: utf-8 -*-
"""錯題討論區的兩個檔案：人可以寫的「基本原則」，以及代理反問人的「待答問題」。

為什麼要一個共用模組，而不是各寫一份
------------------------------------

這兩個流的**讀者不只一個**：審題介面要把當前有效的原則畫出來，修理代理要把同一份原則編進
提示詞，而推上常駐機、重建佇列、部署腳本各有一份「不能弄丟的名字」清單。三個地方各寫一次
折疊規則（add/remove 誰蓋誰、answer 是否覆蓋 ask），就是三個可以不一致的地方——而這正是
`refresh_queue_text.py` 與 `build_review_queue.py` 各有一份 `_taxonomy` 時發生過的事：佇列
導覽的形狀不同，整區開不起來（見 `review_queue.taxonomy_of`）。

所以投影只有這一份。它**只依賴標準庫**，因為 `scripts/serve_question_review_ui.py` 在 Docker
裡服務（`requirements/review-ui.txt` 只裝 psycopg），而 `qbr` 套件在模組層不 import 任何
第三方套件——這條紀律讓伺服器可以直接 `from qbr import discuss`。

兩個流都是 **append-only**
--------------------------

原則的刪除是 append 一個 `remove` 事件，不是改寫或刪行：一個原則被撤掉，以及它曾經生效過，
是兩件事。反問的答案也一樣，`answer` 是新的行，`ask` 留著。這與審核紀錄同一條契約，理由也
一樣——重寫一份「為什麼當初這樣修」的紀錄，就沒有第二次機會。
"""
from __future__ import annotations

import json
import os
import time

#: 人可以寫的「基本原則」。它的消費者是**提示詞**，不是程式邏輯——這是刻意的：專案裡已經
#: 量過，把「讀懂文字的意思」寫成規則會走上「規則→腳本→新問題→新規則」的跑步機。
#: 一條原則是一句給模型看的話，因此它必須能被原封不動地貼進提示詞，而不是被編譯成 if。
PRINCIPLES_STREAM = "question_review_principles.jsonl"

#: 代理反問人的問題與人的回答。修理迴圈在**開放式問題**上被量為不可靠（「這題哪裡不對」），
#: 在**封閉式問題**上可靠（「這段文字與那張截圖一致嗎」）。碰到前者它必須停下來問，而不是猜；
#: 那個問題與人的回答放這裡，下一輪讀它，而不是再問一次。
REPAIR_QUESTIONS_STREAM = "question_repair_questions.jsonl"

#: 兩個流的檔名，給「哪些名字不能被弄丟」的清單一次取用（部署、推送、重建各有一份清單，
#: 但名字只有一個來源）。
STREAMS = (PRINCIPLES_STREAM, REPAIR_QUESTIONS_STREAM)


def load_events(path):
    """一個 append-only 流的每一筆記錄，照檔案順序，壞行跳過。

    逐行讀並解碼，因為檔案**正在被 append**：一個多位元組字元可能寫到一半。解不開的行與
    解不開 JSON 的行一樣丟掉——丟一行壞的，不該讓整個請求倒下。
    """
    rows = []
    if not path or not os.path.isfile(str(path)):
        return rows
    with open(path, "rb") as handle:
        for raw in handle:
            if not raw.strip():
                continue
            try:
                record = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(record, dict):
                rows.append(record)
    return rows


def append_event(path, record):
    """一筆記錄，一行，append。與審核紀錄同一種寫法（單次 `open("a")`）。

    回傳寫入的那筆記錄。`created_at` 由這裡蓋，若呼叫端沒給——時間是記錄的一部分，讓呼叫端
    各自產生時間就是第二種時間來源。
    """
    record = dict(record)
    record.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return record


def principles_projection(events):
    """當前有效的原則，由 append-only 的 add/remove 流折疊而來。

    每個 `principle_id` 以最後一個 `add` 為準，其後若有 `remove` 就撤掉；兩者的先後以**檔案
    順序**為準，因為事件是 append 的，檔案順序就是寫入順序。對一個從未 add 過的 id 做
    `remove`，保留在 `orphans` 而不是默默丟掉——它證明有人試著撤回一個檔案已經不持有的東西，
    這件事值得看見。
    """
    order = []
    latest = {}
    orphans = []
    for event in events:
        action = str(event.get("action") or "").strip().lower()
        principle_id = str(event.get("principle_id") or "").strip()
        if not principle_id or action not in {"add", "remove"}:
            continue
        if action == "add":
            if principle_id not in latest:
                order.append(principle_id)
            latest[principle_id] = event
            continue
        if principle_id in latest:
            latest.pop(principle_id, None)
            if principle_id in order:
                order.remove(principle_id)
        else:
            orphans.append(event)
    active = [latest[pid] for pid in order if pid in latest]
    return {"principles": active, "removed": orphans,
            "count": len(active), "event_count": len(events)}


def active_principles(events):
    """只取有效原則的文字，照加入順序。這是提示詞要的東西。"""
    return [str((event.get("text") or "")).strip()
            for event in principles_projection(events)["principles"]
            if str(event.get("text") or "").strip()]


def repair_questions_projection(events):
    """代理的反問與人的回答，最新的問題排前面。

    一個問題以 `question_id` 識別。`ask` 開啟它，`answer` 帶人的回覆；還沒有答案的問題是
    `open`，也是介面必須先畫的，因為一個還沒被回答的問題，就是代理還停在那裡的地方。
    對一個檔案已不持有的 `ask`（流被截斷或搬移）仍保留其 `answer`，並列出來而不是丟掉。
    """
    questions = {}
    order = []
    for event in events:
        action = str(event.get("action") or "").strip().lower()
        question_id = str(event.get("question_id") or "").strip()
        if not question_id or action not in {"ask", "answer"}:
            continue
        if question_id not in questions:
            order.append(question_id)
            questions[question_id] = {"question_id": question_id, "ask": None, "answer": None}
        if action == "ask":
            questions[question_id]["ask"] = event
            questions[question_id]["answer"] = None
        else:
            questions[question_id]["answer"] = event
    rows = []
    for question_id in reversed(order):
        row = questions[question_id]
        ask = row.get("ask") or {}
        row["open"] = row.get("answer") is None
        row["candidate_key"] = ask.get("candidate_key") or (row.get("answer") or {}).get("candidate_key")
        row["question"] = ask.get("question")
        row["reason"] = ask.get("reason")
        row["answer_text"] = (row.get("answer") or {}).get("answer")
        rows.append(row)
    return {"questions": rows, "open_count": sum(1 for row in rows if row["open"]),
            "count": len(rows), "event_count": len(events)}


def next_id(events, prefix):
    """下一個 `p%d` / `rq%d`，由既有事件自己算，不由一個計數器檔記。

    一個分開的計數器檔是第二個可以不同步的狀態：刪掉它、或兩個寫者同時讀它，就會發出重複的
    id，而 id 是折疊規則的鍵。數既有的事件沒有這個問題——最多重發一個已經被撤回的號碼，
    而撤回是 append 的，重發只是另一筆 add。
    """
    highest = 0
    for event in events:
        value = str(event.get("principle_id") or event.get("question_id") or "")
        if value.startswith(prefix):
            tail = value[len(prefix):]
            if tail.isdigit():
                highest = max(highest, int(tail))
    return "%s%d" % (prefix, highest + 1)
