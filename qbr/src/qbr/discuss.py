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

#: 原則流的 schema。`add`／`remove`／`approve`／`unapprove` 都是這一種記錄——同一個流、
#: 同一種寫法（`review_ui/review_state.py::append_principle` 寫的是同一個字串，見
#: `tests/test_review_ui_principles.py` 把兩邊釘在一起）。
PRINCIPLE_SCHEMA = "qbr_review_principle_v0.1"

#: 人對一條原則的**決定**。它是一個事件，不是改寫 `add` 那一行：與 `remove` 同一個理由，
#: 而且「誰在什麼時候核准的」本身就是稽核要看的東西（append-only 的流沒有第二次機會）。
DECISION_ACTIONS = ("approve", "unapprove")


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
    """當前有效的原則，由 append-only 的 add/remove/approve/unapprove 流折疊而來。

    每個 `principle_id` 以最後一個 `add` 為準，其後若有 `remove` 就撤掉；兩者的先後以**檔案
    順序**為準，因為事件是 append 的，檔案順序就是寫入順序。對一個從未 add 過的 id 做
    `remove`，保留在 `orphans` 而不是默默丟掉——它證明有人試著撤回一個檔案已經不持有的東西，
    這件事值得看見。

    **核准（2026-09-24）**：一條原則進了提示詞，模型就照著它改題目文字，所以「哪幾條是**人**
    點頭過的」必須是一個被記錄下來的狀態，而不是一句推測。`approve`／`unapprove` 是同一條流上
    的事件（append-only：核准不是改寫 add 那一行），折疊成每一條的 `approved` 布林值，連同
    `approved_by`／`approved_at`。三條規則，都是為了讓畫面與提示詞讀到同一個答案：

      * **重複的決定不搬時間。** 再按一次「核准」會 append 第二筆事件（歷史不重寫），但
        `approved_at` 留在第一次——那個欄位是「這個決定是什麼時候做下的」，不是「最後一次有人
        按到它」。所以 `approve` 是冪等的，而檔案仍然只增不減。
      * **重新 add 同一個 id ＝ 一句新的話**（`add` 之後是新的文字），核准的對象是那句話，
        不是那個號碼，所以它的核准狀態回到未核准。
      * `remove` 帶走核准狀態：一條不再生效的原則沒有「已核准」可言。

    `count`／`approved_count`／`pending_count` 都在這裡算，因為介面上的「N 條」與「N 條待你
    核准」必須是**同一次折疊**的兩個數字，不能一個來自投影、一個來自畫面上數。
    """
    order = []
    latest = {}
    orphans = []
    decisions = {}
    for event in events:
        action = str(event.get("action") or "").strip().lower()
        principle_id = str(event.get("principle_id") or "").strip()
        if not principle_id:
            continue
        if action in DECISION_ACTIONS:
            state = decisions.setdefault(
                principle_id, {"approved": False, "approved_by": None, "approved_at": None})
            wanted = action == "approve"
            if wanted != state["approved"]:
                state["approved"] = wanted
                state["approved_by"] = str(event.get("reviewer") or "").strip() or None
                state["approved_at"] = str(event.get("created_at") or "").strip() or None
            continue
        if action not in {"add", "remove"}:
            continue
        if action == "add":
            if principle_id not in latest:
                order.append(principle_id)
            latest[principle_id] = event
            decisions.pop(principle_id, None)
            continue
        if principle_id in latest:
            latest.pop(principle_id, None)
            decisions.pop(principle_id, None)
            if principle_id in order:
                order.remove(principle_id)
        else:
            orphans.append(event)
    active = []
    for pid in order:
        if pid not in latest:
            continue
        event = latest[pid]
        decision = decisions.get(pid) or {}
        # 一份**複本**，不是那一筆事件本身：事件的物件由呼叫端持有（伺服器把它存在
        # `state.principles_events`），折疊不該把顯示用的欄位寫進歷史。
        active.append(dict(event, approved=bool(decision.get("approved")),
                           approved_by=decision.get("approved_by"),
                           approved_at=decision.get("approved_at")))
    approved_count = sum(1 for row in active if row["approved"])
    return {"principles": active, "removed": orphans,
            "count": len(active), "approved_count": approved_count,
            "pending_count": len(active) - approved_count, "event_count": len(events)}


def active_principles(events):
    """**全部**有效原則的文字，照加入順序——含還沒被人核准的。

    這是給**人看的畫面**用的（一條原則被寫下來，就該看得見它在等誰點頭）。**提示詞要用
    `approved_principles`**：未核准的原則若進了提示詞，模型就會照著一句人還沒看過的話改題目
    文字，而畫面上那個「待你核准」的標記會變成一句謊。兩個存取器並存是刻意的——把它們併成
    一個，就是「誰在用哪一種」再次只能靠猜。
    """
    return [str((event.get("text") or "")).strip()
            for event in principles_projection(events)["principles"]
            if str(event.get("text") or "").strip()]


def approved_principles(events):
    """**已核准**原則的文字，照加入順序。這是提示詞唯一該讀的一份。

    一條也沒核准過（例如這個機制剛上線、原則已經寫了但還沒有人按過）時回傳空清單，呼叫端
    必須把「0 條」印出來而不是安靜地送出一個沒有原則的提示詞——見
    `confirm_dispute.py` 的 `基本原則 N 條（已核准 X／待核准 Y）`。
    """
    return [str(row.get("text") or "").strip()
            for row in principles_projection(events)["principles"]
            if row.get("approved") and str(row.get("text") or "").strip()]


def principle_decision_event(principle_id, action, reviewer="local"):
    """一筆核准／取消核准的記錄，形狀與 `add`／`remove` 同一個 schema、同一條流。

    事件由**這裡**組、由呼叫端交給 `append_event`：寫進去的欄位就是折疊讀的欄位，兩者只有
    一份定義（`principles_projection` 讀 `principle_id`／`action`／`reviewer`／`created_at`）。
    """
    action = str(action or "").strip().lower()
    if action not in DECISION_ACTIONS:
        raise ValueError("action must be approve or unapprove")
    principle_id = str(principle_id or "").strip()
    if not principle_id:
        raise ValueError("principle_id is required")
    return {"schema": PRINCIPLE_SCHEMA, "action": action, "principle_id": principle_id,
            "reviewer": str(reviewer or "").strip() or "local"}


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


def repair_asks_by_key(events):
    """每一題「代理還在等答案」的那一筆反問，依 `candidate_key`。

    折疊規則只有一份（上面那個投影），這裡只把它的結果依題目分組——審題介面的題目區要顯示
    「這一題機器讀到了但沒有自己改，原因是什麼」，而它手上只有那一題的 key。

    同一個 key 可以有好幾筆反問（每一輪重讀紙本、或文字改了就得重新問），介面要的是**最新
    的那一筆還沒被回答的**：投影是「最新的問題排前面」，所以第一個命中的就是它。已經被回答
    的不算——那是歷史，那個人已經說過話了，這一題不再是「機器停在這裡」。
    """
    by_key = {}
    for row in repair_questions_projection(events)["questions"]:
        key = row.get("candidate_key")
        if not key or not row.get("open"):
            continue
        if key not in by_key:
            by_key[key] = row
    return by_key


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
