# -*- coding: utf-8 -*-
"""錯題討論區的兩個流，以及它們和修理代理的接點。

這一區有四件事必須成立，而且每一件都有一個「看起來過了但其實沒有」的失敗模式：

1. **只放卡住的題。** 不是整條佇列。一個把 79,090 題全拉進去的清單，會讓真正卡住的幾百題
   找不到——這正是這一區被重做的原因。
2. **基本原則會真的進到提示詞。** 人在介面上寫一句話，下一個 30 分鐘輪次要照著讀。寫進
   `question_review_principles.jsonl` 卻沒有任何讀者，就是一個沒有出口的欄位。
3. **代理讀不懂時會反問人，而不是猜。** 這是開放/封閉問題的界線：紙本讀得到就減法解決，
   讀不到或紙本與抽取一致但人仍阻擋，就是只有人能回答的事。
4. **這兩個流的折疊規則只有一份。** 這條是這一輪最貴的教訓：`refresh_queue_text.py` 自帶一份
   `_taxonomy`，形狀和 `build_review_queue.py` 不同，寫回去把整區導覽弄壞而測試全綠。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

import confirm_dispute  # noqa: E402
from qbr import ai_findings, discuss  # noqa: E402


def _event(path, record):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _question(number=4, stem="下列何者錯誤？"):
    return {
        "candidate_key": "moex:108100:309:33:1:question:q%03d" % number,
        "question_number": number,
        "stem": stem,
        "options": [{"key": k, "text": "選項" + k} for k in "ABCD"],
        "answer": "B",
        "answer_payload": {"accepted_values": ["B"]},
        "disputes": [{"kind": "substituted-ideograph", "severity": "warn",
                      "note": "⻑ 應為 長"}],
        "metadata": {"normalized_subject_name": "放射線器材學",
                     "normalized_category_name": "醫事放射師",
                     "exam_year": 108, "exam_ordinal": 2,
                     "question_pdf_relative": "國考題資料夾/10_official_pdf/x/p.pdf"},
    }


# ------------------------------------------------------- the projection folds once

def test_a_removed_principle_is_an_event_not_a_deletion(tmp_path):
    path = str(tmp_path / discuss.PRINCIPLES_STREAM)
    discuss.append_event(path, {"action": "add", "principle_id": "p1", "text": "上下標要看紙本"})
    discuss.append_event(path, {"action": "add", "principle_id": "p2", "text": "選項數要對"})
    discuss.append_event(path, {"action": "remove", "principle_id": "p1", "reason": "已內建"})
    events = discuss.load_events(path)
    assert len(events) == 3, "撤回必須是第三筆事件，不是把前兩筆改掉"
    assert discuss.active_principles(events) == ["選項數要對"]


def test_a_removal_of_something_the_file_does_not_hold_is_kept_as_an_orphan(tmp_path):
    # Dropping it silently would erase the evidence that someone tried to retract a principle the
    # stream no longer carries - which is a statement about the file, and worth seeing.
    path = str(tmp_path / discuss.PRINCIPLES_STREAM)
    discuss.append_event(path, {"action": "remove", "principle_id": "p9"})
    projection = discuss.principles_projection(discuss.load_events(path))
    assert projection["count"] == 0
    assert [e["principle_id"] for e in projection["removed"]] == ["p9"]


def test_an_answered_question_is_closed_and_an_unanswered_one_is_open(tmp_path):
    path = str(tmp_path / discuss.REPAIR_QUESTIONS_STREAM)
    discuss.append_event(path, {"action": "ask", "question_id": "rq1", "candidate_key": "k1",
                                "question": "這題的選項是圖嗎？"})
    discuss.append_event(path, {"action": "ask", "question_id": "rq2", "candidate_key": "k2",
                                "question": "紙本讀不到，這題還要修嗎？"})
    discuss.append_event(path, {"action": "answer", "question_id": "rq1", "answer": "是圖，不用修",
                                "reviewer": "owner"})
    projection = discuss.repair_questions_projection(discuss.load_events(path))
    assert projection["open_count"] == 1
    open_row = [row for row in projection["questions"] if row["open"]][0]
    assert open_row["question_id"] == "rq2"
    assert open_row["question"] == "紙本讀不到，這題還要修嗎？"
    closed = [row for row in projection["questions"] if not row["open"]][0]
    assert closed["answer_text"] == "是圖，不用修"


def test_the_next_id_counts_events_instead_of_a_counter_file(tmp_path):
    # A counter file is second state that can drift; counting the events cannot hand two adds the
    # same id, and an id is the key the fold uses. Re-issuing a *removed* id is fine, because the
    # removal is append-only and the re-add is just another event.
    path = str(tmp_path / discuss.PRINCIPLES_STREAM)
    discuss.append_event(path, {"action": "add", "principle_id": "p1", "text": "a"})
    discuss.append_event(path, {"action": "add", "principle_id": "p2", "text": "b"})
    discuss.append_event(path, {"action": "remove", "principle_id": "p2"})
    assert discuss.next_id(discuss.load_events(path), "p") == "p3"


# ------------------------------------------------------- the principles reach the prompt

def test_a_principle_reaches_the_prompt_the_model_is_actually_sent():
    # The whole point of the field: a person writes one line and the page read obeys it. If this
    # fails, the principles stream is a field with no reader.
    question = _question()
    system, _user = ai_findings.build_prompt(
        question, population="dispute", principles=["上下標一律照紙本，不要用學科知識推測"])
    assert "基本原則" in system
    assert "上下標一律照紙本" in system


def test_no_principles_and_empty_principles_are_different_runs():
    # An empty block would read as "there are no principles", which is a claim about the reviewer,
    # not about this run - the same distinction `learned_note` keeps.
    _system, _user = ai_findings.build_prompt(_question(), population="dispute")
    none_version = ai_findings.prompt_version("dispute", None, None)
    empty_version = ai_findings.prompt_version("dispute", None, [])
    assert "基本原則" not in ai_findings.build_prompt(_question(), population="dispute")[0]
    assert none_version == empty_version


def test_editing_a_principle_moves_the_prompt_version():
    # The reviewer can change the principles between two runs of a resident loop nobody is watching.
    # Without this, two records produced under different constraints would claim the same version.
    before = ai_findings.prompt_version("dispute", None, ["原則一"])
    after = ai_findings.prompt_version("dispute", None, ["原則一", "原則二"])
    assert before != after


def test_the_finding_record_carries_the_principles_it_was_made_under():
    record = ai_findings.make_record(
        question=_question(), finding={"verdict": "OK", "what": "NONE"}, model="m", endpoint="e",
        prompt_system="s", prompt_user="u", population="dispute", principles=["原則一"])
    assert record["principles"] == ["原則一"]
    # A note made with no principles says so with `None`, which is "not recorded", not "none".
    bare = ai_findings.make_record(
        question=_question(), finding={"verdict": "OK", "what": "NONE"}, model="m", endpoint="e",
        prompt_system="s", prompt_user="u", population="dispute")
    assert bare["principles"] is None


def test_the_stored_prompt_is_the_one_that_was_sent():
    # `transcribe` sends `transcribe_system`, and the record must store that same text. Storing a
    # prompt rebuilt from the finding template would put a prompt in the record the model never saw -
    # the exact kind of record this project treats as no record at all.
    question = _question()
    principles = ["原則一"]
    sent = confirm_dispute.transcribe_system(principles)
    assert sent.startswith(confirm_dispute.reread.SYSTEM)
    assert "原則一" in sent
    assert confirm_dispute.transcribe_system(None) == confirm_dispute.reread.SYSTEM


# ------------------------------------------------------- the agent asks instead of guessing

def test_a_page_that_could_not_be_read_leaves_a_question_for_a_person():
    assert confirm_dispute._needs_person({"error": "no-crop"})
    assert confirm_dispute._needs_person({"changes": []})


def test_a_page_that_settled_the_dispute_does_not_ask_anyone():
    assert not confirm_dispute._needs_person({"changes": [{"field": "stem", "from": "a", "to": "b"}]})


def test_the_agent_asks_once_per_reading_not_once_per_round(tmp_path):
    # The loop runs every 30 minutes forever. Without idempotence the person opens the discussion
    # area to 200 identical questions, which is a worse failure than not asking at all.
    queue_dir = str(tmp_path / "review-ui")
    os.makedirs(queue_dir)
    question = _question()
    endpoint = {"name": "m", "url": "e"}
    first = confirm_dispute._escalate(queue_dir, question, {"changes": []}, endpoint)
    second = confirm_dispute._escalate(queue_dir, question, {"changes": []}, endpoint)
    assert first is True and second is False
    rows = discuss.load_events(os.path.join(queue_dir, discuss.REPAIR_QUESTIONS_STREAM))
    assert len(rows) == 1
    assert rows[0]["action"] == "ask"
    assert "阻擋" in rows[0]["question"] or "一致" in rows[0]["reason"]


def test_a_repaired_reading_gets_asked_afresh(tmp_path):
    # The id is derived from the reading, so a real repair (new text) is a new question rather than
    # one permanently suppressed by an old answer.
    queue_dir = str(tmp_path / "review-ui")
    os.makedirs(queue_dir)
    endpoint = {"name": "m", "url": "e"}
    assert confirm_dispute._escalate(queue_dir, _question(stem="原文"), {"changes": []}, endpoint)
    assert confirm_dispute._escalate(queue_dir, _question(stem="修好的文"),
                                    {"changes": []}, endpoint)


# ------------------------------------------------------- one folding rule, one place

def test_the_discuss_streams_have_exactly_one_reader_implementation():
    # The expensive lesson of this round: `refresh_queue_text.py` carried a second `_taxonomy` whose
    # shape differed from `build_review_queue.py`'s, it overwrote the live queue index, and the whole
    # question area stopped booting while every test stayed green - because the *build* path was
    # correct and only the second implementation was wrong. So the interpretation of these two
    # streams must live in exactly one module, and the server must import it.
    server = os.path.join(PKG, "..", "scripts", "serve_question_review_ui.py")
    with open(os.path.abspath(server), encoding="utf-8") as handle:
        text = handle.read()
    assert "from qbr import discuss" in text, \
        "伺服器必須用 qbr.discuss 的折疊規則，不能自己再寫一份"
    assert 'def principles_projection' in text and 'discuss.principles_projection' in text
    assert 'def repair_questions_projection' in text and 'discuss.repair_questions_projection' in text
    assert 'discuss.append_event' in text, "兩個流只有一個寫者"
    # And the stream names have one definition, so a push/deploy/carry list cannot name a file the
    # projection does not read.
    assert "discuss.PRINCIPLES_STREAM" in text and "discuss.REPAIR_QUESTIONS_STREAM" in text


def test_the_deploy_push_and_rebuild_all_name_the_discuss_streams():
    # A stream that a rebuild does not carry, or a deploy silently deletes, is a stream that was
    # never durable in the first place. The two shell scripts cannot import Python, so they must
    # spell the names out - and that is exactly why they are asserted here rather than trusted.
    # The Python builder can import, so it names `discuss.STREAMS` and must not retype them: a
    # retyped name is the second spelling that can drift.
    root = os.path.abspath(os.path.join(PKG, ".."))
    shell_checks = {
        os.path.join(root, "scripts", "deploy_station.sh"): "question_review_principles.jsonl",
        os.path.join(root, "scripts", "push_reviews_to_station.sh"):
            "question_repair_questions.jsonl",
    }
    for path, needle in shell_checks.items():
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        assert needle in text, "%s 沒有列到 %s" % (os.path.basename(path), needle)
    builder = os.path.join(PKG, "scripts", "build_review_queue.py")
    with open(builder, encoding="utf-8") as handle:
        text = handle.read()
    assert "discuss.STREAMS" in text, "建佇列必須從 discuss.STREAMS 取名字，不能重打字串"
    assert "question_review_principles.jsonl" not in text, \
        "重打字串就是第二個可以漂移的拼法"
