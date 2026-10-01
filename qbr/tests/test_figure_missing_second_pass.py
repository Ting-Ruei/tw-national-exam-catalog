# -*- coding: utf-8 -*-
"""figure-missing 證據包生產者：決策可驗、虛構檔名進不來、隊列零寫入。

契約（owner 2026-10-01 裁決）：deterministic 掃描已命中的 figure-ref-missing 題，指揮者
一次看齊證據（題面＋清單＋裁片影像）決策 insert/none/uncertain；本組測試看的是**記錄的誠實**
——答不可用就是 degraded、refs 只認磁碟清單、queue 一個 byte 都不動、pin 不符拒跑。
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

import figure_missing_second_pass as producer  # noqa: E402

sys.path.insert(0, HERE)
from test_engines import _stub_engine_server  # noqa: E402

GLM = "GLM-5.3-Flash-EXL3"

_CANDIDATE = {
    "candidate_key": "moex:100020:308:11:1:question:q031",
    "question_number": "q031", "subject": "基礎醫學(包括解剖學、生理學與病理學)", "paper": "100020:308",
    "stem": "下列何者構成腹股溝管（inguinal canal）的上壁？",
    "options": [{"key": "A", "text": "腹內斜肌與腹橫肌"}],
    "image_refs": [],
}

_WORKORDER_ROW = {
    "key": _CANDIDATE["candidate_key"], "lens": "figure-ref-missing",
    "note": "deterministic 掃描命中（逐欄）：[{\"run\": \"1152_醫事檢驗師_生物化學與臨床生化學\", "
            "\"question_number\": \"31\", \"crop_pattern\": \"q031_*\"}]",
    "acceptance": "裁片已在磁碟而 refs 空：插入哪個引用、插到哪一格；若無圖引用需求，明說。",
}


def _stub(responder):
    return _stub_engine_server(responder)


def _responder(posts, scripts):
    """`scripts`: list of (matcher, (status, payload)) — matched in order on POST bodies."""
    def handle(method, path, body):
        if method == "GET":
            return 200, {"data": [{"id": GLM}]}
        text = body.decode("utf-8", "replace")
        posts.append(text)
        for matcher, (status, payload) in scripts:
            if matcher in text:
                return status, payload
        return 500, {"error": "no canned answer"}
    return handle


def _chat_payload(obj):
    return {"choices": [{"message": {"content": json.dumps(obj, ensure_ascii=False)}}],
            "model": GLM}


def _write_fixture(tmp, with_crop=True):
    queue = os.path.join(tmp, "queue")
    crops = os.path.join(queue, "review-ui", "crops", "1152_醫事檢驗師_生物化學與臨床生化學")
    os.makedirs(crops, exist_ok=True)
    with open(producer.candidates_path(queue), "w", encoding="utf-8") as handle:
        handle.write(json.dumps(_CANDIDATE, ensure_ascii=False) + "\n")
    crop_name = "q031_embedded-image.png"
    if with_crop:
        with open(os.path.join(crops, crop_name), "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n-fixture")
    workorder = os.path.join(tmp, "wo.jsonl")
    with open(workorder, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(_WORKORDER_ROW, ensure_ascii=False) + "\n")
    out = os.path.join(tmp, "figure_drafts.jsonl")
    return queue, workorder, out, crop_name


def _stubbed_endpoint_env(server):
    import contextlib

    @contextlib.contextmanager
    def env():
        previous = os.environ.get("QBR_ENGINE_DGX_FLASH_URL")
        os.environ["QBR_ENGINE_DGX_FLASH_URL"] = "http://127.0.0.1:%d" % server.server_port
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("QBR_ENGINE_DGX_FLASH_URL", None)
            else:
                os.environ["QBR_ENGINE_DGX_FLASH_URL"] = previous
    return env()


def _run(queue, workorder, out, extra=None):
    argv = ["--workorder", workorder, "--queue", queue, "--out", out] + (extra or [])
    rc = producer.main(argv)
    rows = [json.loads(line) for line in open(out, encoding="utf-8")] if os.path.exists(out) else []
    return rc, rows


def test_insert_decision_writes_advisory_row_and_touches_no_queue_file():
    import tempfile
    posts = []
    tmp = tempfile.mkdtemp()
    queue, workorder, out, crop_name = _write_fixture(tmp)
    before = open(producer.candidates_path(queue), "rb").read()
    payload = _chat_payload({
        "decision": "insert", "refs": [crop_name],
        "where": "stem 後面附圖", "basis": "題幹在問腹股溝管結構，裁片是解剖圖"})
    server = _stub(_responder(posts, [("figure_ref_missing", (200, payload))]))
    with _stubbed_endpoint_env(server):
        rc, rows = _run(queue, workorder, out, ["--tag", "A"])
    server.shutdown()
    assert rc == 0
    assert len(rows) == 1
    row = rows[0]
    assert row["schema"] == "figure_missing_drafts v1"
    assert row["record"]["decision"] == "insert"
    assert row["record"]["refs"] == [crop_name]
    assert row["crop_files"] == [crop_name]
    assert row["tag"] == "A"
    assert row["served_model"] == GLM
    expected_sha8 = hashlib.sha256(open(workorder, "rb").read()).hexdigest()[:8]
    assert row["source"]["workorder_sha8"] == expected_sha8
    assert row["source"]["row"] == 1
    assert open(producer.candidates_path(queue), "rb").read() == before  # 隊列沒動


def test_invented_ref_is_clipped_and_recorded_ref_unverified():
    import tempfile
    posts = []
    tmp = tempfile.mkdtemp()
    queue, workorder, out, crop_name = _write_fixture(tmp)
    payload = _chat_payload({
        "decision": "insert", "refs": [crop_name, "images/made-up-fig.png"],
        "where": "stem", "basis": "編造一個不存在的檔名"})
    server = _stub(_responder(posts, [("figure_ref_missing", (200, payload))]))
    with _stubbed_endpoint_env(server):
        rc, rows = _run(queue, workorder, out)
    server.shutdown()
    assert rc == 0
    row = rows[0]
    assert row["record"]["refs"] == [crop_name]  # 虛構者剔除
    assert row["record"]["ref_unverified"] == 1


def test_unusable_answer_degrades_and_does_not_default_to_insert():
    import tempfile
    posts = []
    tmp = tempfile.mkdtemp()
    queue, workorder, out, _ = _write_fixture(tmp)
    payload = _chat_payload({"verdict": "TRUST", "why": "答非所問的判分格式"})
    server = _stub(_responder(posts, [("figure_ref_missing", (200, payload))]))
    with _stubbed_endpoint_env(server):
        rc, rows = _run(queue, workorder, out)
    server.shutdown()
    assert rc == 0
    row = rows[0]
    assert row["record"]["degraded"] is True  # 不預設 insert
    assert "decision" not in row["record"] or row["record"].get("decision") is None


def test_none_decision_forces_empty_refs():
    import tempfile
    posts = []
    tmp = tempfile.mkdtemp()
    queue, workorder, out, crop_name = _write_fixture(tmp)
    payload = _chat_payload({
        "decision": "none", "refs": [crop_name], "where": "", "basis": "這題不需要圖"})
    server = _stub(_responder(posts, [("figure_ref_missing", (200, payload))]))
    with _stubbed_endpoint_env(server):
        rc, rows = _run(queue, workorder, out)
    server.shutdown()
    assert rc == 0
    assert rows[0]["record"]["refs"] == []  # none/uncertain 一律清空


def test_pin_mismatch_refuses_to_start():
    import tempfile
    posts = []
    tmp = tempfile.mkdtemp()
    queue, workorder, out, _ = _write_fixture(tmp)

    def responder(method, path, body):
        if method == "GET":
            return 200, {"data": [{"id": "some-other-model"}]}
        posts.append(body)
        return 500, {}

    server = _stub(responder)
    with _stubbed_endpoint_env(server):
        argv = ["--workorder", workorder, "--queue", queue, "--out", out]
        rc = producer.main(argv)
    server.shutdown()
    assert rc == 2
    assert not os.path.exists(out)
    assert not posts  # 一個模型呼叫都沒發生


def test_missing_crops_skip_the_model_call():
    import tempfile
    posts = []
    tmp = tempfile.mkdtemp()
    queue, workorder, out, _ = _write_fixture(tmp, with_crop=False)
    server = _stub(_responder(posts, []))
    with _stubbed_endpoint_env(server):
        rc, rows = _run(queue, workorder, out)
    server.shutdown()
    assert rc == 0
    row = rows[0]
    assert not posts  # 沒有模型呼叫
    assert row["record"]["degraded"] is True


def test_compare_rates_identical_decision_and_ref_sets():
    import tempfile
    tmp = tempfile.mkdtemp()
    a = os.path.join(tmp, "a.jsonl")
    b = os.path.join(tmp, "b.jsonl")

    def row(key, decision, refs, degraded=False):
        return {"schema": producer.SCHEMA, "candidate_key": key,
                "record": {} if degraded else {"decision": decision, "refs": list(refs)}}

    with open(a, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(row("k1", "insert", ["x.png"]), ensure_ascii=False) + "\n")
        handle.write(json.dumps(row("k2", "insert", ["y.png"]), ensure_ascii=False) + "\n")
        handle.write(json.dumps(row("k3", "none", []), ensure_ascii=False) + "\n")
        handle.write(json.dumps(row("k4", "uncertain", [], degraded=True), ensure_ascii=False) + "\n")
    with open(b, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(row("k1", "insert", ["x.png"]), ensure_ascii=False) + "\n")
        handle.write(json.dumps(row("k2", "insert", ["DIFFERENT.png"]), ensure_ascii=False) + "\n")
        handle.write(json.dumps(row("k3", "none", []), ensure_ascii=False) + "\n")

    import io
    import contextlib
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rc = producer.main(["--compare", a, b])
    assert rc == 0
    report = json.loads(buffer.getvalue())
    # k4 只在一輪可用 ⇒ 不進母體；k1/k3 同、k2 異
    assert report == {"both": 3, "same": 2, "rate": 0.667}