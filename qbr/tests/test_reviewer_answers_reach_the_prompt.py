# -*- coding: utf-8 -*-
"""審題者的回答要真的進到下一輪的提示詞。

**這個檔案為什麼存在。** `repair_daemon.sh:55-59` 與 `docs/skills/review-ui-v2/SKILL.md:148-152`
都寫著「人對 `ask` 的回答下一輪提示詞讀得到」。實測 2026-09-24：**只有 `PRINCIPLES_STREAM` 被讀**，
`REPAIR_QUESTIONS_STREAM` 從來沒有進入提示詞。你回答的內容進了介面就停在那裡。

當時那個流是 0 bytes，所以還沒有任何東西遺失——線只是沒接上。這個測試把它接上並釘住，
在第一位審題者真的回答了什麼之前。

**負控制**：把 `transcribe_system` 的 `answers` 那一段拿掉（等於修正前的行為），
`test_an_answer_reaches_the_prompt` 必須失敗。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

import confirm_dispute  # noqa: E402
from qbr import ai_findings, discuss, reread  # noqa: E402


def _stream(tmp_path, events):
    path = os.path.join(str(tmp_path), discuss.REPAIR_QUESTIONS_STREAM)
    with open(path, "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return path


def _ask(question_id, key="moex:a:q001", question="這一題的 ① 是上標嗎？"):
    return {"action": "ask", "question_id": question_id, "candidate_key": key,
            "question": question, "reason": "紙本讀不到"}


def _answer(question_id, text, key="moex:a:q001"):
    return {"action": "answer", "question_id": question_id, "candidate_key": key,
            "answer": text, "reviewer": "local"}


# ------------------------------------------------------------------ the projection

def test_only_answered_questions_are_offered_to_the_prompt(tmp_path):
    """還沒被回答的問題不是上下文——它下一輪會被再問一次。

    把它塞進提示詞等於把代理人自己的問題連同「沒有答案」一起餵回去，那會讓它以為自己問過了。
    """
    path = _stream(tmp_path, [_ask("rq1"), _answer("rq1", "是上標，紙本印的是 ⁻"),
                              _ask("rq2")])
    projection = discuss.repair_questions_projection(discuss.load_events(path))
    answered = [row for row in projection["questions"] if str(row.get("answer_text") or "").strip()]
    assert [row["question_id"] for row in answered] == ["rq1"]


# ------------------------------------------------------------------ the prompt

def test_an_answer_reaches_the_prompt(tmp_path):
    """核心：寫進流裡的回答，必須出現在下一輪送出的 system prompt 裡。"""
    path = _stream(tmp_path, [_ask("rq1"), _answer("rq1", "紙本印的是 GABA_B 下標")])
    projection = discuss.repair_questions_projection(discuss.load_events(path))
    system = confirm_dispute.transcribe_system(principles=None, answers=projection)
    assert "GABA_B 下標" in system, "審題者的回答沒有進提示詞——迴路還是斷的"
    # 問題本身也要在：回答是對某個問題的回覆，沒有問題它是一句沒有指涉的話。
    assert "這一題的 ① 是上標嗎？" in system


def test_no_answers_leaves_the_prompt_byte_identical(tmp_path):
    """負控制的反面：沒有回答時，提示詞必須**一個位元組都不變**。

    否則每一輪的 prompt 版本都會不同（`prompt_version` 會把空區塊也雜湊進去），
    而 prompt 版本是拿來比較兩批量測的欄位——它一動，所有歷史紀錄就無法對照。
    """
    baseline = confirm_dispute.transcribe_system(principles=None, answers=None)
    assert baseline == reread.SYSTEM
    empty = {"questions": [], "open_count": 0, "count": 0, "event_count": 0}
    assert confirm_dispute.transcribe_system(principles=None, answers=empty) == baseline


def test_a_question_with_an_empty_answer_is_not_rendered(tmp_path):
    """`answer` 帶空字串（人按了送出但沒寫字）不算回答。"""
    path = _stream(tmp_path, [_ask("rq1"), _answer("rq1", "   ")])
    projection = discuss.repair_questions_projection(discuss.load_events(path))
    assert confirm_dispute.transcribe_system(principles=None, answers=projection) == reread.SYSTEM


# ------------------------------------------------------------------ it is not a principle

def test_an_answer_is_not_folded_into_the_principles_block():
    """回答與基本原則是兩種東西，不能合併。

    原則是通則（「早期試卷的字常常是 酶」），回答是對某一題的說明（「這一題的 ① 不是上標」）。
    把回答當原則會讓提示詞塞滿一次性事實——而 `test_principles_curation.py` 已經量過一次
    同樣的錯誤（把機器說的話當成人的原則）。
    """
    answers = {"questions": [{"question_id": "rq1", "candidate_key": "moex:a:q001",
                              "question": "這題怎麼了", "answer_text": "這是針對這題的說明"}]}
    system = confirm_dispute.transcribe_system(principles=None, answers=answers)
    # The *heading* of the answers block is what must be present; `ANSWERS` itself is a format
    # template, so it still contains `{answers}` and can never appear verbatim in rendered output.
    heading = ai_findings.ANSWERS.split("{answers}")[0].strip()
    assert ai_findings.PRINCIPLES.split("{principles}")[0].strip() not in system, \
        "回答被當成基本原則了"
    assert heading in system
    assert ai_findings.PRINCIPLES.format(principles="") not in system


def test_the_prompt_version_moves_when_the_reviewer_answers():
    """回答改變了送出的提示詞，所以版本必須跟著動——否則兩批量測會看起來是同一次。"""
    before = ai_findings.prompt_version(population="dispute", learned=None,
                                        principles=None, answers=None)
    after = ai_findings.prompt_version(population="dispute", learned=None, principles=None,
                                       answers={"questions": [{"question_id": "rq1",
                                                               "question": "q",
                                                               "answer_text": "a"}]})
    assert before != after
