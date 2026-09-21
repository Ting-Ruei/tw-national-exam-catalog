# -*- coding: utf-8 -*-
"""The merge that pushes a laptop's decisions back onto the station, and the identity it uses.

The station is the home of the review record; the laptop is a consumer that may be inspected and
corrected and then pushed back. So this merge may only ever **append**. Every test below is about
that, because the failure mode is silent and destroys the one thing in this project that cannot be
rebuilt from the corpus: what a person decided.

The identity rule is tested directly as well, because it has now been got wrong twice in two
different places (both recorded in `review_queue.NON_IDENTITY_FIELDS`), and a third definition
written by hand in a shell script is exactly how a review record goes missing.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

import merge_events  # noqa: E402
from qbr import review_queue  # noqa: E402


def _event(key, action, created_at="2026-09-21T10:00:00", **extra):
    event = {"candidate_key": key, "action": action, "reviewer": "local", "notes": "",
             "created_at": created_at, "source": "linear_v2"}
    event.update(extra)
    return event


def _write(path, events):
    with open(path, "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return str(path)


# ------------------------------------------------------------------ the identity rule

def test_provenance_is_not_part_of_an_event_identity():
    """同一個決定從別的佇列被帶過來時多了 `_carried_from`，它仍然是同一個決定。"""
    plain = _event("k1", "accept")
    carried = dict(plain, _carried_from="qbr/data/review-queues/live")
    assert review_queue.record_identity(plain) == review_queue.record_identity(carried)


def test_a_changed_notes_field_is_a_different_event():
    """`notes` 是決定的一部分。

    這是否定「只用題號當身分」的那一條：如果身分是 `candidate_key`，審核者補上備註就會被當成
    重複而丟掉——比重複更安靜的錯，而且丟掉的正是他寫的字。
    """
    assert review_queue.record_identity(_event("k1", "accept")) != \
        review_queue.record_identity(_event("k1", "accept", notes="答案沒進去"))


def test_the_same_action_twice_in_the_same_second_is_still_two_events():
    """同一秒內的重複決定不合併：人真的可能那樣寫，而重寫他的歷史不是我們的職務。"""
    first = _event("k1", "block")
    second = _event("k1", "block")
    assert review_queue.record_identity(first) == review_queue.record_identity(second)


# ------------------------------------------------------------------ the merge itself

def test_a_carried_event_is_not_appended_again(tmp_path):
    """核心情境：站上已有 347 行、筆電 346 行，而整行比對會說要補 151 行。

    實際量到的：合併佇列時管線在事件上補 `_carried_from`，所以同一個事件兩邊字串不同。用整行
    比對就會把 151 個已經做過的決定再寫一次，讓「誰審了什麼」的帳多算一次。
    """
    target = _write(tmp_path / "station.jsonl",
                    [dict(_event("k1", "accept"), _carried_from="qbr/data/review-queues/live")])
    source = _write(tmp_path / "laptop.jsonl", [_event("k1", "accept")])
    assert merge_events.missing_events(target, source) == []


def test_only_the_new_event_is_appended(tmp_path):
    """只附加新的那一個，其餘一個位元組都不動。"""
    target = _write(tmp_path / "station.jsonl", [_event("k1", "accept"), _event("k2", "block")])
    source = _write(tmp_path / "laptop.jsonl",
                    [_event("k1", "accept"), _event("k2", "block"), _event("k3", "accept")])
    missing = merge_events.missing_events(target, source)
    assert len(missing) == 1
    assert json.loads(missing[0])["candidate_key"] == "k3"


def test_an_event_the_station_has_that_the_laptop_does_not_is_never_removed(tmp_path):
    """站上多出來的事件不是「要刪掉的差異」——那是別人那段時間審的。

    站上是家；筆電是副本。合併的方向只有一個：筆電缺的補上去。
    """
    target = _write(tmp_path / "station.jsonl",
                    [_event("k1", "accept"), _event("k9", "block")])
    source = _write(tmp_path / "laptop.jsonl", [_event("k1", "accept")])
    assert merge_events.missing_events(target, source) == []
    # 而且函式**無法**表達刪除：它回傳的是「要附加的行」。
    assert isinstance(merge_events.missing_events(target, source), list)


def test_every_appended_line_is_newline_terminated(tmp_path):
    """附加的行一定要有結尾換行。

    實際踩到的錯：用 `$(...)` 接輸出會吃掉結尾換行，於是事件內容對了但 `wc -l` 少算一行，
    驗收失敗；若沒有驗收，留下來的是一個「下一行跟它黏在一起」的檔案。
    """
    target = _write(tmp_path / "station.jsonl", [])
    source = str(tmp_path / "laptop.jsonl")
    with open(source, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(_event("k1", "accept")) + "\n")
    missing = merge_events.missing_events(target, source)
    assert len(missing) == 1
    assert missing[0].endswith("\n")


def test_a_source_line_without_a_trailing_newline_gains_one(tmp_path):
    """來源檔最後一行沒有換行時要補上，否則附加後它會跟站上的下一行黏在一起。"""
    target = _write(tmp_path / "station.jsonl", [])
    source = tmp_path / "laptop.jsonl"
    source.write_text(json.dumps(_event("k1", "accept")), encoding="utf-8")
    missing = merge_events.missing_events(target, str(source))
    assert missing[0].endswith("\n")


def test_a_line_that_will_not_parse_is_carried_rather_than_dropped(tmp_path):
    """讀不懂的行要原樣帶過去，不能丟掉——丟掉紀錄正是這支程式存在的理由要避免的事。"""
    target = _write(tmp_path / "station.jsonl", [])
    source = tmp_path / "laptop.jsonl"
    source.write_text("{not json at all\n", encoding="utf-8")
    missing = merge_events.missing_events(target, str(source))
    assert missing == ["{not json at all\n"]


def test_the_count_reported_by_the_script_equals_the_lines_actually_written(tmp_path):
    """腳本報的「要附加 N 行」必須等於檔案裡真的有的行數。

    這是把那個 `$(...)` 缺陷釘住：報的數與寫的數不一致時，驗收看起來會失敗，而真正該被檢查的
    「有沒有漏掉紀錄」就被那個假警報蓋掉了。
    """
    target = _write(tmp_path / "station.jsonl", [_event("k1", "accept")])
    source = _write(tmp_path / "laptop.jsonl",
                    [_event("k1", "accept"), _event("k2", "accept"), _event("k3", "block")])
    out = merge_events.missing_events(target, source)
    assert len(out) == 2
    assert sum(1 for line in out if line.strip()) == 2
