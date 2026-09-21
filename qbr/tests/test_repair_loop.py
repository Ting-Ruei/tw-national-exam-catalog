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
