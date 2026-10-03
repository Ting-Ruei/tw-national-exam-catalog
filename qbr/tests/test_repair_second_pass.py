# -*- coding: utf-8 -*-
"""修題證據包生產者（L2）：證據必須真實、理由必進提示詞、不可用就是 degraded、queue 零寫入。

契約（REPAIR_LOOP_PLAN.md §4 L2）：你的 block 理由是 prompt 第一優先、歷次退回理由依序、
經驗隨行；fix 是整欄替換不是建議；裁片不存在於磁碟的引用就是 degraded——每個檢查帶負對照。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

import repair_second_pass as producer  # noqa: E402

sys.path.insert(0, HERE)
from test_engines import _stub_engine_server  # noqa: E402

OCCAMY = "occamy-1.0-6bit-xl-mlx"

_CANDIDATE = {
    "candidate_key": "moex:100020:308:11:1:question:q031",
    "question_number": "q031", "subject": "基礎醫學(包括解剖學、生理學與病理學)",
    "stem": "下列有關一般人過度換氣之敘述，何者正確？一般人的呼吸。",
    "options": [{"key": "A", "text": "甲選項"}, {"key": "B", "text": "乙選項"}],
    "answer": "A",
    "answer_payload": {"answer": "A", "raw_answer": "A", "accepted_values": ["A"]},
    "image_refs": [],
}

_CROP_NAME = "1152_物理治療師_物理治療基礎學_q031.png"


def _write_fixture(tmp, with_crop=True):
    queue = os.path.join(tmp, "queue")
    store = os.path.join(tmp, "store")
    crops = os.path.join(queue, "review-ui", "crops", "1152_物理治療師_物理治療基礎學")
    os.makedirs(crops, exist_ok=True)
    os.makedirs(store, exist_ok=True)
    with open(producer.candidates_path(queue), "w", encoding="utf-8") as handle:
        handle.write(json.dumps(_CANDIDATE, ensure_ascii=False) + "\n")
    crop_path = os.path.join(crops, _CROP_NAME)
    if with_crop:
        with open(crop_path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n-fixture")
    workorder = os.path.join(tmp, "wo.jsonl")
    with open(workorder, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "key": _CANDIDATE["candidate_key"],
            "human_block_reason": "題幹第 2 行錯字：『一般人的呼吸』應為『健康成人的呼吸』",
            "return_reasons": ["上次把答案也改了，不要動答案"],
            "lessons": ["藥劑學：『值』常被讀成『差』——逐字核對"],
            "crops": [crop_path], "fields": ["題幹"],
        }, ensure_ascii=False) + "\n")
    out = os.path.join(tmp, "repair_run.jsonl")
    return queue, store, workorder, out, crop_path


def _responder(posts, scripts):
    def handle(method, path, body):
        if method == "GET":
            return 200, {"data": [{"id": OCCAMY}]}
        text = body.decode("utf-8", "replace")
        posts.append(text)
        for matcher, (status, payload) in scripts:
            if matcher in text:
                return status, payload
        return 500, {"error": "no canned answer"}
    return handle


def _chat_payload(obj):
    return {"choices": [{"message": {"content": json.dumps(obj, ensure_ascii=False)}}],
            "model": OCCAMY}


def _stub_env(server):
    import contextlib

    @contextlib.contextmanager
    def env():
        key = "QBR_ENGINE_OCCAMY_6BIT_URL"
        previous = os.environ.get(key)
        os.environ[key] = "http://127.0.0.1:%d" % server.server_port
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
    return env()


def _run(queue, store, workorder, out, extra=None):
    os.environ["REPAIR_AGENT_STORE"] = store
    argv = ["--workorder", workorder, "--queue", queue, "--out", out] + (extra or [])
    rc = producer.main(argv)
    rows = [json.loads(line) for line in open(out, encoding="utf-8")] if os.path.exists(out) else []
    return rc, rows


def _drafts(store):
    path = os.path.join(store, "repair_drafts.jsonl")
    if not os.path.exists(path):
        return []
    return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]


def test_proposal_writes_draft_row_and_touches_no_queue_file():
    import tempfile
    posts = []
    tmp = tempfile.mkdtemp()
    queue, store, workorder, out, crop_path = _write_fixture(tmp)
    before = open(producer.candidates_path(queue), "rb").read()
    payload = _chat_payload({
        "fix": "下列有關健康成人過度換氣之敘述，何者正確？一般人的呼吸。",
        "insert": "題幹", "basis": "紙本第 2 行：『健康成人的呼吸』"})
    server = _stub_engine_server(_responder(posts, [("repair_proposal", (200, payload))]))
    try:
        with _stub_env(server):
            rc, rows = _run(queue, store, workorder, out, ["--tag", "A"])
    finally:
        server.shutdown()
    assert rc == 0
    assert len(rows) == 1
    row = rows[0]
    assert row["schema"] == producer.SCHEMA
    assert row["record"]["fix"].startswith("下列有關健康成人"), "record 帶整欄 fix"
    assert row["record"]["insert"] == "題幹"
    assert row["record"]["basis"] == "紙本第 2 行：『健康成人的呼吸』"
    assert row["crop_files"] == [_CROP_NAME], "證據是磁碟上真實存在的裁片"
    assert open(producer.candidates_path(queue), "rb").read() == before, "queue 一個 byte 都不動"

    # 草案進 store（status proposed），帶 crop 的 sha256 與自己的 provenance 欄位
    drafts = _drafts(store)
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft["status"] == "proposed"
    assert draft["candidate_key"] == _CANDIDATE["candidate_key"]
    assert draft["fix"] == row["record"]["fix"]
    import hashlib
    assert draft["crop"]["sha256"] == hashlib.sha256(open(crop_path, "rb").read()).hexdigest()


def test_prompt_carries_block_reason_return_reasons_and_lessons():
    """負對照：理由沒進 prompt——AI 學不到「錯在哪」，迴圈就不是迴圈。"""
    import tempfile
    posts = []
    tmp = tempfile.mkdtemp()
    queue, store, workorder, out, _ = _write_fixture(tmp)
    payload = _chat_payload({"fix": "下列有關健康成人過度換氣之敘述，何者正確？一般人的呼吸。",
                             "insert": "題幹", "basis": "紙本第 2 行"})
    server = _stub_engine_server(_responder(posts, [("repair_proposal", (200, payload))]))
    try:
        with _stub_env(server):
            _run(queue, store, workorder, out)
    finally:
        server.shutdown()
    sent = json.loads(posts[0])  # body_for 以 ensure_ascii 送出——先解開再找字
    user_text = " ".join(
        part.get("text", "") if isinstance(part, dict) else str(part)
        for part in sent["messages"][-1]["content"]) if isinstance(
            sent["messages"][-1]["content"], list) else sent["messages"][-1]["content"]
    assert "題幹第 2 行錯字" in user_text, "你的 block 理由（第一優先）必須在 prompt 裡"
    assert "上次把答案也改了" in user_text, "歷次退回理由必須在 prompt 裡"
    assert "逐字核對" in user_text, "該科目經驗必須在 prompt 裡"


def test_missing_crop_degrades_and_writes_no_draft():
    """鐵律 5：生產者只引用真實存在的證據——虛構路徑是 degraded，不是草案。"""
    import tempfile
    tmp = tempfile.mkdtemp()
    queue, store, workorder, out, _ = _write_fixture(tmp, with_crop=False)
    rc, rows = _run(queue, store, workorder, out)
    assert rc == 0
    assert rows[0]["record"] == {"degraded": True}
    assert "裁片不存在" in rows[0]["note"]
    assert _drafts(store) == [], "沒有證據就沒有草案"


def test_unusable_answer_degrades_without_defaulting():
    import tempfile
    tmp = tempfile.mkdtemp()
    queue, store, workorder, out, _ = _write_fixture(tmp)
    for canned, why in [
        ({"degraded": True, "note": "裁片看不清"}, "自報 degraded"),
        ({"fix": "", "insert": "題幹", "basis": "x"}, "空 fix"),
        ({"fix": "改完的題幹。", "insert": "選項 E", "basis": "x"}, "不存在的選項"),
        ({"fix": "改完的題幹。", "insert": "不認得的落點", "basis": "x"}, "認不得的 insert"),
        ({"fix": "下列有關一般人過度換氣之敘述，何者正確？一般人的呼吸。",
          "insert": "題幹", "basis": "x"}, "fix 與現值相同"),
    ]:
        for f in os.listdir(store):
            os.remove(os.path.join(store, f))
        server = _stub_engine_server(_responder([], [("repair_proposal", (200, _chat_payload(canned)))]))
        try:
            with _stub_env(server):
                rc, rows = _run(queue, store, workorder, out)
        finally:
            server.shutdown()
        assert rc == 0, why
        assert rows[0]["record"].get("degraded") is True, (why, rows)
        assert _drafts(store) == [], (why, "不可用的答案沒有草案")
        os.remove(out)


def test_wrong_pin_refuses_to_run():
    import tempfile
    tmp = tempfile.mkdtemp()
    queue, store, workorder, out, _ = _write_fixture(tmp)
    server = _stub_engine_server(lambda m, p, b: (200, {"data": [{"id": "next-leader-v2"}]}))
    try:
        with _stub_env(server):
            rc, _ = _run(queue, store, workorder, out)
    finally:
        server.shutdown()
    assert rc == 2, "pin 不符要拒跑，不是拿錯模型硬跑"
