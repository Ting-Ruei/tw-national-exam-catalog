# -*- coding: utf-8 -*-
"""The repair loop's contract: it reads decisions, never writes one, and never invents a block.

The loop is the one place where a machine's finding can put a *human* decision back in front of a
person, so the properties below are the ones that would hurt if they broke:

  * a `reset_review` proposal is produced for a question a person judged, and only for that;
  * a question nobody judged is never "reopened" - there is nothing to reopen, and inventing one
    would manufacture a review decision out of silence (`GOV-05`);
  * running the collector cannot change any file;
  * an unreadable event line is skipped with a warning rather than silently treated as no decision,
    because "the log is corrupt" and "nobody decided" must not look the same.

Each test builds a throwaway queue rather than reading the live one, so it passes whether or not a
person has been reviewing - a test that only passes while the queue happens to hold decisions is a
test that reports on the reviewer, not the code.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
SCRIPTS = os.path.join(PKG, "scripts")
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, SCRIPTS)

import repair_loop  # noqa: E402


def _question(number, *, key, dispute_kinds=(), stem="下列何者正確？"):
    return {
        "candidate_key": key,
        "question_number": number,
        "stem": stem,
        "options": [{"key": letter, "text": "選項%s" % letter} for letter in "ABCD"],
        "answer": "A",
        "answer_payload": {"accepted_values": ["A"]},
        "metadata": {"normalized_subject_name": "測試科目", "year": 115, "exam_ordinal": 1},
        "disputes": ([{"kind": kind, "severity": "review"} for kind in dispute_kinds] or None),
    }


def _build_queue(tmp_path, questions, events):
    """A queue directory shaped the way `build_review_queue.py` writes one."""
    ui = tmp_path / "review-ui"
    ui.mkdir()
    with (ui / "candidates.jsonl").open("w", encoding="utf-8") as handle:
        for question in questions:
            handle.write(json.dumps(question, ensure_ascii=False) + "\n")
    with (ui / "question_review_events.jsonl").open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return str(tmp_path)


def _event(key, action, notes="", created_at="2026-09-21T10:00:00"):
    return {"candidate_key": key, "action": action, "notes": notes,
            "reviewer": "test", "created_at": created_at, "source": "linear_v2"}


def _run(args):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "repair_loop.py")] + args,
                          capture_output=True, text=True)


# ---------------------------------------------------------------- collecting the human's blocks

def test_a_block_with_no_detector_is_reported_as_the_place_to_build_one(tmp_path):
    """沒有偵測器解釋的 block 才是要建規則的地方；被解釋過的不是缺口。"""
    queue = _build_queue(
        tmp_path,
        [_question(1, key="moex:115020:305:33:1:question:q001", dispute_kinds=()),
         _question(2, key="moex:115020:305:33:1:question:q002", dispute_kinds=("lost-glyph",))],
        [_event("moex:115020:305:33:1:question:q001", "block", "答案沒進去"),
         _event("moex:115020:305:33:1:question:q002", "block")])
    blocked, rows = repair_loop.collect_blocks(repair_loop.review_ui_dir(queue))
    explained, unexplained = repair_loop.explain(blocked, rows)
    assert [entry["kinds"] for entry in unexplained] == [[]]
    assert [entry["kinds"] for entry in explained] == [["lost-glyph"]]
    assert [entry["notes"] for entry in unexplained] == ["答案沒進去"]


def test_an_accepted_question_is_not_a_blocking_input(tmp_path):
    """accept 不是「這裡錯了」，所以它不是迴圈的輸入。"""
    queue = _build_queue(
        tmp_path,
        [_question(1, key="k1"), _question(2, key="k2")],
        [_event("k1", "accept"), _event("k2", "needs_review")])
    blocked, _ = repair_loop.collect_blocks(repair_loop.review_ui_dir(queue))
    assert blocked == {}, "只有 block 是輸入；accept 與 needs_review 都不是"


def test_the_latest_decision_wins_so_a_reopened_question_can_be_re_blocked(tmp_path):
    """同一題有兩個事件時，看最新的那一個——否則重看的結果會被舊事件蓋掉。"""
    queue = _build_queue(
        tmp_path,
        [_question(1, key="k1")],
        [_event("k1", "block", created_at="2026-09-21T10:00:00"),
         _event("k1", "accept", created_at="2026-09-21T11:00:00")])
    blocked, _ = repair_loop.collect_blocks(repair_loop.review_ui_dir(queue))
    assert "k1" not in blocked, "最新決定是 accept，就不該再算成 block"


def test_a_group_level_action_is_not_a_question_decision(tmp_path):
    """群組層級的動作是關於共用題幹的，重設它會重開一批沒被逐題看過的題目。"""
    queue = _build_queue(
        tmp_path,
        [_question(1, key="k1")],
        [{"candidate_key": "k1", "action": "accept_group", "created_at": "2026-09-21T10:00:00"}])
    blocked, _ = repair_loop.collect_blocks(repair_loop.review_ui_dir(queue))
    assert blocked == {}


def test_a_corrupt_line_warns_and_is_skipped_rather_than_counted_as_no_decision(tmp_path, capsys):
    """壞行要出聲：「紀錄壞了」與「沒人決定」不能長得一樣。"""
    ui = tmp_path / "review-ui"
    ui.mkdir()
    (ui / "candidates.jsonl").write_text("", encoding="utf-8")
    (ui / "question_review_events.jsonl").write_text(
        '{not json\n' + json.dumps(_event("k1", "block")) + "\n", encoding="utf-8")
    latest = repair_loop.read_latest_actions(str(ui / "question_review_events.jsonl"))
    assert set(latest) == {"k1"}, "好的那一行還是要讀到"
    assert "not JSON" in capsys.readouterr().err


# ------------------------------------------------------------- rescan: what did the new rule do

def test_the_proposal_reopens_only_questions_a_person_actually_judged(tmp_path):
    """核心安全性質：新規則只能重開「有人判斷過」的題目。

    沒被判斷過的題目本來就排在佇列裡等人看，不需要、也不該產生 reset_review——把沉默當成
    一個決定，就是憑空製造審核紀錄。
    """
    queue = _build_queue(
        tmp_path,
        [_question(1, key="judged", stem="下\u083b何種診斷最有可能？"),
         _question(2, key="unjudged", stem="下\u083b何種處置最適當？")],
        [_event("judged", "accept")])
    proposal = tmp_path / "reset.json"
    result = _run(["--queue", queue, "--rescan", "--propose", str(proposal)])
    assert result.returncode == 0, result.stderr
    written = json.loads(proposal.read_text(encoding="utf-8"))
    keys = [entry["candidate_key"] for entry in written["proposal"]]
    assert keys == ["judged"], "只有被判斷過的那一題該被重開"
    entry = written["proposal"][0]
    assert entry["previous_action"] == "accept", "舊決定要被保留，不是被取代"
    assert entry["repair_kind"] == "detector_change"
    assert "substituted-script" in entry["reset_notes"]


def test_rescan_changes_no_file_in_the_queue(tmp_path):
    """收集與重掃是唯讀的：寫入要另外一個刻意的動作（append_reset_review_events.py --apply）。"""
    queue = _build_queue(
        tmp_path,
        [_question(1, key="judged", stem="下\u083b何種診斷最有可能？")],
        [_event("judged", "accept")])
    ui = os.path.join(queue, "review-ui")
    before = {name: os.path.getmtime(os.path.join(ui, name))
              for name in os.listdir(ui)}
    size_before = os.path.getsize(os.path.join(ui, "question_review_events.jsonl"))
    result = _run(["--queue", queue, "--rescan"])
    assert result.returncode == 0, result.stderr
    assert os.path.getsize(os.path.join(ui, "question_review_events.jsonl")) == size_before
    assert {name: os.path.getmtime(os.path.join(ui, name)) for name in os.listdir(ui)} == before


def test_a_question_whose_stored_disputes_already_match_is_not_reopened(tmp_path):
    """佇列已是最新程式碼的結果時，重掃不該重開任何東西——否則每次跑都會把全部人工作廢。"""
    queue = _build_queue(
        tmp_path,
        [{"candidate_key": "judged", "question_number": 1, "stem": "下\u083b何種診斷最有可能？",
          "options": [{"key": letter, "text": "選項%s" % letter} for letter in "ABCD"],
          "answer": "A", "answer_payload": {"accepted_values": ["A"]},
          "disputes": [{"kind": "substituted-script", "severity": "review"}],
          "metadata": {"normalized_subject_name": "測試科目"}}],
        [_event("judged", "accept")])
    proposal = tmp_path / "reset.json"
    result = _run(["--queue", queue, "--rescan", "--propose", str(proposal)])
    assert result.returncode == 0, result.stderr
    assert json.loads(proposal.read_text(encoding="utf-8"))["proposal"] == []


def test_the_queue_flag_accepts_the_subdirectory_as_well_as_the_root(tmp_path):
    """`--queue` 兩種寫法都要能用；讀錯目錄會回報「零筆決定」，而那看起來像沒人審過。"""
    queue = _build_queue(tmp_path, [_question(1, key="k1")], [_event("k1", "block")])
    assert repair_loop.review_ui_dir(queue) == os.path.join(queue, "review-ui")
    assert repair_loop.review_ui_dir(os.path.join(queue, "review-ui")) == \
        os.path.join(queue, "review-ui")


def test_a_missing_queue_exits_nonzero_instead_of_reporting_no_blocks(tmp_path):
    """找不到佇列要非零結束，不能印出「0 題」假裝掃過了。"""
    result = _run(["--queue", str(tmp_path / "nope")])
    assert result.returncode == 2
    assert "candidates.jsonl" in result.stderr


# ------------------------------------- 被退過的修復：迴圈自己的記憶（owner 2026-09-25）

def _repair_event(key, *, before, after, field="stem", created_at="2026-09-25T01:00:00",
                  applied="field", notes="依 dispute 的機械證據修復：(5-HT1A→5-HT<sub>1A</sub>)"):
    """一筆機器修復事件，形狀同 `apply_dispute_repairs.build_reset_event` 寫出來的。

    `notes` 預設**有值**，因為真實的事件都有（站上 141 筆修復、137 筆帶 summary）：這是
    「機器也會寫 `notes`」這件事在測試裡的代表。
    """
    return {"candidate_key": key, "action": "reset_review", "applied": applied,
            "reviewer": "repair_dispute_apply", "source": "qbr_dispute_apply",
            "repair_kind": "content_change", "notes": notes,
            "changes": [{"field": field, "from": before, "to": after}],
            "created_at": created_at}


def _withdrawal_event(key, *, fields=("stem",), created_at="2026-09-25T01:30:00"):
    """一筆撤回事件，形狀同 `apply_dispute_repairs.build_withdrawal_event`。

    撤銷事件沒有 `source`（契約就那幾個鍵），署名仍是 `repair_dispute_apply`——所以判斷「誰寫的」
    不能只看 `source`。
    """
    return {"candidate_key": key, "action": "reset_review", "applied": "withdrawn",
            "reviewer": "repair_dispute_apply", "withdraw": list(fields),
            "correction": None, "created_at": created_at}


def _events_path(queue):
    return os.path.join(repair_loop.review_ui_dir(queue), "question_review_events.jsonl")


def test_a_block_after_a_machine_repair_is_one_refusal(tmp_path):
    """機器改過、人打回＝一次「被退」，而且記下**被退的是哪一筆改動**。

    這是收斂迴圈唯一的記憶：沒有它，下一次讀取只知道「這一題被 block」，不知道機器試過什麼。
    """
    queue = _build_queue(
        tmp_path, [_question(1, key="k1")],
        [_repair_event("k1", before="5-HT1A", after="5-HT<sub>1A</sub>"),
         _event("k1", "block", created_at="2026-09-25T02:27:00")])
    refusals = repair_loop.rejections_by_key(_events_path(queue))
    assert refusals["k1"]["count"] == 1
    assert refusals["k1"]["fields"] == ["stem"]
    assert refusals["k1"]["changes"] == [{"field": "stem", "from": "5-HT1A",
                                          "to": "5-HT<sub>1A</sub>"}]


def test_a_question_the_machine_never_touched_is_not_a_refusal(tmp_path):
    """**負控制。** 沒有機器修復的 block 是**新問題**，不是「被退的修復」。

    把它算進去，報告上「重複被拒」那個數字就會把沒試過的題目也算成試過了。
    """
    queue = _build_queue(tmp_path, [_question(1, key="k1")], [_event("k1", "block")])
    assert repair_loop.rejections_by_key(_events_path(queue)) == {}


def test_the_second_refusal_counts_after_the_machine_took_its_repair_back(tmp_path):
    """**核心。** 站上真實的順序：修復 → 打回 → 撤回 → 打回（64 題就是這樣被退第二次）。

    撤回（`applied: "withdrawn"`）是那一筆修復的**下場**（被打回之後還原），不是「機器沒改過」。
    把它讀成「沒有待退的修復」，第二次打回就只算 0 次——實測：較窄的規則下 64 題全部只算 1 次、
    沒有任何一題算 2 次；正確的規則是那 64 題算 2 次。
    """
    queue = _build_queue(
        tmp_path, [_question(1, key="k1")],
        [_repair_event("k1", before="5-HT1A", after="5-HT<sub>1A</sub>"),
         _event("k1", "block", created_at="2026-09-25T02:27:00"),
         _withdrawal_event("k1"),
         _event("k1", "block", created_at="2026-09-25T02:29:00")])
    refusals = repair_loop.rejections_by_key(_events_path(queue))
    assert refusals["k1"]["count"] == 2
    # 而被退的是那筆已經被還原的改動——正是「不要再改一次同一個地方」要知道的那一筆。
    assert refusals["k1"]["changes"][0]["to"] == "5-HT<sub>1A</sub>"

    # 負控制：把撤回讀成「機器沒改過」的那一版，這裡會得到 1。
    assert repair_loop.fold_review_events(_events_path(queue))["k1"]["repairs_seen"] == 1


def test_an_accept_clears_the_refusal_count(tmp_path):
    """人接受了就不再是「被退過」：數字要回得到 0，否則報告會一直說著過去。"""
    queue = _build_queue(
        tmp_path, [_question(1, key="k1")],
        [_repair_event("k1", before="A", after="B"),
         _event("k1", "block", created_at="2026-09-25T02:27:00"),
         _event("k1", "accept", created_at="2026-09-25T03:00:00")])
    assert repair_loop.rejections_by_key(_events_path(queue)) == {}


def test_the_refused_change_is_the_one_that_was_on_screen(tmp_path):
    """兩筆修復、兩次打回：第二次留的是第二次的那一筆，不是第一次的。"""
    queue = _build_queue(
        tmp_path, [_question(1, key="k1")],
        [_repair_event("k1", before="一", after="二", created_at="2026-09-25T01:00:00"),
         _event("k1", "block", created_at="2026-09-25T01:10:00"),
         _repair_event("k1", before="二", after="三", created_at="2026-09-25T01:20:00"),
         _event("k1", "block", created_at="2026-09-25T01:30:00")])
    refusals = repair_loop.rejections_by_key(_events_path(queue))
    assert refusals["k1"]["count"] == 2
    assert [change["to"] for change in refusals["k1"]["changes"]] == ["三"]


def test_a_machine_repair_summary_is_not_a_reviewer_note(tmp_path):
    """**負控制。** 機器的修復事件也帶 `notes`，而這個頻道整條路都在說「審題者寫下的原話」。

    站上實測（2026-09-25）：464 題有還站著的註解，其中 **311 題**的那一句是機器寫的
    （「依 dispute 的機械證據修復：…」）。把過濾拿掉，`notes_by_key` 就會把機器的話交給
    `NOTES`／`BLOCKED_WITH_NOTE`，提示詞於是拿機器的摘要冒充人的方向。
    """
    queue = _build_queue(
        tmp_path, [_question(1, key="k1")],
        [_repair_event("k1", before="一", after="二"),
         _event("k1", "block", created_at="2026-09-25T02:27:00")])
    assert repair_loop.notes_by_key(_events_path(queue)) == {}, \
        "機器寫的修復摘要被當成審題者的註解了"
    # 而人寫的那一句仍然照樣傳得出去（否則上面那一行只是「什麼都不回」）。
    (tmp_path / "b").mkdir(parents=True, exist_ok=True)
    queue2 = _build_queue(
        tmp_path / "b", [_question(1, key="k1")],
        [_repair_event("k1", before="一", after="二"),
         _event("k1", "comment", "檢查上下標", created_at="2026-09-25T02:20:00"),
         _event("k1", "block", created_at="2026-09-25T02:27:00")])
    assert [row["notes"] for row in repair_loop.notes_by_key(_events_path(queue2))["k1"]] == \
        ["檢查上下標"]


def test_human_answers_are_scoped_to_their_candidate_and_exclude_machine_replies(tmp_path):
    path = tmp_path / "question_repair_questions.jsonl"
    events = [
        {"action": "ask", "question_id": "human", "candidate_key": "k1",
         "question": "Which figure belongs here?", "reviewer": "repair_agent"},
        {"action": "answer", "question_id": "human", "candidate_key": "k1",
         "answer": "The q2 figure is shown.", "reviewer": "local"},
        {"action": "ask", "question_id": "machine", "candidate_key": "k2",
         "question": "Is this change acceptable?", "reviewer": "repair_agent"},
        {"action": "answer", "question_id": "machine", "candidate_key": "k2",
         "answer": "Applied from prior experience.", "reviewer": "repair_experience_apply"},
    ]
    path.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
                    encoding="utf-8")

    assert repair_loop.human_answers_by_key(str(path)) == {
        "k1": {"questions": [
            {"candidate_key": "k1", "question": "Which figure belongs here?",
             "answer_text": "The q2 figure is shown."}
        ]}
    }


def test_the_report_names_the_questions_that_were_refused_twice(tmp_path):
    """人要看得見那個數字：報告要說出「打回 2 次」與被退的欄位，不能只是一個黑箱。"""
    queue = _build_queue(
        tmp_path, [_question(1, key="k1")],
        [_repair_event("k1", before="一", after="二", created_at="2026-09-25T01:00:00"),
         _event("k1", "block", created_at="2026-09-25T01:10:00"),
         _repair_event("k1", before="二", after="三", created_at="2026-09-25T01:20:00"),
         _event("k1", "block", created_at="2026-09-25T01:30:00")])
    result = _run(["--queue", queue, "--show", "3"])
    assert result.returncode == 0, result.stderr
    assert "被人打回 2 次以上的：1 題" in result.stdout
    assert "打回 2 次" in result.stdout and "stem" in result.stdout
