# -*- coding: utf-8 -*-
"""被同意的機器修復 → 原則**提案**：業主 2026-09-25 的收斂迴圈，同意的那一半。

**這個檔案為什麼存在。** 業主的句子是：「AI 修理之後，人工會同意或拒絕；被**重複拒絕**的題目要讓它
**再進入掃描的迴圈**，然後再修正；被**同意**的題目則成為**可以學習的原則**。」

拒絕那一半有三個讀者在讀同一份事件流（`repair_loop.rejections_by_key` 的計數 →
`scan_state.question_fingerprint`、`ai_findings.rejected_note`、`repair_loop.explain` 的報告）。
同意那一半**原本一個讀者都沒有**：`repair_loop.confirmations_by_key` 這一輪才出現，而
`scripts/review_feedback.py` 那條學習路徑的寫入端只認 `correction`（人自己改文字），
所以「人同意了機器改的一筆」在整條管線上不產生任何訊號。

四件事，四個測試群：

  1. **確認的判準**：修復站著時按 accept ＝同意；修復被撤回後按 accept、或按 unblock，都不是。
  2. **分類與重建**：被同意的改動由那一筆修復的逐欄 `from`/`to` 重建（不是讀現在的列），
     類別由既有的 `review_feedback.classify_change` 判定。
  3. **提案的門檻**：同一類別要有 ≥2 筆**不同題目**的確認才問模型；一筆是軼事。模型說
     `no_generalization`（例子撐不起通則）或 `already_covered`（現行原則裡已經有了）時不寫提案
     （負控制）。
  4. **提案不是核准**：寫出去的只有 `add`，而且 `approved_principles` 仍然是空的——
     核准之前那條原則不會進任何一次讀取的提示詞（`ai_findings.principles_for_prompt`）。
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))
sys.path.insert(0, os.path.dirname(PKG))

import repair_loop  # noqa: E402
from qbr import ai_findings, discuss  # noqa: E402

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "propose_principles", os.path.join(PKG, "scripts", "propose_principles.py"))
propose_principles = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(propose_principles)

KEY_A = "moex:105100:305:33:1:question:q001"
KEY_B = "moex:105100:305:33:1:question:q002"
#: 另一份試卷上的一題（`paper` 是候選鍵的第 2、3 段：`105100:305` 與 `108020:301`）。範圍測試要用
#: **不同**試卷／科目的兩筆例子，否則「例子之間不共享範圍」這件事在測試裡也看不到。
KEY_C = "moex:108020:301:33:1:question:q003"


# ------------------------------------------------------------------ fixtures

def _question(number, key, stem):
    return {
        "candidate_key": key,
        "question_number": number,
        "stem": stem,
        "options": [{"key": letter, "text": "選項%s" % letter} for letter in "ABCD"],
        "answer": "B",
        "answer_payload": {"accepted_values": ["B"]},
        "metadata": {"normalized_subject_name": "測試科目"},
    }


def _repair_event(key, *, before, after, field="stem", created_at="2026-09-25T01:00:00",
                  applied="field", notes="依紙本判讀整欄替換：⻑→長"):
    """A machine repair, shaped like `apply_dispute_repairs.build_reset_event` writes one.

    `applied="field"` means `from`/`to` are the **whole field** (that is what the real event holds);
    `applied="substitution"` means they are the character pair.
    """
    return {"candidate_key": key, "action": "reset_review", "applied": applied,
            "reviewer": "repair_dispute_apply", "source": "qbr_dispute_apply",
            "repair_kind": "content_change", "notes": notes,
            "changes": [{"field": field, "from": before, "to": after}],
            "created_at": created_at}


def _decision(key, action, created_at):
    return {"candidate_key": key, "action": action, "notes": "", "reviewer": "local",
            "source": "linear_v2", "created_at": created_at}


def _queue(tmp_path, *, events, stems=None):
    """A queue directory, with the two streams the review UI writes."""
    ui = tmp_path / "review-ui"
    ui.mkdir(parents=True, exist_ok=True)
    stems = stems or {}
    questions = [_question(1, KEY_A, stems.get(KEY_A, "下列何者是⻑效型的藥物？")),
                 _question(2, KEY_B, stems.get(KEY_B, "下列何者是⻑效型的抗生素？"))]
    with (ui / "candidates.jsonl").open("w", encoding="utf-8") as handle:
        for question in questions:
            handle.write(json.dumps(question, ensure_ascii=False) + "\n")
    with (ui / "question_review_events.jsonl").open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return str(ui)


def _run(queue_dir, *extra):
    return subprocess.run([sys.executable,
                           os.path.join(PKG, "scripts", "propose_principles.py"),
                           "--queue", queue_dir] + list(extra),
                          capture_output=True, text=True)


# ------------------------------------------------------- 1. what counts as a confirmation

def test_an_accept_while_a_repair_stands_is_a_confirmation(tmp_path):
    """人按下 accept、而機器的修復還站在題目上＝同意了那一筆改動。"""
    queue = _queue(tmp_path, events=[
        _repair_event(KEY_A, before="下列何者是⻑效型的藥物？", after="下列何者是長效型的藥物？"),
        _decision(KEY_A, "accept", "2026-09-25T02:00:00")])
    confirmed = repair_loop.confirmations_by_key(
        os.path.join(queue, "question_review_events.jsonl"))
    assert confirmed[KEY_A]["count"] == 1
    assert confirmed[KEY_A]["applied"] == "field"
    assert confirmed[KEY_A]["confirmed_at"] == "2026-09-25T02:00:00"


def test_a_repair_taken_back_is_not_what_the_person_accepted(tmp_path):
    """**負控制。** 修復被撤回之後才按 accept，接受的是解析器那一版，不是那一筆改動。

    把撤回讀成「機器改過」的那一版會把這題算成確認，於是模型會學到一筆**人沒有同意**的改動。
    """
    queue = _queue(tmp_path, events=[
        _repair_event(KEY_A, before="下列何者是⻑效型的藥物？", after="下列何者是長效型的藥物？"),
        {"candidate_key": KEY_A, "action": "reset_review", "applied": "withdrawn",
         "reviewer": "repair_dispute_apply", "withdraw": ["stem"], "correction": None,
         "created_at": "2026-09-25T01:30:00"},
        _decision(KEY_A, "accept", "2026-09-25T02:00:00")])
    assert repair_loop.confirmations_by_key(
        os.path.join(queue, "question_review_events.jsonl")) == {}


def test_a_question_the_machine_never_touched_is_not_a_confirmation(tmp_path):
    """**負控制。** 沒有人工改動的 accept 是「這一題沒問題」，不是「這一筆改動是對的」。"""
    queue = _queue(tmp_path, events=[_decision(KEY_A, "accept", "2026-09-25T02:00:00")])
    assert repair_loop.confirmations_by_key(
        os.path.join(queue, "question_review_events.jsonl")) == {}


def test_unblocking_a_question_is_not_agreeing_with_its_text(tmp_path):
    """**負控制。** `unblock` 把題目放回可用，它沒有說這一題的文字是對的。"""
    queue = _queue(tmp_path, events=[
        _repair_event(KEY_A, before="下列何者是⻑效型的藥物？", after="下列何者是長效型的藥物？"),
        _decision(KEY_A, "unblock", "2026-09-25T02:00:00")])
    assert repair_loop.confirmations_by_key(
        os.path.join(queue, "question_review_events.jsonl")) == {}


# ------------------------------------------------- 2. rebuilding what was confirmed

def test_the_rebuild_uses_the_repairs_own_text_not_todays_row(tmp_path):
    """`before`/`after` 由修復自己的 `from`/`to` 重建：現在的列可能是撤回或再改過的結果。"""
    queue = _queue(tmp_path, events=[
        _repair_event(KEY_A, before="下列何者是⻑效型的藥物？", after="下列何者是長效型的藥物？"),
        _decision(KEY_A, "accept", "2026-09-25T02:00:00")])
    rows = propose_principles.load_rows(queue)
    confirmed = propose_principles.confirmed_changes(queue, rows)
    assert len(confirmed) == 1
    assert confirmed[0]["before"]["stem"] == "下列何者是⻑效型的藥物？"
    assert confirmed[0]["after"]["stem"] == "下列何者是長效型的藥物？"
    # 一個字的替換，既有的分類器會說 `exact_ocr_rule`（同一個判斷，不是這裡再寫一次）。
    assert confirmed[0]["change_class"] == "exact_ocr_rule"


def test_a_substitution_repair_rebuilds_the_character_swap_not_the_whole_field(tmp_path):
    """單字替換（`applied: "substitution"`）的 `from`/`to` 是**一個字**。

    直接賦值會把整個題幹換成一個字，於是模型看到的例子是「一個字」，而人同意的是那一題的整句。
    站上 141 筆機器修復裡有 4 筆是這一種，所以它不是假想的情況。
    """
    queue = _queue(tmp_path, events=[
        _repair_event(KEY_A, before="⻑", after="長", applied="substitution"),
        _decision(KEY_A, "accept", "2026-09-25T02:00:00")])
    confirmed = propose_principles.confirmed_changes(queue, propose_principles.load_rows(queue))
    assert len(confirmed) == 1
    assert confirmed[0]["before"]["stem"] == "下列何者是⻑效型的藥物？"
    assert confirmed[0]["after"]["stem"] == "下列何者是長效型的藥物？"
    # 而分類器讀的是這兩個完整的題幹（單字替換＝`exact_ocr_rule`），不是那兩個字。
    assert confirmed[0]["change_class"] == "exact_ocr_rule"


def test_a_repair_that_changed_nothing_visible_is_skipped(tmp_path):
    """沒有一場看得見的改變就沒有可學的東西——不寫、不問模型。"""
    queue = _queue(tmp_path, events=[
        _repair_event(KEY_A, before="一樣的字", after="一樣的字", field="not_a_visible_field"),
        _decision(KEY_A, "accept", "2026-09-25T02:00:00")])
    assert propose_principles.confirmed_changes(queue, propose_principles.load_rows(queue)) == []


# ------------------------------------------------------- 3. the threshold, and the answer

def test_one_confirmation_is_an_anecdote_not_a_rule(tmp_path):
    """**負控制。** 只有一筆確認時不問模型、不寫提案：一條從一題來的通則就是一個巧合。"""
    queue = _queue(tmp_path, events=[
        _repair_event(KEY_A, before="下列何者是⻑效型的藥物？", after="下列何者是長效型的藥物？"),
        _decision(KEY_A, "accept", "2026-09-25T02:00:00")])
    result = _run(queue, "--apply")
    assert result.returncode == 0, result.stderr
    assert "exact_ocr_rule" in result.stdout
    assert "未達門檻" in result.stdout
    assert not os.path.exists(os.path.join(queue, discuss.PRINCIPLES_STREAM)), \
        "只有一筆確認就寫了提案"
    # 乾跑（沒有 --apply）連模型都不該叫：這裡用一個連不上的 endpoint 名稱以外的路徑證明——
    # 它連 `--apply` 都沒加，所以上面那行 `_run(queue, "--apply")` 才是唯一叫得到模型的一次。


def _two_confirmed(tmp_path):
    """Two confirmed repairs of the **same class** on two different questions."""
    return _queue(tmp_path, events=[
        _repair_event(KEY_A, before="下列何者是⻑效型的藥物？", after="下列何者是長效型的藥物？"),
        _decision(KEY_A, "accept", "2026-09-25T02:00:00"),
        _repair_event(KEY_B, before="下列何者是⻑效型的抗生素？", after="下列何者是長效型的抗生素？",
                      created_at="2026-09-25T01:10:00"),
        _decision(KEY_B, "accept", "2026-09-25T02:10:00")])


def test_two_confirmed_questions_of_one_class_become_one_proposal(tmp_path, monkeypatch):
    """**核心。** 兩題同類的確認 → 一個 `add` 提案，而且**沒有**任何核准事件。

    核准是人的動作（`/api/principles`），所以這裡同時證明兩件事：提案寫進去了、
    而 `approved_principles` 還是空的——也就是提示詞還讀不到它。
    """
    queue = _two_confirmed(tmp_path)
    sent = {}

    def fake_ask(messages, **kwargs):
        sent["packet"] = json.loads(messages[1]["content"])
        answer = {"status": "candidate", "principle_text": "紙本的 ⻑ 要讀成 長。",
                  "negative_controls": ["題目本身在講部首時不適用"],
                  "do_not_generalize": ["不要把所有罕用字都當錯字"],
                  "rationale": "兩題同一種單字替換，人被確認過兩次。"}
        return None, json.dumps(answer, ensure_ascii=False), None, {}, 1.0

    monkeypatch.setattr(propose_principles.ask_about_blocks, "ask", fake_ask)
    from argparse import Namespace
    result = propose_principles.main.__wrapped__ if hasattr(propose_principles.main, "__wrapped__") \
        else None
    args = Namespace(queue=queue, model="splash", min_confirmations=2, apply=True, report=False,
                     max_tokens=2000, timeout=30)
    monkeypatch.setattr(propose_principles, "parse_args", lambda: args)
    assert propose_principles.main() == 0

    assert sent["packet"]["change_class"] == "exact_ocr_rule"
    assert sent["packet"]["confirmed_questions"] == 2, "兩題要一起送，否則模型只看得到一題"
    assert len(sent["packet"]["examples"]) == 2
    principles = discuss.load_events(os.path.join(queue, discuss.PRINCIPLES_STREAM))
    assert [event["action"] for event in principles] == ["add"], \
        "這支腳本寫了 add 以外的動作"
    assert principles[0]["reviewer"] == propose_principles.CURATOR_REVIEWER
    assert principles[0]["source"] == propose_principles.CURATOR_SOURCE
    assert sorted(principles[0]["evidence"]) == sorted([KEY_A, KEY_B])
    assert principles[0]["text"] == "紙本的 ⻑ 要讀成 長。"
    # **提案≠核准**：這條原則現在是「待你核准」，提示詞還讀不到。
    assert discuss.approved_principles(principles) == []
    assert ai_findings.principles_for_prompt(principles) == []
    assert discuss.active_principles(principles) == ["紙本的 ⻑ 要讀成 長。"]
    assert discuss.principles_projection(principles)["pending_count"] == 1


def test_a_model_that_says_no_generalization_writes_nothing(tmp_path, monkeypatch):
    """**負控制。** 模型說「這不能成為通則」時不可以寫提案——那不是提案的替代品，是不提案。"""
    queue = _two_confirmed(tmp_path)

    def fake_ask(messages, **kwargs):
        return None, json.dumps({"status": "no_generalization", "principle_text": None,
                                 "negative_controls": [], "do_not_generalize": [],
                                 "rationale": "兩題的共同點只是同一個罕用字。"}), None, {}, 1.0

    monkeypatch.setattr(propose_principles.ask_about_blocks, "ask", fake_ask)
    args = argparse_namespace(queue)
    monkeypatch.setattr(propose_principles, "parse_args", lambda: args)
    assert propose_principles.main() == 0
    assert not os.path.exists(os.path.join(queue, discuss.PRINCIPLES_STREAM))


def argparse_namespace(queue):
    from argparse import Namespace
    return Namespace(queue=queue, model="splash", min_confirmations=2, apply=True, report=False,
                     max_tokens=2000, timeout=30)


def test_an_approved_proposal_is_a_principle_the_prompt_carries(tmp_path, monkeypatch):
    """最後一步是**人**按下核准：核准之後，同一句話才進得了下一次讀取的提示詞。

    這個測試自己扮演出「人按下核准」的那一筆事件（`discuss.principle_decision_event`），
    因為站上那個動作是 owner 在介面上按的、這一支腳本不准代按。它證明的是這條路的接點：
    提案 → 人核准 → `ai_findings.principles_for_prompt` → 提示詞。
    """
    queue = _two_confirmed(tmp_path)

    def fake_ask(messages, **kwargs):
        return None, json.dumps({"status": "candidate", "principle_text": "紙本的 ⻑ 要讀成 長。",
                                 "negative_controls": ["講部首時不適用"],
                                 "do_not_generalize": [], "rationale": "兩題同一種單字替換。"}), \
            None, {}, 1.0

    monkeypatch.setattr(propose_principles.ask_about_blocks, "ask", fake_ask)
    monkeypatch.setattr(propose_principles, "parse_args", lambda: argparse_namespace(queue))
    assert propose_principles.main() == 0
    path = os.path.join(queue, discuss.PRINCIPLES_STREAM)
    proposed = discuss.load_events(path)
    assert ai_findings.principles_for_prompt(proposed) == [], "提案還沒核准就進了提示詞"

    # 人按下核准（介面走的就是這一筆事件）。
    discuss.append_event(path, discuss.principle_decision_event(proposed[0]["principle_id"], "approve"))
    approved = discuss.load_events(path)
    assert ai_findings.principles_for_prompt(approved) == ["紙本的 ⻑ 要讀成 長。"]
    system, _user = ai_findings.build_prompt(
        _question(1, KEY_A, "下列何者是⻑效型的藥物？"),
        principles=ai_findings.principles_for_prompt(approved))
    assert "紙本的 ⻑ 要讀成 長。" in system
    assert "【基本原則】" in system


def test_a_second_run_does_not_propose_the_same_examples_again(tmp_path, monkeypatch):
    """同一批例子不會被提第二次：確認流是 append-only，而分類是決定性的——重跑不該長出新提案。"""
    queue = _two_confirmed(tmp_path)
    calls = []

    def fake_ask(messages, **kwargs):
        calls.append(messages)
        return None, json.dumps({"status": "candidate", "principle_text": "紙本的 ⻑ 要讀成 長。",
                                 "negative_controls": [], "do_not_generalize": [],
                                 "rationale": "兩題同一種單字替換。"}), None, {}, 1.0

    monkeypatch.setattr(propose_principles.ask_about_blocks, "ask", fake_ask)
    monkeypatch.setattr(propose_principles, "parse_args", lambda: argparse_namespace(queue))
    assert propose_principles.main() == 0
    assert propose_principles.main() == 0
    assert len(calls) == 1, "第二次重跑又叫了一次模型"
    assert len(discuss.load_events(os.path.join(queue, discuss.PRINCIPLES_STREAM))) == 1


def test_a_dry_run_calls_nothing_and_writes_nothing(tmp_path):
    """乾跑是預設：算給你看，但一個位元組都不寫（否則「先看看」會偷偷改動原則流）。"""
    queue = _two_confirmed(tmp_path)
    result = _run(queue)
    assert result.returncode == 0, result.stderr
    assert "乾跑" in result.stdout
    assert not os.path.exists(os.path.join(queue, discuss.PRINCIPLES_STREAM))



def test_the_curator_sees_what_each_example_s_pictures_are(tmp_path):
    """策展員只看得到文字前後時，「這一題有沒有圖、圖屬於哪個選項、歸屬量過沒有」在提案時是隱形的。

    owner 2026-09-25 回報的正是圖被貼錯（「有些題目沒有圖片但是卻截別題的來貼上」）；一條關於圖的
    通則不可能從看不見圖的例子裡生出來。`figure_facts` 用的是判讀提示詞的**同一個渲染**
    （`ai_findings.figures_note`），不是第二份摘要——兩份「這一題的圖是什麼」就是兩個可以不一致的地方。
    """
    ui = tmp_path / "review-ui"
    ui.mkdir(parents=True)
    picture = _question(1, KEY_A, "下列何者是⻑效型的藥物？")
    picture["image_refs"] = [{"asset_role": "option-image", "option_key": "B",
                              "ownership": "unverified"}]
    plain = _question(2, KEY_B, "下列何者是⻑效型的抗生素？")
    with (ui / "candidates.jsonl").open("w", encoding="utf-8") as handle:
        for question in (picture, plain):
            handle.write(json.dumps(question, ensure_ascii=False) + "\n")
    with (ui / "question_review_events.jsonl").open("w", encoding="utf-8") as handle:
        for event in [_repair_event(KEY_A, before="下列何者是⻑效型的藥物？",
                                    after="下列何者是長效型的藥物？"),
                      _decision(KEY_A, "accept", "2026-09-25T02:00:00"),
                      _repair_event(KEY_B, before="下列何者是⻑效型的抗生素？",
                                    after="下列何者是長效型的抗生素？"),
                      _decision(KEY_B, "accept", "2026-09-25T02:00:00")]:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    rows = propose_principles.load_rows(str(ui))
    confirmed = {row["candidate_key"]: row
                 for row in propose_principles.confirmed_changes(str(ui), rows)}
    assert "選項 B 的圖" in confirmed[KEY_A]["figure_facts"]
    assert "無法確認這張圖屬於哪一題" in confirmed[KEY_A]["figure_facts"]
    assert confirmed[KEY_B]["figure_facts"] == "", "沒有圖的題目不可以被說成有圖"

    packet = propose_principles.curation_packet(confirmed[KEY_A]["change_class"],
                                                list(confirmed.values()), [])
    facts = {example["candidate_key"]: example["figure_facts"] for example in packet["examples"]}
    assert "選項 B 的圖" in facts[KEY_A]
    assert facts[KEY_B] is None


def test_the_packet_carries_the_principles_in_force_and_each_example_s_scope(tmp_path, monkeypatch):
    """策展員要看得見兩件事，否則它會重複人寫過的話（機器的 p8 對人的 p6），或從不同科目的例子裡
    泛化出一條通則（p9 就是這樣被提出、又被 owner 拿掉的）。

    `effective_principles` 是判讀提示詞的**同一個讀者**（`ai_findings.principles_for_prompt`）：
    已核准的進封包，還在等人的不進——否則策展員會把「待你核准」的東西當成已生效的。每個例子的
    `subject`／`category`／`paper` 是那筆缺陷量到的範圍，各用既有的一個讀者
    （`ai_findings.subject_of`／`category_of`、`repair_loop.paper_of`），不是這裡再發明第二種拼法。
    """
    ui = tmp_path / "review-ui"
    ui.mkdir(parents=True)
    questions = [_question(1, KEY_A, "下列何者是⻑效型的藥物？"),
                 _question(3, KEY_C, "下列何者是⻑效型的抗生素？")]
    questions[0]["metadata"] = {"normalized_subject_name": "藥理學與藥物化學",
                                "normalized_category_name": "藥師"}
    questions[1]["metadata"] = {"normalized_subject_name": "臨床生理學",
                                "normalized_category_name": "醫事檢驗師"}
    with (ui / "candidates.jsonl").open("w", encoding="utf-8") as handle:
        for question in questions:
            handle.write(json.dumps(question, ensure_ascii=False) + "\n")
    with (ui / "question_review_events.jsonl").open("w", encoding="utf-8") as handle:
        for event in [_repair_event(KEY_A, before="下列何者是⻑效型的藥物？",
                                    after="下列何者是長效型的藥物？"),
                      _decision(KEY_A, "accept", "2026-09-25T02:00:00"),
                      _repair_event(KEY_C, before="下列何者是⻑效型的抗生素？",
                                    after="下列何者是長效型的抗生素？"),
                      _decision(KEY_C, "accept", "2026-09-25T02:10:00")]:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    # 原則流：一條人已核准的、一條還在等人的。兩條都是同一支腳本的身分（就像 p8 對 p6 那樣）。
    path = os.path.join(str(ui), discuss.PRINCIPLES_STREAM)
    discuss.append_event(path, propose_principles.proposal_event(
        str(ui), change_class="exact_ocr_rule", text="上下標要讀成 Unicode 的下標字。",
        keys=[KEY_A], rationale="人寫的"))
    discuss.append_event(path, discuss.principle_decision_event("p1", "approve"))
    discuss.append_event(path, propose_principles.proposal_event(
        str(ui), change_class="exact_ocr_rule", text="還沒核准的那一條。",
        keys=[KEY_C], rationale="等核准"))

    sent = {}

    def fake_ask(messages, **kwargs):
        sent["packet"] = json.loads(messages[1]["content"])
        return None, json.dumps({"status": "candidate", "principle_text": "紙本的 ⻑ 要讀成 長。",
                                 "negative_controls": [], "do_not_generalize": [],
                                 "rationale": "兩題同一種單字替換。"}), None, {}, 1.0

    monkeypatch.setattr(propose_principles.ask_about_blocks, "ask", fake_ask)
    monkeypatch.setattr(propose_principles, "parse_args", lambda: argparse_namespace(str(ui)))
    assert propose_principles.main() == 0

    packet = sent["packet"]
    assert packet["effective_principles"] == ["上下標要讀成 Unicode 的下標字。"], \
        "已核准的原則要進封包，而且只有已核准的"
    scope = {example["candidate_key"]: (example["subject"], example["category"], example["paper"])
             for example in packet["examples"]}
    assert scope[KEY_A] == ("藥理學與藥物化學", "藥師", "105100:305")
    assert scope[KEY_C] == ("臨床生理學", "醫事檢驗師", "108020:301")
    assert scope[KEY_A][0] != scope[KEY_C][0] and scope[KEY_A][2] != scope[KEY_C][2], \
        "兩筆例子來自不同科目／不同試卷，這件事在封包裡必須看得見"


def test_a_model_that_says_already_covered_writes_nothing_and_prints_why(tmp_path, monkeypatch,
                                                                        capsys):
    """**負控制。** 模型說「這條已經有了」時不可以寫提案：p8 就是這樣把人的 p6 重寫了一遍，
    而人還得再讀一次同一件事。這一條連 `principle_text` 都給了——那個 status 才是判準，不是有沒有寫話。
    """
    queue = _two_confirmed(tmp_path)

    def fake_ask(messages, **kwargs):
        return None, json.dumps({"status": "already_covered",
                                 "principle_text": "紙本的 ⻑ 要讀成 長。",
                                 "negative_controls": [], "do_not_generalize": [],
                                 "rationale": "重複的是 p6：上下標那一條。"}), None, {}, 1.0

    monkeypatch.setattr(propose_principles.ask_about_blocks, "ask", fake_ask)
    monkeypatch.setattr(propose_principles, "parse_args", lambda: argparse_namespace(queue))
    assert propose_principles.main() == 0
    out = capsys.readouterr().out
    assert "already_covered" in out
    assert "不寫提案" in out and "p6" in out, "沒有說出為什麼不提案"
    assert not os.path.exists(os.path.join(queue, discuss.PRINCIPLES_STREAM))

    # 負控制：同一個形狀的答案換成 `candidate` 時，提案一定要真的寫下去——「不寫」是那個 status
    # 造成的，不是這一條測試根本叫不到模型。
    control = _two_confirmed(tmp_path / "control")

    def fake_candidate(messages, **kwargs):
        return None, json.dumps({"status": "candidate", "principle_text": "紙本的 ⻑ 要讀成 長。",
                                 "negative_controls": [], "do_not_generalize": [],
                                 "rationale": "兩題同一種單字替換。"}), None, {}, 1.0

    monkeypatch.setattr(propose_principles.ask_about_blocks, "ask", fake_candidate)
    monkeypatch.setattr(propose_principles, "parse_args", lambda: argparse_namespace(control))
    assert propose_principles.main() == 0
    events = discuss.load_events(os.path.join(control, discuss.PRINCIPLES_STREAM))
    assert [event["action"] for event in events] == ["add"]
