# -*- coding: utf-8 -*-
"""審題者的話與機器的修復史，都要真的進到模型眼前——而且要活得比下一顆按鈕久。

**這個檔案為什麼存在。** 2026-09-24 在 `live` 佇列的 `question_review_events.jsonl` 裡實測到
一整段註解，最後一題的形狀是（`moex:105100:305:33:1:question:q046`，5156-5158 行）：

    comment「檢查上下標」→ comment「檢查上下標」→ block notes:""

而**管線沒有任何地方讀過 `notes`**：`ai_findings.build_prompt` 沒有這個頻道、
`confirm_dispute.py` 整個檔案 `grep notes` 找不到引用、`scan_state.question_fingerprint` 只有
（爭議種類、文字、人的決定），`repair_agent.py` 甚至寫死 `"notes": ""`。所以審題者寫下的那一句
話，既不會被送去模型、也不會讓那一題重新排進工作清單——他寫了字，系統當作沒發生。

同一個缺陷在機器那一側還有第二個版本（2026-09-25 實測）：**機器改過、人又打回，第二輪的提示詞
不知道有這回事**。站上 8,307 筆事件裡有 141 筆機器修復、68 題被退（64 題被退兩次），而沒有任何
一個讀者把「被退的是哪一筆改動」交給模型，於是第二輪只能從同一份材料再造一次同一個改動。

八件事，八個測試群：

  1. **寫入端**：一個沒有帶註解的決定，必須保留題目既有的那一句（`_reaffirm_standing_action`）。
     這條有負控制：把 carry-forward 拿掉，`block` 事件的 `notes` 就是空的。
  2. **提示詞**：`build_prompt` 與 `transcribe_system` 都要有【審題者的註解】，而且
     `blocked` 的開場白不能再說「人類標記時並沒有說錯在哪裡」——那句話在有註解時是假的。
  3. **指紋**：新增一句註解要是新工作（`scan_state.question_fingerprint`），沒變的不是。
  4. **來回一趟**：被 block、有註解的題，走過迴圈的挑選，最後出現在送出的 messages 裡。
  5. **機器修復史**：被退幾次、上一次被退的是哪一筆改動（逐欄 before/after），要在提示詞裡，
     而且沒被退過的題目**一個位元組都不多**（否則每一題都假裝被改過又被退）。
  6. **重複被拒是新工作**：第二次打回時動作、文字、註解都可能一字不差，所以計數本身要進指紋。
  7. **人與機器不可以互相冒充**：機器的修復事件也帶 `notes`（站上 464 題還站著的註解裡有 311 題
     是機器寫的），而那個頻道整條路都說「審題者寫下的原話」。過濾掉機器的散文，機器做了什麼改由
     逐欄的 from/to 攜帶。
  8. **一趟真的來回**：修復→打回→撤回→打回的那一題，走完挑選與折疊，帶著那筆被退的改動進到
     messages。

每個測試都有一個**負控制**：把規則拿掉它必須失敗。否則測試只是在描述現在的行為。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

from pathlib import Path  # noqa: E402

import confirm_dispute  # noqa: E402
import repair_loop  # noqa: E402
from qbr import ai_findings, scan_state  # noqa: E402
from qbr.review_ui import events as review_events  # noqa: E402
from qbr.review_ui import review_state  # noqa: E402

KEY = "moex:105100:305:33:1:question:q046"
NOTE = "檢查上下標"
#: 舊版 `blocked` 開場白裡的那句話。有註解時它必須消失——審題者明明說了位置。
SAID_NOTHING = "人類標記時並沒有說錯在哪裡"


# ------------------------------------------------------------------ helpers

def _question(number=46, stem="下列何者正確？", disputes=None):
    """一題有可確認爭議的題目，形狀同 `build_review_queue.py` 寫出來的列。"""
    return {
        "candidate_key": "moex:105100:305:33:1:question:q%03d" % number,
        "question_number": number,
        "stem": stem,
        "options": [{"key": letter, "text": "選項%s" % letter} for letter in "ABCD"],
        "answer": "B",
        "answer_payload": {"accepted_values": ["B"]},
        "disputes": disputes or [{"kind": "substituted-ideograph", "severity": "warn",
                                  "note": "⻑ 應為 長",
                                  "substitutions": [{"char": "⻑", "means": "長"}]}],
        "metadata": {"normalized_subject_name": "放射線器材學",
                     "normalized_category_name": "醫事放射師",
                     "question_pdf_relative": "國考題資料夾/10_official_pdf/x/1082_放射線器材學.pdf"},
    }


def _event(key, action, notes="", at="2026-09-24T03:45:03"):
    return {"candidate_key": key, "action": action, "notes": notes,
            "reviewer": "local", "created_at": at, "source": "linear_v2"}


def _state(tmp_path, questions):
    """A `ReviewState` on a throwaway queue, writing the JSONL backend (no database)."""
    candidates = Path(tmp_path) / "candidates.jsonl"
    with candidates.open("w", encoding="utf-8") as handle:
        for question in questions:
            handle.write(json.dumps(question, ensure_ascii=False) + "\n")
    log = Path(tmp_path) / "question_review_events.jsonl"
    return review_state.ReviewState(candidates, None, log, review_backend="jsonl"), log


def _queue(tmp_path, questions, events):
    """A queue directory shaped the way the review UI writes one."""
    ui = Path(tmp_path) / "review-ui"
    ui.mkdir(parents=True, exist_ok=True)
    with (ui / "candidates.jsonl").open("w", encoding="utf-8") as handle:
        for question in questions:
            handle.write(json.dumps(question, ensure_ascii=False) + "\n")
    with (ui / "question_review_events.jsonl").open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return str(ui)


# ------------------------------------------------------- 1. the writer keeps the note

def test_a_note_survives_a_later_bare_decision(tmp_path):
    """**核心負控制。** 真實的序列（`q046`，5156-5158 行）：註解 → 註解 → 空白阻擋。

    事件流的最後一筆就是題目的狀態，所以那筆 `notes: ""` 的 `block` 會蓋掉人寫過的句子；
    介面的決定框只在註解框開著時才送 `notes`，所以「空白的決定」是常態，不是例外。
    修正必須在**寫入端**：沒有帶註解的決定，保留題目既有的那一句。

    沒有 carry-forward 時這個測試看到的是 `""`——那正是缺陷。
    """
    state, log = _state(tmp_path, [_question()])
    for event in (_event(KEY, "comment", NOTE, "2026-09-24T03:45:03"),
                  _event(KEY, "comment", NOTE, "2026-09-24T03:45:06"),
                  _event(KEY, "block", "", "2026-09-24T03:45:07")):
        state.append_review(dict(event))

    stored = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]
    assert [row["action"] for row in stored] == ["comment", "comment", "block"]
    assert stored[-1]["notes"] == NOTE, \
        "最後一筆 block 把審題者的註解蓋掉了：%r" % stored[-1].get("notes")
    # 而且它必須看得出來**不是**一則註解：block 仍然是 block，只是帶著那一句話。
    assert "note_action" not in stored[-1]
    assert stored[-1]["action"] == "block"

    # 讀者那一側：介面與管線都用最新一筆事件當題目的狀態，所以那一筆一定要帶著句子。
    latest, _counts, _reset = review_events.load_review_events(log)
    assert latest[KEY]["action"] == "block"
    assert latest[KEY]["notes"] == NOTE, "投影出來的註解是空的"

    # 而迴圈讀同一份事件流的那個折疊也要看到同一句話（一份事件流一個折疊規則）。
    assert repair_loop.read_latest_actions(str(log))[KEY]["notes"] == NOTE
    assert repair_loop.notes_by_key(str(log))[KEY][-1]["notes"] == NOTE


def test_a_note_the_reviewer_changes_is_what_they_typed(tmp_path):
    """反面：人**刻意**改寫的註解就是他們打的那一句，carry-forward 不可以蓋掉它。"""
    state, log = _state(tmp_path, [_question()])
    state.append_review(_event(KEY, "comment", NOTE, "2026-09-24T03:45:03"))
    state.append_review(_event(KEY, "block", "", "2026-09-24T03:45:07"))
    state.append_review(_event(KEY, "comment", "改看選項 C 的上下標", "2026-09-24T03:50:00"))

    stored = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]
    assert stored[-1]["notes"] == "改看選項 C 的上下標"
    assert [row["notes"] for row in repair_loop.notes_by_key(str(log))[KEY]] == \
        [NOTE, "改看選項 C 的上下標"], "兩句話都是他寫的，不能只留一句"


def test_a_reset_or_group_event_is_left_alone(tmp_path):
    """**負控制的反面：不該動的事件必須一個位元組都沒動。**

    `reset_review` 不是關於題目內容的陳述，群組／視覺／行動裝置的事件更不是關於這一題；
    `events._merge_note_into_reset` 已經擁有「卡住的題目上的註解」這條規則。把註解搬進一個
    reset 事件會讓一題的註解有兩個主人——所以那個方向必須是 no-op。
    """
    from qbr.review_ui.legacy_assets import _reaffirm_standing_action

    previous = _event(KEY, "block", NOTE)
    reset = _event(KEY, "reset_review", "")
    before = dict(reset)
    _reaffirm_standing_action(reset, previous)
    assert reset == before, "reset 事件被改寫了：%r" % reset

    group = {"candidate_key": KEY, "action": "confirm_group", "notes": ""}
    before = dict(group)
    _reaffirm_standing_action(group, previous)
    assert group == before, "群組事件被改寫了：%r" % group

    # 而一個沒有任何東西可說的決定也是 no-op（呼叫端現在對每個事件都呼叫它）。
    fresh = _event("moex:1:1:1:1:question:q001", "accept", "")
    before = dict(fresh)
    _reaffirm_standing_action(fresh, {})
    assert fresh == before


def test_the_writer_is_the_one_that_puts_it_there():
    """兩個方向都測：有可以搬的註解時搬，沒有時不動——所以上面那些斷言不是空的。"""
    from qbr.review_ui.legacy_assets import _reaffirm_standing_action

    carried = _event(KEY, "block", "")
    _reaffirm_standing_action(carried, _event(KEY, "needs_review", NOTE))
    assert carried["notes"] == NOTE

    empty = _event(KEY, "block", "")
    _reaffirm_standing_action(empty, _event(KEY, "needs_review", ""))
    assert empty["notes"] == ""


# ------------------------------------------------------- 2. the prompt carries it

def test_the_note_is_inside_the_prompt_verbatim():
    """人的句子要原封不動地出現在【審題者的註解】裡，不是被摘要成「人有寫註解」。"""
    system, _user = ai_findings.build_prompt(_question(), notes=NOTE)
    assert "【審題者的註解】" in ai_findings.NOTES.format(notes=NOTE)
    assert "【審題者的註解】" in system
    assert NOTE in system
    assert system.index("【審題者的註解】") > system.index("你是國考題庫抽取品管員")


def test_the_blocked_framing_stops_claiming_the_person_said_nothing():
    """**負控制。** 沒有註解時，那句話是對的（人只按了阻擋）；有註解時它是假的。

    舊的簽名與舊的開場白讓兩者共用同一句話，而「說人沒說」正是這個專案量過最貴的一種誤導
    （見 `POPULATIONS`）：它叫模型自己重新猜一次，而人明明已經寫下位置。
    """
    plain, plain_user = ai_findings.build_prompt(_question(), population="blocked")
    assert SAID_NOTHING in plain, "前提：沒有註解時這句話本來就在"
    assert ai_findings.POPULATIONS["blocked"]["ask"] in plain_user

    noted, noted_user = ai_findings.build_prompt(_question(), population="blocked", notes=NOTE)
    assert SAID_NOTHING not in noted, "有註解時開場白仍然說人沒有說錯在哪裡"
    assert SAID_NOTHING not in noted_user
    assert NOTE in noted
    # 引文要在開場白裡，不只在最後的區塊裡——模型是先讀第一行的。
    assert "「%s」" % NOTE in noted
    # 而 user turn 的問法也要換掉：它不能再叫模型自己重新猜一次。
    assert ai_findings.BLOCKED_WITH_NOTE["ask"] in noted_user
    assert ai_findings.POPULATIONS["blocked"]["ask"] != ai_findings.BLOCKED_WITH_NOTE["ask"]


def test_the_corpus_framing_is_untouched_by_a_note():
    """`corpus` 的開場白本來就沒有說人是誰，所以它有註解時也不該改變——版本也不該亂動。"""
    assert ai_findings.population_framing("corpus", NOTE) == ai_findings.POPULATIONS["corpus"]


def test_no_note_leaves_the_prompt_byte_identical():
    """負控制的反面：沒有註解時，提示詞必須一個位元組都沒變（否則每一題都在假裝有註解）。"""
    baseline, _ = ai_findings.build_prompt(_question())
    for empty in (None, "", [], {}, [{"candidate_key": KEY, "notes": "   "}]):
        assert ai_findings.build_prompt(_question(), notes=empty)[0] == baseline


def test_the_transcription_prompt_carries_the_note(tmp_path):
    """迴圈那一條路（`transcribe_system`）也要有：它讀的是 `reread.SYSTEM`，不是這個模板。"""
    rows = [{"candidate_key": KEY, "notes": NOTE, "created_at": "2026-09-24T03:45:03"}]
    system = confirm_dispute.transcribe_system(principles=None, answers=None, notes=rows)
    assert "【審題者的註解】" in system and NOTE in system
    # 沒有註解時回到原樣：`reread.SYSTEM` 一字不動。
    assert confirm_dispute.transcribe_system(None, None, None) == confirm_dispute.reread.SYSTEM


def test_the_note_is_not_folded_into_the_principles_block():
    """兩個頻道：原則是通則、註解是關於這一題的一句話。擠在同一段會讓一個讀成另一個。"""
    system, _user = ai_findings.build_prompt(_question(), principles=["早期試卷的字常常是 酶"],
                                             notes=NOTE)
    assert "【基本原則】" in system and "【審題者的註解】" in system
    # The arrival mentions the block's name too ("完整的那段在最後的【審題者的註解】"), so the
    # block itself is the last occurrence.
    notes_block = system[system.rindex("【審題者的註解】"):]
    assert NOTE in notes_block and "早期試卷" not in notes_block


def test_the_prompt_version_moves_when_the_notes_change():
    """**負控制。** 兩個只差在註解的提示詞不可以共用一個版本——否則兩批量測看起來是同一次。

    舊的簽名沒有 `notes`，所以這兩個版本必然相同：那正是缺陷。
    """
    question = _question()
    before = ai_findings.prompt_version("blocked")
    after = ai_findings.prompt_version("blocked", notes=NOTE)
    assert before != after
    # 同一句話 = 同一個版本；換一句話 = 換一個版本。
    assert ai_findings.prompt_version("blocked", notes=NOTE) == after
    assert ai_findings.prompt_version("blocked", notes="另一句話") != after
    # 而它必須與真的送出去的字一致：版本不同的兩個提示詞，內容一定不同。
    assert ai_findings.build_prompt(question, notes=NOTE)[0] != \
        ai_findings.build_prompt(question)[0]


def test_the_prompt_version_moves_when_the_reviewer_answers():
    """同一類的修法：`prompt_version` 以前只雜湊 `ANSWERS` 模板，沒有雜湊 `answers_note(...)`。

    模板是常數，所以有回答與沒回答的兩次呼叫得到同一個版本——兩份不同的提示詞聲稱同一代。
    """
    answers = [{"candidate_key": KEY, "question": "這一題的 ① 是上標嗎？", "answer_text": "是羅馬數字"}]
    assert ai_findings.answers_note(answers), "前提：這份回答會被渲染出來"
    assert ai_findings.prompt_version("blocked") != \
        ai_findings.prompt_version("blocked", answers=answers)


def test_the_record_stores_the_notes_and_the_version_they_were_made_under():
    """註解要像 `principles` 一樣留在紀錄裡，而且那一筆紀錄的版本要看得出它有註解。"""
    question = _question()
    system, user = ai_findings.build_prompt(question, notes=NOTE)
    record = ai_findings.make_record(question=question, finding=None, model="m", endpoint="u",
                                     prompt_system=system, prompt_user=user, notes=NOTE)
    assert record["notes"] == [{"candidate_key": None, "notes": NOTE, "created_at": None}]
    assert record["prompt_version"] == ai_findings.prompt_version("blocked", None, None, None, NOTE)
    # 沒被給註解的紀錄是 `None`（「這次沒被給」），不是空清單（「人沒有寫」）。
    plain = ai_findings.make_record(question=question, finding=None, model="m", endpoint="u",
                                    prompt_system="s", prompt_user="u")
    assert plain["notes"] is None


# ------------------------------------------------------- 3. a note is new work

def test_a_note_written_after_a_decision_is_new_work(tmp_path):
    """**負控制。** 掃描記的是「狀態有沒有變」，而註解從前不在指紋裡。

    `block`（沒有註解）→ 人後來寫了一句「檢查上下標」。舊的指紋（爭議種類、題幹、選項、決定）
    對這兩次讀取算出同一個值，所以那一題永遠不會再排進工作清單——那句話永遠不會被送去模型。
    """
    question = _question()
    first, state = scan_state.new_work([question], {}, {KEY: "block"}, {})
    assert [row["candidate_key"] for row in first] == [KEY], "前提：第一次掃描會選中它"

    # 決定沒變、文字沒變、爭議種類沒變——只有一句新的註解。
    same, _ = scan_state.new_work([question], state, {KEY: "block"}, {})
    assert same == [], "什麼都沒變時不該重新排隊"

    notes = {KEY: [{"candidate_key": KEY, "notes": NOTE,
                    "created_at": "2026-09-24T03:45:03"}]}
    changed, _ = scan_state.new_work([question], state, {KEY: "block"}, notes)
    assert [row["candidate_key"] for row in changed] == [KEY], \
        "新註解沒有讓這一題變成新工作——它永遠不會被送去模型"


def test_an_unchanged_note_is_not_new_work():
    """反面：同一句話再讀一次不算改變，否則每一輪掃描都會重排同一批題。"""
    question = _question()
    notes = {KEY: [{"candidate_key": KEY, "notes": NOTE, "created_at": "t1"}]}
    _selected, state = scan_state.new_work([question], {}, {KEY: "block"}, notes)
    again, _ = scan_state.new_work([question], state, {KEY: "block"}, notes)
    assert again == []
    # 而多一句（人回來了第二次）就是改變。
    more = {KEY: notes[KEY] + [{"candidate_key": KEY, "notes": "還有選項 C",
                                "created_at": "t2"}]}
    changed, _ = scan_state.new_work([question], state, {KEY: "block"}, more)
    assert len(changed) == 1


def test_the_scan_reads_the_notes_the_same_way_the_loop_does(tmp_path):
    """註解是**逐字的**，而且順序穩定：兩份指紋只有在人打過的字上不同才會不同。"""
    path = Path(tmp_path) / "question_review_events.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for event in (_event(KEY, "comment", NOTE, "2026-09-24T03:45:03"),
                      _event(KEY, "block", "", "2026-09-24T03:45:07")):
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    rows = repair_loop.notes_by_key(str(path))
    assert scan_state.notes_text(rows[KEY]) == NOTE, \
        "掃描沒有看到那一句（空白事件蓋掉了它？）：%r" % scan_state.notes_text(rows[KEY])
    assert scan_state.notes_text(None) == "" and scan_state.notes_text({}) == ""


# ------------------------------------------------------- 4. one honest round trip

def test_a_blocked_question_with_a_note_arrives_in_the_messages_the_loop_sends(tmp_path,
                                                                               monkeypatch):
    """**核心。** 完整的一趟，只把模型呼叫換成假的。

    佇列裡的形狀就是真實日誌裡的形狀（註解 → 註解 → 空白 `block`），然後照迴圈真正的順序走：
    工作清單（`blocked_keys`）→ 這一輪的題目（`disputed_questions`）→ 這一題的註解
    （`repair_loop.notes_by_key`，一輪讀一次）→ `confirm_one` 送出的 messages。

    舊版會把題目送出去而那句話不在 prompt 裡：`confirm_dispute` 整個檔案沒有讀過 `notes`。
    這個測試是那件事的負控制——把 `notes=note` 拿掉，它就找不到句子。
    """
    question = _question()
    queue_dir = _queue(tmp_path, [question],
                       [_event(KEY, "comment", NOTE, "2026-09-24T03:45:03"),
                        _event(KEY, "comment", NOTE, "2026-09-24T03:45:06"),
                        _event(KEY, "block", "", "2026-09-24T03:45:07")])

    # 1. 迴圈的工作清單：人是 block 的。
    blocked = confirm_dispute.blocked_keys(queue_dir)
    assert blocked == {KEY}
    # 2. 這一輪要問的題目。
    questions = confirm_dispute.disputed_questions(queue_dir, None, None, 0, blocked=blocked)
    assert [row["candidate_key"] for row in questions] == [KEY]
    # 3. 這一題的註解，從那一個折疊讀一次（不是第二個解析器）。
    notes_by_key = repair_loop.notes_by_key(
        os.path.join(queue_dir, "question_review_events.jsonl"))
    note = notes_by_key.get(KEY)
    assert note and note[-1]["notes"] == NOTE

    # 4. 送出。相機／紙本／引擎換成假的，messages 是真的。
    sent = {}

    def fake_ask(messages, **kwargs):
        sent["messages"] = messages
        return None, "", "stub", {}, 0.0

    monkeypatch.setattr(confirm_dispute, "paper_pdf_of", lambda row: "/nowhere/paper.pdf")
    monkeypatch.setattr(confirm_dispute, "crop_for",
                        lambda pdf, number, *, dpi, out_png=None, boxes=(): (b"fake-png", 3, None))
    # 判讀有兩個會 rasterise 紙本的接縫：這一題自己的截圖（`crop_for`）與讀不出結論時的鄰題參考圖
    # （`reference_read`）。本檔驗的是「人的那句話有沒有送到模型」，兩個都換掉。
    monkeypatch.setattr(confirm_dispute, "reference_read", lambda *a, **k: None)
    monkeypatch.setattr(confirm_dispute.ask_about_blocks, "ask", fake_ask)
    import argparse

    result = confirm_dispute.confirm_one(
        questions[0], endpoint={"name": "stub", "url": "http://stub"},
        args=argparse.Namespace(dpi=200, max_tokens=100, timeout=10),
        crops_root=None, queue_root=os.path.dirname(queue_dir), notes=note)

    system = sent["messages"][0]["content"]
    assert sent["messages"][0]["role"] == "system"
    assert "【審題者的註解】" in system, "送出的 system prompt 裡沒有註解區塊"
    assert NOTE in system, "審題者寫的那一句沒有出現在送出的 system prompt 裡"
    assert system.index(NOTE) > system.index(confirm_dispute.reread.SYSTEM)

    # 而紀錄要說得出這一題是帶著哪一句話被問的。
    record = ai_findings.make_record(
        question=questions[0], finding=result.get("finding"), model="stub", endpoint="stub",
        prompt_system=system, prompt_user="u", notes=result.get("notes"),
        population="dispute", principles=result.get("principles"), answers=result.get("answers"))
    assert record["notes"][-1]["notes"] == NOTE
    assert record["prompt_version"] != ai_findings.prompt_version("dispute")


# ------------------------------- 5. 機器的修復史（被退過幾次、被退的是哪一筆）也要到得了模型眼前

#: 一題被退兩次的形狀：兩筆機器修復、兩次打回。站上實測的順序（2026-09-25）：第一筆修復被打回
#: 之後機器**撤回**了它（`applied: "withdrawn"`），人再打回一次——所以「上一次被退的改動」是那筆
#: 已經被還原的改動，而它正是第二輪不可以再貼一次的內容。
REFUSED = {"count": 2, "fields": ["stem"],
           "changes": [{"field": "stem", "from": "5-HT1A", "to": "5-HT<sub>1A</sub>"}]}


def test_the_refusal_block_states_the_count_and_quotes_the_change():
    """**核心。** 被退幾次、上一次被退的是哪一筆改動，都要逐字出現在提示詞裡。

    沒有這個頻道時，第二輪拿到的是同一張截圖與同一份材料，只能再發明一次同一個改動。
    """
    system, _user = ai_findings.build_prompt(_question(), rejected=REFUSED)
    assert "【這一題已經被改過又被退】" in system
    assert "被人打回 2 次" in system
    # 逐欄的 before/after 要在，否則「不要重貼同一個改法」是一句沒有內容的話。
    assert "5-HT1A" in system and "5-HT<sub>1A</sub>" in system
    assert "stem" in system


def test_no_refusal_leaves_the_prompt_byte_identical():
    """**負控制的反面。** 沒被退過的題目不可以多出一段「已經被改過又被退」——那是對題目的錯誤陳述。

    （站上實測：68 題有被退過，其餘 79,022 題沒有。每一題都印這一段的話，模型會以為每一題都被
    機器改過又被退。）
    """
    baseline, _ = ai_findings.build_prompt(_question())
    for empty in (None, "", 0, [], {}, {"count": 0}, {"count": 0, "changes": []}):
        assert ai_findings.build_prompt(_question(), rejected=empty)[0] == baseline


def test_a_count_without_a_change_still_says_the_change_was_not_recorded():
    """只有次數、沒有逐欄內容時，要說「沒留下一筆被退的改動」，不能編一個出來。"""
    text = ai_findings.rejected_note({"count": 1, "fields": [], "changes": []})
    assert "被人打回 1 次" in text
    assert "沒有留下一筆被退的改動" in text


def test_the_prompt_version_moves_when_the_refusal_changes():
    """**負控制。** 版本必須跟著被退的內容動，否則兩輪不同的交代會聲稱同一代。

    舊的簽名沒有 `rejected`，所以這兩次呼叫必然得到同一個版本——兩份不同的提示詞說成同一代。
    """
    once = ai_findings.prompt_version("blocked", rejected={"count": 1, "changes": []})
    twice = ai_findings.prompt_version("blocked", rejected=REFUSED)
    assert ai_findings.prompt_version("blocked") != once
    assert once != twice
    assert ai_findings.prompt_version("blocked", rejected=REFUSED) == twice
    # 而且它必須與真的送出去的字一致。
    assert ai_findings.build_prompt(_question(), rejected=REFUSED)[0] != \
        ai_findings.build_prompt(_question(), rejected={"count": 1, "changes": []})[0]


def test_the_transcription_prompt_carries_the_refusal():
    """迴圈那一條路（`transcribe_system`，讀的是 `reread.SYSTEM`）也要有，而且接在註解之後。"""
    system = confirm_dispute.transcribe_system(None, None, None, REFUSED)
    assert "【這一題已經被改過又被退】" in system and "5-HT<sub>1A</sub>" in system
    # 有註解時，人的方向要排在機器修復史**前面**（人先說話）。
    both = confirm_dispute.transcribe_system(None, None, [{"notes": NOTE}], REFUSED)
    assert both.index(NOTE) < both.index("【這一題已經被改過又被退】")
    # 沒有被退過時回到原樣：`reread.SYSTEM` 一字不動。
    assert confirm_dispute.transcribe_system(None, None, None, None) == confirm_dispute.reread.SYSTEM


def test_the_record_stores_the_refusal_and_the_version_it_was_made_under():
    """紀錄要說得出這一題是帶著哪一段修復史被問的，以及那次失敗是在哪一種交代下發生的。"""
    question = _question()
    system, user = ai_findings.build_prompt(question, rejected=REFUSED)
    record = ai_findings.make_record(question=question, finding=None, model="m", endpoint="u",
                                     prompt_system=system, prompt_user=user, rejected=REFUSED)
    assert record["rejected"] == REFUSED
    assert record["prompt_version"] == ai_findings.prompt_version("blocked", None, None, None, None,
                                                                  REFUSED)
    # 沒被交代這一題被退過的紀錄是 `None`（「這次沒被交代」），不是 0 次的空殼。
    plain = ai_findings.make_record(question=question, finding=None, model="m", endpoint="u",
                                    prompt_system="s", prompt_user="u")
    assert plain["rejected"] is None


# ------------------------------------------------------- 6. 重複被拒是新工作

def test_a_second_refusal_is_new_work(tmp_path):
    """**核心（負控制）。** 業主 2026-09-25：「被重複拒絕的題目要讓它再進入掃描的迴圈」。

    第二次打回時，動作（`block`）、文字（機器已撤回修復，回到解析器那一版）與註解通常都與第一次
    一字不差，所以舊的指紋說「沒變」，那一題不會再被選中——掃描看不見「又被退了一次」。

    負控制：把 `rejections` 從指紋的 payload 拿掉（舊版），第三個斷言會得到空清單。
    """
    question = _question()
    once = {KEY: {"count": 1, "fields": ["stem"], "changes": []}}
    twice = {KEY: {"count": 2, "fields": ["stem"], "changes": []}}
    first, state = scan_state.new_work([question], {}, {KEY: "block"}, {}, once)
    assert [row["candidate_key"] for row in first] == [KEY], "前提：第一次掃描會選中它"
    same, _ = scan_state.new_work([question], state, {KEY: "block"}, {}, once)
    assert same == [], "同一個拒絕次數時不該每一輪都重掃"
    again, _ = scan_state.new_work([question], state, {KEY: "block"}, {}, twice)
    assert [row["candidate_key"] for row in again] == [KEY], \
        "第二次被退沒有讓這一題重新入選——重複被拒就不會再進迴圈"


def test_a_question_accepted_after_a_refusal_is_new_work():
    """人被說服、接受了（計數歸零）也是狀態改變：下一輪要能重新看它一次。

    兩件事都要有：`accept` 讓計數回 0（`fold_review_events`），而計數回 0 本身是指紋看得見的改變。
    任一沒有的話，一個「被退過又被接受」的題目就會卡在舊狀態裡。
    """
    question = _question()
    once = {KEY: {"count": 1, "fields": ["stem"], "changes": []}}
    _first, state = scan_state.new_work([question], {}, {KEY: "block"}, {}, once)
    assert scan_state.question_fingerprint(question, "block", None, 1) != \
        scan_state.question_fingerprint(question, "block", None, 0)
    cleared, _ = scan_state.new_work([question], state, {KEY: "accept"}, {}, {})
    assert [row["candidate_key"] for row in cleared] == [KEY]


# ------------------------------------------------------- 7. 一趟來回：被退過的那一題

def test_a_refused_question_arrives_in_the_messages_with_its_history(tmp_path, monkeypatch):
    """**核心。** 完整一趟：事件流裡的「修復→打回→撤回→打回」走過挑選、折疊，最後進到送出的
    messages，而且帶著那一筆被退的改動。

    這一趟是負控制：把 `rejected=refused_here` 拿掉，送出的 system prompt 裡就沒有
    【這一題已經被改過又被退】——第二輪只會再造一次同一個改動。
    """
    question = _question()
    queue_dir = _queue(tmp_path, [question], _refusal_events())
    events_path = os.path.join(queue_dir, "question_review_events.jsonl")
    blocked = confirm_dispute.blocked_keys(queue_dir)
    assert blocked == {KEY}
    questions = confirm_dispute.disputed_questions(queue_dir, None, None, 0, blocked=blocked)
    assert [row["candidate_key"] for row in questions] == [KEY]
    # 一輪讀一次的那一份（與掃描同一個折疊）。
    refusals = repair_loop.rejections_by_key(events_path)
    refused_here = refusals.get(KEY)
    assert refused_here and refused_here["count"] == 2

    sent = {}

    def fake_ask(messages, **kwargs):
        sent["messages"] = messages
        return None, "", "stub", {}, 0.0

    monkeypatch.setattr(confirm_dispute, "paper_pdf_of", lambda row: "/nowhere/paper.pdf")
    monkeypatch.setattr(confirm_dispute, "crop_for",
                        lambda pdf, number, *, dpi, out_png=None, boxes=(): (b"fake-png", 3, None))
    # 判讀有兩個會 rasterise 紙本的接縫：這一題自己的截圖（`crop_for`）與讀不出結論時的鄰題參考圖
    # （`reference_read`）。本檔驗的是「人的那句話有沒有送到模型」，兩個都換掉。
    monkeypatch.setattr(confirm_dispute, "reference_read", lambda *a, **k: None)
    monkeypatch.setattr(confirm_dispute.ask_about_blocks, "ask", fake_ask)
    import argparse

    result = confirm_dispute.confirm_one(
        questions[0], endpoint={"name": "stub", "url": "http://stub"},
        args=argparse.Namespace(dpi=200, max_tokens=100, timeout=10),
        crops_root=None, queue_root=os.path.dirname(queue_dir), rejected=refused_here)
    system = sent["messages"][0]["content"]
    assert "【這一題已經被改過又被退】" in system
    assert "被人打回 2 次" in system
    assert "5-HT<sub>1A</sub>" in system, "被退的那一筆改動沒有進到送出的提示詞"
    # 失敗的讀取也要留住這段交代，否則沒人查得出這次失敗是在哪一種交代下發生的。
    assert result["rejected"] == refused_here


def _refusal_events():
    """站上實測的那個順序（2026-09-25）：修復 → 打回 → 撤回 → 打回。"""
    return [
        {"candidate_key": KEY, "action": "reset_review", "applied": "field",
         "reviewer": "repair_dispute_apply", "source": "qbr_dispute_apply",
         "repair_kind": "content_change", "notes": "依 dispute 的機械證據修復：(5-HT1A→5-HT<sub>1A</sub>)",
         "changes": [{"field": "stem", "from": "5-HT1A", "to": "5-HT<sub>1A</sub>"}],
         "created_at": "2026-09-25T01:00:00"},
        {"candidate_key": KEY, "action": "block", "notes": "", "reviewer": "local",
         "source": "linear_v2", "created_at": "2026-09-25T02:27:00"},
        {"candidate_key": KEY, "action": "reset_review", "applied": "withdrawn",
         "reviewer": "repair_dispute_apply", "withdraw": ["stem"], "correction": None,
         "created_at": "2026-09-25T02:40:00"},
        {"candidate_key": KEY, "action": "block", "notes": "", "reviewer": "local",
         "source": "linear_v2", "created_at": "2026-09-25T02:47:00"},
    ]


def test_a_machine_summary_never_becomes_the_reviewers_words(tmp_path):
    """**負控制。** 機器的修復摘要不可以被當成審題者的原話送出去。

    站上實測（2026-09-25）：464 題有還站著的註解，其中 311 題的那一句是機器寫的
    （「依 dispute 的機械證據修復：…」）。這個頻道整條路都在說「原話」，所以它不只是文字錯誤：
    `BLOCKED_WITH_NOTE` 會把機器摘要引成「他在標記時寫下了註解，說出他認為問題在哪裡」，等於
    提示詞替人發明了一個方向。
    """
    queue_dir = _queue(
        tmp_path, [_question()],
        [{"candidate_key": KEY, "action": "reset_review", "applied": "field",
          "reviewer": "repair_dispute_apply", "source": "qbr_dispute_apply",
          "repair_kind": "content_change",
          "notes": "依 dispute 的機械證據修復：(⻑→長)",
          "changes": [{"field": "stem", "from": "⻑", "to": "長"}],
          "created_at": "2026-09-25T01:00:00"},
         {"candidate_key": KEY, "action": "block", "notes": "", "reviewer": "local",
          "source": "linear_v2", "created_at": "2026-09-25T02:27:00"}])
    rows = repair_loop.notes_by_key(os.path.join(queue_dir, "question_review_events.jsonl"))
    assert rows.get(KEY) in (None, []), "機器寫的那一句被當成審題者的註解了"
    system, _user = ai_findings.build_prompt(_question(), population="blocked",
                                             notes=rows.get(KEY))
    assert "依 dispute 的機械證據修復" not in system
    # 沒有人的註解，開場白就仍然是「人沒有說錯在哪」那一版——機器摘要不可以把它翻過來。
    assert SAID_NOTHING in system
