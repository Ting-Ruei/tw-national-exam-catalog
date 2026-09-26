# -*- coding: utf-8 -*-
"""文字 → 抽取檔：把已經寫成 review event 的 correction 寫進 `candidates.jsonl` 本身。

這一支要守的合約有四條，每一條都配一個負控制（舊行為會讓它紅）：

1. **correction 要真的落到那一欄。** 事件單獨存在時檔案是壞的（舊行為：只有覆蓋顯示），
   跑過之後壞的字要從檔案裡消失，而且 `--apply` 之前一個位元組都不准動。
2. **被換掉的原文要留在列裡的 `parser_original`。** 這是唯一一份「抽取器當初讀到什麼」的證據；
   把它寫成改過的字，等於親手把證據換掉。
3. **過期的事件要拒絕，不是改錯地方。** 三種形狀各有一條：整欄替換的 `from` 與檔案不符、
   字元替換的差與事件宣稱的不符、只有值沒有宣稱的那一種（人在介面上存的事件）兩邊不是同一行。
4. **重跑不能有動作。** 迴圈每 30 分鐘跑一次，第二次必須是 0 筆、0 拒絕、檔案 sha 不變；
   沒有 correction 的佇列連一個位元組都不該動，而且寫入是原子的（同目錄臨時檔 + `os.replace`，
   不就地截斷、不留臨時檔）。
5. **撤銷（`applied: "withdrawn"`）要把機器改錯的字退回抽取原文。** 2026-09-24 的實測：兩個引擎
   都說 TRUST，於是 `抗癲癇` 變成 `抗癲癲`（4 題），另外五列各換了一個 CJK 字。撤銷是
   append-only log 裡的一筆**修訂**，跟 correction 同一個折疊、同一條「最後一筆贏」的順序；
   還原是回到這一列 `parser_original` 的原文（不是一筆新的判讀，所以不過那三道閘門，但沒有原文
   可還原時要拒絕）。標籤（`applied_kind = "withdrawn"`、「已還原（機器改錯）」）是伺服器投影
   與介面的事，這一支一個標籤都不寫。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(PKG))
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

import apply_text_corrections as produce_mod  # noqa: E402
from scripts.serve_question_review_ui import ReviewState  # noqa: E402

KEY = "moex:111020:305:33:1:question:q030"

#: The real defect shape: the extractor stored a CJK radical where the paper prints the ideograph.
STORED = "下列產品何者屬於吸收性基劑？⻑期使用時要小心"
PAGE = "下列產品何者屬於吸收性基劑？長期使用時要小心"

#: The owner's shape (the stem was cut short, or the extractor invented a run).
TRIMMED = "下列何者為臺灣物理治療學會於西元2030年的願景？"
LONGER = TRIMMED + "請選出最適合的答案"


def _row(stem=STORED, options=None, **extra):
    row = {
        "candidate_key": KEY,
        "question_number": 30,
        "stem": stem,
        "options": options if options is not None else [{"key": k, "text": "選項" + k} for k in "ABCD"],
        "answer": "A",
    }
    row.update(extra)
    return row


def _machine_event(changes, correction, **extra):
    """One `reset_review` as `apply_dispute_repairs.build_repair_event` writes it."""
    event = {
        "candidate_key": KEY,
        "action": "reset_review",
        "reviewer": "repair_dispute_apply",
        "source": "qbr_dispute_apply",
        "repair_kind": "content_change",
        "changes": changes,
        "correction": correction,
        "created_at": "2026-09-24T08:20:05",
    }
    event.update(extra)
    return event


def _build(tmp_path, rows=None, events=None):
    ui = tmp_path / "review-ui"
    ui.mkdir(parents=True)
    (ui / "candidates.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in (rows or [_row()])),
        encoding="utf-8")
    (ui / "question_review_events.jsonl").write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in (events or [])),
        encoding="utf-8")
    return ui


def _produce(monkeypatch, root, *extra):
    monkeypatch.setattr(sys, "argv", ["apply_text_corrections.py", "--queue", str(root), *extra])
    return produce_mod.main()


def _rows(ui):
    out = {}
    for line in (ui / "candidates.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["candidate_key"]] = row
    return out


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------ the text reaches the file

def test_a_character_substitution_reaches_the_field_text(monkeypatch, capsys, tmp_path):
    ui = _build(tmp_path, events=[_machine_event(
        [{"field": "stem", "from": "⻑", "to": "長"}],
        {"stem": PAGE}, applied="substitution")])
    # Negative control: the event exists and the file is still defective - which is exactly the state
    # the whole task is about (the screen is fixed, the extraction file is not).
    assert "⻑" in (ui / "candidates.jsonl").read_text(encoding="utf-8"), "前提：事件不改檔案"

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert _rows(ui)[KEY]["stem"] == PAGE
    assert "⻑" not in _rows(ui)[KEY]["stem"]
    assert "to write     : 1" in out
    assert "REFUSED" not in out


def test_the_negative_control_without_apply_nothing_is_written(monkeypatch, capsys, tmp_path):
    """`--apply` is required: the default is a plan, and a plan must not touch the extraction file."""
    ui = _build(tmp_path, events=[_machine_event(
        [{"field": "stem", "from": "⻑", "to": "長"}],
        {"stem": PAGE}, applied="substitution")])
    before = _sha(ui / "candidates.jsonl")

    assert _produce(monkeypatch, tmp_path) == 0
    out = capsys.readouterr().out
    assert "to write     : 1" in out and "dry run" in out
    assert _sha(ui / "candidates.jsonl") == before
    assert _rows(ui)[KEY]["stem"] == STORED


def test_a_whole_field_replacement_reaches_the_field_text(monkeypatch, capsys, tmp_path):
    """`applied: "field"`: the page read replaced the whole field, so `from` is the whole field text."""
    ui = _build(tmp_path, rows=[_row(stem=STORED)], events=[_machine_event(
        [{"field": "stem", "from": STORED, "to": LONGER}],
        {"stem": LONGER}, applied="field", crop="review-ui/crops/test/q030-dispute.png")])

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    assert _rows(ui)[KEY]["stem"] == LONGER
    assert _rows(ui)[KEY]["parser_original"]["stem"] == STORED


def test_only_the_fields_the_event_names_are_written(monkeypatch, capsys, tmp_path):
    """`correction` carries the whole option list (the applier always sends all four), but the event
    claims only the fields its `changes` name. A field it does not claim is context: writing it would
    put an old reading back into a field nobody looked at (measured: the option texts beside a
    substituted one differ as soon as the queue is rebuilt).
    """
    options = [{"key": "A", "text": "新版選項A"}, {"key": "B", "text": "⻑期服用"},
               {"key": "C", "text": "選項C"}, {"key": "D", "text": "選項D"}]
    corrected = [{"key": "A", "text": "舊版選項A"}, {"key": "B", "text": "長期服用"},
                 {"key": "C", "text": "選項C"}, {"key": "D", "text": "選項D"}]
    ui = _build(tmp_path, rows=[_row(options=options)], events=[_machine_event(
        [{"field": "option B", "from": "⻑", "to": "長"}], {"options": corrected},
        applied="substitution")])

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    row = _rows(ui)[KEY]
    texts = {option["key"]: option["text"] for option in row["options"]}
    assert texts["B"] == "長期服用"
    assert texts["A"] == "新版選項A", "事件沒有宣稱的欄位不可以被舊的 correction 蓋掉"
    assert row["parser_original"]["options"][0]["text"] == "新版選項A"
    assert row["parser_original"]["options"][1]["text"] == "⻑期服用"


# ------------------------------------------------------ the pre-correction text is kept

def test_the_replaced_text_survives_as_parser_original(monkeypatch, capsys, tmp_path):
    ui = _build(tmp_path, events=[_machine_event(
        [{"field": "stem", "from": "⻑", "to": "長"}], {"stem": PAGE}, applied="substitution")])

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    row = _rows(ui)[KEY]
    # Negative control: rebuilding the original from the row (what the server's fold did before the
    # producer persisted one) would store the *corrected* text; that is what this asserts against.
    assert row["parser_original"]["stem"] == STORED
    assert row["parser_original"]["stem"] != row["stem"]


def test_a_stored_original_is_never_overwritten_with_already_corrected_text(monkeypatch, capsys,
                                                                           tmp_path):
    """A row that already carries an original keeps it: the second repair's "before" is the first
    repair's "after", and storing that would replace the only record of what the extractor read."""
    ui = _build(tmp_path, rows=[_row(stem=STORED, parser_original={"stem": "最早的原文"})],
                events=[_machine_event(
                    [{"field": "stem", "from": "⻑", "to": "長"}], {"stem": PAGE},
                    applied="substitution")])

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    row = _rows(ui)[KEY]
    assert row["stem"] == PAGE
    assert row["parser_original"]["stem"] == "最早的原文"


# ------------------------------------------------------------------ stale events are refused

def test_a_whole_field_event_that_no_longer_matches_the_file_is_refused(monkeypatch, capsys,
                                                                        tmp_path):
    ui = _build(tmp_path, events=[_machine_event(
        [{"field": "stem", "from": "事件以為的那段字", "to": PAGE}],
        {"stem": PAGE}, applied="field")])
    before = _sha(ui / "candidates.jsonl")

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert "REFUSED (1)" in out and "已過期" in out
    assert _sha(ui / "candidates.jsonl") == before, "過期的事件不可以改到檔案"
    assert _rows(ui)[KEY]["stem"] == STORED


def test_the_negative_control_the_same_event_applies_when_the_text_still_matches(monkeypatch,
                                                                                capsys, tmp_path):
    """Same shape, `from` = the file's own text: the refusal above is about staleness, not about the
    event having a `from` at all."""
    ui = _build(tmp_path, events=[_machine_event(
        [{"field": "stem", "from": STORED, "to": PAGE}], {"stem": PAGE}, applied="field")])

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert "to write     : 1" in out and "REFUSED" not in out
    assert _rows(ui)[KEY]["stem"] == PAGE


def test_a_substitution_that_no_longer_explains_the_difference_is_refused(monkeypatch, capsys,
                                                                          tmp_path):
    """The file's stem has drifted (a sentence was prepended) since the substitution was measured.
    The declared `⻑→長` pair is still in it, so a tool that only checked "the character is there"
    would apply an edit to a line that has moved; the difference no longer being made *only* of the
    declared pair is what refuses it."""
    drifted = "前面多了一段話。" + STORED
    ui = _build(tmp_path, rows=[_row(stem=drifted)], events=[_machine_event(
        [{"field": "stem", "from": "⻑", "to": "長"}], {"stem": PAGE}, applied="substitution")])
    before = _sha(ui / "candidates.jsonl")

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert "REFUSED (1)" in out and "事件已過期" in out
    assert _sha(ui / "candidates.jsonl") == before


# ------------------------------------------- a value with no claim about the file (a person's edit)

def _human_event(value, **extra):
    """The shape the review UI's `correct` event has: the value the person typed, no before-claim."""
    event = {
        "candidate_key": KEY,
        "action": "correct",
        "reviewer": "local",
        "notes": "紙本沒有那半句",
        "correction": {"stem": value},
        "changes": [{"field": "stem", "from": value, "to": value}],
        "created_at": "2026-09-24T09:00:00",
    }
    event.update(extra)
    return event


def test_a_persons_trim_reaches_the_file(monkeypatch, capsys, tmp_path):
    """The person's value is a piece of the stored text (the extraction invented the run)."""
    ui = _build(tmp_path, rows=[_row(stem=LONGER)], events=[_human_event(TRIMMED)])

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    row = _rows(ui)[KEY]
    assert row["stem"] == TRIMMED
    assert row["parser_original"]["stem"] == LONGER


def test_the_negative_control_a_value_about_another_line_is_refused(monkeypatch, capsys, tmp_path):
    """Neither text is a piece of the other: the row has moved since the event was written, and
    replacing a field nobody has looked at with text about something else is the one outcome this
    must never produce."""
    ui = _build(tmp_path, rows=[_row(stem=TRIMMED)], events=[_human_event("這是事件以為的那段字")])
    before = _sha(ui / "candidates.jsonl")

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert "REFUSED (1)" in out and "連續出現" in out
    assert _sha(ui / "candidates.jsonl") == before


def test_the_negative_control_a_value_too_short_to_be_a_line_is_refused(monkeypatch, capsys,
                                                                        tmp_path):
    """A single character that *is* in the stored text is the case the containment clause alone would
    let through - and then a one-character value would become the whole field. The floors refuse it.
    (A value that is not in the text at all fails the clause before it, which the next test covers.)"""
    ui = _build(tmp_path, rows=[_row(stem=STORED)], events=[_human_event("⻑")])
    before = _sha(ui / "candidates.jsonl")

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert "REFUSED (1)" in out and "只有 1 個字" in out
    assert _sha(ui / "candidates.jsonl") == before


def test_the_negative_control_a_value_that_keeps_too_little_is_refused(monkeypatch, capsys,
                                                                      tmp_path):
    """A contiguous piece of the field, long enough (≥ 8 characters) but too small a share of it:
    the same clause, measured rather than assumed."""
    long_stem = STORED + "，這是一段很長很長的敘述" * 4
    piece = long_stem[10:19]
    assert 8 <= len(piece)
    ui = _build(tmp_path, rows=[_row(stem=long_stem)], events=[_human_event(piece)])
    before = _sha(ui / "candidates.jsonl")

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert "REFUSED (1)" in out and "留下來的部分太少" in out
    assert _sha(ui / "candidates.jsonl") == before


# ------------------------------------------------------------------ which event is the latest

def test_the_latest_correction_wins_and_a_later_block_does_not_withdraw_it(monkeypatch, capsys,
                                                                         tmp_path):
    """A `block` after a repair is the guardrail working (the person looked and bounced it back), not
    a withdrawal of the text change - so the fold must not let it erase the correction. A later
    `correct` does supersede it, because that is the reviewer's own fix."""
    machine = _machine_event([{"field": "stem", "from": "⻑", "to": "長"}], {"stem": PAGE},
                             applied="substitution")
    bounce = {"candidate_key": KEY, "action": "block", "reviewer": "local", "notes": "還是不對",
              "created_at": "2026-09-24T10:00:00"}
    ui = _build(tmp_path, events=[machine, bounce])

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    assert _rows(ui)[KEY]["stem"] == PAGE

    # 人自己後來改的字（同一列的第二個 correction）才是最後一筆，於是以它為準。值是檔案那段文字
    # 的一塊（人的事件只帶值，不帶「改之前是什麼」），所以它過得了同一行的那道閘門。
    trimmed = STORED[:12]
    human = _human_event(trimmed, created_at="2026-09-24T11:00:00")
    ui2 = _build(tmp_path / "second", events=[machine, bounce, human])
    assert _produce(monkeypatch, tmp_path / "second", "--apply") == 0
    row = _rows(ui2)[KEY]
    assert row["stem"] == trimmed
    assert row["parser_original"]["stem"] == STORED


# ------------------------------------------------- 撤銷：機器改錯的字要退回抽取原文

#: 這一輪真正的缺陷形狀：兩個引擎都說 TRUST，所以 `癇` 被讀成 `癲`，`抗癲癇` 變成 `抗癲癲`。
WANTED = "下列何者為抗癲癇藥物的作用機轉？"
WRONG = "下列何者為抗癲癲藥物的作用機轉？"


def _withdrawal_event(withdraw, **extra):
    """One `reset_review` as the applier writes a withdrawal: `applied: "withdrawn"`, no correction."""
    event = {
        "candidate_key": KEY,
        "action": "reset_review",
        "reviewer": "repair_dispute_apply",
        "applied": "withdrawn",
        "withdraw": list(withdraw),
        "correction": None,
        "created_at": "2026-09-24T18:20:00",
        "why": "同一個字對 癇→癲 在這一輪出現 4 題：判讀在這裡系統性看錯",
    }
    event.update(extra)
    return event


def _repair_then_withdraw(withdraw):
    """The two lines of the real case, in the order the loop writes them: the repair, then the
    withdrawal."""
    return [_machine_event([{"field": "stem", "from": "癇", "to": "癲"}], {"stem": WRONG},
                           applied="substitution"),
            _withdrawal_event(withdraw)]


def test_a_withdrawal_puts_the_named_fields_back_to_the_parser_original(monkeypatch, capsys,
                                                                       tmp_path):
    """機器自己寫進去的字被撤銷指名之後，要退回這一列 `parser_original` 的原文；而且只有被指名
    的欄位動，原文原樣留著，這一支也不在列上寫任何標籤（標籤是伺服器投影的事）。"""
    options = [{"key": "A", "text": "⻑期服用"}, {"key": "B", "text": "選項B"}]
    repaired = [{"key": "A", "text": "長期服用"}, {"key": "B", "text": "選項B"}]
    ui = _build(tmp_path,
                rows=[_row(stem=WRONG, options=repaired,
                           parser_original={"stem": WANTED, "options": options})],
                events=[_machine_event([{"field": "stem", "from": "癇", "to": "癲"}],
                                       {"stem": WRONG}, applied="substitution"),
                        _machine_event([{"field": "option A", "from": "⻑", "to": "長"}],
                                       {"options": repaired}, applied="substitution"),
                        _withdrawal_event(["stem", "option A"])])
    # Negative control, as a premise: the file holds the machine's wrong reading right now - which is
    # exactly the state a withdrawal exists to undo.
    assert "癲癲" in (ui / "candidates.jsonl").read_text(encoding="utf-8")

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    row = _rows(ui)[KEY]
    texts = {option["key"]: option["text"] for option in row["options"]}
    assert row["stem"] == WANTED and "癲癲" not in row["stem"]
    assert texts["A"] == "⻑期服用"
    assert texts["B"] == "選項B", "沒有被指名的欄位不准動"
    assert row["parser_original"]["stem"] == WANTED, "原文原樣留著才是紀錄"
    assert row["parser_original"]["options"][0]["text"] == "⻑期服用"
    assert "applied" not in row and "applied_kind" not in row, "這一支不寫任何標籤／狀態"
    assert "to write     : 1" in out and "REFUSED" not in out
    assert "withdrawn    : 2 欄／1 列" in out
    assert "[withdrawn]" in out and "還原成 parser_original" in out


def test_a_repair_newer_than_the_withdrawal_wins(monkeypatch, capsys, tmp_path):
    """撤銷之後又有更新的修復：log 是 append-only，行的位置就是時間，所以最後一筆關於這一列文字
    的事件贏——檔案照原本的三道閘門寫新的字，而不是退回原文。"""
    extended = PAGE + "請選出最適合的答案"
    ui = _build(tmp_path, rows=[_row(stem=PAGE, parser_original={"stem": STORED})],
                events=[_machine_event([{"field": "stem", "from": "⻑", "to": "長"}], {"stem": PAGE},
                                       applied="substitution"),
                        _withdrawal_event(["stem"]),
                        _machine_event([{"field": "stem", "from": PAGE, "to": extended}],
                                       {"stem": extended}, applied="field")])

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert _rows(ui)[KEY]["stem"] == extended, "更新的一筆修復要贏過先前的撤銷"
    assert "withdrawn    :" not in out and "整欄替換" in out


def test_a_persons_correction_after_a_withdrawal_is_the_last_word(monkeypatch, capsys, tmp_path):
    """撤銷之後人在介面上自己改的字比撤銷晚：那一筆贏（人自己的修正是最後一句話，跟 `block`
    抹不掉 correction 是同一條規則），而且存過的原文一樣不可以被蓋掉。"""
    value = PAGE[:12]
    ui = _build(tmp_path, rows=[_row(stem=PAGE, parser_original={"stem": STORED})],
                events=[_machine_event([{"field": "stem", "from": "⻑", "to": "長"}], {"stem": PAGE},
                                       applied="substitution"),
                        _withdrawal_event(["stem"]),
                        _human_event(value, created_at="2026-09-24T19:00:00")])

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    row = _rows(ui)[KEY]
    assert row["stem"] == value
    assert row["parser_original"]["stem"] == STORED, "存過的原文才是原文"
    assert "withdrawn    :" not in out


def test_a_block_after_a_withdrawal_does_not_resurrect_the_repair(monkeypatch, capsys, tmp_path):
    """人在撤銷之後按了 block（這一題還是要人看）：那不是一筆關於文字的修訂，所以撤銷仍然有效。"""
    ui = _build(tmp_path, rows=[_row(stem=WRONG, parser_original={"stem": WANTED})],
                events=_repair_then_withdraw(["stem"]) + [
                    {"candidate_key": KEY, "action": "block", "reviewer": "local",
                     "notes": "這題還是要人看", "created_at": "2026-09-24T18:30:00"}])

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    assert _rows(ui)[KEY]["stem"] == WANTED


def test_the_negative_control_neutralising_the_withdrawal_path_leaves_the_wrong_character(
        monkeypatch, capsys, tmp_path):
    """負對照，自成一個測試：把撤銷那一條路拆掉（舊行為——撤銷不是一筆修訂，而事件本身沒有
    `correction`，所以折疊之後什麼都不寫），檔案裡就留著機器讀錯的那個字。上面那一條測試是靠
    這一條才成立的。"""
    ui = _build(tmp_path, rows=[_row(stem=WRONG, parser_original={"stem": WANTED})],
                events=_repair_then_withdraw(["stem"]))
    monkeypatch.setattr(produce_mod, "plan_row",
                        lambda row, revision: produce_mod.plan_correction(row, revision["event"]))

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert _rows(ui)[KEY]["stem"] == WRONG, "少了撤銷這一條路，錯的字就留在檔案裡"
    assert "withdrawn    :" not in out and "to write     : 0" in out


def test_a_second_run_after_a_withdrawal_writes_nothing(monkeypatch, capsys, tmp_path):
    ui = _build(tmp_path, rows=[_row(stem=WRONG, parser_original={"stem": WANTED})],
                events=_repair_then_withdraw(["stem"]))

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    settled = _sha(ui / "candidates.jsonl")

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert _rows(ui)[KEY]["stem"] == WANTED
    assert "to write     : 0" in out
    # Negative control: a field that already equals its original is "nothing to write", never a
    # refusal - a refusal here would print a false alarm on every one of the loop's rounds.
    assert "REFUSED" not in out
    assert "already correct: 1" in out
    assert _sha(ui / "candidates.jsonl") == settled


def test_the_negative_control_a_withdrawal_naming_a_field_the_row_never_had_is_refused(
        monkeypatch, capsys, tmp_path):
    """`withdraw` 指名這一列沒有的欄位（這裡是一個沒有的選項，以及根本不是欄位的 `answer`）：
    整列拒絕、檔案一個位元組都不動，而不是丟例外。"""
    ui = _build(tmp_path, rows=[_row(stem=WRONG, parser_original={"stem": WANTED})],
                events=_repair_then_withdraw(["option Z", "answer"]))
    before = _sha(ui / "candidates.jsonl")

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert "REFUSED (1)" in out and "這一行沒有這個欄位" in out
    assert _sha(ui / "candidates.jsonl") == before
    assert _rows(ui)[KEY]["stem"] == WRONG


def test_the_negative_control_a_row_with_no_parser_original_is_refused(monkeypatch, capsys,
                                                                      tmp_path):
    """沒有 `parser_original` 就沒有東西可以還原：拒絕、檔案不動，而且訊息說得出來缺的是什麼。"""
    ui = _build(tmp_path, rows=[_row(stem=WRONG)], events=_repair_then_withdraw(["stem"]))
    before = _sha(ui / "candidates.jsonl")

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert "REFUSED (1)" in out and "沒有存到這一欄的 parser_original" in out
    assert _sha(ui / "candidates.jsonl") == before


def test_the_negative_control_a_field_whose_original_was_never_stored_is_refused(monkeypatch,
                                                                                capsys, tmp_path):
    """折疊只存 correction 碰過的欄位，所以一列有 `parser_original` 不代表每一欄都有：指名一個
    沒存到的欄位一樣是拒絕，不可以拿別的欄位的原文來頂。"""
    ui = _build(tmp_path, rows=[_row(stem=WRONG, parser_original={"option B": "⻑期服用"})],
                events=_repair_then_withdraw(["stem"]))
    before = _sha(ui / "candidates.jsonl")

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert "REFUSED (1)" in out and "沒有存到這一欄的 parser_original" in out
    assert _sha(ui / "candidates.jsonl") == before


# ------------------------------------------------------------------ idempotence

def test_a_second_run_writes_nothing(monkeypatch, capsys, tmp_path):
    ui = _build(tmp_path, events=[_machine_event(
        [{"field": "stem", "from": "⻑", "to": "長"}], {"stem": PAGE}, applied="substitution")])

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    settled = _sha(ui / "candidates.jsonl")

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert "to write     : 0" in out
    # Negative control: the row's text is now the corrected one, so a rule that only compared against
    # `changes[].from` would report the event as stale. Nothing is written *and* nothing is refused.
    assert "REFUSED" not in out
    assert _sha(ui / "candidates.jsonl") == settled


def test_a_queue_with_no_corrections_is_left_byte_identical(monkeypatch, capsys, tmp_path):
    ui = _build(tmp_path, events=[
        {"candidate_key": KEY, "action": "block", "reviewer": "local", "notes": "不對",
         "created_at": "2026-09-24T08:00:00"},
        {"candidate_key": KEY, "action": "accept", "reviewer": "local",
         "created_at": "2026-09-24T08:10:00"},
    ])
    before = _sha(ui / "candidates.jsonl")

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert "to write     : 0" in out
    assert _sha(ui / "candidates.jsonl") == before, "沒有 correction 的佇列一個位元組都不該動"
    assert sorted(os.listdir(ui)) == ["candidates.jsonl", "question_review_events.jsonl"]


# ------------------------------------------------------------------ the write is atomic

def test_the_write_is_atomic_and_leaves_no_temp_file(monkeypatch, capsys, tmp_path):
    ui = _build(tmp_path, events=[_machine_event(
        [{"field": "stem", "from": "⻑", "to": "長"}], {"stem": PAGE}, applied="substitution")])
    candidates = ui / "candidates.jsonl"
    calls = []
    real_replace = os.replace

    def record(source, destination, **kwargs):
        assert os.path.dirname(os.path.abspath(source)) == \
            os.path.dirname(os.path.abspath(str(destination))), "臨時檔必須與目的檔同目錄"
        assert os.path.basename(str(destination)) == "candidates.jsonl"
        # Negative control: at the moment of the swap the destination is still the *whole* old file.
        # A tool that truncated it in place would be observed here with a half-written file.
        assert "⻑" in open(str(destination), encoding="utf-8").read()
        calls.append((source, destination))
        return real_replace(source, destination, **kwargs)

    monkeypatch.setattr(os, "replace", record)
    assert _produce(monkeypatch, tmp_path, "--apply") == 0

    assert len(calls) == 1, "抽取檔要由一次 os.replace 換過去"
    assert os.path.basename(str(calls[0][0])) != "candidates.jsonl"
    assert sorted(os.listdir(ui)) == ["candidates.jsonl", "question_review_events.jsonl"], \
        "臨時檔不可以留下來"
    assert _rows(ui)[KEY]["stem"] == PAGE


def test_a_row_the_event_does_not_know_is_copied_through_byte_for_byte(monkeypatch, capsys,
                                                                       tmp_path):
    """Untouched rows are copied verbatim, so a rewritten queue differs from the old one only where
    a correction says it should. The line here is dumped with different separators than the JSON
    writer would produce, which is how a byte-for-byte copy shows itself."""
    odd_line = json.dumps(_row(stem=LONGER).copy() | {"candidate_key": "moex:other"}, ensure_ascii=False)
    odd_line = odd_line.replace(", ", ",").replace(": ", ":")
    ui = _build(tmp_path, events=[_machine_event(
        [{"field": "stem", "from": "⻑", "to": "長"}], {"stem": PAGE}, applied="substitution")])
    with (ui / "candidates.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(odd_line + "\n")

    assert _produce(monkeypatch, tmp_path, "--apply") == 0
    lines = (ui / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
    assert odd_line in lines, "沒有被 correction 碰到的列要原樣透過"


# ------------------------------------------------- the display fold keeps that same original

def _state():
    """A `ReviewState` with empty projections: `candidate_payload` reads them and nothing else."""
    state = ReviewState.__new__(ReviewState)
    for name in ("issues", "latest_reviews", "review_counts", "latest_reset_reviews",
                 "latest_answer_reviews", "answer_review_counts", "latest_ai_reviews",
                 "ai_review_counts", "latest_group_reviews", "latest_ai_feedbacks",
                 "latest_correction_feedbacks"):
        setattr(state, name, {})
    return state


def _fold_event():
    return {"action": "reset_review", "reviewer": "repair_dispute_apply",
            "correction": {"stem": PAGE}}


def test_the_display_fold_serves_the_persisted_original():
    """The server folds the correction onto the copy it serves. Once the producer has persisted an
    original, the fold must serve *that* text: the row's own text is the corrected one by then, so a
    fold that rebuilt the dict from the row would present the repair as if the extractor had always
    read it that way - and there is exactly one record of what the extractor read."""
    row = _row(stem=PAGE, parser_original={"stem": STORED})
    payload = _state().candidate_payload(row, latest_reset_reviews={KEY: _fold_event()})

    assert payload["stem"] == PAGE
    assert payload["parser_original"]["stem"] == STORED
    assert payload["parser_original"]["stem"] != payload["stem"]


def test_the_negative_control_rebuilding_from_the_row_would_serve_the_corrected_text():
    """The negative control, as its own test: the old fold was `{"stem": item.get("stem"), ...}`, and
    this row's stem is already the corrected text - so that fold returns the corrected text as the
    "original" and the assertion below goes red."""
    row = _row(stem=PAGE, parser_original={"stem": STORED})
    old_fold = {"stem": row.get("stem")}
    payload = _state().candidate_payload(row, latest_reset_reviews={KEY: _fold_event()})

    assert old_fold["stem"] == payload["stem"] == PAGE
    assert payload["parser_original"]["stem"] == STORED, "存過的原文才是原文"
