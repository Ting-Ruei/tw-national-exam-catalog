# -*- coding: utf-8 -*-
"""管線的 reset 不可以消滅人的決定。

**這個檔案為什麼存在。** 2026-09-23 實測發現：`reset_review` 事件（管線為了「內容被改過、
請重看」而寫的）在狀態推導時一律 `latest.pop(key)`，於是一題只要被修過，審題者先前的
`block` 就從介面上消失——紀錄還在檔案裡（它是 append-only），但它不再是最後一筆事件，
所以推導看不到它。實測 72 題的 `reset_review` 落在人為決定之後，其中 17 題的人為決定
（block/needs_review/exclude）因此不可見。

修法的判準是**誰寫的**，不是寫了什麼：`reviewer` 前綴或 `repair_kind` 只有管線會寫。
人自己的 reset 照舊——你的決定本來就可以被你自己改變。

這裡的每個測試都有一個**負控制**：把判準拿掉（`_is_repair_reset` 永遠回 False），
它必須失敗。沒有這一步，測試只是在描述現在的行為，不是在證明它對。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

from pathlib import Path  # noqa: E402

from qbr.review_ui import events as E  # noqa: E402


def _write(path, rows):
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return Path(path)


def _human(key, action, notes="", at="2026-09-19T10:00:00"):
    return {"candidate_key": key, "action": action, "reviewer": "local",
            "notes": notes, "created_at": at}


def _machine_reset(key, at="2026-09-19T20:00:00", **extra):
    """A reset as `repair_reviewed_text_normalization.py` writes it."""
    row = {"candidate_key": key, "action": "reset_review",
           "reviewer": "codex-text-normalization-repair",
           "pipeline_note": "上下標修復：紙本印刷的英文上下標…請重新確認。",
           "repair_kind": "safe_text_normalization",
           "previous_action": "block", "created_at": at}
    row.update(extra)
    return row


# ------------------------------------------------------------------ the identity of a reset

def test_a_pipeline_repair_reset_is_recognised_by_its_reviewer():
    assert E._is_repair_reset(_machine_reset("k1")) is True


def test_a_person_writing_reset_review_is_not_a_machine(tmp_path):
    """負控制的反面：人自己按的 reset 仍然是人的。"""
    assert E._is_repair_reset(_human("k1", "reset_review")) is False


def test_an_unknown_reviewer_is_treated_as_human():
    """預設是「人」。

    兩個方向的代價不對稱：把管線誤判成人，代價是一個決定重新出現（看得見、可以再處理）；
    把人誤判成管線，代價是一個決定無聲消失——那正是這次要修的缺陷。所以不認識的名字
    一律當人。
    """
    assert E._is_repair_reset(_human("k1", "reset_review", at="2026-09-19T10:00:00")
                              | {"reviewer": "someone_new"}) is False


def test_an_accepted_reaudit_is_not_a_repair_and_must_still_reopen():
    """**負控制，來自一個真實的既有契約。**

    `codex-luna-accepted-reaudit` 是**刻意**把已通過的題目退回重審的（`tests/test_review_ui_scope.py::
    test_accepted_reaudit_keeps_effective_correction_and_is_not_new_queue` 釘住這件事）。
    它不叫「內容被改過」，它叫「這題要再看一次」——所以它的 pending 狀態**就是它的目的**，
    保留 accept 反而是錯的。

    我第一版把判準寫成「管線寫的 reset 一律保留人為決定」，這個測試當場失敗。判準必須窄到
    「修復文字」這一類，而不是所有機器 reset。
    """
    row = {"candidate_key": "k1", "action": "reset_review",
           "reviewer": "codex-luna-accepted-reaudit", "previous_action": "accept",
           "approval_ref": "user-approved-accepted-reaudit-20260803"}
    assert E._is_repair_reset(row) is False


def test_an_uncategorised_machine_reset_keeps_its_veto():
    """沒有公認前綴也沒有 `repair_kind` 的機器 reset,行為不變——回到原本的「重開」。"""
    row = {"candidate_key": "k1", "action": "reset_review", "reviewer": "someone_new",
           "previous_action": "block"}
    assert E._is_repair_reset(row) is False


# ------------------------------------------------------------------ the fold

def test_a_human_block_survives_a_pipeline_reset(tmp_path):
    """核心情境：你 block 了,管線修了文字並 reset——你的 block 必須還在,而且帶著提醒。"""
    path = _write(tmp_path / "events.jsonl", [
        _human("k1", "block", notes="表格應該用截圖的"),
        _machine_reset("k1"),
    ])
    latest, _counts, _reset = E.load_review_events(path)
    assert latest["k1"]["action"] == "block", "管線的 reset 消滅了人的決定"
    assert latest["k1"]["pending_reset"]["reviewer"] == "codex-text-normalization-repair"
    # 你的原話要原封不動——它是你當時的判斷,管線的句子不該蓋上去
    assert latest["k1"]["notes"] == "表格應該用截圖的"


def test_a_person_reset_does_clear_their_own_decision(tmp_path):
    """負控制：人自己 reset 時,狀態必須真的重開——否則這個修正會讓人無法改變心意。"""
    path = _write(tmp_path / "events.jsonl", [
        _human("k1", "block"),
        _human("k1", "reset_review", at="2026-09-19T20:00:00"),
    ])
    latest, _counts, reset = E.load_review_events(path)
    assert "k1" not in latest
    assert reset["k1"]["action"] == "reset_review"


def test_a_human_reset_after_a_machine_one_still_clears(tmp_path):
    """順序：block → 機器 reset → 人 reset。人最後說話,所以重開。"""
    path = _write(tmp_path / "events.jsonl", [
        _human("k1", "block"),
        _machine_reset("k1"),
        _human("k1", "reset_review", at="2026-09-20T10:00:00"),
    ])
    latest, _counts, reset = E.load_review_events(path)
    assert "k1" not in latest
    assert reset["k1"]["action"] == "reset_review"


def test_a_machine_reset_leaves_an_unreviewed_question_unreviewed(tmp_path):
    """沒有人為決定時,機器的 reset 行為不變——沒有東西要保護。"""
    path = _write(tmp_path / "events.jsonl", [_machine_reset("k1")])
    latest, _counts, reset = E.load_review_events(path)
    assert "k1" not in latest
    assert reset["k1"]["action"] == "reset_review"


def test_the_correction_still_survives_the_reset(tmp_path):
    """既有的行為不能被這個修正弄丟：修好的文字要跟著 reset 傳下去。"""
    path = _write(tmp_path / "events.jsonl", [
        _human("k1", "block"),
        _machine_reset("k1", correction={"question_text": "修正後"}),
    ])
    latest, _counts, _reset = E.load_review_events(path)
    assert latest["k1"].get("correction") == {"question_text": "修正後"}


def test_the_machine_sentence_never_lands_in_the_persons_note(tmp_path):
    """機器的那句話進 `pipeline_note`,不進 `notes`。

    這是 `test_principles_curation.py` 擋的那件事的上游：介面把 reset 的說明預填進
    `comment` 輸入框,人一送出,機器的公告就變成了「人的原則」。
    """
    path = _write(tmp_path / "events.jsonl", [
        _human("k1", "block", notes="下標"),
        _machine_reset("k1"),
    ])
    latest, _counts, _reset = E.load_review_events(path)
    notes = latest["k1"]["notes"]
    assert "上下標修復" not in notes, "機器的公告跑進人的欄位了"
    assert latest["k1"]["pending_reset"]["notes"].startswith("上下標修復")
