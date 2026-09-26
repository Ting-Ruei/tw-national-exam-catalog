# -*- coding: utf-8 -*-
"""逐題註解 → 基本原則：只有**新的**才進去，而且要指得出來源。

使用者的規則：「逐題 comment 自己讀，是原本沒有的規則才加入」。這一條裡有兩個字最容易做錯：

1. **新的**。9 筆註解裡 8 筆是既有規則已涵蓋的形狀（上下標、字形）或一次性事實。
   把它們也寫成原則，提示詞變長、下一輪的模型多背幾句沒有用的話。
2. **自己讀**。這是「讀出文字的意義」，專案說它是提示詞，不是腳本——所以判讀結果是一句
   給模型看的話，不是新的 `if`。

最貴的失敗模式是**把機器自己寫的字當成人寫的**：`comment` 事件的 `notes` 被介面拿
`reset_review` 的說明文字（「上下標修復：…請重新確認。」）預填過，那句話在 3 題上一字不差。
它是機器的公告，不是審題者的原則。下面的測試把這件事釘住。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

import curate_principles_from_comments as curate  # noqa: E402
from qbr import ai_findings, discuss, reread  # noqa: E402

#: The machine's own sentence, copied here verbatim so the test fails loudly if the curated text ever
#: starts to include it. Source: the `reset_review` events' `notes` (e.g. q055/q059/q061/q071/q076/q062).
MACHINE_HEADER = "上下標修復：紙本印刷的英文上下標"


def _comment(key, notes):
    return {"action": "comment", "candidate_key": key, "notes": notes, "note_action": "note"}


def _args(events_path, principles_path, **over):
    values = {
        "events": str(events_path),
        "principles": str(principles_path),
        "reviewer": "principle_curator",
        "apply": False,
    }
    values.update(over)
    return type("Args", (), values)()


def test_evidence_must_be_a_comment_that_is_really_in_the_log(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps(_comment("moex:a:q001", "表格要用圖")) + "\n", encoding="utf-8")
    keys = curate.comment_keys(curate.read_events(str(events)))
    assert keys == {"moex:a:q001"}
    # A question that is in the log but was only *blocked* is not a source for a principle: the rule
    # comes from a note, and a block with no note says nothing about what the note-writer wanted.
    events.write_text(json.dumps({"action": "block", "candidate_key": "moex:b:q002"}) + "\n",
                      encoding="utf-8")
    assert curate.comment_keys(curate.read_events(str(events))) == set()


def test_only_the_new_rule_is_added_and_it_is_grounded(tmp_path):
    # 9 comment events over 7 keys, one of which (`q071`) carries the new table rule. The curated set
    # is a decision made by reading; this asserts the decision, and that its claim of novelty is
    # backed by a comment that exists.
    events = tmp_path / "events.jsonl"
    rows = [_comment("moex:108030:305:33:1:question:q059", "⁻ 0.23t 是上標"),
            _comment("moex:108030:305:33:1:question:q071", "表格應該用截圖的"),
            _comment("moex:115020:305:0403:1:question:q062", "Ae-αt＋Be-βt 並沒有改到"),
            _comment("moex:100140:104:0304:1:question:q008", "\ue2c6 應該是 酶")]
    events.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                      encoding="utf-8")
    principles = tmp_path / discuss.PRINCIPLES_STREAM
    rc = curate_main(events, principles, reviewer="principle_curator", apply=True)
    assert rc == 0
    texts = discuss.active_principles(discuss.load_events(str(principles)))
    assert len(texts) == 1, "只有表格那條算新規則，實際寫了 %d 條" % len(texts)
    assert "表格" in texts[0]
    event = discuss.load_events(str(principles))[0]
    assert event["evidence"] == ["moex:108030:305:33:1:question:q071"]
    assert event["source"] == "comment_review"
    assert event["reviewer"] == "principle_curator", "代理的身分要誠實，不可以是 local 或人的名字"


def test_the_machine_sentence_is_never_curated_as_a_principle(tmp_path):
    """機器預填的那句話不是人的原則。

    這是負對照：如果判讀把 `reset_review` 的說明當成人寫的，表格以外就會多出一條含
    `MACHINE_HEADER` 的原則，這個測試會紅。
    """
    for item in curate.CURATED:
        assert MACHINE_HEADER not in item["text"], \
            "把機器產生的修復說明當成了人的原則：%s" % item["text"]


def test_rerunning_adds_nothing_the_second_time(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps(_comment("moex:108030:305:33:1:question:q071", "表格應該用截圖的"),
                                 ensure_ascii=False) + "\n", encoding="utf-8")
    principles = tmp_path / discuss.PRINCIPLES_STREAM
    assert curate_main(events, principles, apply=True) == 0
    before = discuss.load_events(str(principles))
    assert len(before) == 1
    # The second run must not append a duplicate - text equality is the dedup key, matching how the
    # UI's own "add" button collides two identical principles into one.
    assert curate_main(events, principles, apply=True) == 0
    assert len(discuss.load_events(str(principles))) == 1


def test_a_principle_whose_source_is_missing_is_refused(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps(_comment("moex:108030:305:33:1:question:q071", "表格應該用截圖的"),
                                 ensure_ascii=False) + "\n", encoding="utf-8")
    principles = tmp_path / discuss.PRINCIPLES_STREAM
    # Point one curated item at a key with no comment: it is not evidence, it is an assertion.
    original = curate.CURATED
    curate.CURATED = ({**original[0], "evidence": ["moex:does-not-exist:q999"]},)
    try:
        assert curate_main(events, principles, apply=True) == 0
    finally:
        curate.CURATED = original
    assert not os.path.isfile(str(principles)), "沒有來源的原則不該被寫進流裡"


def test_the_curator_cannot_call_itself_a_human(tmp_path, capsys):
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps(_comment("moex:108030:305:33:1:question:q071", "表格應該用截圖的"),
                                 ensure_ascii=False) + "\n", encoding="utf-8")
    principles = tmp_path / discuss.PRINCIPLES_STREAM
    for forbidden in ("local", "human", "reviewer", ""):
        assert curate_main(events, principles, reviewer=forbidden, apply=True) == 2
    assert not os.path.isfile(str(principles))
    assert "冒充" in capsys.readouterr().err


def test_the_written_principle_reaches_the_prompts_that_will_read_it(tmp_path):
    """寫進流裡卻沒有讀者，就是一個沒有出口的欄位。

    兩條讀它：轉錄的系統提示（`reread.SYSTEM` + `principles_note`），以及審核的
    `build_prompt`。這一條驅動**真的**那兩個函式，證明句子真的到得了模型眼前。
    """
    principles = tmp_path / discuss.PRINCIPLES_STREAM
    discuss.append_event(str(principles), {
        "action": "add", "principle_id": "p1",
        "text": "表格要以紙本圖為準，不要用抽取的數字拼成文字表格"})
    active = discuss.active_principles(discuss.load_events(str(principles)))
    assert active
    transcription = reread.SYSTEM + ai_findings.principles_note(active)
    assert active[0] in transcription
    _, user = ai_findings.build_prompt({"stem": "s", "options": []}, population="dispute",
                                       principles=active)
    system = ai_findings.build_prompt({"stem": "s", "options": []}, population="dispute",
                                      principles=active)[0]
    assert active[0] in system


# ----------------------------------------------------------------- helpers

def curate_main(events, principles, **over):
    """Run `curate.main()` with an argparse-like namespace, without touching process argv."""
    real_parse_args = curate.parse_args
    curate.parse_args = lambda: _args(events, principles, **over)
    try:
        return curate.main()
    finally:
        curate.parse_args = real_parse_args
