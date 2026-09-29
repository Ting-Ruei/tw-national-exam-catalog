# -*- coding: utf-8 -*-
"""The progress panel's readout of **which engine judged**.

Owner's instruction (2026-09-25): 「30分鐘驅動者與做事者的LLM都改用192.168.10.90:8888，才能逐漸
評估地端的做事能力」. A number that averages two engines answers "how is the pipeline doing" but not
"how is *the on-prem box* doing" — and the switch itself is only visible as a change in that number.
So `orchestration.verdicts` stays what it was (the whole population) and the per-model split is added
beside it, over the same records, so the two can be compared without re-reading the 500 MB stream.

What is tested here is the *readout*, not the engine: the counting is a pure function of the records,
so it can be tested without a model, a queue, or a station.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

import report_repair_progress as report  # noqa: E402


def _record(key, model, verdict, at="2026-09-25T05:00:00Z"):
    return {"candidate_key": key, "at": at,
            "finding": {"verdict": verdict, "what": "TEXT"},
            "orchestration": {"verdict": verdict, "model": model}}


def test_each_engine_keeps_its_own_verdict_tallies():
    records = [
        _record("k1", "deepseek-v4.1-flash", "CARE"),
        _record("k2", "qwen3.8-flash-next", "TRUST"),
        _record("k3", "qwen3.8-flash-next", "CARE"),
        _record("k4", "qwen3.8-flash-next", "CARE"),
    ]
    summary = report.summarize(records, examples=5)

    # The whole-population number is unchanged by the split: it is still one counter over everything.
    assert summary["orchestration"]["verdicts"] == {"CARE": 3, "TRUST": 1}
    assert summary["orchestration"]["by_model"] == {
        "deepseek-v4.1-flash": {"CARE": 1},
        "qwen3.8-flash-next": {"TRUST": 1, "CARE": 2},
    }

    lines = report.render(summary, examples=5).splitlines()
    source = [line for line in lines if line.startswith("  判讀來源：")]
    assert len(source) == 1, lines
    # Busiest engine first, so the line reads as "what is judging today" rather than as a list.
    assert source[0].index("qwen3.8-flash-next") < source[0].index("deepseek-v4.1-flash")
    assert "qwen3.8-flash-next TRUST 1、CARE 2" in source[0]


def test_one_engine_prints_no_source_line_at_all():
    """負控制：只有一顆引擎判過時，那一行**不能出現**。

    「換過引擎了」與「還沒換」長得一樣，就等於沒有讀數。而且這一行的存在本身就是一個訊息：
    在 owner 剛把驅動者搬到地端的那幾輪，兩種 model 並存正是「切換正在發生」的證據。
    """
    summary = report.summarize([_record("k1", "qwen3.8-flash-next", "CARE")], examples=5)
    assert summary["orchestration"]["by_model"] == {"qwen3.8-flash-next": {"CARE": 1}}
    assert "判讀來源：" not in report.render(summary, examples=5)


def test_a_judgement_without_a_recorded_engine_is_not_attributed_to_one():
    """舊紀錄（改動之前判的）沒有 `model`：它要自成一個名字，不能併進任何一顆。

    併進去會讓某一顆的數字看起來變好，而那個變化來自時間而不是能力——正是這個讀數要回答的問題。
    """
    legacy = {"candidate_key": "k9", "at": "2026-09-25T05:00:00Z",
              "orchestration": {"verdict": "DOUBT"}}
    degraded = {"candidate_key": "k8", "orchestration": {"degraded": True, "model": "none"}}
    summary = report.summarize([legacy, degraded, _record("k7", "qwen3.8-flash-next", "TRUST")],
                               examples=5)

    assert summary["orchestration"]["by_model"] == {
        "（未記引擎）": {"DOUBT": 1},
        "qwen3.8-flash-next": {"TRUST": 1},
    }
    assert summary["orchestration"]["degraded"] == 1
    # `degraded` 是「指揮者沒回答」，不是某一顆的裁決：它有自己的數字，不進任何 model 的計數。
    assert sum(sum(counts.values()) for counts in summary["orchestration"]["by_model"].values()) == 2


def _ownership(tmp_path, *, stats=None, records=None, stamp="20260925-210000"):
    review = tmp_path / "review-ui"
    review.mkdir(parents=True, exist_ok=True)
    (review / "figure_ownership.json").write_text(json.dumps({
        "stamp": stamp, "stats": stats or {}, "records": records or []}),
        encoding="utf-8")
    return str(review)


def test_the_figure_pass_says_out_loud_what_it_removed_and_whose_words_did_it(tmp_path):
    """圖版那一趟的決定只寫在 `figure_ownership.json`——一份人不會打開的檔案。

    業主 2026-09-25 的抱怨就是這樣來的：「我已經強調有些題目沒有圖…但他還是不改？」所以那一趟的
    結果要落在**他讀的那張紙**上：移除幾張、以及逐題印出他的原話。
    """
    directory = _ownership(tmp_path, stats={
        "dropped-no-figure": 26, "dropped-by-note": 6, "widened": 51, "sliver": 4},
        records=[
            {"key": "moex:108030:305:11:1:question:q061", "number": 61,
             "dropped": "human-flagged-and-no-picture-measured",
             "note": "這題主題目就是沒有圖，你為什麼多放一張圖", "note_at": "2026-09-25T05:16:40"},
            {"key": "moex:105100:305:11:1:question:q078", "number": 78,
             "dropped": "human-said-extra-crop", "note": "多截圖", "quote": "多截圖"},
            {"key": "moex:1:2:3:4:question:q001", "number": 1, "widened": [[9, [1, 2, 3, 4]]]}])
    text = report.figure_section(directory)

    assert "人說沒有圖 ⇒ 不放圖 26" in text
    assert "重切／加寬 51" in text
    assert "moex:108030:305:11:1:question:q061" in text
    assert "這題主題目就是沒有圖，你為什麼多放一張圖" in text, "要印他的原話，不是機器說的話"
    # 「多截圖」走的是另一條（讀他的話）⇒ 它的紀錄不能假裝自己是「紙本量不到」那一種。
    assert "moex:105100:305:11:1:question:q078" not in text


def test_a_queue_without_a_figure_pass_prints_nothing_rather_than_zeros(tmp_path):
    """沒跑過那一趟就不該印一行全 0——那會讀成「跑了，什麼都沒做」。"""
    review = tmp_path / "review-ui"
    review.mkdir(parents=True)
    assert report.figure_section(str(review)) == ""
