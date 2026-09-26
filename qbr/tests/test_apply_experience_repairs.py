# -*- coding: utf-8 -*-
"""經驗自動套用：人同意過的改動形狀，用到同一類的題目上。

這一支要守的合約有五條，每一條都配一個負控制（舊行為會讓它紅）：

1. **只有「人確認過的形狀」可以自動套用。** 業主 2026-09-25 同意的是「只加上下標標記、看得到的
   字元一個都沒變」那一種（52 題裡 51 題）。同一批判讀裡把整個選項換成別的字、或把選項清空
   （站上真實的 DEFECT 判讀長這樣），形狀就不同，不可以自動套用——那種改動只有人看過才能同意。
2. **判讀過期不套用。** 判讀帶的 `stored` 必須等於列上現在的文字；另一筆修復先落地的話，
   舊判讀會把新文字蓋回去。
3. **被退過的那一筆改動不重提。** 這是迴圈的記憶：同一題、同一欄、同一組 before/after 被同一個人
   退過，就不再提（業主的方向是「被重複拒絕的題目要再進入掃描」——再進入的是**掃描**，不是同一筆
   被退過的改動再送一次）。
4. **反問被回答成機器說的話，且不會冒充審題者的話。** 套用後那一則 `ask` 有一筆 `answer`，
   署名是這支腳本；而 `confirm_dispute` 只把**人寫的**回答放進提示詞（`is_human_event`）。
5. **乾跑不寫任何東西，`--apply` 只寫 append-only 事件＋先備份。** 機器不宣告任何人的判定。
6. **確定性類別不必有人確認過**（業主 2026-09-25 放行的三種）：私用區字元還原（`lost_glyph`）、
   同一族的排版變體（`typographic_variant`）、Unicode 說的同一個字（`compatibility_fold`）。
   它們的判準在字元自己身上，所以站的是判準而不是先例；而「整欄同類」是硬條件——旁邊夾一個真
   改字（`癇`／`癲`）、混到大小寫（`s`／`S`）、動到小數點（`P2.5` 讀成 `P2·5`）、標記骨架不同，
   整欄一律回「要人看過」那一邊。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
sys.path.insert(0, str(PKG / "src"))
sys.path.insert(0, str(PKG / "scripts"))
sys.path.insert(0, str(PKG.parent / "scripts"))
sys.path.insert(0, str(PKG.parent))

import apply_experience_repairs as experience_mod  # noqa: E402
from qbr.dispute_apply.withdrawals import MAX_ATTEMPTS  # noqa: E402
import repair_loop  # noqa: E402
from qbr import discuss  # noqa: E402

KEY_A = "moex:105100:305:33:1:question:q001"
KEY_B = "moex:105100:305:33:1:question:q002"
KEY_C = "moex:105100:305:33:1:question:q003"
KEY_D = "moex:105100:305:33:1:question:q004"

SUB = "<sub>"
SUB_END = "</sub>"


def _candidate(key, number, *, stem="下列何者是MAOA抑制劑？", options=None):
    return {"candidate_key": key, "question_number": number, "stem": stem,
            "options": options or [{"key": "A", "text": "MAOA抑制劑"},
                                   {"key": "B", "text": "MAOB抑制劑"}],
            "answer": "A", "answer_payload": {"accepted_values": ["A"]},
            "metadata": {"normalized_subject_name": "測試科目"}}


def _repair(key, *, field="stem", before, after, created_at="2026-09-25T01:00:00",
            reviewer="repair_dispute_apply", applied="field"):
    return {"candidate_key": key, "action": "reset_review", "applied": applied,
            "reviewer": reviewer, "source": "qbr_dispute_apply", "repair_kind": "content_change",
            "notes": "依紙本判讀整欄替換：…",
            "changes": [{"field": field, "from": before, "to": after}],
            "created_at": created_at}


def _decision(key, action, created_at="2026-09-25T02:00:00", notes=""):
    return {"candidate_key": key, "action": action, "notes": notes, "reviewer": "local",
            "source": "linear_v2", "created_at": created_at}


def _finding(key, *, changes, error=None, created_at="2026-09-25T03:00:00", crop=None,
             population="dispute"):
    """One reading, in the shape `confirm_dispute._append_finding` stores (`{field, from, to,
    stored, page}` — `stored`/`page` are the raw texts an editor can use).

    `population` matters: only `dispute` readings may authorise a repair (`POPULATIONS`).
    """
    record = {"candidate_key": key, "created_at": created_at, "crop": crop, "error": error,
              "population": population,
              "finding": {"verdict": "DEFECT" if changes else "OK"},
              "changes": changes, "model": "stub", "endpoint": "http://stub"}
    return record


def _change(field, stored, page):
    """`from`/`to` are the comparison form and `stored`/`page` the raw one; for a whole-field repair of
    markup only they are the same two strings, which is what the real records show."""
    return {"field": field, "from": stored, "to": page, "stored": stored, "page": page}


def _queue(tmp_path, *, candidates, events, findings):
    root = Path(tmp_path) / "queue" / "review-ui"
    root.mkdir(parents=True)
    (root / "candidates.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in candidates), encoding="utf-8")
    (root / "question_review_events.jsonl").write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")
    (root / "question_ai_findings.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in findings),
        encoding="utf-8")
    return str(root)


def _two_confirmed(tmp_path, **kwargs):
    """A queue where **two** questions confirm the markup-only form (the threshold)."""
    return _queue(tmp_path, **kwargs)


def _namespaces(queue, *, apply=False, report=False, limit=0, min_confirmations=2, show=10,
                withdraw_population=None, max_attempts=MAX_ATTEMPTS):
    return argparse.Namespace(queue=queue, apply=apply, report=report, limit=limit,
                              min_confirmations=min_confirmations, show=show,
                              withdraw_population=withdraw_population, max_attempts=max_attempts)


def _run(queue, monkeypatch, capsys, **options):
    monkeypatch.setattr(experience_mod, "parse_args", lambda: _namespaces(queue, **options))
    code = experience_mod.main()
    return code, capsys.readouterr().out


# --- the form ------------------------------------------------------------------------------------

def test_markup_only_is_the_confirmed_shape():
    assert experience_mod.form_of("MAOA抑制劑", "MAO<sub>A</sub>抑制劑") == "markup_only"
    assert experience_mod.strip_markup("MAO<sub>A</sub>抑制劑") == "MAOA抑制劑"


def test_a_word_swap_is_not_the_confirmed_shape():
    """站上真實的 DEFECT 判讀會把選項清空、或換成別的字（`5.67L/min`→`""`）：那不是形狀，
    是內容決定，而內容決定需要人看過那一題。"""
    assert experience_mod.form_of("5.67L/min", "") == ""
    assert experience_mod.form_of("長效型", "短效型") == ""
    assert experience_mod.form_of("MAOA", "MAO<sub>A</sub>抑制劑") == ""


def test_experience_counts_confirmations_of_the_form(tmp_path):
    """經驗是「人按過接受的題數」，不是「事件筆數」：同一題的兩筆事件算一題。"""
    queue = _queue(tmp_path, candidates=[_candidate(KEY_A, 1)], findings=[],
                   events=[_repair(KEY_A, before="MAOA", after="MAO<sub>A</sub>"),
                           _decision(KEY_A, "accept"),
                           _decision(KEY_A, "accept", "2026-09-25T02:05:00")])
    forms = experience_mod.experience(queue)
    assert forms["markup_only"]["count"] == 1
    assert forms["markup_only"]["keys"] == [KEY_A]


# --- the plan ------------------------------------------------------------------------------------

def _planned_queue(tmp_path, *, extra_events=None, extra_findings=None, candidates=None):
    """Two confirmed questions (the threshold), one blocked question to repair, and one whose only
    difference is a **word swap** (the negative control)."""
    events = [
        _repair(KEY_A, before="MAOA", after="MAO<sub>A</sub>"),
        _decision(KEY_A, "accept"),
        _repair(KEY_B, field="option A", before="MAOB", after="MAO<sub>B</sub>",
                created_at="2026-09-25T01:05:00"),
        _decision(KEY_B, "accept", "2026-09-25T02:05:00"),
        _decision(KEY_C, "block", "2026-09-25T02:20:00", notes="上下標不對"),
        _decision(KEY_D, "block", "2026-09-25T02:30:00"),
    ] + list(extra_events or [])
    findings = [
        _finding(KEY_C, changes=[_change("stem", "下列何者是MAOA抑制劑？",
                                         "下列何者是MAO<sub>A</sub>抑制劑？")]),
        _finding(KEY_D, changes=[_change("stem", "下列何者是MAOA抑制劑？", "下列何者是別的藥？")]),
    ] + list(extra_findings or [])
    rows = candidates or [_candidate(KEY_A, 1), _candidate(KEY_B, 2), _candidate(KEY_C, 3),
                          _candidate(KEY_D, 4)]
    return _queue(tmp_path, candidates=rows, events=events, findings=findings)


def test_a_confirmed_shape_is_applied_to_the_same_kind_and_nothing_else(tmp_path, monkeypatch, capsys):
    queue = _planned_queue(tmp_path)
    code, out = _run(queue, monkeypatch, capsys)
    assert code == 0
    assert "可以自動套用的：1 題" in out, out
    assert KEY_C[-6:] in out
    assert KEY_D[-6:] not in out, "字形不同（換掉一個字）的那一題不該被套用"
    assert "乾跑" in out


def test_one_confirmation_is_below_the_threshold(tmp_path, monkeypatch, capsys):
    """一次點擊可能是手滑；門檻是「不同題數」。"""
    queue = _queue(tmp_path, candidates=[_candidate(KEY_A, 1), _candidate(KEY_C, 3)],
                   events=[_repair(KEY_A, before="MAOA", after="MAO<sub>A</sub>"),
                           _decision(KEY_A, "accept"),
                           _decision(KEY_C, "block", "2026-09-25T02:20:00")],
                   findings=[_finding(KEY_C, changes=[_change(
                       "stem", "下列何者是MAOA抑制劑？", "下列何者是MAO<sub>A</sub>抑制劑？")])])
    _code, out = _run(queue, monkeypatch, capsys)
    assert "可以自動套用的：0 題" in out, out


def test_a_stale_reading_is_not_applied(tmp_path, monkeypatch, capsys):
    """判讀帶的 `stored` 與列上現在的文字不符（另一筆修復先落地）→ 不套用。"""
    queue = _planned_queue(tmp_path)
    # 把 KEY_C 的列文改成別的，判讀就過期了。
    path = Path(queue) / "candidates.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if row["candidate_key"] == KEY_C:
            row["stem"] = "列上的文字已經被別的修復換過了"
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                    encoding="utf-8")
    _code, out = _run(queue, monkeypatch, capsys)
    assert "可以自動套用的：0 題" in out, out


def test_a_readings_population_decides_whether_it_may_change_the_text(tmp_path, monkeypatch, capsys):
    """只有 `dispute` 的判讀可以改題目文字。

    負控制是站上真實發生過的事（2026-09-25 第一次上線量到：74 筆套用裡 **56 筆**的授權判讀來自
    `category-scan`），而整科掃描的人口自己寫著「整科掃描的讀法不會被套用那一步拿去改題目文字」。
    """
    queue = _planned_queue(tmp_path, extra_findings=[
        _finding(KEY_D, population="category-scan",
                 changes=[_change("stem", "下列何者是MAOA抑制劑？",
                                  "下列何者是MAO<sub>A</sub>抑制劑？")]),
    ])
    # KEY_D 的判讀改成整科掃描的（字形不同那一筆換掉，否則它本來就會被形狀擋掉）。
    path = Path(queue) / "question_ai_findings.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for record in records:
        if record["candidate_key"] == KEY_D:
            record["population"] = "category-scan"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                    encoding="utf-8")
    _code, out = _run(queue, monkeypatch, capsys, report=True)
    assert "可以自動套用的：1 題" in out, out
    assert "判讀來自 category-scan" in out, out
    assert KEY_D[-6:] not in out.split("可以自動套用的")[1], "整科掃描的判讀不可以改題目文字"


def test_a_repair_whose_authority_was_a_scan_reading_can_be_taken_back(tmp_path, monkeypatch, capsys):
    """撤回：授權它的判讀來自該人口時，用共用的撤銷形狀收回，而且不是撤回別題。"""
    queue = _planned_queue(tmp_path)
    # 讓 KEY_C 的判讀變成 category-scan（KEY_A/KEY_B 的確認照舊），套用一次。
    path = Path(queue) / "question_ai_findings.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for record in records:
        if record["candidate_key"] == KEY_C:
            record["population"] = "category-scan"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                    encoding="utf-8")
    monkeypatch.setattr(experience_mod, "POPULATIONS", ("dispute", "category-scan"))
    _run(queue, monkeypatch, capsys, apply=True)
    events_path = Path(queue) / "question_review_events.jsonl"
    assert len([json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()
                if json.loads(line).get("reviewer") == "repair_experience_apply"]) == 1

    # 乾跑不寫，`--apply` 才寫一筆撤銷事件。
    _code, out = _run(queue, monkeypatch, capsys, apply=False, withdraw_population="category-scan")
    assert "仍站著的自動套用：1 題" in out and "乾跑" in out
    assert not any(json.loads(line).get("applied") == "withdrawn"
                   for line in events_path.read_text(encoding="utf-8").splitlines())
    _code, out = _run(queue, monkeypatch, capsys, apply=True, withdraw_population="category-scan")
    assert "撤回了 1 題" in out, out
    withdrawals = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()
                   if json.loads(line).get("applied") == "withdrawn"]
    assert len(withdrawals) == 1
    assert withdrawals[0]["withdraw"] == ["stem"]
    assert withdrawals[0]["reviewer"] == "repair_experience_apply"
    assert "category-scan" in withdrawals[0]["why"]
    # 撤回之後它就不站著了：再跑一次是 0 題（而且沒有任何一題被撤回第二次）。
    _code, out = _run(queue, monkeypatch, capsys, apply=True, withdraw_population="category-scan")
    assert "仍站著的自動套用：0 題" in out, out


def test_taking_a_repair_back_reopens_the_question_it_answered(tmp_path, monkeypatch, capsys):
    """撤回一筆套用時，這一支自己的那則回答也要一起收回——否則那一題在討論區看起來「已答」，
    而它其實沒有東西站在上面了。"""
    queue = _planned_queue(tmp_path)
    path = Path(queue) / "question_ai_findings.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for record in records:
        if record["candidate_key"] == KEY_C:
            record["population"] = "category-scan"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                    encoding="utf-8")
    with open(os.path.join(queue, discuss.REPAIR_QUESTIONS_STREAM), "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"action": "ask", "question_id": "rq-1", "candidate_key": KEY_C,
                                 "question": "這一題是什麼問題？", "reviewer": "repair_agent"},
                                ensure_ascii=False) + "\n")
    monkeypatch.setattr(experience_mod, "POPULATIONS", ("dispute", "category-scan"))
    _run(queue, monkeypatch, capsys, apply=True)
    opened = discuss.repair_questions_projection(
        discuss.load_events(os.path.join(queue, discuss.REPAIR_QUESTIONS_STREAM)))
    assert opened["open_count"] == 0

    _code, out = _run(queue, monkeypatch, capsys, apply=True, withdraw_population="category-scan")
    assert "重新打開" in out, out
    back = discuss.repair_questions_projection(
        discuss.load_events(os.path.join(queue, discuss.REPAIR_QUESTIONS_STREAM)))
    assert back["open_count"] == 1
    row = back["questions"][0]
    assert "自動套用的修正已撤回" in row["question"]
    assert row["answer"] is None
    # 重新打開的那一則依然署名機器，而人寫的回答不會被這一支動到。
    assert row["ask"]["reviewer"] == "repair_experience_apply"


def test_the_landing_step_recognises_this_producers_withdrawal(tmp_path):
    """落地（④）要認得這一支的撤銷——它原本只認 `repair_dispute_apply` 這個 id。

    一個只認第一個生產者的落地步驟，會把它自己第二個生產者的撤銷**安靜地不寫**：機器改錯、
    也寫下了撤回，而檔案仍然停在改錯的字上。
    """
    text = (PKG / "scripts" / "apply_text_corrections.py").read_text(encoding="utf-8")
    assert "REPAIR_REVIEWER_PREFIXES" in text
    assert 'reviewer") or "").strip() == REPAIR_REVIEWER' not in text, "只認一個 id 的舊規則還在"
    from scripts.serve_question_review_ui import REPAIR_REVIEWER_PREFIXES
    assert "repair_" in REPAIR_REVIEWER_PREFIXES


def test_an_answer_field_is_never_touched(tmp_path, monkeypatch, capsys):
    """`answer` 不在可以自動套用的欄位裡：答案的改動是內容決定。"""
    queue = _queue(tmp_path, candidates=[_candidate(KEY_A, 1), _candidate(KEY_B, 2),
                                         _candidate(KEY_C, 3)],
                   events=[_repair(KEY_A, before="MAOA", after="MAO<sub>A</sub>"),
                           _decision(KEY_A, "accept"),
                           _repair(KEY_B, field="option A", before="MAOB", after="MAO<sub>B</sub>",
                                   created_at="2026-09-25T01:05:00"),
                           _decision(KEY_B, "accept", "2026-09-25T02:05:00"),
                           _decision(KEY_C, "block", "2026-09-25T02:20:00")],
                   findings=[_finding(KEY_C, changes=[{"field": "answer", "from": "A", "to": "A",
                                                       "stored": "A", "page": "A<sub>1</sub>"}])])
    _code, out = _run(queue, monkeypatch, capsys)
    assert "可以自動套用的：0 題" in out, out


def test_a_refused_change_is_not_proposed_again(tmp_path, monkeypatch, capsys):
    """人被退過的那一筆改動不重提（同一題、同一欄、同一組 before/after）。"""
    queue = _planned_queue(tmp_path, extra_events=[
        _repair(KEY_C, before="下列何者是MAOA抑制劑？", after="下列何者是MAO<sub>A</sub>抑制劑？",
                created_at="2026-09-25T04:00:00"),
        _decision(KEY_C, "block", "2026-09-25T04:10:00", notes="還是不對"),
    ])
    _code, out = _run(queue, monkeypatch, capsys)
    assert "可以自動套用的：0 題" in out, out


def test_a_refused_kind_of_edit_on_a_field_is_not_sent_again(tmp_path, monkeypatch, capsys):
    """同一欄的同一**種**改動被退過就不再自動送一次，即使這一筆的字面不同。

    業主 2026-09-24 的原話是「剛才上下標亂改的我全部都 block」——那一批就是這一種形狀，而它們
    逐字不同，所以字面比對攔不住第二輪。被退過的題目回到判讀迴圈（新判讀帶著被退的逐欄 from/to），
    不是把同一種改動再送一次。
    """
    queue = _planned_queue(tmp_path, extra_events=[
        # 字面不同、形狀相同（都只加上下標標記）：字面比對攔不住這種。
        _repair(KEY_C, before="下列何者是MAOA抑制劑？", after="下列何者是M<sub>A</sub>OA抑制劑？",
                created_at="2026-09-25T04:00:00"),
        _decision(KEY_C, "block", "2026-09-25T04:10:00", notes="上下標改錯了"),
    ])
    _code, out = _run(queue, monkeypatch, capsys, report=True)
    assert "可以自動套用的：0 題" in out, out
    assert "人退過這一欄的同一種改動" in out, out


def test_an_unanswered_ask_makes_a_question_pending(tmp_path, monkeypatch, capsys):
    """待辦＝block ∪ 反問未答：只有一反問、沒有 block 的題目也要被處理。"""
    queue = _queue(tmp_path, candidates=[_candidate(KEY_A, 1), _candidate(KEY_B, 2),
                                         _candidate(KEY_C, 3)],
                   events=[_repair(KEY_A, before="MAOA", after="MAO<sub>A</sub>"),
                           _decision(KEY_A, "accept"),
                           _repair(KEY_B, field="option A", before="MAOB", after="MAO<sub>B</sub>",
                                   created_at="2026-09-25T01:05:00"),
                           _decision(KEY_B, "accept", "2026-09-25T02:05:00")],
                   findings=[_finding(KEY_C, changes=[_change(
                       "stem", "下列何者是MAOA抑制劑？", "下列何者是MAO<sub>A</sub>抑制劑？")])])
    with open(os.path.join(queue, discuss.REPAIR_QUESTIONS_STREAM), "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"action": "ask", "question_id": "rq-1", "candidate_key": KEY_C,
                                 "question": "這一題是什麼問題？", "reason": "人阻擋",
                                 "reviewer": "repair_agent"}, ensure_ascii=False) + "\n")
    _code, out = _run(queue, monkeypatch, capsys)
    assert "可以自動套用的：1 題" in out, out


# --- the write -----------------------------------------------------------------------------------

def test_apply_writes_one_machine_repair_that_a_person_can_still_refuse(tmp_path, monkeypatch, capsys):
    """`--apply`：一筆 `reset_review`（帶 correction、`applied: field`、署名機器、帶出處），
    而它**不是**任何人的判定。"""
    queue = _planned_queue(tmp_path)
    _code, out = _run(queue, monkeypatch, capsys, apply=True)
    assert "寫了 1 筆機器修復" in out, out
    events = [json.loads(line) for line
              in (Path(queue) / "question_review_events.jsonl").read_text(encoding="utf-8").splitlines()]
    written = [event for event in events if event.get("reviewer") == "repair_experience_apply"]
    assert len(written) == 1
    event = written[0]
    assert event["action"] == "reset_review"
    assert event["applied"] == "field"
    assert event["source"] == "qbr_experience_apply"
    assert event["correction"]["stem"] == "下列何者是MAO<sub>A</sub>抑制劑？"
    assert event["changes"] == [{"field": "stem", "from": "下列何者是MAOA抑制劑？",
                                 "to": "下列何者是MAO<sub>A</sub>抑制劑？"}]
    assert event["experience"]["confirmed_questions"] == 2
    assert set(event["experience"]["evidence"]) == {KEY_A, KEY_B}
    assert event["previous_action"] == "block"
    # 機器的修復不是人的判定，這件事由署名決定（伺服器只認 `repair_` 前綴）。
    assert event["reviewer"].startswith("repair_")
    # 檔案是 append-only：原本的 6 筆事件都還在，而且有一份備份。
    assert len(events) == 7
    assert list(Path(queue).glob("question_review_events.jsonl.before-*"))


def test_apply_answers_the_open_ask_as_the_machine(tmp_path, monkeypatch, capsys):
    """套用後那一則反問有答案，署名是機器——所以它不會冒充審題者的話。"""
    queue = _planned_queue(tmp_path)
    with open(os.path.join(queue, discuss.REPAIR_QUESTIONS_STREAM), "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"action": "ask", "question_id": "rq-1", "candidate_key": KEY_C,
                                 "question": "這一題是什麼問題？", "reason": "人阻擋",
                                 "reviewer": "repair_agent"}, ensure_ascii=False) + "\n")
    _code, out = _run(queue, monkeypatch, capsys, apply=True)
    assert "回答了 1 則反問" in out, out
    projection = discuss.repair_questions_projection(
        discuss.load_events(os.path.join(queue, discuss.REPAIR_QUESTIONS_STREAM)))
    assert projection["open_count"] == 0
    row = projection["questions"][0]
    assert "已依人已同意的同形改動" in row["answer_text"]
    assert row["answer"]["reviewer"] == "repair_experience_apply"
    assert not repair_loop.is_human_event(row["answer"]), "機器的回答不可以讀成人的話"


def test_the_machine_answer_never_reaches_the_prompt_as_the_reviewers_words(tmp_path):
    """`confirm_dispute` 只收人寫的回答。"""
    path = os.path.join(tmp_path, discuss.REPAIR_QUESTIONS_STREAM)
    discuss.append_event(path, {"action": "ask", "question_id": "rq-1", "candidate_key": KEY_C,
                                "question": "這一題是什麼問題？", "reviewer": "repair_agent"})
    discuss.append_event(path, {"action": "answer", "question_id": "rq-1", "candidate_key": KEY_C,
                                "answer": "已依經驗改成這樣。",
                                "reviewer": "repair_experience_apply",
                                "source": "qbr_experience_apply"})
    projection = discuss.repair_questions_projection(discuss.load_events(path))
    row = projection["questions"][0]
    assert not repair_loop.is_human_event(row["answer"])
    # 而人寫的回答照樣是人的話（`local`／`linear_v2_discuss`），頻道沒有因此被關掉。
    discuss.append_event(path, {"action": "answer", "question_id": "rq-1", "candidate_key": KEY_C,
                                "answer": "答案是紙本印錯，改成 C。",
                                "reviewer": "local", "source": "linear_v2_discuss"})
    fresh = discuss.repair_questions_projection(discuss.load_events(path))
    assert repair_loop.is_human_event(fresh["questions"][0]["answer"])


def test_a_second_run_writes_nothing(tmp_path, monkeypatch, capsys):
    """迴圈每 30 分鐘跑一次：第二次必須是 0 筆——已經站在這一題上的同一筆改動不再寫一次。"""
    queue = _planned_queue(tmp_path)
    _run(queue, monkeypatch, capsys, apply=True)
    _code, out = _run(queue, monkeypatch, capsys, apply=True)
    assert "可以自動套用的：0 題" in out, out
    events = [json.loads(line) for line
              in (Path(queue) / "question_review_events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(1 for event in events if event.get("reviewer") == "repair_experience_apply") == 1


def test_a_dry_run_writes_nothing_at_all(tmp_path, monkeypatch, capsys):
    queue = _planned_queue(tmp_path)
    before = (Path(queue) / "question_review_events.jsonl").read_bytes()
    _code, out = _run(queue, monkeypatch, capsys)
    assert "乾跑" in out
    assert (Path(queue) / "question_review_events.jsonl").read_bytes() == before
    assert not list(Path(queue).glob("*.before-*"))


# --- the loop closes -----------------------------------------------------------------------------

def test_the_repair_then_becomes_a_confirmation_a_person_can_give(tmp_path, monkeypatch, capsys):
    """走完整圈：套用 → 人接受 → 它自己變成經驗的一部分（下一次門檻的計數會加一）。"""
    queue = _planned_queue(tmp_path)
    _run(queue, monkeypatch, capsys, apply=True)
    path = Path(queue) / "question_review_events.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_decision(KEY_C, "accept", "2026-09-25T05:00:00"),
                                ensure_ascii=False) + "\n")
    forms = experience_mod.experience(queue)
    assert forms["markup_only"]["count"] == 3
    assert KEY_C in forms["markup_only"]["keys"]
    # 而這一筆新的確認也讓 `propose_principles` 看得到（同一個折疊、同一個分類器）。
    import propose_principles
    confirmed = propose_principles.confirmed_changes(queue, propose_principles.load_rows(queue))
    assert KEY_C in {row["candidate_key"] for row in confirmed}




# --- 確定性類別（業主 2026-09-25 放行的那一批）-----------------------------------------------------

def _pending_only(tmp_path, *, stored, page, number=3):
    """一題被 block、**沒有任何被確認過的經驗**：這裡套用成功的東西，只能是那條確定性判準扛的。"""
    return _queue(tmp_path, candidates=[_candidate(KEY_C, number, stem=stored)],
                  events=[_decision(KEY_C, "block", "2026-09-25T02:20:00")],
                  findings=[_finding(KEY_C, changes=[_change("stem", stored, page)])])


def test_italic_markup_is_a_shape_this_producer_can_see():
    """業主 2026-09-25：「管線好像開始抓斜體字……這部分可以加入」。

    紙本把學名排成斜體（那一本微生物學紙本是 `Helvetica-Oblique`），而收錄的文字是平的、判讀寫了
    `<i>`。舊行為**看不見**它：`MARKUP_RE` 只認 `sub|sup`，所以整欄的可見字元一模一樣、形狀判不出來、
    永遠不會被套用。這一條把它變成同一種可檢查的形狀（人確認過的先例那一種）。
    """
    assert experience_mod.form_of("為Bacteroides spp.生長？", "為<i>Bacteroides</i> spp.生長？") \
        == "markup_only"
    assert experience_mod.form_of("為Bacteroides spp.", "為<i>Bacteroides</i>spp.") == "markup_only"


def test_the_negative_control_an_unknown_tag_is_not_a_markup_repair():
    """平台認得的標記才有意義；`<foo>` 留在可見文字裡，所以它不是「看得到的字元沒變」。"""
    assert experience_mod.form_of("為Bacteroides spp.生長？", "為<foo>Bacteroides</foo> spp.生長？") == ""


def test_the_negative_control_italic_around_a_changed_character_is_not_markup_only():
    assert experience_mod.form_of("為Bacteroides spp.生長？", "為<i>Bacteroidess</i> spp.生長？") == ""
    # 私用區還原**夾帶**標記也不行：整組必須是同一種形狀（這裡判讀同時加了 `<i>` 與還原字元）。
    assert experience_mod.form_of("為\ue2c6抑制劑", "為<i>酶</i>抑制劑") == ""


def test_the_negative_control_a_whitespace_only_difference_is_not_a_shape():
    """可見字元收乾淨之後空白不算改動——但它也不是標記，所以不能靠這一條混進 `markup_only`。"""
    assert experience_mod.form_of("為Bacteroides spp.生長？", "為Bacteroides spp. 生長？") == ""


def test_a_private_use_character_is_restored_without_any_confirmation(tmp_path, monkeypatch, capsys):
    """`\\ue2c6` 站在題目文字中間時不是文字（PDF 的字型沒有對映到它），紙本判讀給的是真字元。

    站上的量：待辦題裡最大的單一模式就是它（`\\ue2c6` → `酶` 33 題），全佇列 53 題含 48 個這種字元。
    這一條不需要任何確認，因為它不是「這個讀法對不對」而是「這個字元壞了」。
    """
    stored, page = "下列何者是\ue2c6抑制劑？", "下列何者是酶抑制劑？"
    assert experience_mod.form_of(stored, page) == "lost_glyph"
    queue = _pending_only(tmp_path, stored=stored, page=page)
    _code, out = _run(queue, monkeypatch, capsys, report=True)
    assert "可以自動套用的：1 題" in out, out
    assert "lost_glyph 1 題" in out, out
    # 報告是人要據以放行的那一份：私用區字元印出來是**空白**，所以舊行為把這一筆寫成
    # `…是抑制劑？→…是酶抑制劑？` 之外的樣子就分不出改了什麼（站上實測一題長成 `A→A`）。
    assert "\\ue2c6" in out, "碼位要寫出來，不然人看到的是一筆看不出差別的改動"


def test_a_restoration_says_it_stands_on_the_rule_not_on_a_precedent(tmp_path, monkeypatch, capsys):
    """事件的 `experience` 塊要分得出證據是哪一種：這一筆站的是判準，不是誰的先例（0 題）。"""
    queue = _pending_only(tmp_path, stored="下列何者是\ue2c6抑制劑？", page="下列何者是酶抑制劑？")
    _code, out = _run(queue, monkeypatch, capsys, apply=True)
    assert "寫了 1 筆機器修復" in out, out
    events = [json.loads(line) for line
              in (Path(queue) / "question_review_events.jsonl").read_text(encoding="utf-8").splitlines()]
    event = [row for row in events if row.get("reviewer") == "repair_experience_apply"][-1]
    assert event["correction"]["stem"] == "下列何者是酶抑制劑？"
    assert event["experience"]["form"] == "lost_glyph"
    assert event["experience"]["confirmed_questions"] == 0
    assert event["experience"]["evidence"] == []
    assert "可檢查的機械還原" in event["experience"]["why"]


def test_the_same_symbol_written_another_way_is_applied(tmp_path, monkeypatch, capsys):
    """同一族的排版變體（全形半形標點、彎引號、破折號）：字義不變，所以不必有人確認過。"""
    for stored, page in (("A，B", "A,B"), ("Graves’ disease", "Graves' disease"),
                         ("P2.5－P97.5", "P2.5-P97.5"), ("第1；2", "第1;2")):
        assert experience_mod.form_of(stored, page) == "typographic_variant", (stored, page)
    queue = _pending_only(tmp_path, stored="鉀離子，鈉離子", page="鉀離子,鈉離子")
    _code, out = _run(queue, monkeypatch, capsys)
    assert "可以自動套用的：1 題" in out, out


def test_a_whitespace_only_difference_is_not_a_repair():
    """只有空白的差別不算一筆修復：紙本判讀是同一行的第二次讀，抽取器留的空格不是內容
    （`dispute_apply.text._without_whitespace` 對同一件事寫過同樣的理由），而既然這一欄沒有別的
    東西要改，就沒有東西可以寫——所以這一欄回「不套用」，不是「套用一個空白」。
    """
    assert experience_mod.form_of("第 3 題", "第　3　題") == ""
    assert experience_mod.form_of("A B", "A  B") == ""


def test_a_decimal_point_is_never_read_as_a_middle_dot(tmp_path, monkeypatch, capsys):
    """`.／．` 夾在兩個數字中間是小數點（站上 17 題的 `.` → `·` 就是這一族）。

    守門：這一欄只要有 `\\d.\\d`，這一族就不准動——`P2.5` 讀成 `P2·5` 是改數字，不是改排法。
    同一欄只動破折號時照樣放行（守門要窄到只擋那一種）。
    """
    assert experience_mod.form_of("P2.5－P97.5", "P2·5-P97·5") == ""
    assert experience_mod.form_of("P2.5－P97.5", "P2.5-P97.5") == "typographic_variant"
    queue = _pending_only(tmp_path, stored="P2.5－P97.5", page="P2·5-P97·5")
    _code, out = _run(queue, monkeypatch, capsys, report=True)
    assert "可以自動套用的：0 題" in out, out


def test_a_size_letter_is_not_a_typographic_variant():
    """`s` → `S` 是內容：單位的字母大小寫不同義（站上被退過的那 4 欄就是它）。"""
    assert experience_mod.form_of("30 s", "30 S") == ""


def test_a_different_ideograph_is_a_word_decision():
    """換掉一個漢字是內容決定（`癇`／`癲`、`長效型`／`短效型`），形狀分不出對錯。"""
    assert experience_mod.form_of("癇", "癲") == ""
    assert experience_mod.form_of("長效型", "短效型") == ""


def test_a_restoration_does_not_carry_a_word_swap_with_it():
    """整欄同類才算數：私用區還原旁邊夾一個真改字，整欄退回「要人看過」那一邊。"""
    assert experience_mod.form_of("下列何者是\ue2c6抑制劑？", "下列何者是酶拮抗劑？") == ""


def test_one_private_use_character_for_another_is_not_a_restoration():
    """兩邊都不是文字時，這支沒有東西可以斷定哪一邊對——判準是「一邊壞了、一邊是真字元」。"""
    assert experience_mod.form_of("\ue2c6", "\ue2c7") == ""


def test_a_marker_change_takes_the_deterministic_class_away():
    """私用區還原**同時**動了下標標記時不是同一類的整組改動：回判讀迴圈，不自動套用。"""
    assert experience_mod.form_of("\ue2c6<sub>x</sub>", "酶x") == ""


def test_a_compatible_glyph_is_the_same_character_written_differently():
    """`⽣` → `生`、`⼗` → `十`：NFKC 說它們是同一個字，換過去不是改字。

    負控制兩條：`⻑` → `長` **不在**這一類（CJK Radicals Supplement 沒有相容分解，那一種只由偵測器
    自己的表提供），而 `①` → `1` 也不算（NFKC 會說同一個字，但換過去改掉了題目的讀法）。
    """
    assert experience_mod.form_of("⽣理食鹽水", "生理食鹽水") == "compatibility_fold"
    assert experience_mod.form_of("⼗", "十") == "compatibility_fold"
    assert experience_mod.form_of("⻑", "長") == ""
    assert experience_mod.form_of("①", "1") == ""


def test_the_answer_names_the_evidence_it_stands_on():
    """回覆反問的那一句不能比事實寬：「看得到的字元不變」對私用區還原不成立（被換掉的正是它）。"""
    restored = [{"field": "stem", "before": "A\ue2c6", "after": "A酶", "replace": True}]
    text = experience_mod.answer_text(restored)
    assert "私用區" in text and "字元不變" not in text
    markup = [{"field": "stem", "before": "MAOA", "after": "MAO<sub>A</sub>", "replace": True}]
    assert "字元不變" in experience_mod.answer_text(markup)


def test_a_question_at_the_cap_is_left_to_the_person(tmp_path, monkeypatch, capsys):
    """業主 2026-09-25 的循環上限，與 ③ 同一個定義（`withdrawals.MAX_ATTEMPTS`）：一題的機器修復
    被人退滿三次而沒有人接受過之後，這一支也不再自動套用它——它交給人，不交給下一輪。"""
    extra = []
    for index in range(3):
        extra.append(_repair(KEY_C, before="第 %d 版" % index, after="第 %d 版改" % index,
                             created_at="2026-09-25T1%d:00:00" % index))
        extra.append(_decision(KEY_C, "block", "2026-09-25T1%d:30:00" % index))
    queue = _planned_queue(tmp_path, extra_events=extra)
    _code, out = _run(queue, monkeypatch, capsys, report=True)
    assert "已退滿上限而不再修：1 題" in out, out
    assert "可以自動套用的：0 題" in out, out
