# -*- coding: utf-8 -*-
"""指揮者的行為，以及它**不能**做的事。

兩個負控制，各自對應一個實測過的失敗模式：

1. `test_the_orchestrator_cannot_change_a_review_state`——它可以判斷，不可以決定。charter 第 3 節
   與 GOV-05：AI 不自動 accept/block。這一條在有人把 `record_review` 接進指揮者時必須失敗。
2. `test_a_closed_gate_sends_nothing`——出網閘門關著時**一個 byte 都不送**。這一條在閘門被繞過
   （例如有人在呼叫端自己組請求）時必須失敗。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))

from qbr import engines, orchestrator  # noqa: E402


def _q():
    return {"candidate_key": "moex:a:q001", "question_number": 1, "subject": "放射線器材學",
            "paper": "111020:309", "kinds": ["substituted-script"],
            "stem": "在醫用直線加速器中?ћ磁鐵ќ聚焦線圈", "options": [{"text": "ћќѝў"}]}


def _finding():
    return {"verdict": "DEFECT", "where": "stem：紙本「1磁鐵2聚焦線圈」→ 抽取「ћ磁鐵ќ聚焦線圈」",
            "fix": "把 ћ 改成 1", "transcription": {"stem": "①磁鐵 ②聚焦線圈"}}


def _endpoint():
    return {"url": "http://example.invalid", "name": "test-orch", "key": "k",
            "reasoning": "none", "external": True}


# ------------------------------------------------------------------ 出網閘門

def test_a_closed_gate_sends_nothing(monkeypatch):
    """**負控制。** 閘門關著時：`ask` 不發請求、回 `None`、稽核檔不存在。

    舊行為（沒有閘門）會直接把題目內容送到外部 provider——這正是 charter 第 6 節要另案修訂的事。
    """
    monkeypatch.delenv("QBR_ALLOW_EXTERNAL_LLM", raising=False)
    audit = "/tmp/qbr-test-audit-should-not-exist.jsonl"
    if os.path.exists(audit):
        os.remove(audit)
    monkeypatch.setenv("QBR_EXTERNAL_AUDIT_LOG", audit)

    called = {"n": 0}

    def _no_network(request, timeout=None):
        called["n"] += 1
        raise AssertionError("閘門關著卻發了網路請求")

    monkeypatch.setattr(engines.urllib.request, "urlopen", _no_network)

    raw, seconds = engines.ask([{"role": "user", "content": "題目內容"}],
                               endpoint=_endpoint(), max_tokens=10, timeout=5)
    assert raw is None
    assert seconds == 0.0
    assert called["n"] == 0, "閘門關著卻嘗試連線"
    assert not os.path.exists(audit), "沒有出網卻寫了稽核紀錄"


def test_an_open_gate_lets_the_call_through(monkeypatch):
    """閘門開著才會送。這一條證明上一條不是「什麼都不做也能過」。"""
    monkeypatch.setenv("QBR_ALLOW_EXTERNAL_LLM", "1")
    monkeypatch.setenv("QBR_EXTERNAL_AUDIT_LOG", "/tmp/qbr-test-audit-open.jsonl")
    if os.path.exists("/tmp/qbr-test-audit-open.jsonl"):
        os.remove("/tmp/qbr-test-audit-open.jsonl")
    seen = {}

    class _Response:
        def read(self):
            return json.dumps({"choices": [{"message": {"content": "{}"}}],
                               "usage": {"prompt_tokens": 5, "completion_tokens": 1}}).encode()

    def _fake(request, timeout=None):
        seen["body"] = json.loads(request.data.decode())
        return _Response()

    monkeypatch.setattr(engines.urllib.request, "urlopen", _fake)
    raw, _seconds = engines.ask([{"role": "user", "content": "題目內容"}],
                                endpoint=_endpoint(), max_tokens=10, timeout=5)
    assert raw is not None
    # And what left is recorded, with its size — the audit is the approval's evidence.
    with open("/tmp/qbr-test-audit-open.jsonl", encoding="utf-8") as handle:
        entry = json.loads(handle.readline())
    assert entry["ok"] is True
    assert entry["prompt_bytes"] > 0
    assert entry["model"] == "test-orch"
    # The audit records the size, never the prompt text.
    assert "題目內容" not in json.dumps(entry, ensure_ascii=False)


def test_a_local_engine_is_not_gated(monkeypatch):
    """內網引擎不受閘門影響；否則「關掉外網」會等於「關掉整個迴圈」。"""
    monkeypatch.delenv("QBR_ALLOW_EXTERNAL_LLM", raising=False)
    local = {"url": "http://127.0.0.1:1", "name": "local", "key": "", "reasoning": "none"}
    assert engines.egress_refusal(local) == ""


# ------------------------------------------------------------------ 判斷的解析

def test_a_trust_verdict_does_not_look(monkeypatch):
    assert orchestrator.parse_judgement(
        {"choices": [{"message": {"content": '{"verdict":"TRUST","why":"機械性還原","self_look":false}'}}]}
    ) == {"verdict": "TRUST", "why": "機械性還原", "self_look": False}


def test_a_care_verdict_forces_a_look(monkeypatch):
    """說「可疑」卻不看，是自相矛盾——一致化，不是相信模型答得工整。"""
    parsed = orchestrator.parse_judgement(
        {"choices": [{"message": {"content": '{"verdict":"CARE","why":"可能改答案","self_look":false}'}}]}
    )
    assert parsed["self_look"] is True, "CARE 卻不看，下游的『可疑就要看』會被繞過"


def test_an_unrecognised_verdict_is_a_failure_not_a_default():
    """不能預設 TRUST（什麼都放行），也不能預設 DOUBT（什麼都問人）。"""
    for bad in ('{"verdict":"MAYBE"}', "我看不出來", "", '{"why":"沒有 verdict"}'):
        assert orchestrator.parse_judgement(
            {"choices": [{"message": {"content": bad}}]}) is None, bad


def test_a_fenced_json_answer_is_still_readable():
    parsed = orchestrator.parse_judgement(
        {"choices": [{"message": {"content": '```json\n{"verdict":"DOUBT","why":"有編造痕跡"}\n```'}}]}
    )
    assert parsed["verdict"] == "DOUBT"


# ------------------------------------------------------------------ 資料邊界

def test_triage_sends_the_diff_but_not_the_whole_question():
    """分流要送出**判準**（哪裡不同），不必送出整題。

    兩個都要：只送 verdict 不送 where，指揮者只能說「不知道你在講什麼」——實測三題全部回 CARE，
    理由一模一樣（「地端回報 DEFECT 但未說明缺陷內容」）。那不是判斷，是模型正確地回報「你什麼
    都沒給我」。這個 population 的 `what` 是 null，全部實質內容都在 `where`。
    """
    payload = orchestrator.judgement_payload(_q(), _finding())
    body = json.dumps(payload, ensure_ascii=False)
    # 判準在：地端說哪裡不同
    assert payload["local_model"]["where"], "分流沒送 where，指揮者只能盲猜"
    assert "ћ磁鐵" in body, "where 裡的差異是判準，要送"
    # 但整題的文字與紙本讀法不在
    assert "extracted" not in payload, "分流階段就送了整題抽取文字"
    assert "page_read" not in payload, "分流階段就送了紙本讀法"
    assert payload["dispute_kinds"] == ["substituted-script"]


def test_looking_sends_the_content():
    """決定要看之後才送內容。兩個 payload 的差別就是那條界線。"""
    payload = orchestrator.look_payload(_q(), _finding())
    assert payload["extracted"]["stem"]
    assert payload["page_read"]["stem"] == "①磁鐵 ②聚焦線圈"


# ------------------------------------------------------------------ 權限邊界

def test_the_orchestrator_cannot_change_a_review_state():
    """**負控制。** 指揮者可以判斷，不可以決定。

    它不得寫任何 review event、不得 accept/block、不得改題目文字。這是 charter 第 3 節與 GOV-05。
    這一條在有人把 review 寫入接進 `orchestrator.py` 時必須失敗。
    """
    source = open(os.path.join(PKG, "src", "qbr", "orchestrator.py"), encoding="utf-8").read()
    for forbidden in ("append_event", "review_events", "save_preferences", "record_review",
                      "apply_dispute_repairs"):
        assert forbidden not in source, \
            "指揮者碰了人的決定（%s）——它只做 advisory，決定權在人" % forbidden
    # And it is advisory by construction: every public entry point returns a record, never a state.
    import ast

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("judge", "review_with_orchestrator"):
            returns = [n for n in ast.walk(node) if isinstance(n, ast.Return)]
            assert returns, node.name


# ------------------------------------------------------------------ 五項輸入的整合

def test_the_judge_is_told_the_persons_note_and_what_was_already_refused():
    """業主 2026-09-25：「你應該參考原則 ＋ 原始題目的整個狀態 ＋ 截圖PDF找線索 ＋ 之前自己寫的
    怎麼修 ＋ 人的註記或建議 統合這些資料來解決問題，感覺目前沒有整合好」。

    量到的事實（改動前）：`judgement_payload` 只有題號／科目／卷／爭議種類與 `local_model` 的
    verdict/what/where/confidence。人的那句話與「上次這個改動被退」只進**地端模型的提示詞**，
    指揮者一無所知——而 ③ 的整欄改寫唯一的放行依據就是指揮者的 TRUST。這裡用同一個渲染函式
    （`ai_findings.notes_note`／`rejected_note`／`principles_note`）給它同一段文字，不另寫摘要。
    """
    rejected = {"count": 1, "fields": ["option B"],
                "changes": [{"field": "option B", "from": "較⻑", "to": "較長"}]}
    payload = orchestrator.judgement_payload(_q(), _finding(), notes="選項的上下標要檢查",
                                             rejected=rejected,
                                             principles=["選項文字不可以自己加標點。"])
    note = payload["reviewer_note"]
    refused = payload["previous_repairs_refused"]
    principles = payload["reviewer_principles"]
    assert "選項的上下標要檢查" in note and "審題者的註解" in note
    assert "已經被改過又被退" in refused and "較⻑" in refused and "較長" in refused
    assert "選項文字不可以自己加標點。" in principles

    # 負控制：沒有這些頻道時，欄位**不得**出現（空字串會變成一個沒有內容的區塊，讀者還得猜）。
    bare = orchestrator.judgement_payload(_q(), _finding())
    assert "reviewer_note" not in bare and "previous_repairs_refused" not in bare
    assert "reviewer_principles" not in bare
    # 分流呼叫是小的那一次：一份長成論文的註解不可以把它變成第二次判讀。
    long_note = orchestrator.judgement_payload(_q(), _finding(), notes="甲" * 5000)
    assert len(long_note["reviewer_note"]) <= 800


def test_looking_sends_the_figure_facts_when_there_are_any():
    """「這題的圖長什麼樣子」是指揮者判斷的最後一塊：文字差異是空的時候（選項是圖），只剩圖可看。"""
    assert "figure_facts" not in orchestrator.look_payload(_q(), _finding())
    payload = orchestrator.look_payload(_q(), _finding(), figures="這一題的圖片：1 張（option-image）")
    assert "option-image" in payload["figure_facts"]


def _judgement_engine(answers, captured):
    """A fake `engines.ask` that replays `answers` and records the messages it was given.

    Wrapped in the gateway envelope (`choices[0].message.content`) because that is the shape
    `engines.content_of` reads - a fake that returned bare strings would test a response shape no
    engine produces.
    """
    def ask(messages, *, endpoint, max_tokens, timeout):
        captured.append(messages)
        return {"choices": [{"message": {"content": answers.pop(0)}}]}, 0.5
    return ask


def test_the_second_look_actually_sends_the_picture(monkeypatch, tmp_path=None):
    """**負控制。** `ORCHESTRATOR_SYSTEM` 一直寫著指揮者可以「自己看那張圖」，而改動前送出的第二次
    呼叫**只有文字**、`look_payload` 連一個放圖的路徑欄位都沒有——實測 2026-09-25。這一條在有人把
    圖拿掉、或把 `crop_path` 接錯時必須失敗。"""
    import tempfile

    monkeypatch.setenv("QBR_ALLOW_EXTERNAL_LLM", "1")
    crop = os.path.join(tempfile.mkdtemp(), "q001-dispute.png")
    with open(crop, "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    captured = []
    answers = ['{"verdict":"CARE","why":"讀法可疑","self_look":true}',
               '{"page_read":{"stem":"①磁鐵"},"disagrees_with_local":true,"why":"少了②"}']
    monkeypatch.setattr(orchestrator.engines, "ask", _judgement_engine(answers, captured))

    record, note = orchestrator.review_with_orchestrator(
        _q(), _finding(), endpoint=_endpoint(), crop_path=crop)
    assert note == "" and record["self_look"] is True
    first, second = captured
    assert isinstance(first[1]["content"], str), "分流是小的那一次：只送結構化欄位"
    assert isinstance(second[1]["content"], list), "『自己看』送出的還是純文字"
    assert second[1]["content"][0]["type"] == "text"
    image = second[1]["content"][1]
    assert image["type"] == "image_url"
    assert image["image_url"]["url"].startswith("data:image/png;base64,")
    assert record["look_image"] is True
    assert record["second_look"]["disagrees_with_local"] is True


def test_a_question_with_no_crop_still_gets_its_second_look():
    """沒有截圖時：判斷照做、紀錄明說 `look_image: false`，而且提示詞裡明說「沒有截圖可送」——
    不假裝看過圖。"""
    captured = []
    answers = ['{"verdict":"DOUBT","why":"有編造痕跡","self_look":true}',
               '{"page_read":{},"disagrees_with_local":true,"why":"看不出來"}']
    original = orchestrator.engines.ask
    os.environ["QBR_ALLOW_EXTERNAL_LLM"] = "1"
    orchestrator.engines.ask = _judgement_engine(answers, captured)
    try:
        record, note = orchestrator.review_with_orchestrator(
            _q(), _finding(), endpoint=_endpoint(), crop_path="/nowhere/missing.png")
    finally:
        orchestrator.engines.ask = original
        os.environ.pop("QBR_ALLOW_EXTERNAL_LLM", None)
    assert note == "" and record["look_image"] is False
    assert isinstance(captured[1][1]["content"], str)
    assert "沒有截圖可送" in captured[1][1]["content"]
