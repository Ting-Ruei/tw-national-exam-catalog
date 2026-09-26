# -*- coding: utf-8 -*-
"""掃描與修復是兩個狀態。

owner 的原話：「目前可能會發生一個問題，就是現在錯題很多，所以 30 分鐘地端模型解決不了很多
問題，第二次掃描要算是已做還是未做，我認為**掃描到跟做完了是兩回事**」。

這個檔案把那句話變成行為。舊版只有一個「跑過／沒跑過」的概念，所以 100 題裡做完 5 題的那一輪，
下一輪看不出剩下的 95 題還沒做——它們要嘛被當成已完成（靜默遺失），要嘛被重掃（浪費）。

**負控制**：`test_a_question_found_but_not_finished_is_still_pending` 在 pending 與 fingerprint
被混成同一個狀態時必須失敗（也就是舊行為）。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))

from qbr import scan_state  # noqa: E402


def _q(key, kinds=("subscript_lost",), stem="題幹", options=("A", "B")):
    return {"candidate_key": key, "kinds": list(kinds), "stem": stem,
            "options": [{"text": text} for text in options]}


def test_a_new_question_is_selected(tmp_path):
    selected, state = scan_state.new_work([_q("k1")], {})
    assert [q["candidate_key"] for q in selected] == ["k1"]
    assert state["k1"]


def test_an_unchanged_question_is_not_selected_again(tmp_path):
    """收斂：同樣的內容不會每一輪重掃。這是舊版「每輪都選同一批」的反面。"""
    _first, state = scan_state.new_work([_q("k1")], {})
    selected, _state = scan_state.new_work([_q("k1")], state)
    assert selected == []


def test_a_repaired_question_is_selected_again_without_anyone_resetting_a_flag():
    """內容變了就是新工作——「重設旗標」不該是人要記得的事。"""
    _first, state = scan_state.new_work([_q("k1", stem="舊的")], {})
    selected, _state = scan_state.new_work([_q("k1", stem="修好的")], state)
    assert len(selected) == 1


def test_a_new_dispute_kind_is_new_work():
    """偵測器改變心意是一個新的問題，即使文字沒動。"""
    _first, state = scan_state.new_work([_q("k1", kinds=("subscript_lost",))], {})
    selected, _state = scan_state.new_work([_q("k1", kinds=("subscript_lost", "figure_missing"))], state)
    assert len(selected) == 1


def test_a_standing_human_action_is_new_work():
    """人剛擋掉一題——那題對他來說是新的，即使內容一模一樣。"""
    _first, state = scan_state.new_work([_q("k1")], {}, {"k1": "accept"})
    selected, _state = scan_state.new_work([_q("k1")], state, {"k1": "block"})
    assert len(selected) == 1

def test_reviewer_answers_principles_and_figure_facts_are_new_work():
    question = _q("k1")
    answers = {"k1": {"questions": []}}
    principles = ["Check figure ownership before changing text."]
    figures = {"k1": "figure facts v1"}
    _first, state = scan_state.new_work(
        [question], {}, {"k1": "block"}, {}, {}, answers, principles, figures)
    unchanged, _ = scan_state.new_work(
        [question], state, {"k1": "block"}, {}, {}, answers, principles, figures)
    assert unchanged == [], "identical prompt context must converge"

    human_answer = {"k1": {"questions": [
        {"candidate_key": "k1", "question": "What is wrong?", "answer_text": "The label is q2."}
    ]}}
    answer_changed, _ = scan_state.new_work(
        [question], state, {"k1": "block"}, {}, {}, human_answer, principles, figures)
    assert [row["candidate_key"] for row in answer_changed] == ["k1"]

    principle_changed, _ = scan_state.new_work(
        [question], state, {"k1": "block"}, {}, {}, answers,
        principles + ["Do not infer ownership from visual similarity."], figures)
    assert [row["candidate_key"] for row in principle_changed] == ["k1"]

    figure_changed, _ = scan_state.new_work(
        [question], state, {"k1": "block"}, {}, {}, answers, principles,
        {"k1": "figure facts v2"})
    assert [row["candidate_key"] for row in figure_changed] == ["k1"]


# ------------------------------------------------------------------ found ≠ finished

def test_a_question_found_but_not_finished_is_still_pending(tmp_path):
    """**核心負控制。** 一輪找到 3 題、只做完 1 題，另外 2 題必須還是 pending。

    舊行為把它們混成一個狀態，於是它們會被當成已完成（靜默遺失）。owner 說的就是這件事。
    """
    queue_dir = str(tmp_path)
    selected, state = scan_state.new_work([_q("k1"), _q("k2"), _q("k3")], {})
    assert len(selected) == 3
    scan_state.save_state(queue_dir, state)
    scan_state.mark_pending(queue_dir, [q["candidate_key"] for q in selected])

    # 這一輪只完成了 k1
    scan_state.mark_processed(queue_dir, "k1")

    pending = scan_state.pending_keys(queue_dir)
    assert pending == ["k2", "k3"], "掃到但沒做完的題被當成完成了：%r" % pending


def test_a_pending_question_is_not_re_scanned_by_content(tmp_path):
    """它已經被找到了，所以指紋沒變時不該重掃——pending 是「等著做」，不是「還沒看過」。"""
    queue_dir = str(tmp_path)
    _selected, state = scan_state.new_work([_q("k1")], {})
    scan_state.save_state(queue_dir, state)
    scan_state.mark_pending(queue_dir, ["k1"])
    selected, _state = scan_state.new_work([_q("k1")], scan_state.load_state(queue_dir))
    assert selected == [], "已排進 pending 的題又被重掃了一次"
    assert scan_state.pending_keys(queue_dir) == ["k1"]


def test_finishing_takes_a_question_out_of_pending(tmp_path):
    queue_dir = str(tmp_path)
    scan_state.mark_pending(queue_dir, ["k1", "k2"])
    scan_state.mark_processed(queue_dir, "k1")
    assert scan_state.pending_keys(queue_dir) == ["k2"]


# ------------------------------------------------------------------ robustness

def test_a_corrupt_store_is_rebuilt_rather_than_fatal(tmp_path):
    """一個壞掉的 JSON 不該讓常駐停止——代價是一輪重複的讀取，那是可承受的。"""
    queue_dir = str(tmp_path)
    with open(os.path.join(queue_dir, scan_state.SCAN_STATE), "w", encoding="utf-8") as handle:
        handle.write("{not json")
    assert scan_state.load_state(queue_dir) == {}
    selected, _state = scan_state.new_work([_q("k1")], scan_state.load_state(queue_dir))
    assert len(selected) == 1


def test_state_is_written_atomically(tmp_path):
    """不會留下半寫的檔案：`.tmp` 寫完才 `replace`。"""
    queue_dir = str(tmp_path)
    scan_state.save_state(queue_dir, {"k1": "abc"})
    assert not os.path.exists(os.path.join(queue_dir, scan_state.SCAN_STATE + ".tmp"))
    with open(os.path.join(queue_dir, scan_state.SCAN_STATE), encoding="utf-8") as handle:
        assert json.load(handle) == {"k1": "abc"}


def test_the_scan_writes_state_where_the_repair_pass_reads_it(tmp_path):
    """**這個缺陷真的發生過，而且症狀是靜默的。**

    `scan_for_repairs.py` 一開始把指紋寫在 `--queue` 指向的目錄，而 `confirm_dispute.py` 把同一個
    flag 經由 `repair_loop.review_ui_dir` 解析成 `review-ui/` 子目錄。於是掃描剛排進 813 題，
    修復卻回報「pending 是空的」——一個看起來像「沒有工作」的訊息，實際上是工作被寫到隔壁。

    兩者必須對「這個佇列在哪」有同一個答案。
    """
    import json
    import os as _os
    import subprocess
    import sys as _sys

    queue_root = tmp_path / "live"
    ui = queue_root / "review-ui"
    ui.mkdir(parents=True)
    (ui / "candidates.jsonl").write_text(
        json.dumps({"candidate_key": "k1", "stem": "題", "options": [{"text": "A"}],
                    "disputes": [{"kind": "subscript_lost"}]}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    # The scan's gate is a human block (`blocked_keys`), so the queue has to carry one or the
    # question would never be selected and this test would be asserting about nothing.
    (ui / "question_review_events.jsonl").write_text(
        json.dumps({"candidate_key": "k1", "action": "block", "reviewer": "local"}) + "\n",
        encoding="utf-8")

    scripts = _os.path.join(PKG, "scripts")
    env = dict(_os.environ)
    env["PYTHONPATH"] = _os.path.join(PKG, "src")

    # **Both spellings of `--queue`.** This is the part the first version of this test missed, and
    # the miss was real: with `--queue <root>` the naive implementation also lands on the root, so
    # the test passed while the defect was still present. The defect only shows when the flag names
    # `review-ui/` — which is equally natural to type, and is what `repair_loop.review_ui_dir`'s own
    # docstring says the flag must tolerate. A regression test that only drives the spelling the bug
    # happens to survive is not a regression test.
    _sys.path.insert(0, scripts)
    import repair_loop

    for spelling in (str(queue_root), str(ui)):
        for dirpath, _dirnames, filenames in _os.walk(str(tmp_path)):
            if scan_state.SCAN_STATE in filenames:
                _os.remove(_os.path.join(dirpath, scan_state.SCAN_STATE))

        result = subprocess.run(
            [_sys.executable, _os.path.join(scripts, "scan_for_repairs.py"),
             "--queue", spelling],
            capture_output=True, text=True, env=env)
        assert result.returncode == 0, result.stderr

        # Where the repair pass reads: the same resolver `confirm_dispute.py --pending-only` uses.
        resolved = repair_loop.review_ui_dir(spelling)
        root = _os.path.dirname(resolved)
        assert scan_state.pending_keys(root) == ["k1"], \
            "用 --queue %s 掃描後，pending 不在修復讀的位置 %s：%s" % (
                spelling, root, _os.listdir(root))

        # And exactly one state file, so there is no second spelling of "where this queue is".
        found = []
        for dirpath, _dirnames, filenames in _os.walk(str(tmp_path)):
            if scan_state.SCAN_STATE in filenames:
                found.append(dirpath)
        assert found == [str(root)], \
            "狀態檔有 %d 份（應該是 1）：%r" % (len(found), found)


# ------------------------------------------------------------------ a cap is a budget, not a read

def _scan_queue(tmp_path, keys, *, blocked=None, kindless=()):
    """A queue in the layout both scripts share: `blocked` (default: all of `keys`) carries a human block.

    The block is not decoration: since 2026-09-24 the scan's gate is "a person blocked this question"
    (`confirm_dispute.blocked_keys`), so a fixture without a decision is a fixture that selects
    nothing - which is the correct answer for it, and not what these tests are about.
    """
    blocked = set(keys if blocked is None else blocked)
    queue_root = tmp_path / "live"
    ui = queue_root / "review-ui"
    ui.mkdir(parents=True)
    (ui / "candidates.jsonl").write_text(
        "".join(json.dumps({"candidate_key": key, "stem": "題 %s" % key,
                            "options": [{"text": "A"}, {"text": "B"}],
                            "disputes": [] if key in kindless
                                        else [{"kind": "subscript_lost"}]},
                           ensure_ascii=False) + "\n"
                for key in keys),
        encoding="utf-8")
    with (ui / "question_review_events.jsonl").open("w", encoding="utf-8") as handle:
        for key in keys:
            if key in blocked:
                handle.write(json.dumps({"candidate_key": key, "action": "block",
                                         "reviewer": "local"}, ensure_ascii=False) + "\n")
    return queue_root


def _run_scan(queue_root, *extra):
    import os as _os
    import subprocess
    import sys as _sys

    env = dict(_os.environ)
    env["PYTHONPATH"] = _os.path.join(PKG, "src")
    return subprocess.run(
        [_sys.executable, _os.path.join(PKG, "scripts", "scan_for_repairs.py"),
         "--queue", str(queue_root), *extra],
        capture_output=True, text=True, env=env)


def test_only_the_questions_a_person_blocked_are_scanned(tmp_path):
    """**負控制：閘門是「人擋掉了」，不是「有偵測器解釋得了的爭議」。**（owner 2026-09-24）

    實測本機鏡射（`data/review-queues/live`）：813 題有爭議種類，其中只有 105 題是人 block 的；
    而 279 題被 block 的裡面有 **174 題一個種類都沒有**。舊閘門同時做錯兩個方向：替 708 題
    沒有人看過的題排隊，又對人親手標記的那 174 題視而不見——而後者正是「只有紙本能說哪裡錯」
    的那種題。

    這個測試兩個方向都釘住：沒有 block 的題不排隊，被 block 而**沒有種類**的題要排隊。
    舊閘門下它會失敗（pending 空）。
    """
    queue_root = _scan_queue(tmp_path, ["unjudged", "flagged"],
                             blocked=["flagged"], kindless=["flagged"])

    result = _run_scan(queue_root)
    assert result.returncode == 0, result.stderr
    assert scan_state.pending_keys(str(queue_root)) == ["flagged"], \
        "掃描的工作清單不是「人已 block 的題」：%r" % scan_state.pending_keys(str(queue_root))


# ------------------------------------------------------------------ a cap is a budget, not a read

def test_a_capped_scan_does_not_mark_the_rest_as_seen(tmp_path):
    """**負控制。** `--limit` 是這一輪的名額，不是「其餘的已經看過」。

    舊行為：`new_work` 替**所有**選中的題存了指紋，卻只把截短後的清單放進 pending。被截掉的題
    既不在 pending、指紋又已經存好，所以下一輪掃描看到「狀態沒變」→ 永遠不會再被選。整批工作
    消失，而且沒有任何錯誤訊息——它只是不再出現。這是這條線最糟的一種缺陷：安靜。

    在舊行為下這條必須失敗，失敗點在第二次掃描：它會回報 0 題可做、pending 停在 1 題。
    """
    queue_root = _scan_queue(tmp_path, ["k1", "k2", "k3"])

    result = _run_scan(queue_root, "--limit", "1")
    assert result.returncode == 0, result.stderr
    assert scan_state.pending_keys(str(queue_root)) == ["k1"], \
        "名額 1 題，pending 卻是 %r" % scan_state.pending_keys(str(queue_root))

    # 名額以外的兩題還是工作。第二次掃描（無名額）必須重新選到它們。
    result = _run_scan(queue_root)
    assert result.returncode == 0, result.stderr
    pending = scan_state.pending_keys(str(queue_root))
    assert sorted(pending) == ["k1", "k2", "k3"], \
        "被 --limit 截掉的題沒有回到工作清單（靜默遺失）：%r" % pending


# ------------------------------------------------------------------ 欠②一次讀紙本

def _write_findings(queue_root, rows):
    """這一輪的 findings 流（`confirm_dispute.confirmed_keys` 讀的就是它）。"""
    ui = queue_root / "review-ui"
    with (ui / "question_ai_findings.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _a_reading(key, population, *, transcription="下列何者…", error=None):
    """一筆判讀紀錄，形狀照 `confirm_dispute` 寫的（只用得到 population 與 finding）。"""
    return {"candidate_key": key, "population": population, "error": error,
            "finding": {"transcription": transcription}}


def test_a_blocked_question_without_a_dispute_read_is_owed_one(tmp_path):
    """**站得住的人類 block ＋ 沒有可用的 dispute 判讀＝欠著的工作**（業主 2026-09-25）。

    站上實測（2026-09-25）：355 題站得住的 block 裡 **210 題**沒有可用的 dispute 判讀
    （166 題只有整庫掃描的 `category-scan`、43 題的判讀自己失敗、其餘從沒被判讀過），而套用端
    **只吃 `population == "dispute"`**——所以那些題的「機器寫好的改法」永遠不會被套用，而掃描
    因為「指紋沒變」也不會再排它們。業主的原話：「你明明很多題目都有自己寫應該怎麼改，但為什麼
    沒有按照你寫的去修改」。

    這條測的是那條規則的兩半：`category-scan` 的紀錄**不算**讀過（它不算的理由就是套用端不採信它），
    有轉錄的 `dispute` 紀錄才算。第二半是負控制——沒有它，這條測試就只是「有沒有排隊」。
    """
    sys.path.insert(0, os.path.join(PKG, "scripts"))
    import confirm_dispute
    from qbr import ai_findings, discuss
    import repair_loop

    queue_root = _scan_queue(tmp_path, ["swept", "read"])
    ui = queue_root / "review-ui"
    candidates = {row["candidate_key"]: row for row in
                  repair_loop.load_candidates(str(ui / "candidates.jsonl"))}
    read = _a_reading("read", "dispute")
    read["reading_sha256"] = ai_findings.reading_fingerprint(candidates["read"])
    events_path = str(ui / "question_review_events.jsonl")
    questions_path = str(ui / discuss.REPAIR_QUESTIONS_STREAM)
    principles_path = str(ui / discuss.PRINCIPLES_STREAM)
    read["review_context_sha256"] = confirm_dispute.prompt_context_fingerprint(
        candidates["read"],
        principles=ai_findings.principles_for_prompt(discuss.load_events(principles_path)),
        answers=repair_loop.human_answers_by_key(questions_path).get("read"),
        notes=repair_loop.notes_by_key(events_path).get("read"),
        rejected=repair_loop.rejections_by_key(events_path).get("read"))
    _write_findings(queue_root, [_a_reading("swept", "category-scan"), read])

    # 第一次掃描：兩題都是「狀態有變」（沒有指紋），所以兩題都排隊——這不是這條在測的東西。
    result = _run_scan(queue_root)
    assert result.returncode == 0, result.stderr
    assert sorted(scan_state.pending_keys(str(queue_root))) == ["read", "swept"]

    # 清空 pending（②做完之後它就清掉了）：現在只剩「欠著的工作」這一條規則會排隊。
    scan_state.mark_pending(str(queue_root), [])
    result = _run_scan(queue_root)
    assert result.returncode == 0, result.stderr
    pending = scan_state.pending_keys(str(queue_root))
    assert pending == ["swept"], \
        "只有整庫掃描紀錄的那一題沒有被當成「欠一次 dispute 判讀」：%r" % pending
    # 而且掃描自己說得出有幾題欠著（不是安靜地排隊）。
    assert "欠②一次讀紙本）：1 題" in result.stdout, result.stdout


def test_a_stale_pending_entry_is_drained_after_its_current_read(tmp_path):
    import json as _json
    import os as _os

    sys.path.insert(0, _os.path.join(PKG, "scripts"))
    import confirm_dispute
    from qbr import ai_findings

    queue_root = _scan_queue(tmp_path, ["k1"])
    result = _run_scan(queue_root)
    assert result.returncode == 0, result.stderr
    assert scan_state.pending_keys(str(queue_root)) == ["k1"]

    ui = queue_root / "review-ui"
    question = _json.loads((ui / "candidates.jsonl").read_text(encoding="utf-8").splitlines()[0])
    inputs = confirm_dispute.load_prompt_inputs(str(ui))
    key = question["candidate_key"]
    _write_findings(queue_root, [{
        "candidate_key": key,
        "population": "dispute",
        "reading_sha256": ai_findings.reading_fingerprint(question),
        "review_context_sha256": confirm_dispute.prompt_context_fingerprint(
            question, principles=inputs["principles"], answers=inputs["answers_by_key"].get(key),
            notes=inputs["notes_by_key"].get(key), rejected=inputs["rejections_by_key"].get(key)),
        "finding": {"transcription": {"stem": question["stem"]}},
    }])

    result = _run_scan(queue_root)
    assert result.returncode == 0, result.stderr
    assert scan_state.pending_keys(str(queue_root)) == [], \
        "a successfully read, unchanged question must not remain stranded in old pending state"


def test_a_failed_or_absent_read_does_not_count_as_read(tmp_path):
    """**沒有讀出來的那一筆不算讀過**（一筆 error 紀錄也是「欠著」）。

    這一條與上面共用同一個判準（`confirm_dispute.confirmed_keys`），而那個函式自己的理由記著一次
    真實事故：第一版只要求 `finding` 非空，於是五題模型讀不出來的題被標成完成、永遠不會再問，
    而那是**安靜地**掉工作——「跳過」與「讀過」在紀錄上長得一模一樣。所以 error 的紀錄不進
    confirmed，於是這一題仍然欠②一次。
    """
    queue_root = _scan_queue(tmp_path, ["failed", "never-asked"])
    _write_findings(queue_root, [_a_reading("failed", "dispute", error="unparsed")])

    _run_scan(queue_root)
    scan_state.mark_pending(str(queue_root), [])
    result = _run_scan(queue_root)
    assert result.returncode == 0, result.stderr
    assert sorted(scan_state.pending_keys(str(queue_root))) == ["failed", "never-asked"], \
        "讀失敗或沒讀過的題都還欠一次：%r" % scan_state.pending_keys(str(queue_root))


# ------------------------------------------------------------------ 循環上限（業主 2026-09-25）

def _with_attempts(queue_root, key, rounds):
    """替某一題加上 `rounds` 次「機器修復 → 人打回」（就是業主說的「循環」）。"""
    ui = queue_root / "review-ui"
    with (ui / "question_review_events.jsonl").open("a", encoding="utf-8") as handle:
        for index in range(rounds):
            handle.write(json.dumps({"candidate_key": key, "action": "reset_review",
                                     "source": "qbr_dispute_apply", "reviewer": "repair_dispute_apply",
                                     "applied": "field", "created_at": "2026-09-%02dT10:00:00" % (index + 1)},
                                    ensure_ascii=False) + "\n")
            handle.write(json.dumps({"candidate_key": key, "action": "block", "reviewer": "local",
                                     "created_at": "2026-09-%02dT11:00:00" % (index + 1)},
                                    ensure_ascii=False) + "\n")


def test_a_question_at_the_cap_is_not_scanned_until_a_person_reopens_it(tmp_path):
    """業主 2026-09-25：循環三次之後才送入「AI無法判斷」。

    退滿的題目不再被選（所以也不會花一次判讀去問——貴的那一步在②），而人再表態一次（放回或接受
    把次數歸零、再擋一次）它就自己回到工作清單：上限不是靜默丟掉，是等人。
    """
    queue_root = _scan_queue(tmp_path, ["k1", "k2"])
    _with_attempts(queue_root, "k2", 3)

    result = _run_scan(queue_root)
    assert result.returncode == 0, result.stderr
    assert "已退滿 3 次" in result.stdout, result.stdout
    assert scan_state.pending_keys(str(queue_root)) == ["k1"], \
        scan_state.pending_keys(str(queue_root))

    ui = queue_root / "review-ui"
    with (ui / "question_review_events.jsonl").open("a", encoding="utf-8") as handle:
        # 放回把次數歸零，而它同時讓這一題離開「人已 block」這個人口——所以要回到工作清單，
        # 人得再表態一次（這正是「上限不是墓碑」的意思）。
        for action in ("unblock", "block"):
            handle.write(json.dumps({"candidate_key": "k2", "action": action, "reviewer": "local"},
                                    ensure_ascii=False) + "\n")
    result = _run_scan(queue_root)
    assert result.returncode == 0, result.stderr
    assert sorted(scan_state.pending_keys(str(queue_root))) == ["k1", "k2"], \
        scan_state.pending_keys(str(queue_root))
